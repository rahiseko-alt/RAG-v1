import os
import sqlite3
import sys
import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.documents import Document

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pydantic import ValidationError  # noqa: E402

import src.api as api  # noqa: E402
from src.coverage_loop import AgentAnswer, CoverageQuestion, FactCheckJudgment  # noqa: E402
from src.quality import OnlineVerifier, QualityWorkbench, WorkbenchStore  # noqa: E402
from src.quality.store import sha256_file  # noqa: E402
from src.quality.workbench import retrieved_chunks_from_sources  # noqa: E402
from src.rag import engine_fingerprint  # noqa: E402
from src.structured_extraction import ExtractedKnowledgeBatch  # noqa: E402
from src.structured_knowledge import EvidenceRef, KnowledgeFact, StructuredKnowledgeStore  # noqa: E402


class PassingStructuredVerifier:
    def verify(self, **kwargs):
        return {
            "faithfulness": 2,
            "relevance": 2,
            "no_misinfo": 2,
            "claims": [
                {
                    "claim_id": "claim-1",
                    "claim": kwargs["candidate_answer"],
                    "status": "supported",
                    "evidence": [{"rank": 1, "reason": "The cited excerpt supports the claim."}],
                    "reason": "Supported by evidence rank 1.",
                }
            ],
        }


class BlockingStructuredVerifier:
    def verify(self, **_kwargs):
        return {
            "faithfulness": 0,
            "relevance": 1,
            "no_misinfo": 0,
            "claims": [
                {
                    "claim_id": "claim-1",
                    "claim": "非公開候補の根拠回答です。",
                    "status": "unclear",
                    "evidence": [{"rank": 1, "reason": "private candidate wording"}],
                    "reason": "private candidate wording",
                }
            ],
        }


class FakeStructuredExtractor:
    def extract_document(self, document):
        return ExtractedKnowledgeBatch(
            entities=[],
            facts=[
                KnowledgeFact(
                    fact_id="fact:auto-expense-threshold",
                    subject="経費承認規程",
                    predicate="approval_threshold",
                    value={"amount": 50000, "currency": "JPY", "approver": "部長"},
                    evidence=[
                        EvidenceRef(
                            source=str(document.metadata.get("source") or "source.md"),
                            chunk_id=document.metadata.get("chunk_id"),
                            excerpt="5万円以上の経費は部長が承認する。",
                        )
                    ],
                )
            ],
        )


class FakeExtractionVectorStore:
    def similarity_search_with_relevance_scores(self, query, k):
        assert query == "虎杖が簡易領域を習得できた理由"
        return [
            (
                Document(
                    page_content="虎杖は魂の入れ替え修行で日下部から簡易領域を習得した。",
                    metadata={"source": "source.md", "page": 0, "chunk_id": 42},
                ),
                0.91,
            )
        ]

    def get(self, where=None, include=None):
        if where:
            return {"documents": [], "metadatas": []}
        return {
            "documents": ["虎杖は魂の入れ替え修行で日下部から簡易領域を習得した。"],
            "metadatas": [{"source": "source.md", "page": 0, "chunk_id": 42}],
        }


class FakeCoverageQuestionGenerator:
    def generate(self, **_kwargs):
        return [
            CoverageQuestion(
                question="虎杖が簡易領域を習得できた理由を答えろ。",
                intent="causal gap",
            )
        ]


class FakeExternalAnswerer:
    def answer(self, *, question):
        return AgentAnswer(
            role="external",
            answer="虎杖は入れ替え修行を通じて日下部から簡易領域を学んだ。",
            status="ok",
            sources=[{"source": "external-reference", "snippet": "fixture"}],
        )


class FakeFactChecker:
    def check(self, *, question, external_answer, knowledge_answer):
        return FactCheckJudgment(
            external_status="pass",
            knowledge_status="abstain",
            same_answer=False,
            missing_knowledge="虎杖、日下部、入れ替え修行、簡易領域習得の因果関係",
            reason="A is usable and B abstained.",
        )


