"""Application service for revision-aware RAG execution and quality jobs."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable, TypeGuard

from src.coverage_loop import (
    AgentAnswer,
    ExternalAnswerer,
    FactChecker,
    LLMCoverageQuestionGenerator,
    LLMExternalAnswerer,
    LLMFactChecker,
    MissingExternalAnswerer,
    ProvidedFactChecker,
    CoverageQuestionGenerator,
    RetrievedChunk,
    run_coverage_loop,
)
from src.ingest import load_and_chunk
from src.knowledge_config import PRODUCT_ROOT
from src.observability import (
    AuditResult,
    finalize_langfuse_trace,
    new_trace_id,
    record_langfuse_quality_observations,
)
from src.rag import ask as rag_ask
from src.rag import (
    RAGState,
    bm25_rescue_search,
    engine_fingerprint,
    expand_with_neighbor_chunks,
    make_rag,
    rerank_retrieved_documents,
)
from src.runtime_errors import safe_error_message
from src.structured_extraction import (
    LLMStructuredExtractor,
    StructuredExtractor,
    extract_structured_records,
)
from src.structured_knowledge import StructuredKnowledgeStore

from .store import WorkbenchConflictError, WorkbenchStore, sha256_file
from .verifier import CANONICAL_ABSTENTION, AnswerMode, OnlineVerifier, public_verification


def _is_real_number(value: Any) -> TypeGuard[int | float]:
    """True for an int/float score. `bool` is an int subclass, so exclude it explicitly."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_rank(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def retrieved_chunks_from_sources(sources: list[dict[str, Any]] | None) -> list[RetrievedChunk]:
    """Normalize `answer_question`'s public sources into coverage-loop retrieval evidence.

    Design-doc step 7: a coverage-loop B-answer must carry the chunks its retriever
    actually surfaced, so D can tell `missing_knowledge` (nothing relevant was
    retrievable) apart from `retrieval_failure` / `generation_failure` (something
    relevant was retrieved but ranked too low or ignored). Without this, D only sees
    B's final text and has to guess, which is exactly the false-positive path the
    failure taxonomy exists to close.

    This runs on the *public* source dicts (the ones already returned to callers and
    persisted with the run), not on private evidence, so nothing new is exposed and
    the ledger row stays free of full chunk text.
    """
    chunks: list[RetrievedChunk] = []
    for index, source in enumerate(sources or [], start=1):
        raw_chunk_id = source.get("chunk_id")
        raw_rank = source.get("rank")
        raw_score = source.get("score")
        page = source.get("page")
        origin = str(source.get("source") or "").strip()
        # Locator only — the passage itself stays in `sources[*].snippet`.
        citation = f"{origin} p.{page}" if origin and page not in (None, "") else origin
        chunks.append(
            RetrievedChunk(
                # `chunk_id` is an opaque 0-based counter (`src/ingest`), so a falsy
                # test would blank out the first chunk of every document — which is
                # also the one retrieval surfaces most often.
                chunk_id="" if raw_chunk_id is None else str(raw_chunk_id),
                score=float(raw_score) if _is_real_number(raw_score) else None,
                citation=citation,
                # `rank` is 1-based and doubles as the `[N]` citation marker, so a 0
                # or a stray bool must not pass through as a rank.
                rank=raw_rank if _is_positive_rank(raw_rank) else index,
            )
        )
    return chunks


