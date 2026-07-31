"""Application service for revision-aware RAG execution and quality jobs."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from src.knowledge_config import PRODUCT_ROOT
from src.observability import (
    AuditResult,
    finalize_langfuse_trace,
    new_trace_id,
    record_langfuse_quality_observations,
)
from src.rag import ask as rag_ask
from src.rag import engine_fingerprint, make_rag
from src.runtime_errors import safe_error_message

from .store import WorkbenchStore, sha256_file
from .verifier import OnlineVerifier, public_verification


class QualityWorkbench:
    """Coordinate revision-specific engines, verification, audit, and persistence."""

    def __init__(
        self,
        store: WorkbenchStore,
        *,
        verifier: OnlineVerifier | None = None,
        rag_factory: Callable[..., tuple[Any, Any, int]] = make_rag,
        ask_fn: Callable[..., dict[str, Any]] = rag_ask,
    ) -> None:
        self.store = store
        self.verifier = verifier or OnlineVerifier()
        self.rag_factory = rag_factory
        self.ask_fn = ask_fn
        self._rag_cache: dict[tuple[str, str], tuple[Any, Any, int]] = {}

    def clear_rag_cache(self) -> None:
        self._rag_cache.clear()

    def fingerprint(self) -> str:
        return engine_fingerprint()

    def prepare_revision(
        self,
        revision_id: str | None = None,
    ) -> tuple[Any, Any, int]:
        revision = (
            self.store.get_revision(revision_id)
            if revision_id is not None
            else self.store.get_active_revision()
        )
        return self._get_rag(revision)

    def _get_rag(self, revision: dict[str, Any]) -> tuple[Any, Any, int]:
        fingerprint = self.fingerprint()
        key = (str(revision["id"]), fingerprint)
        source_path = Path(str(revision["source_path"]))
        if sha256_file(source_path) != revision["source_sha256"]:
            raise RuntimeError("immutable revision source hash mismatch")
        if key not in self._rag_cache:
            persist_dir = (
                self.store.runtime_root
                / "revisions"
                / str(revision["id"])
                / "indexes"
                / fingerprint
            )
            self._rag_cache[key] = self.rag_factory(
                source_path=source_path,
                persist_dir=persist_dir,
                collection_name=f"revision_{revision['id']}",
            )
        return self._rag_cache[key]

    @staticmethod
    def _evidence(docs: list[tuple[Any, float]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        private: list[dict[str, Any]] = []
        public: list[dict[str, Any]] = []
        for rank, (document, score) in enumerate(docs, start=1):
            text = " ".join(str(document.page_content).split())
            base = {
                "rank": rank,
                "source": str(document.metadata.get("source", "?")),
                "page": document.metadata.get("page", "?"),
                "chunk_id": document.metadata.get("chunk_id"),
                "score": round(float(score), 3),
            }
            private.append({**base, "text": text})
            public.append({**base, "snippet": text[:240]})
        return private, public

    def answer_question(
        self,
        *,
        question: str,
        revision_id: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_tags: list[str] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        revision = (
            self.store.get_revision(revision_id)
            if revision_id is not None
            else self.store.get_active_revision()
        )
        graph, vectorstore, chunks_indexed = self._get_rag(revision)
        trace_id = new_trace_id()
        state = self.ask_fn(
            graph,
            question,
            session_id=session_id,
            user_id=user_id,
            trace_tags=trace_tags,
            trace_id=trace_id,
        )
        candidate = str(state.get("answer") or "")
        private_evidence, public_sources = self._evidence(list(state.get("docs") or []))
        verification = self.verifier.evaluate(
            question=question,
            candidate_answer=candidate,
            evidence=private_evidence,
        )
        released = verification.get("release_allowed") is True
        delivery_status = "released" if released else "blocked"
        blocked_reason = None if released else str(verification.get("reason_code") or "quality_gate_failed")
        quality_audit_status = record_langfuse_quality_observations(
            trace_id=trace_id,
            question=question,
            verification=verification,
            delivery_status=delivery_status,
        )
        audit = finalize_langfuse_trace(trace_id)
        if quality_audit_status == "error":
            audit = AuditResult(trace_id=trace_id, status="error", trace_url=audit.trace_url)
        run_id = uuid.uuid4().hex
        self.store.record_run(
            run_id=run_id,
            revision_id=str(revision["id"]),
            question=question,
            candidate_answer=candidate,
            delivery_status=delivery_status,
            blocked_reason=blocked_reason,
            verification=verification,
            sources=public_sources,
            trace_id=trace_id,
            audit_status=audit.status,
            trace_url=audit.trace_url,
            job_id=job_id,
        )
        return {
            "run_id": run_id,
            "revision": revision,
            "delivery_status": delivery_status,
            "answer": candidate if released else None,
            "blocked_reason": blocked_reason,
            "verification": public_verification(
                verification,
                delivery_status=delivery_status,
            ),
            "sources": public_sources,
            "chunks_indexed": chunks_indexed,
            "vectorstore": vectorstore,
            "audit": {
                "status": audit.status,
                "trace_id": audit.trace_id,
                "trace_url": audit.trace_url,
            },
        }

    def create_revision(
        self,
        *,
        source_path: str | None,
        content: str | None,
        content_bytes: bytes | None = None,
        source_name: str | None = None,
        label: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if content_bytes is not None:
            if source_name is None:
                raise ValueError("source_name is required for uploaded bytes")
            return self.store.create_revision_from_bytes(
                content=content_bytes,
                source_name=source_name,
                label=label,
                engine_fingerprint=self.fingerprint(),
                config=config,
            )
        if content is not None:
            return self.store.create_revision_from_content(
                content=content,
                label=label,
                engine_fingerprint=self.fingerprint(),
                config=config,
            )
        if source_path is None:
            raise ValueError("source_path or content is required")
        return self.store.create_revision(
            source_path=source_path,
            label=label,
            engine_fingerprint=self.fingerprint(),
            config=config,
        )

    def create_job(
        self,
        *,
        revision_id: str,
        kind: str,
        question_limit: int | None = None,
        question: str | None = None,
    ) -> dict[str, Any]:
        request = {"question_limit": question_limit, "question": question}
        return self.store.create_job(
            revision_id=revision_id,
            kind=kind,
            engine_fingerprint=self.fingerprint(),
            request=request,
        )

    def _load_questions(
        self,
        revision: dict[str, Any],
        *,
        limit: int | None,
    ) -> tuple[list[dict[str, Any]], str]:
        configured = revision["config"].get("eval_set")
        if not configured:
            raise ValueError("revision has no eval_set configured")
        path = Path(str(configured))
        path = (PRODUCT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(PRODUCT_ROOT) or not path.is_file():
            raise ValueError("revision eval_set must be an existing file inside the product workspace")
        payload = json.loads(path.read_text(encoding="utf-8"))
        questions = payload.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ValueError("revision eval_set contains no questions")
        bounded = questions[:limit] if limit is not None else questions
        normalized = []
        for index, item in enumerate(bounded):
            if not isinstance(item, dict) or not str(item.get("question") or "").strip():
                raise ValueError(f"revision eval_set question {index + 1} is invalid")
            normalized.append(
                {
                    "id": str(item.get("id") or index + 1),
                    "question": str(item["question"]).strip(),
                }
            )
        return normalized, sha256_file(path)

    def _evaluate_revision(
        self,
        revision: dict[str, Any],
        *,
        questions: list[dict[str, Any]],
        job_id: str,
    ) -> dict[str, Any]:
        items = []
        for item in questions:
            try:
                outcome = self.answer_question(
                    question=item["question"],
                    revision_id=str(revision["id"]),
                    trace_tags=["quality-job", job_id],
                    job_id=job_id,
                )
                items.append(
                    {
                        "id": item["id"],
                        "question": item["question"],
                        "run_id": outcome["run_id"],
                        "delivery_status": outcome["delivery_status"],
                        "answer": outcome["answer"],
                        "blocked_reason": outcome["blocked_reason"],
                        "verification_status": outcome["verification"]["status"],
                        "axes": outcome["verification"]["axes"],
                        "verification": outcome["verification"],
                        "sources": outcome["sources"],
                    }
                )
            except Exception as exc:
                items.append(
                    {
                        "id": item["id"],
                        "question": item["question"],
                        "delivery_status": "blocked",
                        "verification_status": "error",
                        "axes": {},
                        "error": safe_error_message(exc),
                    }
                )
        released = sum(item["delivery_status"] == "released" for item in items)
        errors = sum(item["verification_status"] == "error" for item in items)
        return {
            "total": len(items),
            "released": released,
            "blocked": len(items) - released,
            "errors": errors,
            "items": items,
        }

    def run_job(self, job_id: str) -> None:
        """Run one persisted job. Exceptions are converted to terminal error state."""
        try:
            job = self.store.get_job(job_id)
            self.store.mark_job_running(job_id)
            revision = self.store.get_revision(str(job["revision_id"]))
            current_fingerprint = self.fingerprint()
            if current_fingerprint != job["engine_fingerprint"]:
                raise RuntimeError("engine settings changed after the job was queued")
            requested_question = str(job["request"].get("question") or "").strip()
            limit = job["request"].get("question_limit")
            if requested_question:
                questions = [{"id": "comparison-question", "question": requested_question}]
                eval_set_sha256 = None
            else:
                questions, eval_set_sha256 = self._load_questions(
                    revision,
                    limit=limit,
                )
            before_revision = self.store.get_active_revision()
            before = self._evaluate_revision(before_revision, questions=questions, job_id=job_id)
            after = self._evaluate_revision(revision, questions=questions, job_id=job_id)
            before_by_id = {item["id"]: item for item in before["items"]}
            regressions = [
                item["id"]
                for item in after["items"]
                if before_by_id.get(item["id"], {}).get("delivery_status") == "released"
                and item["delivery_status"] != "released"
            ]
            no_regression = not regressions and after["released"] >= before["released"]
            full_pass = (
                after["total"] > 0
                and after["released"] == after["total"]
                and after["errors"] == 0
            )
            result = {
                "mode": "full" if job["kind"] == "validation" else "comparison",
                "engine_fingerprint": current_fingerprint,
                "eval_set_sha256": eval_set_sha256,
                "source_hashes": {
                    "before": before_revision["source_sha256"],
                    "after": revision["source_sha256"],
                },
                "before": before,
                "after": after,
                "regression_item_ids": regressions,
                "no_regression": no_regression,
                "full_pass": full_pass,
            }
            terminal_status = (
                "passed"
                if job["kind"] == "comparison" or (full_pass and no_regression)
                else "failed"
            )
            self.store.finish_job(job_id, status=terminal_status, result=result)
        except Exception as exc:
            try:
                current = self.store.get_job(job_id)
                if current["status"] == "pending":
                    self.store.mark_job_running(job_id)
                current = self.store.get_job(job_id)
                if current["status"] == "running":
                    self.store.finish_job(
                        job_id,
                        status="error",
                        error_message=safe_error_message(exc),
                    )
            except Exception:
                return
