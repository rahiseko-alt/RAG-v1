"""FastAPI entrypoint for the medguide-rag question answering demo."""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.observability import get_langfuse_config_error, is_langfuse_configured
from src.rag import TOP_K, ask, get_generation_model, get_llm_provider, is_generation_configured, make_rag
from src.runtime_errors import safe_error_message


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


class AskResponse(BaseModel):
    """RAG answer with source-tracking metadata."""

    question: str
    answer: str
    sources: list[SourceHit]
    chunks_indexed: int
    model: str
    audit_enabled: bool


_RAG_CACHE: dict[str, Any] = {}


def reset_rag_cache() -> None:
    """Clear the lazy RAG cache. Mainly useful for tests and local reloads."""
    _RAG_CACHE.clear()


def get_rag() -> tuple[Any, int]:
    """Build the RAG graph lazily and reuse it across requests."""
    if "graph" not in _RAG_CACHE:
        graph, _vs, chunks_indexed = make_rag()
        _RAG_CACHE.update({"graph": graph, "chunks_indexed": chunks_indexed})
    return _RAG_CACHE["graph"], int(_RAG_CACHE["chunks_indexed"])


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


def create_app() -> FastAPI:
    app = FastAPI(
        title="medguide-rag API",
        description="Medical guideline RAG demo with source tracking and optional Langfuse audit traces.",
        version="0.1.0",
    )

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
            graph, chunks_indexed = get_rag()
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

        return AskResponse(
            question=question,
            answer=state["answer"],
            sources=_format_sources(state["docs"]),
            chunks_indexed=chunks_indexed,
            model=get_generation_model(),
            audit_enabled=is_langfuse_configured(),
        )

    return app


app = create_app()