class QualityWorkbench:
    """Coordinate revision-specific engines, verification, audit, and persistence."""

    def __init__(
        self,
        store: WorkbenchStore,
        *,
        verifier: OnlineVerifier | None = None,
        rag_factory: Callable[..., tuple[Any, Any, int]] = make_rag,
        # RAGState is a TypedDict, which is not assignable to `dict[str, Any]`, so the
        # previous annotation rejected the real `rag.ask` it defaults to.
        ask_fn: Callable[..., RAGState] = rag_ask,
        structured_store: StructuredKnowledgeStore | None = None,
        structured_extractor: StructuredExtractor | None = None,
        coverage_question_generator: CoverageQuestionGenerator | None = None,
        coverage_external_answerer: ExternalAnswerer | None = None,
        coverage_fact_checker: FactChecker | None = None,
    ) -> None:
        self.store = store
        self.verifier = verifier or OnlineVerifier()
        self.rag_factory = rag_factory
        self.ask_fn = ask_fn
        self.structured_store = structured_store or StructuredKnowledgeStore(
            self.store.runtime_root / "structured_knowledge.sqlite3"
        )
        active_revision = self.store.get_active_revision(required=False)
        if active_revision is not None:
            self.structured_store.freeze_revision(str(active_revision["id"]))
        self.structured_extractor = structured_extractor
        self.coverage_question_generator = coverage_question_generator
        self.coverage_external_answerer = coverage_external_answerer
        self.coverage_fact_checker = coverage_fact_checker
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

    def structured_digest(self, revision_id: str) -> str:
        return self.structured_store.revision_digest(revision_id)

    def _get_structured_extractor(self) -> StructuredExtractor:
        if self.structured_extractor is None:
            self.structured_extractor = LLMStructuredExtractor()
        return self.structured_extractor

    def _get_coverage_question_generator(self) -> CoverageQuestionGenerator:
        if self.coverage_question_generator is None:
            self.coverage_question_generator = LLMCoverageQuestionGenerator()
        return self.coverage_question_generator

    def _get_coverage_external_answerer(self) -> ExternalAnswerer:
        if self.coverage_external_answerer is None:
            self.coverage_external_answerer = LLMExternalAnswerer()
        return self.coverage_external_answerer

    def _get_coverage_fact_checker(self) -> FactChecker:
        if self.coverage_fact_checker is None:
            self.coverage_fact_checker = LLMFactChecker()
        return self.coverage_fact_checker

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
                "source_id": document.metadata.get("source_id"),
                "source_url": document.metadata.get("source_url"),
                "source_type": document.metadata.get("source_type"),
                "authority": document.metadata.get("authority"),
                "fetched_at": document.metadata.get("fetched_at"),
                "retrieval_relation": document.metadata.get("retrieval_relation", "direct"),
                "anchor_chunk_id": document.metadata.get("anchor_chunk_id"),
                "bm25_score": document.metadata.get("bm25_score"),
                "rerank_score": document.metadata.get("rerank_score"),
                "structured_record_id": document.metadata.get("structured_record_id"),
                "structured_kind": document.metadata.get("structured_kind"),
                "structured_score": document.metadata.get("structured_score"),
            }
            private.append({**base, "text": text})
            public.append({**base, "snippet": text[:240]})
        return private, public

    @staticmethod
    def _public_structured_hit(hit: Any) -> dict[str, Any]:
        if hasattr(hit, "model_dump"):
            return hit.model_dump()
        return hit.dict()

    def answer_question(
        self,
        *,
        question: str,
        revision_id: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_tags: list[str] | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        answer_mode: AnswerMode = "strict",
    ) -> dict[str, Any]:
        revision = (
            self.store.get_revision(revision_id)
            if revision_id is not None
            else self.store.get_active_revision()
        )
        graph, vectorstore, chunks_indexed = self._get_rag(revision)
        trace_id = new_trace_id()
        if run_id:
            self.store.append_run_event(
                run_id,
                "knowledge_selected",
                payload={
                    "stage": "knowledge_selected",
                    "revision_id": str(revision["id"]),
                    "source_sha256": revision["source_sha256"],
                    "structured_sha256": self.structured_digest(str(revision["id"])),
                },
            )
        structured_hits = [
            self._public_structured_hit(hit)
            for hit in self.structured_store.search(str(revision["id"]), question)
        ]
        if run_id:
            self.store.append_run_event(
                run_id,
                "retrieve_started",
                payload={
                    "stage": "retrieve",
                    "input": {"question_length": len(question)},
                    "structured_hits": len(structured_hits),
                },
            )
        state = self.ask_fn(
            graph,
            question,
            session_id=session_id,
            user_id=user_id,
            trace_tags=trace_tags,
            trace_id=trace_id,
            answer_mode=answer_mode,
            structured_hits=structured_hits,
        )
        candidate = str(state.get("answer") or "")
        private_evidence, public_sources = self._evidence(list(state.get("docs") or []))
        if run_id:
            self.store.append_run_event(
                run_id,
                "generate_completed",
                payload={
                    "stage": "generate",
                    "retrieved_sources": len(public_sources),
                    "candidate_answer_saved": True,
                },
            )
        verification = self.verifier.evaluate(
            question=question,
            candidate_answer=candidate,
            evidence=private_evidence,
            answer_mode=answer_mode,
        )
        released = verification.get("release_allowed") is True
        delivery_status = "released" if released else "blocked"
        blocked_reason = None if released else str(verification.get("reason_code") or "quality_gate_failed")
        if run_id:
            self.store.append_run_event(
                run_id,
                "verify_completed",
                payload={
                    "stage": "verify",
                    "status": verification.get("status"),
                    "claim_count": len(verification.get("claims") or []),
                    "release_allowed": verification.get("release_allowed"),
                },
            )
            self.store.append_run_event(
                run_id,
                "gate_completed",
                payload={
                    "stage": "gate",
                    "delivery_status": delivery_status,
                    "blocked_reason": blocked_reason,
                },
            )
        quality_audit_status = record_langfuse_quality_observations(
            trace_id=trace_id,
            question=question,
            verification=verification,
            delivery_status=delivery_status,
        )
        audit = finalize_langfuse_trace(trace_id)
        if quality_audit_status == "error":
            audit = AuditResult(trace_id=trace_id, status="error", trace_url=audit.trace_url)
        run_id = run_id or uuid.uuid4().hex
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
            "answer_mode": answer_mode,
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

    def extract_revision_structured_records(
        self,
        revision_id: str,
        *,
        query: str | None = None,
        chunk_limit: int | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        revision = self.store.get_revision(revision_id)
        source_path = Path(str(revision["source_path"]))
        if sha256_file(source_path) != revision["source_sha256"]:
            raise RuntimeError("immutable revision source hash mismatch")
        if persist:
            self._ensure_structured_records_mutable(revision_id)
        normalized_query = query.strip() if query else None
        chunks_total: int
        if normalized_query:
            _graph, vectorstore, _chunks_indexed = self._get_rag(revision)
            chunks_total = _chunks_indexed
            top_k = chunk_limit or 8
            hits = vectorstore.similarity_search_with_relevance_scores(normalized_query, k=top_k)
            hits = bm25_rescue_search(
                vectorstore,
                normalized_query,
                hits,
                max_results=max(top_k * 2, top_k),
            )
            hits = expand_with_neighbor_chunks(
                vectorstore,
                hits,
                max_results=max(len(hits) * 3, top_k),
            )
            hits = rerank_retrieved_documents(
                normalized_query,
                hits,
                max_results=max(top_k * 3, top_k),
            )
            documents = [document for document, _score in hits]
        else:
            all_documents = load_and_chunk(source_path)
            chunks_total = len(all_documents)
            documents = all_documents
            if chunk_limit is not None:
                documents = documents[:chunk_limit]
        scanned = len(documents)
        batch = extract_structured_records(
            documents,
            extractor=self._get_structured_extractor(),
        )
        if persist:
            self.structured_store.replace_revision(
                revision_id,
                entities=batch.entities,
                facts=batch.facts,
            )
        return {
            "revision_id": revision_id,
            "query": normalized_query,
            "chunks_scanned": scanned,
            "chunks_total": chunks_total,
            "persisted": persist,
            "entities": [
                item.model_dump() if hasattr(item, "model_dump") else item.dict()
                for item in batch.entities
            ],
            "facts": [
                item.model_dump() if hasattr(item, "model_dump") else item.dict()
                for item in batch.facts
            ],
        }

    def _ensure_structured_records_mutable(self, revision_id: str) -> dict[str, Any]:
        revision = self.store.get_revision(revision_id)
        if revision["active"]:
            raise WorkbenchConflictError("active revision structured records are frozen")
        if self.store.revision_has_validation_or_decision(revision_id):
            raise WorkbenchConflictError(
                "structured records cannot change after validation, approval, or rejection"
            )
        if self.structured_store.is_revision_frozen(revision_id):
            raise WorkbenchConflictError("structured revision is frozen")
        return revision

    def replace_revision_structured_records(
        self,
        revision_id: str,
        *,
        entities: list[Any],
        facts: list[Any],
    ) -> dict[str, Any]:
        self._ensure_structured_records_mutable(revision_id)
        self.structured_store.replace_revision(
            revision_id,
            entities=entities,
            facts=facts,
        )
        return {
            "revision_id": revision_id,
            "entities": len(entities),
            "facts": len(facts),
            "structured_sha256": self.structured_store.revision_digest(revision_id),
        }

    def run_revision_coverage_loop(
        self,
        revision_id: str,
        *,
        focus: str | None = None,
        questions: list[str] | None = None,
        external_answers: dict[str, Any] | None = None,
        knowledge_answers: dict[str, Any] | None = None,
        fact_checks: dict[str, Any] | None = None,
        rounds: int = 1,
        max_questions_per_round: int = 5,
        answer_mode: AnswerMode = "standard",
        allow_llm_agents: bool = False,
        run_knowledge_answerer: bool = False,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Find questions where external truth exists but local knowledge fails."""
        revision = self.store.get_revision(revision_id)
        normalized_questions = [question.strip() for question in questions or [] if question.strip()]
        if not allow_llm_agents:
            if not normalized_questions:
                raise ValueError("coverage-loop requires questions when allow_llm_agents is false")
            missing_external = [
                question
                for question in normalized_questions
                if question not in (external_answers or {})
            ]
            if missing_external:
                raise ValueError(
                    "coverage-loop requires external_answers for all questions when allow_llm_agents is false"
                )
            missing_knowledge = [
                question
                for question in normalized_questions
                if question not in (knowledge_answers or {})
            ]
            if missing_knowledge and not run_knowledge_answerer:
                raise ValueError(
                    "coverage-loop requires knowledge_answers for all questions unless run_knowledge_answerer is true"
                )
            missing_checks = [
                question
                for question in normalized_questions
                if question not in (fact_checks or {})
            ]
            if missing_checks:
                raise ValueError(
                    "coverage-loop requires fact_checks for all questions when allow_llm_agents is false"
                )

        def knowledge_answerer(*, question: str, answer_mode: AnswerMode) -> AgentAnswer:
            if not run_knowledge_answerer:
                raise ValueError(
                    f"knowledge answer is required for coverage-loop question: {question}"
                )
            try:
                outcome = self.answer_question(
                    question=question,
                    revision_id=str(revision["id"]),
                    trace_tags=["coverage-loop"],
                    answer_mode=answer_mode,
                )
            except Exception as exc:
                return AgentAnswer(
                    role="knowledge",
                    answer=None,
                    status="error",
                    notes=safe_error_message(exc),
                )
            sources = list(outcome.get("sources") or [])
            return AgentAnswer(
                role="knowledge",
                answer=outcome.get("answer"),
                status=str(outcome.get("delivery_status") or "unknown"),
                sources=sources,
                notes=str(outcome.get("blocked_reason") or ""),
                # Recorded even when the quality gate blocked the answer (`answer`
                # is None then): "B produced nothing" and "B retrieved nothing" are
                # different failures, and only the retrieval evidence separates them.
                retrieved_chunks=retrieved_chunks_from_sources(sources),
            )

        result = run_coverage_loop(
            revision_id=str(revision["id"]),
            focus=focus.strip() if focus else None,
            seed_questions=normalized_questions,
            external_answers=external_answers or {},
            knowledge_answers=knowledge_answers or {},
            rounds=rounds,
            max_questions_per_round=max_questions_per_round,
            question_generator=self._get_coverage_question_generator(),
            external_answerer=(
                self._get_coverage_external_answerer()
                if allow_llm_agents
                else MissingExternalAnswerer()
            ),
            fact_checker=(
                self._get_coverage_fact_checker()
                if allow_llm_agents
                else ProvidedFactChecker(fact_checks or {})
            ),
            knowledge_answerer=knowledge_answerer,
            answer_mode=answer_mode,
        )
        result_dict = result.model_dump()
        if persist:
            saved = self.store.save_coverage_loop_items(str(revision["id"]), result_dict["items"])
            saved_by_question = {candidate["question"]: candidate for candidate in saved}
            for item in result_dict["items"]:
                candidate = saved_by_question.get(item["question"])
                if candidate is not None:
                    item["candidate_id"] = candidate["id"]
                    item["ledger_status"] = candidate["status"]
        return result_dict

    def create_job(
        self,
        *,
        revision_id: str,
        kind: str,
        question_limit: int | None = None,
        question: str | None = None,
    ) -> dict[str, Any]:
        request = {"question_limit": question_limit, "question": question}
        if kind == "validation":
            self.structured_store.freeze_revision(revision_id)
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
                    "in_doc": item.get("in_doc"),
                    "expected_terms": [
                        str(term)
                        for term in item.get("expected_terms", [])
                        if str(term).strip()
                    ],
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
                answer = str(outcome.get("answer") or "")
                expected_terms = item.get("expected_terms", [])
                normalized_answer = " ".join(answer.split()).strip().rstrip("。.!！")
                expectation_pass = (
                    outcome["delivery_status"] == "released"
                    and (
                        normalized_answer == CANONICAL_ABSTENTION
                        if item.get("in_doc") is False
                        else (
                            all(term.casefold() in answer.casefold() for term in expected_terms)
                            if expected_terms
                            else True
                        )
                    )
                )
                items.append(
                    {
                        "id": item["id"],
                        "question": item["question"],
                        "run_id": outcome["run_id"],
                        "delivery_status": outcome["delivery_status"],
                        "answer": outcome["answer"],
                        "in_doc": item.get("in_doc"),
                        "expected_terms": expected_terms,
                        "expectation_pass": expectation_pass,
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
                        "in_doc": item.get("in_doc"),
                        "expected_terms": item.get("expected_terms", []),
                        "expectation_pass": False,
                        "axes": {},
                        "error": safe_error_message(exc),
                    }
                )
        released = sum(item["delivery_status"] == "released" for item in items)
        accepted = sum(item["expectation_pass"] for item in items)
        errors = sum(item["verification_status"] == "error" for item in items)
        return {
            "total": len(items),
            "released": released,
            "accepted": accepted,
            "blocked": len(items) - released,
            "expectation_failures": len(items) - accepted,
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
            before_structured_digest = self.structured_store.revision_digest(
                str(before_revision["id"])
            )
            after_structured_digest = self.structured_store.revision_digest(
                str(revision["id"])
            )
            before_by_id = {item["id"]: item for item in before["items"]}
            regressions = [
                item["id"]
                for item in after["items"]
                if before_by_id.get(item["id"], {}).get("expectation_pass") is True
                and item["expectation_pass"] is not True
            ]
            no_regression = not regressions and after["accepted"] >= before["accepted"]
            full_pass = (
                after["total"] > 0
                and after["accepted"] == after["total"]
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
                "structured_hashes": {
                    "before": before_structured_digest,
                    "after": after_structured_digest,
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
