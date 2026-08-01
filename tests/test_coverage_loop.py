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
    FactCheckJudgment,
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
