"""FastAPI entrypoint for the medguide-rag question answering demo."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, model_validator

from src.observability import (
    check_langfuse_trace,
    get_langfuse_config_error,
    is_langfuse_configured,
)
from src.quality import (
    QualityWorkbench,
    WorkbenchConflictError,
    WorkbenchNotFoundError,
    WorkbenchStore,
)
from src.rag import (
    EMBED_MODEL,
    TOP_K,
    engine_fingerprint,
    get_generation_model,
    get_llm_provider,
    is_generation_configured,
    make_rag,
)
from src.runtime_errors import safe_error_message
from src.structured_knowledge import KnowledgeEntity, KnowledgeFact


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 15 * 1024 * 1024


class AskRequest(BaseModel):
    """HTTP request body for one RAG question."""

    question: str = Field(..., min_length=1, max_length=4000)
    revision_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    answer_mode: Literal["strict", "standard", "explore"] = "standard"
    trace_tags: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None,
        max_length=20,
    )


class SourceHit(BaseModel):
    """One retrieved source chunk shown to the caller."""

    rank: int
    source: str
    page: int | str
    chunk_id: int | None = None
    score: float
    snippet: str
    source_id: str | None = None
    source_url: str | None = None
    source_type: str | None = None
    authority: str | None = None
    fetched_at: str | None = None
    retrieval_relation: str = "direct"
    anchor_chunk_id: int | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None
    structured_record_id: str | None = None
    structured_kind: str | None = None
    structured_score: float | None = None


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
    run_id: str
    delivery_status: str
    answer: str | None
    answer_mode: str
    blocked_reason: str | None
    verification: dict[str, Any]
    audit: dict[str, Any]
    sources: list[SourceHit]
    chunks_indexed: int
    model: str
    audit_enabled: bool
    knowledge: dict[str, Any]
    process_steps: list[ProcessStep]


class RunCreateResponse(BaseModel):
    run_id: str
    status: str
    events_url: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    revision_id: str
    question: str
    answer_mode: str
    response: dict[str, Any] | None = None
    error_message: str | None = None
    events: list[dict[str, Any]]


class RevisionCreateRequest(BaseModel):
    source_path: str | None = Field(default=None, min_length=1, max_length=512)
    content: str | None = Field(default=None, min_length=1, max_length=5_000_000)
    content_base64: str | None = Field(default=None, min_length=1, max_length=14_000_000)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    operator: str | None = Field(default=None, max_length=120)
    change_summary: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_source(self) -> "RevisionCreateRequest":
        choices = [
            self.source_path is not None,
            self.content is not None,
            self.content_base64 is not None,
        ]
        if sum(choices) != 1:
            raise ValueError("provide exactly one of source_path, content, or content_base64")
        if self.content_base64 is not None and self.filename is None:
            raise ValueError("filename is required with content_base64")
        return self


class JobCreateRequest(BaseModel):
    question_limit: int | None = Field(default=None, ge=1, le=200)
    question: str | None = Field(default=None, min_length=1, max_length=4000)


class DecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)
    operator: str | None = Field(default=None, max_length=120)


class StructuredRecordsRequest(BaseModel):
    entities: list[KnowledgeEntity] = Field(default_factory=list, max_length=5000)
    facts: list[KnowledgeFact] = Field(default_factory=list, max_length=20000)


class StructuredExtractionRequest(BaseModel):
    query: str | None = Field(default=None, min_length=1, max_length=4000)
    chunk_limit: int | None = Field(default=20, ge=1, le=500)
    persist: bool = True


class CoverageLoopRequest(BaseModel):
    focus: str | None = Field(default=None, min_length=1, max_length=1000)
    questions: list[Annotated[str, Field(min_length=1, max_length=4000)]] = Field(
        default_factory=list,
        max_length=30,
    )
    external_answers: dict[str, Any] = Field(default_factory=dict)
    knowledge_answers: dict[str, Any] = Field(default_factory=dict)
    fact_checks: dict[str, Any] = Field(default_factory=dict)
    rounds: int = Field(default=1, ge=1, le=5)
    max_questions_per_round: int = Field(default=5, ge=1, le=20)
    answer_mode: Literal["strict", "standard", "explore"] = "standard"
    allow_llm_agents: bool = False
    run_knowledge_answerer: bool = False
    persist: bool = True


class CoverageCandidateResolutionRequest(BaseModel):
    status: Literal["auto_approved", "auto_rejected"]
    reason: str = Field(default="", max_length=500)
    operator: str | None = Field(default=None, max_length=120)


_RAG_CACHE: dict[str, Any] = {}
_DEFAULT_WORKBENCH: QualityWorkbench | None = None


def reset_rag_cache() -> None:
    """Clear the lazy RAG cache. Mainly useful for tests and local reloads."""
    _RAG_CACHE.clear()


def get_rag() -> tuple[Any, Any, int]:
    """Build the RAG graph lazily and reuse it across requests."""
    if "graph" not in _RAG_CACHE:
        graph, vs, chunks_indexed = make_rag()
        _RAG_CACHE.update({"graph": graph, "vectorstore": vs, "chunks_indexed": chunks_indexed})
    return _RAG_CACHE["graph"], _RAG_CACHE.get("vectorstore"), int(_RAG_CACHE["chunks_indexed"])


def get_default_workbench() -> QualityWorkbench:
    global _DEFAULT_WORKBENCH
    if _DEFAULT_WORKBENCH is None:
        store = WorkbenchStore(engine_fingerprint=engine_fingerprint())
        _DEFAULT_WORKBENCH = QualityWorkbench(store)
    return _DEFAULT_WORKBENCH


def _public_revision(revision: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in revision.items()
        if key not in {"source_path", "config"}
    }
    config = dict(revision.get("config") or {})
    config.pop("source_path", None)
    result["config"] = config
    result["title"] = config.get("title") or revision.get("label")
    result["change_summary"] = config.get("change_summary") or revision.get("label")
    result["operator"] = config.get("operator")
    result["source_url"] = config.get("source_url")
    result["status"] = (
        "active"
        if revision.get("active")
        else revision.get("decision") or "draft"
    )
    return result


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    internal_status = str(result["status"])
    result["persistence_status"] = internal_status
    result["status"] = {
        "pending": "queued",
        "passed": "succeeded",
        "interrupted": "cancelled",
    }.get(internal_status, internal_status)
    job_result = dict(result.get("result") or {})
    approval_allowed = (
        result.get("kind") == "validation"
        and internal_status == "passed"
        and job_result.get("full_pass") is True
        and job_result.get("no_regression") is True
    )
    result["approval_allowed"] = approval_allowed
    if job_result:
        job_result["approval_allowed"] = approval_allowed
        result["result"] = job_result
    return result


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
                source_id=doc.metadata.get("source_id"),
                source_url=doc.metadata.get("source_url"),
                source_type=doc.metadata.get("source_type"),
                authority=doc.metadata.get("authority"),
                fetched_at=doc.metadata.get("fetched_at"),
                retrieval_relation=doc.metadata.get("retrieval_relation", "direct"),
                anchor_chunk_id=doc.metadata.get("anchor_chunk_id"),
                bm25_score=doc.metadata.get("bm25_score"),
                rerank_score=doc.metadata.get("rerank_score"),
                structured_record_id=doc.metadata.get("structured_record_id"),
                structured_kind=doc.metadata.get("structured_kind"),
                structured_score=doc.metadata.get("structured_score"),
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


def _answer_excerpt(answer: str | None) -> str:
    if answer is None:
        return "品質ゲートで保留。候補回答はローカル監査記録にのみ保存。"
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
    delivery_status: str,
    blocked_reason: str | None,
    verification: dict[str, Any],
    audit: dict[str, Any],
    answer_mode: str,
    vs: Any = None,
    answer: str | None = "",
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
            input=f"モード: {answer_mode} / モデル: {model} / LLMへ渡した根拠候補数: {len(sources)}\n渡した候補:\n{source_names}",
            process="システムプロンプトで『提供された抜粋だけを根拠にする』『抜粋にない情報は記載なし』と制約し、抜粋番号付きで回答させた。",
            output=f"生成後の回答:\n{_answer_excerpt(answer)}",
            check="回答中の固有名詞、術式名、時系列、数値が上の取得候補に含まれているかを照合する。",
        ),
        ProcessStep(
            id="verification",
            title="回答照合",
            status="完了" if verification.get("status") == "pass" else "NG",
            purpose="回答中の各主張が、取得した根拠に実際に支持されるかを公開前に検品した。",
            input=f"機械検査と別LLM照合へ、回答候補と根拠候補 {len(sources)}件を渡した。",
            process=f"引用形式を機械検査し、別LLMが主張単位で支持・矛盾・判断不能と3評価軸を判定した。最後に {answer_mode} モードのゲート条件を適用した。",
            output=(
                f"検証状態: {verification.get('status', 'error')} / "
                f"根拠忠実性: {verification.get('axes', {}).get('faithfulness', 0)} / "
                f"質問直接性: {verification.get('axes', {}).get('relevance', 0)} / "
                f"誤情報なし: {verification.get('axes', {}).get('no_misinfo', 0)} / "
                f"ゲート: {', '.join(verification.get('gate_checks', [])) or '-'}"
            ),
            check="厳格は全条件満点、通常は矛盾なし・根拠あり、探索は候補提示を優先する。危険な矛盾や壊れた引用はどのモードでも止める。",
        ),
        ProcessStep(
            id="release",
            title="出荷判定",
            status="完了" if delivery_status == "released" else "出荷停止",
            purpose="検品済み回答を利用者へ表示してよいかをサーバー側で最終決定した。",
            input=f"回答照合結果: {verification.get('status', 'error')}",
            process="fail-closedで判定し、NG・判断不能・検品エラー時は候補回答を公開レスポンスから除外した。",
            output=(
                "回答を公開した。"
                if delivery_status == "released"
                else f"候補回答を隔離した。理由: {blocked_reason or 'quality_gate_failed'}"
            ),
            check="出荷停止時は回答欄、APIのanswer、CLI標準出力のいずれにも候補本文を出さない。",
        ),
        ProcessStep(
            id="audit",
            title="監査記録",
            status="完了" if audit.get("status") == "confirmed" else audit.get("status", "未送信"),
            purpose="今回の実行を後から追跡できる状態にした。",
            input=f"Langfuse監査設定: {'ON' if audit_enabled else 'OFF'} / session trace tags はリクエスト値を使用。",
            process="有効時はLangChain callback経由で検索・生成のトレース送信を試みた。無効時はAPIレスポンス内の工程詳細をローカル証跡として返した。",
            output=(
                "APIレスポンスに knowledge、sources、process_steps を含めて返却。"
                f"外部監査状態: {audit.get('status', 'disabled')} / trace_id: {audit.get('trace_id', '-')}"
            ),
            check="問題調査では、この工程詳細とLangfuse traceを突き合わせる。ログは修正せず、ナレッジ・設定・評価セットを変えて再実行する。",
        ),
    ]


def _build_ask_response(
    *,
    payload: AskRequest,
    question: str,
    outcome: dict[str, Any],
) -> AskResponse:
    knowledge = dict(outcome["revision"]["config"])
    knowledge.pop("source_path", None)
    sources = [SourceHit.model_validate(item) for item in outcome["sources"]]
    return AskResponse(
        question=question,
        run_id=outcome["run_id"],
        delivery_status=outcome["delivery_status"],
        answer=outcome["answer"],
        answer_mode=outcome["answer_mode"],
        blocked_reason=outcome["blocked_reason"],
        verification=outcome["verification"],
        audit=outcome["audit"],
        sources=sources,
        chunks_indexed=outcome["chunks_indexed"],
        model=get_generation_model(),
        audit_enabled=outcome["audit"]["status"] != "disabled",
        knowledge=knowledge,
        process_steps=_build_process_steps(
            raw_question=payload.question,
            question=question,
            sources=sources,
            chunks_indexed=outcome["chunks_indexed"],
            model=get_generation_model(),
            audit_enabled=outcome["audit"]["status"] != "disabled",
            knowledge=knowledge,
            delivery_status=outcome["delivery_status"],
            blocked_reason=outcome["blocked_reason"],
            verification=outcome["verification"],
            audit=outcome["audit"],
            answer_mode=outcome["answer_mode"],
            vs=outcome["vectorstore"],
            answer=outcome["answer"],
        ),
    )


def _sse_event(event: dict[str, Any]) -> str:
    return f"id: {event['id']}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(workbench: QualityWorkbench | None = None) -> FastAPI:
    wb = workbench or get_default_workbench()
    app = FastAPI(
        title="medguide-rag API",
        description=(
            "Single-PC RAG quality workbench with source tracking and optional Langfuse traces. "
            "Authentication is intentionally disabled for local-only use."
        ),
        version="0.2.0",
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def reject_oversized_requests(request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "request body exceeds the 15 MiB local-workbench limit"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "invalid content-length header"},
                )
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        active_revision = wb.store.get_active_revision()
        knowledge = dict(active_revision.get("config") or {})
        knowledge.pop("source_path", None)
        knowledge.update(
            {
                "source": active_revision["source_name"],
                "source_sha256": active_revision["source_sha256"],
                "revision_id": active_revision["id"],
            }
        )
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
            "knowledge": knowledge,
            "active_revision_id": active_revision["id"],
            "auth_mode": "single_pc_none",
            "answer_modes": ["strict", "standard", "explore"],
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
            outcome = wb.answer_question(
                question=question,
                revision_id=payload.revision_id,
                session_id=payload.session_id,
                user_id=payload.user_id,
                trace_tags=payload.trace_tags,
                answer_mode=payload.answer_mode,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"RAG execution failed: {safe_error_message(exc)}") from exc

        return _build_ask_response(payload=payload, question=question, outcome=outcome)

    def _execute_async_run(run_id: str, payload: AskRequest) -> None:
        question = payload.question.strip()
        try:
            wb.store.mark_async_run_running(run_id)
            wb.store.append_run_event(
                run_id,
                "question_received",
                payload={
                    "stage": "question_received",
                    "input": {"question_length": len(question)},
                    "answer_mode": payload.answer_mode,
                },
            )
            outcome = wb.answer_question(
                question=question,
                revision_id=payload.revision_id,
                session_id=payload.session_id,
                user_id=payload.user_id,
                trace_tags=payload.trace_tags,
                answer_mode=payload.answer_mode,
                run_id=run_id,
            )
            response = _build_ask_response(
                payload=payload,
                question=question,
                outcome=outcome,
            ).model_dump()
            wb.store.finish_async_run(run_id, response)
        except Exception as exc:
            wb.store.fail_async_run(run_id, safe_error_message(exc))

    @app.post("/runs", response_model=RunCreateResponse, status_code=202)
    def create_run(
        payload: AskRequest,
        background_tasks: BackgroundTasks,
    ) -> RunCreateResponse:
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question must not be blank")
        if not is_generation_configured():
            provider = get_llm_provider()
            key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
            raise HTTPException(
                status_code=503,
                detail=f"{key_name} is not configured for LLM_PROVIDER={provider}. Set it in .env before calling /runs.",
            )
        revision = (
            wb.store.get_revision(payload.revision_id)
            if payload.revision_id
            else wb.store.get_active_revision()
        )
        run = wb.store.create_async_run(
            revision_id=str(revision["id"]),
            question=question,
            answer_mode=payload.answer_mode,
        )
        background_tasks.add_task(_execute_async_run, str(run["id"]), payload)
        return RunCreateResponse(
            run_id=str(run["id"]),
            status=str(run["status"]),
            events_url=f"/runs/{run['id']}/events",
        )

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    def get_run(run_id: str) -> RunStatusResponse:
        try:
            run = wb.store.get_async_run(run_id)
            events = wb.store.list_run_events(run_id)
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        return RunStatusResponse(
            run_id=str(run["id"]),
            status=str(run["status"]),
            revision_id=str(run["revision_id"]),
            question=str(run["question"]),
            answer_mode=str(run["answer_mode"]),
            response=run.get("response"),
            error_message=run.get("error_message"),
            events=events,
        )

    @app.get("/runs/{run_id}/events")
    def stream_run_events(
        run_id: str,
        after_id: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        def generate():
            cursor = after_id
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                try:
                    events = wb.store.list_run_events(run_id, after_id=cursor)
                    run = wb.store.get_async_run(run_id)
                except WorkbenchNotFoundError:
                    yield "event: error\ndata: {\"detail\":\"run not found\"}\n\n"
                    return
                for event in events:
                    cursor = int(event["id"])
                    yield _sse_event(event)
                if run["status"] in {"completed", "failed"} and not events:
                    return
                time.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/workbench/revisions")
    def list_revisions() -> dict[str, Any]:
        return {
            "items": [_public_revision(item) for item in wb.store.list_revisions()],
            "active_revision_id": wb.store.get_active_revision()["id"],
        }

    @app.post("/workbench/revisions", status_code=201)
    def create_revision(payload: RevisionCreateRequest) -> dict[str, Any]:
        try:
            label = (
                payload.label
                or payload.change_summary
                or payload.title
                or "workbench revision"
            ).strip()
            if not label:
                raise ValueError("revision label must not be blank")
            if payload.source_url and not payload.source_url.startswith(("https://", "http://")):
                raise ValueError("source_url must use http or https")
            uploaded_bytes = None
            if payload.content_base64 is not None:
                try:
                    uploaded_bytes = base64.b64decode(
                        payload.content_base64,
                        validate=True,
                    )
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("content_base64 is invalid") from exc
            revision = wb.create_revision(
                source_path=payload.source_path,
                content=payload.content,
                content_bytes=uploaded_bytes,
                source_name=payload.filename,
                label=label,
                config={
                    key: value
                    for key, value in {
                        "operator": payload.operator,
                        "change_summary": payload.change_summary,
                        "title": payload.title,
                        "source_url": payload.source_url,
                    }.items()
                    if value is not None
                },
            )
            return _public_revision(revision)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc

    def _create_job(
        *,
        revision_id: str,
        kind: str,
        payload: JobCreateRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        try:
            job = wb.create_job(
                revision_id=revision_id,
                kind=kind,
                question_limit=payload.question_limit,
                question=payload.question,
            )
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=429, detail=safe_error_message(exc)) from exc
        background_tasks.add_task(wb.run_job, str(job["id"]))
        return _public_job(job)

    @app.post("/workbench/revisions/{revision_id}/comparison-jobs", status_code=202)
    def create_comparison_job(
        revision_id: str,
        background_tasks: BackgroundTasks,
        payload: JobCreateRequest | None = None,
    ) -> dict[str, Any]:
        request = payload or JobCreateRequest()
        if not request.question or not request.question.strip():
            raise HTTPException(status_code=400, detail="comparison requires one question")
        if request.question_limit is not None:
            raise HTTPException(status_code=400, detail="question_limit is not allowed for comparison")
        return _create_job(
            revision_id=revision_id,
            kind="comparison",
            payload=request,
            background_tasks=background_tasks,
        )

    @app.post("/workbench/revisions/{revision_id}/validation-jobs", status_code=202)
    def create_validation_job(
        revision_id: str,
        background_tasks: BackgroundTasks,
        payload: JobCreateRequest | None = None,
    ) -> dict[str, Any]:
        request = payload or JobCreateRequest()
        if request.question is not None or request.question_limit is not None:
            raise HTTPException(
                status_code=400,
                detail="full validation does not accept question or question_limit",
            )
        return _create_job(
            revision_id=revision_id,
            kind="validation",
            payload=request,
            background_tasks=background_tasks,
        )

    @app.get("/workbench/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return _public_job(wb.store.get_job(job_id))
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc

    @app.post("/workbench/revisions/{revision_id}/structured-records")
    def replace_structured_records(
        revision_id: str,
        payload: StructuredRecordsRequest,
    ) -> dict[str, Any]:
        try:
            return wb.replace_revision_structured_records(
                revision_id,
                entities=payload.entities,
                facts=payload.facts,
            )
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc

    @app.get("/workbench/revisions/{revision_id}/structured-search")
    def search_structured_records(
        revision_id: str,
        q: str = Query(..., min_length=1, max_length=4000),
        limit: int = Query(default=8, ge=1, le=50),
    ) -> dict[str, Any]:
        try:
            wb.store.get_revision(revision_id)
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        hits = wb.structured_store.search(revision_id, q, limit=limit)
        return {
            "revision_id": revision_id,
            "query": q,
            "items": [
                hit.model_dump() if hasattr(hit, "model_dump") else hit.dict()
                for hit in hits
            ],
        }

    @app.post("/workbench/revisions/{revision_id}/structured-extract")
    def extract_structured_records_endpoint(
        revision_id: str,
        payload: StructuredExtractionRequest | None = None,
    ) -> dict[str, Any]:
        request = payload or StructuredExtractionRequest()
        try:
            return wb.extract_revision_structured_records(
                revision_id,
                query=request.query,
                chunk_limit=request.chunk_limit,
                persist=request.persist,
            )
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=safe_error_message(exc)) from exc

    @app.post("/workbench/revisions/{revision_id}/coverage-loop")
    def run_coverage_loop_endpoint(
        revision_id: str,
        payload: CoverageLoopRequest | None = None,
    ) -> dict[str, Any]:
        request = payload or CoverageLoopRequest()
        requires_local_llm = request.allow_llm_agents or request.run_knowledge_answerer
        if requires_local_llm and not is_generation_configured():
            provider = get_llm_provider()
            key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
            raise HTTPException(
                status_code=503,
                detail=f"{key_name} is not configured for LLM_PROVIDER={provider}. Set it in .env before running coverage-loop.",
            )
        try:
            return wb.run_revision_coverage_loop(
                revision_id,
                focus=request.focus,
                questions=[question.strip() for question in request.questions],
                external_answers=request.external_answers,
                knowledge_answers=request.knowledge_answers,
                fact_checks=request.fact_checks,
                rounds=request.rounds,
                max_questions_per_round=request.max_questions_per_round,
                answer_mode=request.answer_mode,
                allow_llm_agents=request.allow_llm_agents,
                run_knowledge_answerer=request.run_knowledge_answerer,
                persist=request.persist,
            )
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=safe_error_message(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=safe_error_message(exc)) from exc

    @app.get("/workbench/revisions/{revision_id}/coverage-candidates")
    def list_coverage_candidates(
        revision_id: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        try:
            wb.store.get_revision(revision_id)
            return {"items": wb.store.list_coverage_candidates(revision_id, status=status)}
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc

    @app.post("/workbench/coverage-candidates/{candidate_id}/resolve")
    def resolve_coverage_candidate(
        candidate_id: str,
        payload: CoverageCandidateResolutionRequest,
    ) -> dict[str, Any]:
        try:
            return wb.store.resolve_coverage_candidate(
                candidate_id,
                status=payload.status,
                reason=payload.reason.strip(),
                operator=(payload.operator.strip() if payload.operator else None),
            )
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except (WorkbenchConflictError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc

    @app.post("/workbench/revisions/{revision_id}/approve")
    def approve_revision(
        revision_id: str,
        payload: DecisionRequest | None = None,
    ) -> dict[str, Any]:
        try:
            revision = wb.store.approve_revision(
                revision_id,
                reason=(payload.reason.strip() if payload else ""),
                engine_fingerprint=wb.fingerprint(),
                structured_digest=wb.structured_digest(revision_id),
                operator=(payload.operator.strip() if payload and payload.operator else None),
            )
            wb.clear_rag_cache()
            return _public_revision(revision)
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc

    @app.post("/workbench/revisions/{revision_id}/reject")
    def reject_revision(
        revision_id: str,
        payload: DecisionRequest | None = None,
    ) -> dict[str, Any]:
        try:
            revision = wb.store.reject_revision(
                revision_id,
                reason=(payload.reason.strip() if payload else ""),
                operator=(payload.operator.strip() if payload and payload.operator else None),
            )
            return _public_revision(revision)
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        except WorkbenchConflictError as exc:
            raise HTTPException(status_code=409, detail=safe_error_message(exc)) from exc

    @app.get("/workbench/adjustments")
    def list_adjustments(
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        return {"items": wb.store.list_adjustments(limit=limit)}

    @app.get("/audit/traces/{trace_id}")
    def get_audit_trace(trace_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[a-f0-9]{32}", trace_id) is None:
            raise HTTPException(status_code=400, detail="invalid trace_id")
        try:
            local = wb.store.get_trace(trace_id)
        except WorkbenchNotFoundError as exc:
            raise HTTPException(status_code=404, detail=safe_error_message(exc)) from exc
        remote = check_langfuse_trace(trace_id)
        return {
            **local,
            "local_status": local["status"],
            "status": remote.status,
            "trace_url": remote.trace_url or local.get("trace_url"),
            "observation_names": list(remote.observation_names),
        }

    return app


app = create_app()
