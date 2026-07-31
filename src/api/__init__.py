"""FastAPI entrypoint for the medguide-rag question answering demo."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.knowledge_config import get_active_knowledge
from src.observability import get_langfuse_config_error, is_langfuse_configured
from src.rag import EMBED_MODEL, TOP_K, ask, get_generation_model, get_llm_provider, is_generation_configured, make_rag
from src.runtime_errors import safe_error_message


STATIC_DIR = Path(__file__).resolve().parent / "static"


class AskRequest(BaseModel):
    """HTTP request body for one RAG question."""

    question: str = Field(..., min_length=1)
    session_id: str | None = None
    user_id: str | None = None
    trace_tags: list[str] | None = None


class SourceHit(BaseModel):
    """One retrieved source chunk shown to the caller."""

    rank: int
    source: str
    page: int | str
    chunk_id: int | None = None
    score: float
    snippet: str


class ProcessStep(BaseModel):
    """One observable processing step for non-engineer review."""

    id: str
    title: str
    status: str
    purpose: str
    input: str
    process: str
    output: str
    check: str


class AskResponse(BaseModel):
    """RAG answer with source-tracking metadata."""

    question: str
    answer: str
    sources: list[SourceHit]
    chunks_indexed: int
    model: str
    audit_enabled: bool
    knowledge: dict[str, Any]
    process_steps: list[ProcessStep]


_RAG_CACHE: dict[str, Any] = {}


def reset_rag_cache() -> None:
    """Clear the lazy RAG cache. Mainly useful for tests and local reloads."""
    _RAG_CACHE.clear()


def get_rag() -> tuple[Any, Any, int]:
    """Build the RAG graph lazily and reuse it across requests."""
    if "graph" not in _RAG_CACHE:
        graph, vs, chunks_indexed = make_rag()
        _RAG_CACHE.update({"graph": graph, "vectorstore": vs, "chunks_indexed": chunks_indexed})
    return _RAG_CACHE["graph"], _RAG_CACHE.get("vectorstore"), int(_RAG_CACHE["chunks_indexed"])


def _format_sources(docs: list[tuple[Any, float]]) -> list[SourceHit]:
    hits: list[SourceHit] = []
    for rank, (doc, score) in enumerate(docs, start=1):
        text = " ".join(doc.page_content.split())
        hits.append(
            SourceHit(
                rank=rank,
                source=str(doc.metadata.get("source", "?")),
                page=doc.metadata.get("page", "?"),
                chunk_id=doc.metadata.get("chunk_id"),
                score=round(float(score), 3),
                snippet=text[:240],
            )
        )
    return hits


def _score_band(score: float) -> str:
    if score >= 0.85:
        return "高"
    if score >= 0.75:
        return "中"
    return "低"


def _summarize_indexed_chunks(vs: Any) -> str:
    """Summarize the actual indexed chunks visible to Chroma."""
    if vs is None:
        return "テスト用グラフのため、索引チャンク一覧は取得していません。"
    try:
        raw = vs.get(include=["metadatas", "documents"])
    except Exception as exc:
        return f"索引チャンク一覧の取得に失敗: {safe_error_message(exc)}"

    rows: list[tuple[int, str]] = []
    documents = raw.get("documents", []) or []
    metadatas = raw.get("metadatas", []) or []
    for doc, metadata in zip(documents, metadatas):
        metadata = metadata or {}
        chunk_id = metadata.get("chunk_id")
        try:
            sort_key = int(chunk_id)
        except (TypeError, ValueError):
            sort_key = 999999
        snippet = " ".join(str(doc).split())[:90]
        rows.append(
            (
                sort_key,
                f"chunk {chunk_id}: {metadata.get('source', '?')} p.{metadata.get('page', '?')} / "
                f"{len(str(doc))}文字 / {snippet}",
            )
        )
    if not rows:
        return "Chroma collection内に表示できるチャンクがありません。"
    return "\n".join(row for _key, row in sorted(rows, key=lambda item: item[0]))


def _summarize_retrieved_sources(sources: list[SourceHit]) -> str:
    if not sources:
        return "取得0件。質問に近い候補は返りませんでした。"
    return "\n".join(
        f"[{s.rank}] chunk {s.chunk_id} / score {s.score:.3f}（{_score_band(s.score)}） / "
        f"{s.source} p.{s.page} / 抜粋: {s.snippet}"
        for s in sources
    )


def _answer_excerpt(answer: str) -> str:
    compact = " ".join(answer.split())
    return compact[:360] + ("..." if len(compact) > 360 else "")


def _build_process_steps(
    *,
    raw_question: str,
    question: str,
    sources: list[SourceHit],
    chunks_indexed: int,
    model: str,
    audit_enabled: bool,
    knowledge: dict[str, Any],
    vs: Any = None,
    answer: str = "",
) -> list[ProcessStep]:
    top = sources[0] if sources else None
    top_summary = (
        f"{top.source} / chunk {top.chunk_id} / 検索スコア {top.score:.3f}（{_score_band(top.score)}）"
        if top
        else "該当候補なし"
    )
    source_names = _summarize_retrieved_sources(sources)
    indexed_chunks = _summarize_indexed_chunks(vs)
    removed_chars = len(raw_question) - len(question)
    query_for_embedding = f"query: {question}"
    evidence_check = (
        "根拠候補あり。スコアは近さの目安なので、回答中の固有名詞・条件・時系列が抜粋と一致するかを見る。"
        if sources
        else "根拠候補なし。回答は原則『提供された文書には記載がありません』になるべき。"
    )
    return [
        ProcessStep(
            id="received",
            title="質問受付",
            status="完了",
            purpose="今回入力された質問を、検索と生成に渡す正式な質問文として確定した。",
            input=f"受信した生入力: {raw_question}",
            process=f"前後空白を除去。削除文字数: {removed_chars}。空文字チェックを通過。",
            output=f"処理後の質問: {question} / 文字数: {len(question)}",
            check="この時点で質問文が変わっていたら、UI入力または前処理の問題。今回は上記の質問文で後続工程へ渡した。",
        ),
        ProcessStep(
            id="knowledge",
            title="ナレッジ選択",
            status="完了",
            purpose="今回の質問に対して、どのナレッジ本文と索引を使ったかを確定した。",
            input=f"設定ファイル: {knowledge.get('config_path', '-')}",
            process=(
                "TOML設定を読み、本文パス、collection、出典URL、評価セットを確定。"
                f"本文: {knowledge.get('source_path', '-')} / 評価セット: {knowledge.get('eval_set', '-')}"
            ),
            output=f"使用ナレッジ: {knowledge.get('title', '-')} / collection: {knowledge.get('collection', '-')} / 登録チャンク数: {chunks_indexed}",
            check="回答はこのナレッジ外の情報で補ってはいけない。想定と違うナレッジ名なら設定ミス。",
        ),
        ProcessStep(
            id="searching",
            title="意味検索",
            status="完了",
            purpose="登録済みチャンクの中から、今回の質問に近い候補を実際に取得した。",
            input=f"検索対象 {chunks_indexed}件:\n{indexed_chunks}",
            process=(
                f"検索用文字列に変換: {query_for_embedding}\n"
                f"埋め込みモデル: {EMBED_MODEL}\n"
                f"Chroma cosine collection: {knowledge.get('collection', '-')}\n"
                f"取得要求: top_k={TOP_K}"
            ),
            output=f"実際に取得した {len(sources)}件:\n{source_names}",
            check="検索スコアは正確性ではなく近さ。取得された抜粋に質問へ答える材料があるかを次工程で確認する。",
        ),
        ProcessStep(
            id="sources",
            title="根拠候補確認",
            status="完了",
            purpose="取得した候補の中に、回答に使える根拠が含まれていたかを確認した。",
            input=f"意味検索で取得した候補:\n{source_names}",
            process="各候補について、rank、chunk_id、検索スコア、出典、抜粋を保存し、回答画面の根拠パネルへ渡した。",
            output=f"最上位候補: {top_summary}\n判定メモ: {evidence_check}",
            check="必要情報が抜粋にないのに回答が断定していたら生成ミス。必要情報を含むチャンクが取得されていなければ検索ミス。",
        ),
        ProcessStep(
            id="generating",
            title="回答生成",
            status="完了",
            purpose="確認済みの候補抜粋だけを根拠に、今回の回答を生成した。",
            input=f"モデル: {model} / LLMへ渡した根拠候補数: {len(sources)}\n渡した候補:\n{source_names}",
            process="システムプロンプトで『提供された抜粋だけを根拠にする』『抜粋にない情報は記載なし』と制約し、抜粋番号付きで回答させた。",
            output=f"生成後の回答:\n{_answer_excerpt(answer)}",
            check="回答中の固有名詞、術式名、時系列、数値が上の取得候補に含まれているかを照合する。",
        ),
        ProcessStep(
            id="audit",
            title="監査ログ",
            status="完了" if audit_enabled else "未送信",
            purpose="今回の実行を後から追跡できる状態にした。",
            input=f"Langfuse監査設定: {'ON' if audit_enabled else 'OFF'} / session trace tags はリクエスト値を使用。",
            process="有効時はLangChain callback経由で検索・生成のトレース送信を試みた。無効時はAPIレスポンス内の工程詳細をローカル証跡として返した。",
            output=f"APIレスポンスに knowledge、sources、process_steps を含めて返却。外部監査: {'送信対象' if audit_enabled else '未送信'}。",
            check="問題調査では、この工程詳細とLangfuse traceを突き合わせる。ログは修正せず、ナレッジ・設定・評価セットを変えて再実行する。",
        ),
    ]


def create_app() -> FastAPI:
    app = FastAPI(
        title="medguide-rag API",
        description="Medical guideline RAG demo with source tracking and optional Langfuse audit traces.",
        version="0.1.0",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": get_llm_provider(),
            "generation_configured": is_generation_configured(),
            "anthropic_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
            "langfuse_configured": is_langfuse_configured(),
            "langfuse_config_error": get_langfuse_config_error(),
            "model": get_generation_model(),
            "top_k": TOP_K,
            "knowledge": get_active_knowledge().public_dict(),
        }

    @app.post("/ask", response_model=AskResponse)
    def ask_endpoint(payload: AskRequest) -> AskResponse:
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question must not be blank")
        if not is_generation_configured():
            provider = get_llm_provider()
            key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
            raise HTTPException(
                status_code=503,
                detail=f"{key_name} is not configured for LLM_PROVIDER={provider}. Set it in .env before calling /ask.",
            )

        try:
            graph, vs, chunks_indexed = get_rag()
            state = ask(
                graph,
                question,
                session_id=payload.session_id,
                user_id=payload.user_id,
                trace_tags=payload.trace_tags,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"RAG execution failed: {safe_error_message(exc)}") from exc

        knowledge = get_active_knowledge().public_dict()
        sources = _format_sources(state["docs"])
        return AskResponse(
            question=question,
            answer=state["answer"],
            sources=sources,
            chunks_indexed=chunks_indexed,
            model=get_generation_model(),
            audit_enabled=is_langfuse_configured(),
            knowledge=knowledge,
            process_steps=_build_process_steps(
                raw_question=payload.question,
                question=question,
                sources=sources,
                chunks_indexed=chunks_indexed,
                model=get_generation_model(),
                audit_enabled=is_langfuse_configured(),
                knowledge=knowledge,
                vs=vs,
                answer=state["answer"],
            ),
        )

    return app


app = create_app()
