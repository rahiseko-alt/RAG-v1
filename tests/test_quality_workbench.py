"""Tests for the workbench half of the coverage-loop corpus evidence.

`tests/test_coverage_loop.py` covers the pure functions with fake probers. Everything
here needs the workbench itself, because the properties being pinned are the ones a
fake prober cannot show: that chunk ids resolve against a real chunked file, and that
the judge-only channel is actually stripped before the ledger and the API response see
it. Both were asserted in prose and by hand before, and neither was machine-enforced —
deleting the strip left the suite green.
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.coverage_loop import AgentAnswer, FactCheckJudgment, RetrievedChunk  # noqa: E402
from src.quality.store import WorkbenchStore  # noqa: E402
from src.quality.workbench import QualityWorkbench, RevisionCorpusProber  # noqa: E402


def _revision(tmp_path):
    """A store on a temp DB, over the configured knowledge source.

    The real source rather than a synthetic file: the store only accepts sources inside
    the product workspace, and chunk ids are only meaningful against the file the index
    was actually built from — which is the property these tests exist to check.
    """
    store = WorkbenchStore(
        tmp_path / "workbench.sqlite3",
        runtime_root=tmp_path / "runtime",
        engine_fingerprint="fp",
    )
    return store, store.get_active_revision()


def test_prober_resolves_real_chunk_ids_including_zero(tmp_path):
    """chunk_id 0 is the first chunk of every document and the one retrieval returns
    most often; this repo has already shipped one bug that blanked it (docs/failures.md
    2026-08-02), so the lookup is pinned rather than assumed."""
    _store, revision = _revision(tmp_path)
    prober = RevisionCorpusProber(revision["source_path"])

    corpus = prober.corpus()
    assert corpus, "chunking produced nothing"
    assert corpus[0].chunk_id == "0"
    assert corpus[0].text

    texts = prober.surfaced_texts(
        knowledge_answer=AgentAnswer(
            role="knowledge", retrieved_chunks=[RetrievedChunk(chunk_id="0", rank=1)]
        )
    )
    assert [entry["chunk_id"] for entry in texts] == ["0"]
    assert texts[0]["unresolved"] is False
    assert texts[0]["text"] == corpus[0].text


def test_unresolvable_chunk_ids_are_reported_not_dropped(tmp_path):
    """An empty list read as "no chunk answered it" is what sends D to the weak lexical
    evidence. A chunk it could not read must look different from a chunk it read and
    found silent — structured-knowledge hits carry no chunk id at all."""
    _store, revision = _revision(tmp_path)
    prober = RevisionCorpusProber(revision["source_path"])

    texts = prober.surfaced_texts(
        knowledge_answer=AgentAnswer(
            role="knowledge",
            retrieved_chunks=[
                RetrievedChunk(chunk_id="0", rank=1),
                RetrievedChunk(chunk_id="", rank=2),
                RetrievedChunk(chunk_id="99999", rank=3),
            ],
        )
    )
    assert len(texts) == 3
    assert [entry["unresolved"] for entry in texts] == [False, True, True]
    assert texts[1]["text"] is None


def test_surfaced_texts_never_reach_the_ledger_or_the_response(tmp_path):
    """The judge gets full chunk text; the ledger and the API response must not. Both
    answers are the same model, so a caller could otherwise push unbounded text in
    through `external_answers` — which is exactly what the knowledge-side-only strip
    allowed."""
    store, revision = _revision(tmp_path)
    workbench = QualityWorkbench(store)
    question = "呪力とは何ですか。"

    result = workbench.run_revision_coverage_loop(
        str(revision["id"]),
        questions=[question],
        external_answers={
            question: {
                "answer": "A の回答",
                # A caller trying to smuggle bulk text in through the judge-only field.
                "surfaced_texts": [{"rank": 1, "chunk_id": "0", "text": "巨大な本文" * 500}],
            }
        },
        knowledge_answers={
            question: {
                "answer": "記載がありません",
                "retrieved_chunks": [{"chunk_id": "0", "rank": 1}],
            }
        },
        fact_checks={question: {"external_status": "pass", "knowledge_status": "fail"}},
        persist=True,
    )

    item = result["items"][0]
    assert "surfaced_texts" not in item["knowledge_answer"]
    assert "surfaced_texts" not in item["external_answer"]

    candidates = store.list_coverage_candidates()
    assert len(candidates) == 1
    stored = json.dumps(candidates[0], ensure_ascii=False)
    assert "surfaced_texts" not in stored
    assert "巨大な本文" not in stored


def test_injected_evidence_cannot_replace_what_the_prober_produced(tmp_path):
    """`surfaced_texts` ends the judgment at step 1, so it is the most valuable thing to
    forge. An injected B-answer records which chunks were retrieved; it does not get to
    say what they contained."""
    store, revision = _revision(tmp_path)
    seen: dict[str, object] = {}

    class RecordingChecker:
        def check(self, *, question, external_answer, knowledge_answer):
            seen["texts"] = knowledge_answer.surfaced_texts
            seen["probe"] = knowledge_answer.corpus_probe
            return FactCheckJudgment(external_status="pass", knowledge_status="fail")

    workbench = QualityWorkbench(store, coverage_fact_checker=RecordingChecker())
    question = "呪力とは何ですか。"
    workbench.run_revision_coverage_loop(
        str(revision["id"]),
        questions=[question],
        external_answers={question: {"answer": "A の回答"}},
        knowledge_answers={
            question: {
                "answer": "記載がありません",
                "retrieved_chunks": [{"chunk_id": "0", "rank": 1}],
                "surfaced_texts": [{"rank": 1, "chunk_id": "0", "text": "捏造された本文"}],
            }
        },
        # The injected path swaps in `ProvidedFactChecker`, so observing what D was
        # handed means going through the configured checker instead. A and B are still
        # supplied, so no model is reached.
        allow_llm_agents=True,
        persist=False,
    )

    texts = seen["texts"]
    assert isinstance(texts, list) and texts
    assert all("捏造された本文" != entry["text"] for entry in texts)
    assert seen["probe"] is not None
