"""Unit tests for the classifier gold-set construction and scoring (Phase 1).

Fabricated `RetrievedChunk`/corpus-text fixtures only — no embeddings, no Chroma, no
LLM — matching `src/quality/classifier_gold.py`'s own design goal of staying
hermetic and fast. `scripts/build_classifier_gold_set.py` is what wires these
functions to the real corpus and retrieval pipeline; that wiring is exercised by
actually running the script, not by these tests.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.coverage_loop import (  # noqa: E402
    AgentAnswer,
    EvidenceSource,
    FactCheckJudgment,
    RetrievedChunk,
    has_acceptable_external_evidence,
)
from src.quality.classifier_gold import (  # noqa: E402
    GoldItem,
    aggregate_scores,
    build_chunking_failure_items,
    build_generation_failure_items,
    build_invalid_a_items,
    build_missing_knowledge_items,
    build_retrieval_failure_items,
    score_item,
)


def test_retrieval_failure_drops_the_gold_chunk_from_bs_context():
    item = {
        "id": "E-01",
        "question": "誰が作者ですか",
        "answer_chunk_id": "5",
        "answer_span": "作者は芥見下々です",
    }
    retrieved = {
        "E-01": [
            RetrievedChunk(chunk_id="1", rank=1),
            RetrievedChunk(chunk_id="2", rank=2),
            RetrievedChunk(chunk_id="5", rank=3),
        ]
    }
    corpus = {
        "1": "無関係な文章その1",
        "2": "無関係な文章その2",
        "5": "作者は芥見下々です。単行本は集英社。",
    }
    items = build_retrieval_failure_items([item], retrieved, corpus, n=6)
    assert len(items) == 1
    gold = items[0]
    assert gold.gold_cause == "retrieval_failure"
    kept_ids = [chunk.chunk_id for chunk in gold.knowledge_answer.retrieved_chunks]
    assert kept_ids == ["1", "2"]
    assert "5" not in kept_ids
    assert gold.construction_evidence["verified_span_absent_from_kept"] is True
    assert gold.construction_evidence["measured_rank"] == 3


def test_retrieval_failure_skips_gold_rank_one_items():
    item = {
        "id": "E-02",
        "question": "誰が作者ですか",
        "answer_chunk_id": "5",
        "answer_span": "作者は芥見下々です",
    }
    retrieved = {"E-02": [RetrievedChunk(chunk_id="5", rank=1)]}
    corpus = {"5": "作者は芥見下々です。"}
    assert build_retrieval_failure_items([item], retrieved, corpus, n=6) == []


def test_retrieval_failure_skips_when_span_leaks_into_a_kept_chunk():
    item = {
        "id": "E-03",
        "question": "誰が作者ですか",
        "answer_chunk_id": "5",
        "answer_span": "作者は芥見下々です",
    }
    retrieved = {
        "E-03": [
            RetrievedChunk(chunk_id="1", rank=1),
            RetrievedChunk(chunk_id="5", rank=2),
        ]
    }
    corpus = {"1": "重複して、作者は芥見下々です、と書かれている", "5": "作者は芥見下々です。"}
    assert build_retrieval_failure_items([item], retrieved, corpus, n=6) == []


def test_missing_knowledge_carries_absence_check_through_unchanged():
    item = {
        "id": "U-01",
        "question": "誕生日はいつですか",
        "absence_check": "全41チャンクを検索し出現数0",
        "plausible_distractor_chunk_id": "12",
    }
    retrieved = {"U-01": [RetrievedChunk(chunk_id="12", rank=1)]}
    items = build_missing_knowledge_items(
        [item], retrieved, synthetic_a_answers={"U-01": "12月7日です"}, n=6
    )
    assert len(items) == 1
    gold = items[0]
    assert gold.gold_cause == "missing_knowledge"
    assert gold.construction_evidence["absence_check"] == "全41チャンクを検索し出現数0"
    assert gold.construction_evidence["synthetic_a_answer"] is True
    assert gold.external_answer.answer == "12月7日です"
    assert gold.knowledge_answer.answer == "記載がありません"


def test_missing_knowledge_skips_items_without_a_synthetic_answer():
    item = {"id": "U-02", "question": "Q", "absence_check": "x", "plausible_distractor_chunk_id": "1"}
    assert build_missing_knowledge_items([item], {}, synthetic_a_answers={}, n=6) == []


def test_generation_failure_requires_the_gold_chunk_actually_surfaced_with_the_span():
    item = {
        "id": "E-04",
        "question": "出版社はどこですか",
        "answer_chunk_id": "6",
        "answer_span": "出版社は集英社です",
    }
    retrieved = {"E-04": [RetrievedChunk(chunk_id="6", rank=1)]}
    corpus = {"6": "この作品の出版社は集英社です。連載中。"}
    items = build_generation_failure_items([item], retrieved, corpus, n=6)
    assert len(items) == 1
    gold = items[0]
    assert gold.gold_cause == "generation_failure"
    surfaced_ids = [chunk.chunk_id for chunk in gold.knowledge_answer.retrieved_chunks]
    assert "6" in surfaced_ids
    assert gold.knowledge_answer.answer == "記載がありません"
    assert gold.construction_evidence["verified_span_in_surfaced_gold_chunk"] is True


def test_generation_failure_skips_when_span_not_actually_in_the_surfaced_chunk_text():
    item = {
        "id": "E-05",
        "question": "出版社はどこですか",
        "answer_chunk_id": "6",
        "answer_span": "出版社は集英社です",
    }
    retrieved = {"E-05": [RetrievedChunk(chunk_id="6", rank=1)]}
    corpus = {"6": "この文章には答えが含まれていない"}
    assert build_generation_failure_items([item], retrieved, corpus, n=6) == []


def test_chunking_failure_requires_both_gold_chunks_surfaced():
    item = {
        "id": "M-01",
        "question": "所属と術式は",
        "answer_chunk_ids": ["21", "14"],
        "answer_spans": ["不義遊戯は東堂葵の術式", "東堂葵は京都校三年"],
    }
    retrieved = {
        "M-01": [RetrievedChunk(chunk_id="21", rank=1), RetrievedChunk(chunk_id="14", rank=2)]
    }
    corpus = {"21": "不義遊戯は東堂葵の術式。効果は入れ替え。", "14": "東堂葵は京都校三年。"}
    items = build_chunking_failure_items([item], retrieved, corpus, n=6)
    assert len(items) == 1
    gold = items[0]
    assert gold.gold_cause == "chunking_failure"
    assert gold.construction_evidence["chunk_ids"] == ["21", "14"]
    assert gold.knowledge_answer.answer == "不義遊戯は東堂葵の術式"


def test_chunking_failure_skips_when_only_one_gold_chunk_is_surfaced():
    item = {
        "id": "M-02",
        "question": "所属と術式は",
        "answer_chunk_ids": ["21", "14"],
        "answer_spans": ["span a", "span b"],
    }
    retrieved = {"M-02": [RetrievedChunk(chunk_id="21", rank=1)]}
    corpus = {"21": "span a", "14": "span b"}
    assert build_chunking_failure_items([item], retrieved, corpus, n=6) == []


def test_invalid_a_asserts_the_false_premise_sourced_only_fan():
    item = {
        "id": "F-01",
        "question": "芻霊呪法は誰の術式ですか",
        "false_premise": "芻霊呪法が東堂葵の術式であるという前提",
        "premise_error_type": "wrong_attribution",
        "contradicting_chunk_id": "20",
    }
    items = build_invalid_a_items([item], retrieved={}, n=6)
    assert len(items) == 1
    gold = items[0]
    assert gold.gold_cause == "invalid_A"
    assert gold.external_answer.evidence[0].source_type == "fan"
    assert "芻霊呪法が東堂葵の術式である" in str(gold.external_answer.answer)
    assert has_acceptable_external_evidence(gold.external_answer) is False
    assert gold.construction_evidence["a_notes_states_unconfirmed"] is True


def _gold(*, id_: str, gold_cause: str) -> GoldItem:
    return GoldItem(
        id=id_,
        gold_cause=gold_cause,  # type: ignore[arg-type]
        source_id=id_,
        question="質問",
        external_answer=AgentAnswer(
            role="external",
            answer="Aの回答",
            status="ok",
            evidence=[EvidenceSource(url="https://example.test", source_type="official")],
        ),
        knowledge_answer=AgentAnswer(role="knowledge", answer="記載がありません", status="released"),
        construction_rationale="test fixture",
    )


def test_score_item_marks_correct_and_incorrect_predictions_and_exercises_disposition():
    gold = _gold(id_="G-1", gold_cause="missing_knowledge")
    correct_judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="abstain",
        same_answer=False,
        failure_cause="missing_knowledge",
    )
    row = score_item(gold=gold, judgment=correct_judgment)
    assert row["cause_correct"] is True
    assert row["disposition"] == "add_candidate"

    wrong_judgment = FactCheckJudgment(
        external_status="pass",
        knowledge_status="abstain",
        same_answer=False,
        failure_cause="generation_failure",
    )
    wrong_row = score_item(gold=gold, judgment=wrong_judgment)
    assert wrong_row["cause_correct"] is False
    assert wrong_row["predicted_cause"] == "generation_failure"


def test_aggregate_scores_computes_accuracy_confusion_matrix_and_per_label_metrics():
    rows = [
        score_item(
            gold=_gold(id_="G-1", gold_cause="missing_knowledge"),
            judgment=FactCheckJudgment(
                external_status="pass",
                knowledge_status="abstain",
                failure_cause="missing_knowledge",
            ),
        ),
        score_item(
            gold=_gold(id_="G-2", gold_cause="retrieval_failure"),
            judgment=FactCheckJudgment(
                external_status="pass",
                knowledge_status="abstain",
                failure_cause="missing_knowledge",
            ),
        ),
    ]
    summary = aggregate_scores(rows)
    assert summary["total"] == 2
    assert summary["correct"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["confusion_matrix"]["missing_knowledge"] == {"missing_knowledge": 1}
    assert summary["confusion_matrix"]["retrieval_failure"] == {"missing_knowledge": 1}
    assert summary["by_label"]["missing_knowledge"]["support"] == 1
    assert summary["by_label"]["missing_knowledge"]["recall"] == 1.0
    assert summary["by_label"]["missing_knowledge"]["precision"] == 0.5
    assert summary["by_label"]["retrieval_failure"]["support"] == 1
    assert summary["by_label"]["retrieval_failure"]["recall"] == 0.0
    assert summary["by_label"]["retrieval_failure"]["precision"] is None
