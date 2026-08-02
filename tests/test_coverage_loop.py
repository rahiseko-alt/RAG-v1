"""Unit tests for the coverage-loop failure-cause taxonomy.

These test `classify_coverage_item` directly (not through the FastAPI layer,
which `tests/test_api.py` already covers for one end-to-end case) because the
taxonomy's whole point is a decision table: cause -> disposition. That table is
easiest to pin down with one small case per row, including the "D judgment is
internally inconsistent" cases that fail closed to quarantine.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.coverage_loop import (  # noqa: E402
    ADDABLE_CAUSES,
    REJECTED_CAUSES,
    AgentAnswer,
    CorpusChunk,
    FactCheckJudgment,
    RetrievedChunk,
    build_corpus_probe,
    classify_coverage_item,
)


def _external(status: str = "ok") -> AgentAnswer:
    return AgentAnswer(role="external", answer="A の回答", status=status)


def _knowledge(*, answer: str = "記載がありません", status: str = "released") -> AgentAnswer:
    return AgentAnswer(role="knowledge", answer=answer, status=status)


def test_no_failure_cause_falls_back_to_legacy_heuristic():
    """Callers that predate the taxonomy (no failure_cause) keep old behavior."""
    judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="abstain",
        same_answer=False,
        missing_knowledge="欠けている知識",
    )
    disposition, reason = classify_coverage_item(
        external_answer=_external(), knowledge_answer=_knowledge(), judgment=judgment
    )
    assert disposition == "add_candidate"
    assert "D judged B as abstain" in reason
    assert "欠けている知識" in reason


def test_missing_knowledge_with_b_failure_is_add_candidate():
    judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="abstain",
        same_answer=False,
        failure_cause="missing_knowledge",
        missing_knowledge="欠けている知識",
    )
    disposition, reason = classify_coverage_item(
        external_answer=_external(), knowledge_answer=_knowledge(), judgment=judgment
    )
    assert disposition == "add_candidate"
    assert "欠けている知識" in reason


def test_all_addable_causes_produce_add_candidate_when_b_actually_failed():
    for cause in sorted(ADDABLE_CAUSES):
        judgment = FactCheckJudgment(
            external_status="pass",
            knowledge_status="fail",
            same_answer=False,
            failure_cause=cause,
        )
        disposition, _ = classify_coverage_item(
            external_answer=_external(),
            knowledge_answer=_knowledge(status="error"),
            judgment=judgment,
        )
        assert disposition == "add_candidate", cause


def test_all_rejected_causes_are_rejected_even_when_a_passed():
    for cause in sorted(REJECTED_CAUSES):
        judgment = FactCheckJudgment(
            external_status="pass",
            knowledge_status="fail",
            same_answer=False,
            failure_cause=cause,
        )
        disposition, _ = classify_coverage_item(
            external_answer=_external(),
            knowledge_answer=_knowledge(status="error"),
            judgment=judgment,
        )
        assert disposition == "rejected", cause


def test_needs_quarantine_cause_is_quarantined():
    judgment = FactCheckJudgment(
        external_status="unclear",
        knowledge_status="unclear",
        same_answer=False,
        failure_cause="needs_quarantine",
        reason="two plausible causes, could not decide",
    )
    disposition, reason = classify_coverage_item(
        external_answer=_external(status="unclear"),
        knowledge_answer=_knowledge(status="error"),
        judgment=judgment,
    )
    assert disposition == "quarantined"
    assert "could not decide" in reason


def test_same_answer_is_no_gap_regardless_of_cause():
    """A and B agreeing means there is nothing to act on, whatever D named as cause."""
    judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="pass",
        same_answer=True,
        failure_cause="missing_knowledge",
    )
    disposition, _ = classify_coverage_item(
        external_answer=_external(), knowledge_answer=_knowledge(answer="A と同じ回答", status="released"),
        judgment=judgment,
    )
    assert disposition == "no_gap"


def test_addable_cause_without_a_passing_is_quarantined_not_trusted():
    """missing_knowledge claimed, but A itself did not pass -> contradictory, quarantine."""
    judgment = FactCheckJudgment(
        external_status="fail",
        knowledge_status="fail",
        same_answer=False,
        failure_cause="missing_knowledge",
    )
    disposition, _ = classify_coverage_item(
        external_answer=_external(status="fail"),
        knowledge_answer=_knowledge(status="error"),
        judgment=judgment,
    )
    assert disposition == "quarantined"


def test_addable_cause_without_any_b_failure_signal_is_quarantined_not_silently_added():
    """D named a knowledge-fixable cause, but B shows no failure signal at all —
    likely a stale or copy-pasted judgment. Do not silently create a candidate."""
    judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="pass",
        same_answer=False,
        failure_cause="retrieval_failure",
    )
    disposition, _ = classify_coverage_item(
        external_answer=_external(),
        knowledge_answer=_knowledge(answer="十分に回答済み", status="released"),
        judgment=judgment,
    )
    assert disposition == "quarantined"


def test_disposition_and_add_knowledge_candidate_stay_consistent_via_run_coverage_loop():
    """CoverageLoopItem.add_knowledge_candidate must always mirror disposition,
    since existing consumers (tests/test_api.py, any future ledger) read the
    boolean while the taxonomy work reads `disposition`."""
    from src.coverage_loop import (
        CoverageLoopItem,
    )

    item = CoverageLoopItem(
        question="q",
        external_answer=_external(),
        knowledge_answer=_knowledge(),
        fact_check=FactCheckJudgment(
            external_status="pass", knowledge_status="fail", failure_cause="chunking_failure"
        ),
        disposition="add_candidate",
        add_knowledge_candidate=True,
    )
    assert item.add_knowledge_candidate == (item.disposition == "add_candidate")


# --- corpus probe -----------------------------------------------------------
#
# The probe is what lets D pick between missing_knowledge / retrieval_failure /
# generation_failure at all: B's own evidence is consistent with all three. Each test
# below pins one of those three readings, because a probe that mislabels which bucket
# a term falls into would push D toward a wrong cause with apparent evidence behind it
# — worse than the quarantine it replaces.

_CORPUS = [
    CorpusChunk(chunk_id="0", text="五条悟は無下限呪術の使い手である。"),
    CorpusChunk(chunk_id="1", text="反転術式は負の呪力を掛け合わせて正のエネルギーを生む。"),
    CorpusChunk(chunk_id="7", text="縛りは制約と引き換えに出力を高める仕組みである。"),
]


def _probe(*, question, answer, surfaced_chunk_ids):
    return build_corpus_probe(
        question=question,
        external_answer=AgentAnswer(role="external", answer=answer),
        knowledge_answer=AgentAnswer(
            role="knowledge",
            retrieved_chunks=[RetrievedChunk(chunk_id=cid) for cid in surfaced_chunk_ids],
        ),
        corpus=_CORPUS,
    )


def test_term_missing_from_every_chunk_supports_missing_knowledge():
    probe = _probe(
        question="黒閃とは何ですか。",
        answer="黒閃は呪力の衝突で生じる現象である。",
        surfaced_chunk_ids=["0", "1"],
    )
    assert "黒閃" in probe.absent_from_corpus
    assert "黒閃" not in probe.present_but_unsurfaced
    assert "黒閃" not in probe.present_in_surfaced


def test_term_in_corpus_but_not_retrieved_supports_retrieval_failure():
    """The whole point: this is invisible to B, which only ever sees what it retrieved."""
    probe = _probe(
        question="出力を高める方法はありますか。",
        answer="縛りを設けると出力が高まる。",
        surfaced_chunk_ids=["0", "1"],
    )
    assert "縛り" in probe.present_but_unsurfaced
    evidence = next(item for item in probe.probed_terms if item.term == "縛り")
    assert evidence.corpus_chunk_ids == ["7"]
    assert evidence.surfaced_chunk_ids == []


def test_term_inside_a_surfaced_chunk_supports_generation_failure():
    probe = _probe(
        question="呪力について教えてください。",
        answer="反転術式は正のエネルギーを生む。",
        surfaced_chunk_ids=["1"],
    )
    assert "反転術式" in probe.present_in_surfaced
    evidence = next(item for item in probe.probed_terms if item.term == "反転術式")
    assert evidence.surfaced_chunk_ids == ["1"]


def test_question_only_terms_are_probed_and_labelled():
    """The question's own subject is often the decisive term — probing only what A
    added would drop it. Terms are labelled by origin instead of filtered out."""
    probe = _probe(
        question="黒閃とは何ですか。",
        answer="呪力の衝突で生じる現象である。",
        surfaced_chunk_ids=["0", "1"],
    )
    evidence = next(item for item in probe.probed_terms if item.term == "黒閃")
    assert evidence.source == "question"
    assert "黒閃" in probe.absent_from_corpus


def test_terms_in_both_question_and_answer_are_labelled_both():
    probe = _probe(
        question="反転術式とは何ですか。",
        answer="反転術式は負の呪力を掛け合わせる。",
        surfaced_chunk_ids=["1"],
    )
    evidence = next(item for item in probe.probed_terms if item.term == "反転術式")
    assert evidence.source == "both"


def test_probe_counts_report_corpus_and_surfaced_sizes():
    probe = _probe(
        question="質問",
        answer="黒閃について。",
        surfaced_chunk_ids=["0", "1"],
    )
    assert probe.corpus_chunk_count == 3
    assert probe.surfaced_chunk_count == 2
    assert "lexical" in probe.method


def test_run_coverage_loop_attaches_the_probe_to_b_before_judging():
    """D must receive the probe; that is the entire delivery path for this evidence."""
    from src.coverage_loop import CoverageQuestion, run_coverage_loop

    seen = {}

    class RecordingChecker:
        def check(self, *, question, external_answer, knowledge_answer):
            seen["probe"] = knowledge_answer.corpus_probe
            return FactCheckJudgment(
                external_status="pass", knowledge_status="fail", failure_cause="retrieval_failure"
            )

    class Prober:
        def probe(self, *, question, external_answer, knowledge_answer):
            return build_corpus_probe(
                question=question,
                external_answer=external_answer,
                knowledge_answer=knowledge_answer,
                corpus=_CORPUS,
            )

    class Generator:
        def generate(self, *, focus, seed_questions, previous_findings, max_questions):
            return [CoverageQuestion(question=q) for q in seed_questions]

    result = run_coverage_loop(
        revision_id="rev",
        focus=None,
        seed_questions=["出力を高める方法はありますか。"],
        external_answers={"出力を高める方法はありますか。": "縛りを設けると出力が高まる。"},
        knowledge_answers={"出力を高める方法はありますか。": {"answer": "記載がありません"}},
        rounds=1,
        max_questions_per_round=1,
        question_generator=Generator(),
        external_answerer=None,
        fact_checker=RecordingChecker(),
        knowledge_answerer=None,
        answer_mode="standard",
        corpus_prober=Prober(),
    )
    assert seen["probe"] is not None
    assert "縛り" in seen["probe"].present_but_unsurfaced
    # and it survives onto the item, so the ledger persists what D was shown
    assert result.items[0].knowledge_answer.corpus_probe is not None
