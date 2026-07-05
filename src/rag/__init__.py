"""src/rag — LangChain / LangGraph による医療ガイドライン RAG 本体。

チャンクの埋め込み（多言語ローカルモデル）→ Chroma への格納 → 質問に対する
検索 → 根拠付き回答生成（日本語・出典引用）を担当する。

設計判断（architecture.md の未決事項をここで確定）:
- 埋め込み: `intfloat/multilingual-e5-small`（ローカル・追加APIキー不要）。日本語質問→英語文書の
  クロスリンガル検索が可能。e5 は "query:" / "passage:" 接頭辞が必須。
- ベクトルDB: Chroma（`chroma/` に永続化・cosine 距離）。
- なぜ LangGraph か: 検索(retrieve)→生成(generate) を State を持つ 2 ノードのグラフにし、
  検索した Document（出典 metadata 付き）を state に載せて回答へ引用させる。単純な chain より
  「回答根拠の出典追跡」を state で明示的に扱え、将来 根拠不足時の再検索ノート追加に拡張しやすい。
- 生成 LLM: Anthropic Claude（既定 claude-opus-4-8・env `ANTHROPIC_MODEL` で上書き可）。
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

from src.ingest import load_and_chunk

load_dotenv()  # リポジトリ直下 .env の ANTHROPIC_API_KEY 等を環境変数へ

# ---- 設定（self-contained な既定値。必要なら環境変数で上書き）----
PRODUCT_ROOT = Path(__file__).resolve().parents[2]  # products/medguide-rag
DEFAULT_PDF = PRODUCT_ROOT / "data" / "sample" / "who-hearts-healthy-lifestyle-counselling.pdf"
CHROMA_DIR = PRODUCT_ROOT / "chroma"
COLLECTION = "who-hearts"
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
TOP_K = int(os.getenv("RAG_TOP_K", "4"))

SYSTEM_PROMPT = (
    "あなたは医療ガイドライン文書の検索アシスタントです。以下のルールを厳守してください。\n"
    "1. 回答は必ず『提供された抜粋（英語）』の内容だけを根拠にする。抜粋に無い情報は決して述べない。\n"
    "2. 抜粋に根拠が無い、または質問が文書の対象外の場合は『提供された文書には記載がありません』と答える。\n"
    "3. 回答は日本語で、平易に述べる。主張の末尾には根拠にした抜粋番号を [1] のように付す。\n"
    "4. 最後に『※これは学習用デモの文書要約であり、医療上の助言ではありません』と一文添える。"
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
    pdf_path: str | Path = DEFAULT_PDF,
    persist_dir: str | Path = CHROMA_DIR,
) -> tuple[Chroma, int]:
    """Chroma 索引を取得。空なら PDF を ingest して構築・永続化する。

    戻り値: (vectorstore, 格納チャンク数)
    """
    vs = Chroma(
        collection_name=COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(persist_dir),
        collection_metadata={"hnsw:space": "cosine"},  # 正規化埋め込み＝cosine が適切
    )
    existing = vs.get()  # 既存 id 一覧
    count = len(existing.get("ids", []))
    if count == 0:
        chunks = load_and_chunk(pdf_path)
        if not chunks:
            # テキスト抽出に失敗（スキャン画像PDF等）→ 空索引で add_documents が例外化する前に明示エラー
            raise ValueError(f"チャンクが空です（PDFのテキスト抽出に失敗した可能性）: {pdf_path}")
        vs.add_documents(chunks)
        count = len(chunks)
    return vs, count


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


def build_graph(vs: Chroma, model: str = ANTHROPIC_MODEL, top_k: int = TOP_K):
    """検索→生成の LangGraph をコンパイルして返す。"""
    # temperature 等のサンプリング引数は渡さない（opus-4-8 等では 400 になるため）
    llm = ChatAnthropic(model=model, max_tokens=1024, timeout=60, stop=None)

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
    pdf_path: str | Path = DEFAULT_PDF,
    persist_dir: str | Path = CHROMA_DIR,
    model: str = ANTHROPIC_MODEL,
    top_k: int = TOP_K,
):
    """索引（無ければ構築）＋グラフを用意して返す。

    戻り値: (graph, vectorstore, チャンク数)
    """
    vs, n = get_or_build_index(pdf_path, persist_dir)
    graph = build_graph(vs, model=model, top_k=top_k)
    return graph, vs, n


def ask(graph, question: str) -> RAGState:
    """1 問を投げ、question / docs（出典追跡用）/ answer を含む state を返す。"""
    return graph.invoke({"question": question})