def _make_workbench(
    tmp_path,
    *,
    blocked=False,
    ask_kwargs_log=None,
    structured_extractor=None,
    vectorstore=None,
    coverage_question_generator=None,
    coverage_external_answerer=None,
    coverage_fact_checker=None,
):
    store = WorkbenchStore(
        tmp_path / "workbench.sqlite3",
        runtime_root=tmp_path / "runtime",
        engine_fingerprint=engine_fingerprint(),
    )

    def fake_factory(**_kwargs):
        return "graph", vectorstore, 71

    evaluation = json.loads(
        (Path(_ROOT) / "data" / "eval" / "jujutsu-kaisen-questions.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation_by_question = {
        item["question"]: item
        for item in evaluation["questions"]
    }

    def fake_ask(graph, question, **kwargs):
        assert graph == "graph"
        assert question
        assert kwargs["trace_id"]
        if ask_kwargs_log is not None:
            ask_kwargs_log.append(dict(kwargs))
        evaluation_item = evaluation_by_question.get(question)
        if evaluation_item is None:
            answer = "非公開候補の根拠回答です [1]。"
        elif evaluation_item["in_doc"]:
            answer = f"{' '.join(evaluation_item['expected_terms'])} [1]。"
        else:
            answer = "提供された文書には記載がありません。"
        return {
            "question": question,
            "answer": answer,
            "docs": [
                (
                    Document(
                        page_content="登録済みナレッジに含まれる根拠の抜粋です。",
                        metadata={"source": "sample.md", "page": 0, "chunk_id": 3},
                    ),
                    0.859,
                )
            ],
        }

    verifier = BlockingStructuredVerifier() if blocked else PassingStructuredVerifier()
    return QualityWorkbench(
        store,
        verifier=OnlineVerifier(verifier, timeout_seconds=1),
        rag_factory=fake_factory,
        ask_fn=fake_ask,
        structured_store=StructuredKnowledgeStore(tmp_path / "structured.sqlite3"),
        structured_extractor=structured_extractor,
        coverage_question_generator=coverage_question_generator,
        coverage_external_answerer=coverage_external_answerer,
        coverage_fact_checker=coverage_fact_checker,
    )


def test_index_serves_workbench_ui(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    res = client.get("/")

    assert res.status_code == 200
    assert "medguide-rag 運用ワークベンチ" in res.text
    assert "<main" in res.text
    assert "これは学習用デモです" in res.text


def test_health_reports_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(api, "get_llm_provider", lambda: "anthropic")
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)

    workbench = _make_workbench(tmp_path)
    active_revision = workbench.store.get_active_revision()
    client = TestClient(api.create_app(workbench))
    res = client.get("/health")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["provider"] == "anthropic"
    assert body["generation_configured"] is False
    assert body["active_revision_id"] == active_revision["id"]
    assert body["knowledge"]["revision_id"] == active_revision["id"]
    assert body["knowledge"]["source"] == active_revision["source_name"]
    assert body["knowledge"]["source_sha256"] == active_revision["source_sha256"]
    assert body["auth_mode"] == "single_pc_none"
    assert body["anthropic_configured"] is False
    assert body["openai_configured"] is False
    assert body["langfuse_configured"] is False


def test_ask_requires_configured_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "get_llm_provider", lambda: "anthropic")
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    client = TestClient(api.create_app(_make_workbench(tmp_path)))

    res = client.post("/ask", json={"question": "運動はどのくらい？"})

    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_ask_releases_verified_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))

    res = client.post(
        "/ask",
        json={"question": " 運動はどのくらい？ ", "session_id": "session-1"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["question"] == "運動はどのくらい？"
    assert body["run_id"]
    assert body["delivery_status"] == "released"
    assert body["answer"].startswith("非公開候補")
    assert body["blocked_reason"] is None
    assert body["verification"]["status"] == "pass"
    assert body["audit"]["trace_id"]
    assert body["chunks_indexed"] == 71
    assert body["sources"][0]["score"] == 0.859
    assert body["process_steps"][0]["title"] == "質問受付"
    assert [step["title"] for step in body["process_steps"]][-3:] == [
        "回答照合",
        "出荷判定",
        "監査記録",
    ]

    trace = client.get(f"/audit/traces/{body['audit']['trace_id']}")
    assert trace.status_code == 200
    assert trace.json()["run_id"] == body["run_id"]


def test_ask_accepts_answer_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))

    res = client.post(
        "/ask",
        json={"question": "呪術廻戦の作者は誰ですか？", "answer_mode": "standard"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["answer_mode"] == "standard"
    assert body["verification"]["answer_mode"] == "standard"
    assert "standard" in body["process_steps"][4]["input"]


def test_ask_passes_revision_structured_hits_to_rag(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    ask_kwargs_log = []
    workbench = _make_workbench(tmp_path, ask_kwargs_log=ask_kwargs_log)
    revision = workbench.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        content=None,
        label="candidate",
    )
    workbench.structured_store.replace_revision(
        revision["id"],
        entities=[],
        facts=[
            KnowledgeFact(
                fact_id="fact:itadori-simple-domain",
                subject="虎杖悠仁",
                predicate="learned_reason",
                object_text="簡易領域",
                value={"reason": "魂の入れ替え修行で日下部から習得した"},
                evidence=[
                    EvidenceRef(
                        source="source.md",
                        chunk_id=42,
                        excerpt="虎杖は魂の入れ替え修行で簡易領域を習得した。",
                    )
                ],
            )
        ],
    )
    client = TestClient(api.create_app(workbench))

    res = client.post(
        "/ask",
        json={
            "question": "虎杖が簡易領域を習得できた理由",
            "revision_id": revision["id"],
        },
    )

    assert res.status_code == 200
    structured_hits = ask_kwargs_log[0]["structured_hits"]
    assert structured_hits[0]["record_id"] == "fact:itadori-simple-domain"
    assert structured_hits[0]["kind"] == "fact"


def test_structured_records_can_be_replaced_and_searched_by_api(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    created = client.post(
        "/workbench/revisions",
        json={
            "source_path": "data/sample/jujutsu-kaisen-wikipedia.md",
            "label": "candidate",
        },
    )
    revision_id = created.json()["id"]

    replaced = client.post(
        f"/workbench/revisions/{revision_id}/structured-records",
        json={
            "entities": [
                {
                    "entity_id": "policy:expense",
                    "name": "経費承認規程",
                    "entity_type": "policy",
                    "aliases": ["経費ルール"],
                }
            ],
            "facts": [
                {
                    "fact_id": "fact:expense-threshold",
                    "subject_id": "policy:expense",
                    "subject": "経費承認規程",
                    "predicate": "approval_threshold",
                    "value": {"amount": 50000, "currency": "JPY", "approver": "部長"},
                }
            ],
        },
    )

    assert replaced.status_code == 200
    assert replaced.json()["entities"] == 1
    assert replaced.json()["facts"] == 1
    assert len(replaced.json()["structured_sha256"]) == 64

    searched = client.get(
        f"/workbench/revisions/{revision_id}/structured-search",
        params={"q": "5万円の経費は誰が承認する"},
    )

    assert searched.status_code == 200
    item = searched.json()["items"][0]
    assert item["record_id"] == "fact:expense-threshold"
    assert item["data"]["value"]["approver"] == "部長"


def test_active_structured_records_cannot_be_replaced_by_api(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    active_revision_id = workbench.store.get_active_revision()["id"]

    replaced = client.post(
        f"/workbench/revisions/{active_revision_id}/structured-records",
        json={"entities": [], "facts": []},
    )

    assert replaced.status_code == 409
    assert "active revision" in replaced.json()["detail"]


def test_structured_records_can_be_extracted_and_persisted_by_api(tmp_path):
    workbench = _make_workbench(tmp_path, structured_extractor=FakeStructuredExtractor())
    client = TestClient(api.create_app(workbench))
    created = client.post(
        "/workbench/revisions",
        json={
            "source_path": "data/sample/jujutsu-kaisen-wikipedia.md",
            "label": "candidate",
        },
    )
    revision_id = created.json()["id"]

    extracted = client.post(
        f"/workbench/revisions/{revision_id}/structured-extract",
        json={"chunk_limit": 1, "persist": True},
    )

    assert extracted.status_code == 200
    body = extracted.json()
    assert body["chunks_scanned"] == 1
    assert body["facts"][0]["fact_id"] == "fact:auto-expense-threshold"

    searched = client.get(
        f"/workbench/revisions/{revision_id}/structured-search",
        params={"q": "5万円の経費は誰が承認する"},
    )

    assert searched.status_code == 200
    assert searched.json()["items"][0]["record_id"] == "fact:auto-expense-threshold"


def test_structured_extraction_can_target_query_retrieved_chunks(tmp_path):
    workbench = _make_workbench(
        tmp_path,
        structured_extractor=FakeStructuredExtractor(),
        vectorstore=FakeExtractionVectorStore(),
    )
    client = TestClient(api.create_app(workbench))
    created = client.post(
        "/workbench/revisions",
        json={
            "source_path": "data/sample/jujutsu-kaisen-wikipedia.md",
            "label": "candidate",
        },
    )
    revision_id = created.json()["id"]

    extracted = client.post(
        f"/workbench/revisions/{revision_id}/structured-extract",
        json={
            "query": "虎杖が簡易領域を習得できた理由",
            "chunk_limit": 1,
            "persist": True,
        },
    )

    assert extracted.status_code == 200
    body = extracted.json()
    assert body["query"] == "虎杖が簡易領域を習得できた理由"
    assert body["chunks_scanned"] == 1
    assert body["chunks_total"] == 71
    assert body["facts"][0]["evidence"][0]["chunk_id"] == 42


def test_structured_extract_returns_503_without_a_key_when_no_extractor_is_injected(monkeypatch, tmp_path):
    """Previously this fell through to a generic 500 (the real LLMStructuredExtractor
    failing at construction) instead of the same 503-with-message /ask and /runs give
    when no LLM key is configured."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    created = client.post(
        "/workbench/revisions",
        json={"source_path": "data/sample/jujutsu-kaisen-wikipedia.md", "label": "candidate"},
    )
    revision_id = created.json()["id"]

    res = client.post(f"/workbench/revisions/{revision_id}/structured-extract", json={})

    assert res.status_code == 503
    assert "API_KEY" in res.json()["detail"]


def test_structured_extract_still_works_with_an_injected_extractor_and_no_key(monkeypatch, tmp_path):
    """A workbench wired with a non-LLM extractor (this test's fake, or a future
    non-LLM production alternative) never needed a key; the new guard must only
    fire when the real LLM extractor would actually be built."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path, structured_extractor=FakeStructuredExtractor())
    client = TestClient(api.create_app(workbench))
    created = client.post(
        "/workbench/revisions",
        json={"source_path": "data/sample/jujutsu-kaisen-wikipedia.md", "label": "candidate"},
    )
    revision_id = created.json()["id"]

    res = client.post(
        f"/workbench/revisions/{revision_id}/structured-extract",
        json={"chunk_limit": 1, "persist": True},
    )

    assert res.status_code == 200


def test_coverage_loop_marks_external_pass_b_abstention_as_add_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "虎杖が簡易領域を習得できた理由を答えろ。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "focus": "因果関係",
            "questions": [question],
            "external_answers": {
                question: {
                    "answer": "虎杖は入れ替え修行を通じて日下部から簡易領域を学んだ。",
                    "status": "ok",
                    "sources": [{"source": "subagent", "snippet": "fixture"}],
                }
            },
            "knowledge_answers": {
                question: {
                    "answer": "提供された文書には、虎杖が簡易領域を習得できた理由の記載がありません [3]。",
                    "status": "released",
                    "sources": [{"source": "local-rag-log", "snippet": "fixture"}],
                }
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "missing_knowledge": "虎杖、日下部、入れ替え修行、簡易領域習得の因果関係",
                    "reason": "A is usable and B abstained.",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["revision_id"] == revision_id
    assert body["add_candidates"] == 1
    item = body["items"][0]
    assert item["add_knowledge_candidate"] is True
    assert "D judged B as abstain" in item["candidate_reason"]
    assert "簡易領域習得の因果関係" in item["fact_check"]["missing_knowledge"]

    # Persisted to the coverage-candidate ledger (design-doc step 3) with the
    # add_candidate disposition mapped to auto_classified, not auto_approved —
    # promoting further needs a before/after check that does not exist yet.
    assert item["ledger_status"] == "auto_classified"
    candidate_id = item["candidate_id"]

    listed = client.get(f"/workbench/revisions/{revision_id}/coverage-candidates")
    assert listed.status_code == 200
    candidates = listed.json()["items"]
    assert len(candidates) == 1
    assert candidates[0]["id"] == candidate_id
    assert candidates[0]["status"] == "auto_classified"
    assert candidates[0]["disposition"] == "add_candidate"
    assert candidates[0]["question"] == question

    filtered = client.get(
        f"/workbench/revisions/{revision_id}/coverage-candidates",
        params={"status": "auto_rejected"},
    )
    assert filtered.json()["items"] == []


def test_coverage_loop_30_question_fixture_is_well_formed_and_api_submittable():
    """Design-doc step 6 needs a *fixed* 30-question set: before/after comparison is
    meaningless if the questions are regenerated per run. The previous session's set
    was never persisted and was lost, so this pins the replacement down — including
    that it still fits `CoverageLoopRequest`'s 30-question ceiling in one call.
    """
    fixture = json.loads(
        (Path(_ROOT) / "data" / "eval" / "coverage-loop-30-questions.json").read_text(
            encoding="utf-8"
        )
    )
    questions = fixture["questions"]

    assert len(questions) == 30
    per_role = {role: 0 for role in fixture["roles"]}
    for item in questions:
        per_role[item["role"]] += 1
    assert per_role == {"C-1": 10, "C-2": 10, "C-3": 10}

    ids = [item["id"] for item in questions]
    texts = [item["question"] for item in questions]
    assert len(set(ids)) == 30, "duplicate id would collide when aggregating the weakness table"
    # run_coverage_loop de-duplicates by question text, so a duplicate would silently
    # shrink the run below 30 and skew every per-role count computed from it.
    assert len(set(texts)) == 30, "duplicate question text would be dropped by the loop"
    assert all(item["intent"].strip() for item in questions)

    request = api.CoverageLoopRequest(questions=texts)
    assert len(request.questions) == 30


def test_retrieved_chunks_from_sources_normalizes_public_sources():
    """Design-doc step 7: B's retrieval evidence is derived from the public sources
    only — a locator, never the passage text, which stays in `sources[*].snippet`."""
    chunks = retrieved_chunks_from_sources(
        [
            {"rank": 1, "source": "sample.md", "page": 3, "chunk_id": 7, "score": 0.859,
             "snippet": "本文の抜粋"},
            {"rank": 2, "source": "sample.md", "page": None, "chunk_id": None, "score": None},
        ]
    )

    assert [chunk.model_dump() for chunk in chunks] == [
        {"chunk_id": "7", "score": 0.859, "citation": "sample.md p.3", "rank": 1},
        {"chunk_id": "", "score": None, "citation": "sample.md", "rank": 2},
    ]


def test_retrieved_chunks_from_sources_keeps_chunk_id_zero_and_rejects_bad_ranks():
    """`chunk_id` is a 0-based counter (`src/ingest`), so a falsy test would blank out
    the first chunk of every document — the one retrieval surfaces most often. `rank`
    is 1-based and doubles as the `[N]` citation marker, so 0 and bool must not pass
    through as ranks or the marker stops correlating with its source record."""
    chunks = retrieved_chunks_from_sources(
        [
            {"rank": 0, "source": "sample.md", "page": 0, "chunk_id": 0, "score": 0.5},
            {"rank": True, "source": "sample.md", "page": 1, "chunk_id": "abc", "score": True},
        ]
    )

    assert [chunk.model_dump() for chunk in chunks] == [
        {"chunk_id": "0", "score": 0.5, "citation": "sample.md p.0", "rank": 1},
        {"chunk_id": "abc", "score": None, "citation": "sample.md p.1", "rank": 2},
    ]


def test_retrieved_chunks_from_sources_is_empty_when_nothing_was_retrieved():
    """"B retrieved nothing" must stay distinguishable from "B was never asked",
    so an empty/absent source list produces an empty list rather than a fake row."""
    assert retrieved_chunks_from_sources(None) == []
    assert retrieved_chunks_from_sources([]) == []


def test_coverage_loop_records_b_retrieval_evidence_in_the_ledger(monkeypatch, tmp_path):
    """Design-doc step 7: when B is run locally, the chunks its retriever surfaced are
    recorded on the B-answer and persisted with the candidate.

    Without this, a D judgment only sees B's final text, so `missing_knowledge`
    (nothing relevant was retrievable) is indistinguishable from `retrieval_failure`
    (a relevant chunk was retrieved but ranked too low) — the exact conflation the
    failure taxonomy exists to prevent.
    """
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    # Answerable from outside, but absent from the indexed document, so B abstains
    # while its retriever still surfaces (irrelevant) chunks.
    question = "呪術廻戦の全台詞を引用できますか？"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "questions": [question],
            "external_answers": {
                question: {
                    "answer": "全台詞の網羅的な引用は権利上できない。",
                    "status": "ok",
                    # Without an acceptable source type the gap is real but not
                    # auto-adoptable, and the item quarantines instead — see
                    # `ACCEPTABLE_EXTERNAL_SOURCE_TYPES`.
                    "evidence": [
                        {
                            "url": "https://www.shonenjump.com/j/rensai/jujutsu/",
                            "source_type": "official",
                            "span": "無断転載を禁じます",
                            "updated_at": "2026-08-02",
                        }
                    ],
                }
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "failure_cause": "missing_knowledge",
                    "reason": "B abstained and no retrieved chunk carried the answer.",
                }
            },
            "run_knowledge_answerer": True,
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )

    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["disposition"] == "add_candidate"
    assert item["knowledge_answer"]["retrieved_chunks"] == [
        {"chunk_id": "3", "score": 0.859, "citation": "sample.md p.0", "rank": 1}
    ]

    # The evidence has to survive into the ledger: the 30-question experiment
    # (design-doc step 6) is analysed from stored candidates, not from the response.
    candidate = client.get(
        f"/workbench/revisions/{revision_id}/coverage-candidates"
    ).json()["items"][0]
    assert candidate["knowledge_answer"]["retrieved_chunks"] == [
        {"chunk_id": "3", "score": 0.859, "citation": "sample.md p.0", "rank": 1}
    ]


def test_coverage_loop_keeps_injected_b_answers_free_of_invented_retrieval_evidence(
    monkeypatch, tmp_path
):
    """An injected B-answer (subagent output, prior log) that carries no retrieval
    evidence must stay empty rather than have a locator synthesised from its
    `sources`. Fabricated evidence would let D "confirm" a cause it cannot see."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "虎杖が簡易領域を習得できた理由を答えろ。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "questions": [question],
            "external_answers": {question: {"answer": "入れ替え修行で日下部から学んだ。", "status": "ok"}},
            "knowledge_answers": {
                question: {
                    "answer": "提供された文書には記載がありません。",
                    "status": "released",
                    "sources": [{"source": "local-rag-log", "snippet": "fixture"}],
                }
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "failure_cause": "missing_knowledge",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )

    assert res.status_code == 200
    assert res.json()["items"][0]["knowledge_answer"]["retrieved_chunks"] == []


def test_coverage_loop_accepts_injected_retrieval_evidence(monkeypatch, tmp_path):
    """A subagent or replayed log that *did* record retrieval evidence can supply it
    directly, so offline runs reach D with the same evidence a local B run would."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "虎杖が簡易領域を習得できた理由を答えろ。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "questions": [question],
            "external_answers": {question: {"answer": "入れ替え修行で日下部から学んだ。", "status": "ok"}},
            "knowledge_answers": {
                question: {
                    "answer": "提供された文書には記載がありません。",
                    "status": "released",
                    "retrieved_chunks": [
                        {"chunk_id": "12", "score": 0.41, "citation": "sample.md p.5", "rank": 1}
                    ],
                }
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "failure_cause": "retrieval_failure",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )

    assert res.status_code == 200
    assert res.json()["items"][0]["knowledge_answer"]["retrieved_chunks"] == [
        {"chunk_id": "12", "score": 0.41, "citation": "sample.md p.5", "rank": 1}
    ]


def test_coverage_loop_request_accepts_a_100_question_stratified_set_but_not_201():
    """The cap used to be 30 (a leftover from an early 30-question test run), which
    blocked feeding the 100-question stratified eval set through /coverage-loop.
    This pins the new boundary directly on the request model (Pydantic validation
    runs before any business logic, so this doesn't need matching external_answers
    /knowledge_answers/fact_checks for every question)."""
    api.CoverageLoopRequest(questions=["q"] * 100)
    api.CoverageLoopRequest(questions=["q"] * 200)
    try:
        api.CoverageLoopRequest(questions=["q"] * 201)
    except ValidationError:
        pass
    else:
        raise AssertionError("201 questions should exceed the CoverageLoopRequest cap")


def test_coverage_candidate_quarantine_can_be_resolved_by_hand(monkeypatch, tmp_path):
    """A `needs_quarantine` D verdict lands in the ledger as auto_quarantined,
    which is the one status a human is expected to resolve by hand (design-doc
    step 5: "隔離だけまとめてユーザー確認する")."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "呪霊操術の等級差はどう扱われるか。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "focus": "条件/例外",
            "questions": [question],
            "external_answers": {
                question: {"answer": "外部基準回答", "status": "ok"},
            },
            "knowledge_answers": {
                question: {"answer": "記載がありません", "status": "released"},
            },
            "fact_checks": {
                question: {
                    "external_status": "unclear",
                    "knowledge_status": "unclear",
                    "same_answer": False,
                    "failure_cause": "needs_quarantine",
                    "reason": "two plausible causes, could not decide",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )
    assert res.status_code == 200
    candidate_id = res.json()["items"][0]["candidate_id"]
    assert res.json()["items"][0]["ledger_status"] == "auto_quarantined"

    # Resolving before it is quarantined-eligible is refused; only auto_quarantined
    # candidates may be resolved, and only into one of the two terminal states.
    bad_status = client.post(
        f"/workbench/coverage-candidates/{candidate_id}/resolve",
        json={"status": "auto_approved", "reason": "not a real status check"},
    )
    # (this call is valid — auto_quarantined -> auto_approved is allowed — but a
    # second resolution attempt on the now-resolved candidate must be refused)
    assert bad_status.status_code == 200
    assert bad_status.json()["status"] == "auto_approved"

    second_attempt = client.post(
        f"/workbench/coverage-candidates/{candidate_id}/resolve",
        json={"status": "auto_rejected", "reason": "changed my mind"},
    )
    assert second_attempt.status_code == 409

    missing = client.post(
        "/workbench/coverage-candidates/does-not-exist/resolve",
        json={"status": "auto_approved", "reason": "n/a"},
    )
    assert missing.status_code == 404


def test_coverage_candidate_can_be_driven_to_active_through_the_api(monkeypatch, tmp_path):
    """Phase 3: the API surface for Phase 2's implement/verify/activate endpoints.

    Drives one candidate auto_classified -> auto_approved -> implemented -> verified
    -> active entirely through HTTP, with a fake (not LLM-generated) validation job
    standing in for a real one — the same shape the quarantine-list UI's action
    buttons call."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "五条悟の代表的な技には、どんな名前が付いていますか。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "questions": [question],
            "external_answers": {
                question: {
                    "answer": "術式順転「蒼」など",
                    "status": "ok",
                    "evidence": [{"url": "https://example.test/a", "source_type": "official"}],
                },
            },
            "knowledge_answers": {
                question: {"answer": "記載がありません", "status": "released"},
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "failure_cause": "missing_knowledge",
                    "missing_knowledge": "術式順転「蒼」等の技名を追記する。",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )
    assert res.status_code == 200
    candidate_id = res.json()["items"][0]["candidate_id"]
    assert res.json()["items"][0]["ledger_status"] == "auto_classified"

    # GET single candidate.
    got = client.get(f"/workbench/coverage-candidates/{candidate_id}")
    assert got.status_code == 200
    assert got.json()["status"] == "auto_classified"
    assert client.get("/workbench/coverage-candidates/does-not-exist").status_code == 404

    # auto_classified is now resolvable directly (Phase 2), not just auto_quarantined.
    approved = client.post(
        f"/workbench/coverage-candidates/{candidate_id}/resolve",
        json={"status": "auto_approved", "reason": "sourced acceptably"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "auto_approved"

    # implement: too-early verify/activate are refused (409), not skippable.
    assert client.post(f"/workbench/coverage-candidates/{candidate_id}/verify").status_code == 409
    assert (
        client.post(
            f"/workbench/coverage-candidates/{candidate_id}/activate", json={"reason": "too soon"}
        ).status_code
        == 409
    )

    implemented = client.post(f"/workbench/coverage-candidates/{candidate_id}/implement")
    assert implemented.status_code == 200
    assert implemented.json()["status"] == "implemented"
    new_revision_id = implemented.json()["implemented_revision_id"]
    assert new_revision_id

    # Re-implementing an already-implemented candidate is refused.
    assert client.post(f"/workbench/coverage-candidates/{candidate_id}/implement").status_code == 409

    # verify: no validation job yet -> refused.
    assert client.post(f"/workbench/coverage-candidates/{candidate_id}/verify").status_code == 409

    new_revision = workbench.store.get_revision(new_revision_id)
    eval_path = Path(_ROOT) / new_revision["config"]["eval_set"]
    job = workbench.store.create_job(
        revision_id=new_revision_id, kind="validation", engine_fingerprint=engine_fingerprint()
    )
    workbench.store.mark_job_running(job["id"])
    workbench.store.finish_job(
        job["id"],
        status="passed",
        result={
            "full_pass": True,
            "no_regression": True,
            "source_hashes": {"after": new_revision["source_sha256"]},
            "structured_hashes": {"after": workbench.structured_digest(new_revision_id)},
            "eval_set_sha256": sha256_file(eval_path),
        },
    )

    verified = client.post(f"/workbench/coverage-candidates/{candidate_id}/verify")
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"

    activated = client.post(
        f"/workbench/coverage-candidates/{candidate_id}/activate",
        json={"reason": "promoted via api"},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert workbench.store.get_active_revision()["id"] == new_revision_id


def test_verify_and_activate_reject_a_chunking_failure_candidate(monkeypatch, tmp_path):
    """Phase 1 (2026-08-03) measured `chunking_failure` at 0/5 against a construction-
    verified gold set — it must not verify even with a perfectly passing validation
    job, matching `AUTO_PROMOTABLE_CAUSES` (src/coverage_loop.py)."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    revision_id = workbench.store.get_active_revision()["id"]
    client = TestClient(api.create_app(workbench))
    question = "術式Aと術式Bはどちらも誰のものですか。"

    res = client.post(
        f"/workbench/revisions/{revision_id}/coverage-loop",
        json={
            "questions": [question],
            "external_answers": {
                question: {
                    "answer": "両方とも同一人物のものです",
                    "status": "ok",
                    "evidence": [{"url": "https://example.test/a", "source_type": "official"}],
                },
            },
            "knowledge_answers": {
                question: {"answer": "記載がありません", "status": "released"},
            },
            "fact_checks": {
                question: {
                    "external_status": "pass",
                    "knowledge_status": "abstain",
                    "same_answer": False,
                    "failure_cause": "chunking_failure",
                    "missing_knowledge": "分断された事実を統合する。",
                }
            },
            "rounds": 1,
            "max_questions_per_round": 1,
        },
    )
    candidate_id = res.json()["items"][0]["candidate_id"]
    client.post(
        f"/workbench/coverage-candidates/{candidate_id}/resolve",
        json={"status": "auto_approved", "reason": "sourced acceptably"},
    )
    implemented = client.post(f"/workbench/coverage-candidates/{candidate_id}/implement")
    new_revision_id = implemented.json()["implemented_revision_id"]
    new_revision = workbench.store.get_revision(new_revision_id)
    eval_path = Path(_ROOT) / new_revision["config"]["eval_set"]
    job = workbench.store.create_job(
        revision_id=new_revision_id, kind="validation", engine_fingerprint=engine_fingerprint()
    )
    workbench.store.mark_job_running(job["id"])
    workbench.store.finish_job(
        job["id"],
        status="passed",
        result={
            "full_pass": True,
            "no_regression": True,
            "source_hashes": {"after": new_revision["source_sha256"]},
            "structured_hashes": {"after": workbench.structured_digest(new_revision_id)},
            "eval_set_sha256": sha256_file(eval_path),
        },
    )

    assert client.post(f"/workbench/coverage-candidates/{candidate_id}/verify").status_code == 409


def test_blocked_candidate_is_not_returned_but_is_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path, blocked=True)
    client = TestClient(api.create_app(workbench))

    res = client.post("/ask", json={"question": "危険な質問"})

    assert res.status_code == 200
    body = res.json()
    assert body["delivery_status"] == "blocked"
    assert body["answer"] is None
    assert "非公開候補" not in res.text
    assert "private candidate wording" not in res.text
    assert body["verification"]["status"] == "block"
    assert body["blocked_reason"] == "standard_gate_failed"

    with sqlite3.connect(workbench.store.db_path) as connection:
        saved = connection.execute(
            "SELECT candidate_answer FROM runs WHERE id = ?", (body["run_id"],)
        ).fetchone()
    assert saved[0].startswith("非公開候補")
    adjustments = client.get("/workbench/adjustments").json()["items"]
    assert adjustments[0]["run_id"] == body["run_id"]


def test_async_run_records_events_without_leaking_blocked_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path, blocked=True)
    client = TestClient(api.create_app(workbench))

    created = client.post("/runs", json={"question": "危険な質問"})

    assert created.status_code == 202
    run_id = created.json()["run_id"]

    status = client.get(f"/runs/{run_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "completed"
    assert body["response"]["delivery_status"] == "blocked"
    assert body["response"]["answer"] is None
    assert "非公開候補" not in status.text
    assert "private candidate wording" not in status.text

    event_types = [event["event_type"] for event in body["events"]]
    assert event_types[:2] == ["queued", "running"]
    assert "question_received" in event_types
    assert "knowledge_selected" in event_types
    assert "verify_completed" in event_types
    assert "gate_completed" in event_types
    assert event_types[-1] == "completed"

    streamed = client.get(f"/runs/{run_id}/events")
    assert streamed.status_code == 200
    assert "text/event-stream" in streamed.headers["content-type"]
    assert "非公開候補" not in streamed.text


def test_revision_validation_and_atomic_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "is_generation_configured", lambda: True)
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    original_active = workbench.store.get_active_revision()["id"]

    created = client.post(
        "/workbench/revisions",
        json={
            "source_path": "data/sample/jujutsu-kaisen-wikipedia.md",
            "label": "candidate",
        },
    )
    assert created.status_code == 201
    revision_id = created.json()["id"]
    assert created.json()["source_sha256"]

    premature = client.post(f"/workbench/revisions/{revision_id}/approve", json={})
    assert premature.status_code == 409
    assert workbench.store.get_active_revision()["id"] == original_active

    queued = client.post(
        f"/workbench/revisions/{revision_id}/validation-jobs",
        json={},
    )
    assert queued.status_code == 202
    job = client.get(f"/workbench/jobs/{queued.json()['id']}").json()
    assert job["status"] == "succeeded"
    assert job["persistence_status"] == "passed"
    assert job["approval_allowed"] is True
    assert job["result"]["full_pass"] is True
    assert job["result"]["no_regression"] is True
    assert job["result"]["engine_fingerprint"]
    assert set(job["result"]["source_hashes"]) == {"before", "after"}
    assert set(job["result"]["structured_hashes"]) == {"before", "after"}
    assert job["result"]["before"]["items"][0]["answer"]
    assert job["result"]["after"]["items"][0]["verification"]["status"] == "pass"

    locked = client.post(
        f"/workbench/revisions/{revision_id}/structured-records",
        json={"entities": [], "facts": []},
    )
    assert locked.status_code == 409

    approved = client.post(
        f"/workbench/revisions/{revision_id}/approve",
        json={"reason": "full validation passed"},
    )
    assert approved.status_code == 200
    assert approved.json()["active"] is True
    assert workbench.store.get_active_revision()["id"] == revision_id


def test_out_of_document_eval_requires_canonical_abstention(tmp_path, monkeypatch):
    workbench = _make_workbench(tmp_path)
    monkeypatch.setattr(
        workbench,
        "answer_question",
        lambda **_kwargs: {
            "run_id": "run",
            "delivery_status": "released",
            "answer": "根拠のない任意回答です。",
            "blocked_reason": None,
            "verification": {"status": "pass", "axes": {}},
            "sources": [],
        },
    )

    result = workbench._evaluate_revision(
        workbench.store.get_active_revision(),
        questions=[
            {
                "id": "out-of-doc",
                "question": "文書外の質問",
                "in_doc": False,
                "expected_terms": [],
            }
        ],
        job_id="offline-test",
    )

    assert result["released"] == 1
    assert result["accepted"] == 0
    assert result["items"][0]["expectation_pass"] is False


def test_validation_cannot_be_reduced_to_a_question_or_limit(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    revision = client.post(
        "/workbench/revisions",
        json={"content": "# Candidate\n\nKnowledge.", "label": "candidate"},
    ).json()

    limited = client.post(
        f"/workbench/revisions/{revision['id']}/validation-jobs",
        json={"question_limit": 1},
    )
    targeted = client.post(
        f"/workbench/revisions/{revision['id']}/validation-jobs",
        json={"question": "one question"},
    )
    missing_comparison_question = client.post(
        f"/workbench/revisions/{revision['id']}/comparison-jobs",
        json={},
    )

    assert limited.status_code == 400
    assert targeted.status_code == 400
    assert missing_comparison_question.status_code == 400


def test_comparison_and_validation_jobs_return_503_without_a_key(monkeypatch, tmp_path):
    """Previously job creation always returned 202 and a missing key only surfaced
    asynchronously inside the job — and for a comparison job it didn't even surface
    as `status="error"`, because `_evaluate_revision` swallows per-item exceptions
    and `run_job` marks any comparison job `"passed"` unconditionally. Guard
    upfront instead, matching /ask and /runs."""
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    revision = client.post(
        "/workbench/revisions",
        json={"content": "# Candidate\n\nKnowledge.", "label": "candidate"},
    ).json()

    comparison = client.post(
        f"/workbench/revisions/{revision['id']}/comparison-jobs",
        json={"question": "one question"},
    )
    validation = client.post(
        f"/workbench/revisions/{revision['id']}/validation-jobs",
        json={},
    )

    assert comparison.status_code == 503
    assert "API_KEY" in comparison.json()["detail"]
    assert validation.status_code == 503
    assert "API_KEY" in validation.json()["detail"]


def test_rejecting_active_revision_is_conflict(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    active_id = workbench.store.get_active_revision()["id"]

    response = client.post(
        f"/workbench/revisions/{active_id}/reject",
        json={"reason": "should fail"},
    )

    assert response.status_code == 409


def test_revision_source_path_rejects_workspace_escape(tmp_path):
    client = TestClient(api.create_app(_make_workbench(tmp_path)))

    res = client.post(
        "/workbench/revisions",
        json={"source_path": str(tmp_path / "outside.md"), "label": "escape"},
    )

    assert res.status_code == 400


def test_revision_accepts_workbench_content_contract(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))

    res = client.post(
        "/workbench/revisions",
        json={
            "operator": "local-reviewer",
            "change_summary": "用語を明確化",
            "title": "改訂ナレッジ",
            "source_url": "https://example.com/source",
            "content": "# 改訂\n\n根拠となる本文です。",
        },
    )

    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "draft"
    assert body["change_summary"] == "用語を明確化"
    assert body["title"] == "改訂ナレッジ"
    assert "source_path" not in body
    internal = workbench.store.get_revision(body["id"])
    assert open(internal["source_path"], encoding="utf-8").read().startswith("# 改訂")


def test_revision_accepts_pdf_replacement_upload(tmp_path):
    workbench = _make_workbench(tmp_path)
    client = TestClient(api.create_app(workbench))
    pdf_bytes = b"%PDF-1.4\n% local replacement fixture\n"

    response = client.post(
        "/workbench/revisions",
        json={
            "operator": "local-reviewer",
            "change_summary": "PDF差し替え",
            "title": "PDF改訂",
            "filename": "replacement.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        },
    )

    assert response.status_code == 201
    internal = workbench.store.get_revision(response.json()["id"])
    assert internal["source_name"] == "replacement.pdf"
    assert open(internal["source_path"], "rb").read() == pdf_bytes
