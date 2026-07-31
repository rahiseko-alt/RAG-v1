"""src/rag — LangChain / LangGraph によるナレッジ RAG 本体。

チャンクの埋め込み（多言語ローカルモデル）→ Chroma への格納 → 質問に対する
検索 → 根拠付き回答生成（日本語・出典引用）を担当する。

設計判断（architecture.md の未決事項をここで確定）:
- 埋め込み: `intfloat/multilingual-e5-small`（ローカル・追加APIキー不要）。日本語質問→英語文書の
  クロスリンガル検索が可能。e5 は "query:" / "passage:" 接頭辞が必須。
- ベクトルDB: Chroma（`chroma/` に永続化・cosine 距離）。
- なぜ LangGraph か: 検索(retrieve)→生成(generate) を State を持つ 2 ノードのグラフにし、
  検索した Document（出典 metadata 付き）を state に載せて回答へ引用させる。単純な chain より
  「回答根拠の出典追跡」を state で明示的に扱え、将来 根拠不足時の再検索ノート追加に拡張しやすい。
- 生成 LLM: env `LLM_PROVIDER` で OpenAI / Anthropic を切替。既定は OpenAI。
  文脈のみを根拠に日本語で答え、文脈に無ければ「記載なし」と答えさせる（幻覚抑止）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langgraph.graph import END, START, StateGraph

from src.ingest import load_and_chunk, source_sha256
from src.knowledge_config import get_active_knowledge
from src.observability import build_langfuse_runnable_config

load_dotenv()  # リポジトリ直下 .env の ANTHROPIC_API_KEY 等を環境変数へ

# ---- 設定（self-contained な既定値。必要なら環境変数で上書き）----
PRODUCT_ROOT = Path(__file__).resolve().parents[2]  # products/medguide-rag
KNOWLEDGE = get_active_knowledge()
DEFAULT_SOURCE = KNOWLEDGE.source_path
CHROMA_DIR = PRODUCT_ROOT / "chroma"
COLLECTION = os.getenv("CHROMA_COLLECTION", KNOWLEDGE.collection)
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
TOP_K = int(os.getenv("RAG_TOP_K", "4"))

SYSTEM_PROMPT = (
    "あなたは登録済みナレッジ文書の検索アシスタントです。以下のルールを厳守してください。\n"
    "1. 回答は必ず『提供された抜粋』の内容だけを根拠にする。抜粋に無い情報は決して述べない。\n"
    "2. 抜粋に根拠が無い、または質問が文書の対象外の場合は『提供された文書には記載がありません』と答える。\n"
    "3. 回答は日本語で、平易に述べる。主張の末尾には根拠にした抜粋番号を [1] のように付す。\n"
    "4. 最後に『※これは学習用デモの文書要約です』と一文添える。"
)


class E5Embeddings(HuggingFaceEmbeddings):
    """multilingual-e5 系は文書側に "passage: "、質問側に "query: " の接頭辞が必要。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([f"passage: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return super().embed_query(f"query: {text}")


def get_embeddings() -> E5Embeddings:
    """ローカル多言語埋め込みモデルを返す（初回はモデルDLが走る）。"""
    return E5Embeddings(
        model_name=EMBED_MODEL,
        encode_kwargs={"normalize_embeddings": True},  # cosine 前提で正規化
    )


def get_or_build_index(
    source_path: str | Path = DEFAULT_SOURCE,
    persist_dir: str | Path = CHROMA_DIR,
) -> tuple[Chroma, int]:
    """Chroma 索引を取得。空ならナレッジを ingest して構築・永続化する。

    戻り値: (vectorstore, 格納チャンク数)
    """
    source_path = Path(source_path)
    current_sha = source_sha256(source_path)
    vs = Chroma(
        collection_name=COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(persist_dir),
        collection_metadata={"hnsw:space": "cosine"},  # 正規化埋め込み＝cosine が適切
    )
    existing = vs.get()  # 既存 id 一覧
    count = len(existing.get("ids", []))
    if count:
        metadatas = existing.get("metadatas", [])
        indexed_shas = {m.get("source_sha256") for m in metadatas if m and m.get("source_sha256")}
        if indexed_shas != {current_sha}:
            vs.delete_collection()
            vs = Chroma(
                collection_name=COLLECTION,
                embedding_function=get_embeddings(),
                persist_directory=str(persist_dir),
                collection_metadata={"hnsw:space": "cosine"},
            )
            count = 0
    if count == 0:
        chunks = load_and_chunk(source_path)
        if not chunks:
            raise ValueError(f"チャンクが空です（文書のテキスト抽出に失敗した可能性）: {source_path}")
        for chunk in chunks:
            chunk.metadata["source_sha256"] = current_sha
        vs.add_documents(chunks)
        count = len(chunks)
    return vs, count


def get_llm_provider() -> str:
    """回答生成に使うLLMプロバイダ名を返す。"""
    if LLM_PROVIDER not in {"anthropic", "openai"}:
        raise ValueError("LLM_PROVIDER must be 'anthropic' or 'openai'")
    return LLM_PROVIDER


def get_generation_model() -> str:
    """現在のLLMプロバイダに対応するモデル名を返す。"""
    return OPENAI_MODEL if get_llm_provider() == "openai" else ANTHROPIC_MODEL


def is_generation_configured() -> bool:
    """現在のLLMプロバイダで回答生成できるAPIキーがあるかを返す。"""
    if get_llm_provider() == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def build_chat_model(model: str | None = None):
    """設定されたプロバイダの LangChain chat model を作る。"""
    provider = get_llm_provider()
    model = model or get_generation_model()
    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=openai is configured, but 'langchain-openai' is not installed. "
                "Run: pip install langchain-openai"
            ) from exc
        return ChatOpenAI(model=model, timeout=60)
    # temperature 等のサンプリング引数は渡さない（opus-4-8 等では 400 になるため）
    return ChatAnthropic(model=model, max_tokens=1024, timeout=60, stop=None)


# ---- LangGraph: retrieve → generate ----
class RAGState(TypedDict):
    question: str
    docs: list[tuple[Document, float]]  # (チャンク, 類似度スコア)
    answer: str


def _format_context(docs: list[tuple[Document, float]]) -> str:
    """検索チャンクを、番号付き・出典付きの文脈テキストに整形する。"""
    blocks = []
    for i, (d, _score) in enumerate(docs, start=1):
        page = d.metadata.get("page", "?")
        src = d.metadata.get("source", "?")
        blocks.append(f"[{i}] (出典: {src} p.{page})\n{d.page_content.strip()}")
    return "\n\n".join(blocks)


def build_graph(vs: Chroma, model: str | None = None, top_k: int = TOP_K):
    """検索→生成の LangGraph をコンパイルして返す。"""
    llm = build_chat_model(model=model)

    def retrieve(state: RAGState) -> dict[str, Any]:
        results = vs.similarity_search_with_relevance_scores(state["question"], k=top_k)
        return {"docs": results}

    def generate(state: RAGState) -> dict[str, Any]:
        context = _format_context(state["docs"])
        human = (
            f"次の抜粋だけを根拠に、質問に日本語で答えてください。\n\n"
            f"=== 抜粋 ===\n{context}\n\n=== 質問 ===\n{state['question']}"
        )
        msg = llm.invoke([("system", SYSTEM_PROMPT), ("human", human)])
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        return {"answer": text}

    g = StateGraph(RAGState)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    return g.compile()


def make_rag(
    source_path: str | Path = DEFAULT_SOURCE,
    persist_dir: str | Path = CHROMA_DIR,
    model: str | None = None,
    top_k: int = TOP_K,
):
    """索引（無ければ構築）＋グラフを用意して返す。

    戻り値: (graph, vectorstore, チャンク数)
    """
    vs, n = get_or_build_index(source_path, persist_dir)
    graph = build_graph(vs, model=model, top_k=top_k)
    return graph, vs, n


def ask(
    graph,
    question: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    trace_tags: list[str] | None = None,
) -> RAGState:
    """1 問を投げ、question / docs（出典追跡用）/ answer を含む state を返す。

    Langfuse の環境変数が設定されている場合は、LangChain callback 経由で検索・生成の
    トレースを送信する。未設定なら config なしで従来通り実行する。
    """
    config = build_langfuse_runnable_config(
        question=question,
        session_id=session_id,
        user_id=user_id,
        tags=trace_tags,
    )
    if config is None:
        return graph.invoke({"question": question})
    return graph.invoke({"question": question}, config=config)
