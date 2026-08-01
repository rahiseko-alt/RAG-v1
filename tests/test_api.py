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

import src.api as api  # noqa: E402
from src.coverage_loop import AgentAnswer, CoverageQuestion, FactCheckJudgment  # noqa: E402
from src.quality import OnlineVerifier, QualityWorkbench, WorkbenchStore  # noqa: E402
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
