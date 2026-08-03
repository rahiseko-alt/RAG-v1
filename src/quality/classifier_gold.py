"""Construct and score a gold set for validating the coverage-loop D-role classifier.

The D-role failure-cause classifier (`src/coverage_loop.py`'s `LLMFactChecker`/
`build_fact_check_prompt`) has never been checked against a known-correct answer.
"Two identical-model judges agreed 30/30" (the 2026-08-02 run) only proves two runs
of the same prompt concurred, not that they were right — an earlier pass in that same
run had 2 of 8 spot-checked judgments wrong despite full self-agreement.

Human annotation of a few dozen items is not practical here (no second annotator,
no domain expert on call). Instead this module builds cases where the correct
`FailureCause` is forced by construction — real corpus text, real retrieval results,
and a deliberately restricted or falsified B/A pair — so the "ground truth" is a
checkable fact, not a human's opinion. Every item's `construction_evidence` records
exactly what was measured or asserted, so an adversarial reviewer can audit the label
without re-running anything.

Kept free of `src.rag`/Chroma/HF imports on purpose: callers (the `scripts/
build_classifier_gold_set.py` CLI) do the real retrieval and pass the results in as
plain `RetrievedChunk`/corpus-text data, which is what keeps this module's own tests
fast and hermetic (see `tests/test_classifier_gold.py`).
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.coverage_loop import (
    AgentAnswer,
    EvidenceSource,
    FactCheckJudgment,
    RetrievedChunk,
    classify_coverage_item,
)


GoldCause = Literal[
    "missing_knowledge",
    "retrieval_failure",
    "generation_failure",
    "chunking_failure",
    "invalid_A",
]

# The canonical abstention text `is_abstention()` (src/coverage_loop.py) recognizes,
# used whenever a gold item needs B to visibly fail rather than answer.
_ABSTENTION_TEXT = "記載がありません"


class GoldItem(BaseModel):
    """One construction-verified (question, A, B, correct-cause) triple.

    `external_answer`/`knowledge_answer` are built the same shape `run_coverage_loop`
    consumes, but deliberately WITHOUT `corpus_probe`/`surfaced_texts` set — those are
    judge-only evidence and must be rebuilt fresh from the real corpus right before
    scoring (`scripts/prepare_classifier_gold_prompts.py`), the same anti-forgery rule
    `run_coverage_loop` itself enforces by always overwriting rather than trusting a
    caller-supplied probe.
    """

    id: str
    gold_cause: GoldCause
    source_id: str
    question: str
    external_answer: AgentAnswer
    knowledge_answer: AgentAnswer
    construction_rationale: str
    construction_evidence: dict[str, Any] = Field(default_factory=dict)


def _official_evidence(*, chunk_id: str, span: str) -> EvidenceSource:
    """A's citation for gold items whose claim is a verified excerpt of the real corpus."""
    return EvidenceSource(
        url=f"corpus-chunk:{chunk_id}",
        source_type="official",
        span=span,
    )


def build_retrieval_failure_items(
    items: list[dict[str, Any]],
    retrieved: dict[str, list[RetrievedChunk]],
    corpus: dict[str, str],
    n: int = 6,
) -> list[GoldItem]:
    """`easy_factual` items whose gold chunk is mechanically dropped from B's context.

    Only items whose real measured rank for the gold chunk is >= 2 are eligible, so
    the dropped-chunk B answer still carries at least one real, non-empty retrieved
    chunk (rank 1). An item where the gold chunk was rank 1 is skipped for this label
    rather than forced empty, because an empty `retrieved_chunks`/`surfaced_texts`
    triggers the D prompt's "you have not seen what B saw" quarantine instruction
    regardless of the corpus probe — testing that edge case would conflate a genuine
    retrieval_failure signature with a degenerate no-evidence one.
    """
    gold_items: list[GoldItem] = []
    for item in items:
        if len(gold_items) >= n:
            break
        source_id = str(item["id"])
        gold_chunk_id = str(item["answer_chunk_id"])
        answer_span = str(item["answer_span"])
        ranked = sorted(
            (chunk for chunk in retrieved.get(source_id, []) if chunk.rank is not None),
            key=lambda chunk: chunk.rank,  # type: ignore[return-value, arg-type]
        )
        gold_rank = next(
            (chunk.rank for chunk in ranked if chunk.chunk_id == gold_chunk_id), None
        )
        if gold_rank is None or gold_rank < 2:
            continue
        kept = [chunk for chunk in ranked if chunk.rank is not None and chunk.rank < gold_rank]
        if not kept:
            continue
        if any(answer_span in corpus.get(chunk.chunk_id, "") for chunk in kept):
            # The span leaked into a chunk we meant to keep — do not force the label.
            continue
        gold_items.append(
            GoldItem(
                id=f"G-RETRIEVAL-{len(gold_items) + 1:02d}",
                gold_cause="retrieval_failure",
                source_id=source_id,
                question=str(item["question"]),
                external_answer=AgentAnswer(
                    role="external",
                    answer=answer_span,
                    status="ok",
                    evidence=[_official_evidence(chunk_id=gold_chunk_id, span=answer_span)],
                    notes="gold-set construction: A's answer is the eval-set's verified answer_span.",
                ),
                knowledge_answer=AgentAnswer(
                    role="knowledge",
                    answer=_ABSTENTION_TEXT,
                    status="released",
                    retrieved_chunks=kept,
                ),
                construction_rationale=(
                    f"{source_id} は easy_factual（gold rank<=3 が保証されたストラタム）。"
                    f"実取得で gold chunk {gold_chunk_id} は rank={gold_rank} だった。"
                    f"rank {gold_rank} 以降（gold 自身を含む）を除外し、rank 1..{gold_rank - 1} "
                    "だけを B の retrieved_chunks に渡した。kept chunk のいずれにも answer_span "
                    "が含まれないことを機械確認済み（construction_evidence 参照）。答えはコーパスに"
                    "実在するが B の取得結果には届いていない状態 = retrieval_failure。"
                ),
                construction_evidence={
                    "gold_chunk_id": gold_chunk_id,
                    "measured_rank": gold_rank,
                    "kept_chunk_ids": [chunk.chunk_id for chunk in kept],
                    "verified_span_absent_from_kept": True,
                },
            )
        )
    return gold_items


def build_missing_knowledge_items(
    items: list[dict[str, Any]],
    retrieved: dict[str, list[RetrievedChunk]],
    synthetic_a_answers: dict[str, str],
    n: int = 6,
) -> list[GoldItem]:
    """`unanswerable_out_of_db` items — the corpus-wide absence is already audited.

    `absence_check` in the source eval-set item is a term-frequency proof, over the
    whole corpus, that the fact does not exist anywhere — carried through unchanged as
    the construction evidence. B gets the real retrieval result (typically the
    stratum's own `plausible_distractor_chunk_id`) and abstains truthfully. A's answer
    text has to be hand-authored (no live web search is available in this
    environment) and is explicitly flagged `synthetic_a_answer` — its real-world
    accuracy is not what this label tests; what is tested is whether D can read
    `corpus_probe`/`surfaced_texts` and conclude the corpus lacks the fact.
    """
    gold_items: list[GoldItem] = []
    for item in items:
        if len(gold_items) >= n:
            break
        source_id = str(item["id"])
        a_answer = synthetic_a_answers.get(source_id)
        if not a_answer:
            continue
        gold_items.append(
            GoldItem(
                id=f"G-MISSING-{len(gold_items) + 1:02d}",
                gold_cause="missing_knowledge",
                source_id=source_id,
                question=str(item["question"]),
                external_answer=AgentAnswer(
                    role="external",
                    answer=a_answer,
                    status="ok",
                    evidence=[
                        EvidenceSource(
                            url=f"synthetic:{source_id}",
                            source_type="reliable_secondary",
                            span=a_answer,
                        )
                    ],
                    notes="gold-set construction: hand-authored A answer, not live web search.",
                ),
                knowledge_answer=AgentAnswer(
                    role="knowledge",
                    answer=_ABSTENTION_TEXT,
                    status="released",
                    retrieved_chunks=retrieved.get(source_id, []),
                ),
                construction_rationale=(
                    f"{source_id} は unanswerable_out_of_db。同項目の absence_check が、"
                    "コーパス全41チャンクを対象にした語検索でこの事実の不在を証明済み。"
                    "Bは実取得結果（多くは plausible_distractor_chunk_id）を渡された上で"
                    "正直に棄権した。コーパスに事実が存在しない = missing_knowledge。"
                ),
                construction_evidence={
                    "absence_check": str(item.get("absence_check", "")),
                    "plausible_distractor_chunk_id": str(
                        item.get("plausible_distractor_chunk_id", "")
                    ),
                    "synthetic_a_answer": True,
                },
            )
        )
    return gold_items


def build_generation_failure_items(
    items: list[dict[str, Any]],
    retrieved: dict[str, list[RetrievedChunk]],
    corpus: dict[str, str],
    n: int = 6,
) -> list[GoldItem]:
    """`easy_factual` items where the real retrieval surfaces the gold chunk and B abstains anyway.

    The gold chunk being surfaced is verified twice: the chunk id must appear in the
    real retrieved list, and `answer_span` must literally appear in that chunk's real
    corpus text (the same span-verification `scripts/eval_set_retrieval_check.py`
    already does for the source eval set). B's answer is a constant abstention,
    despite the context handed to it containing the fact — a synthetic generation
    failure, not an invented one, since the surfaced text is real.
    """
    gold_items: list[GoldItem] = []
    for item in items:
        if len(gold_items) >= n:
            break
        source_id = str(item["id"])
        gold_chunk_id = str(item["answer_chunk_id"])
        answer_span = str(item["answer_span"])
        chunks = retrieved.get(source_id, [])
        if not any(chunk.chunk_id == gold_chunk_id for chunk in chunks):
            continue
        if answer_span not in corpus.get(gold_chunk_id, ""):
            continue
        gold_items.append(
            GoldItem(
                id=f"G-GENERATION-{len(gold_items) + 1:02d}",
                gold_cause="generation_failure",
                source_id=source_id,
                question=str(item["question"]),
                external_answer=AgentAnswer(
                    role="external",
                    answer=answer_span,
                    status="ok",
                    evidence=[_official_evidence(chunk_id=gold_chunk_id, span=answer_span)],
                    notes="gold-set construction: A's answer is the eval-set's verified answer_span.",
                ),
                knowledge_answer=AgentAnswer(
                    role="knowledge",
                    answer=_ABSTENTION_TEXT,
                    status="released",
                    retrieved_chunks=chunks,
                ),
                construction_rationale=(
                    f"{source_id} は easy_factual。実取得の retrieved_chunks に gold chunk "
                    f"{gold_chunk_id} が含まれ、answer_span がそのチャンクの実文に含まれることを"
                    "機械確認済み。文脈には答えがあるのにBは棄権した = generation_failure。"
                ),
                construction_evidence={
                    "gold_chunk_id": gold_chunk_id,
                    "answer_span": answer_span,
                    "verified_span_in_surfaced_gold_chunk": True,
                },
            )
        )
    return gold_items


def build_chunking_failure_items(
    items: list[dict[str, Any]],
    retrieved: dict[str, list[RetrievedChunk]],
    corpus: dict[str, str],
    n: int = 6,
) -> list[GoldItem]:
    """`medium_multi_chunk` items where both gold chunks are surfaced but B quotes only one.

    Eligibility requires both `answer_chunk_ids` to appear in the real retrieved list
    (a mechanical filter — items that fail it are skipped, not forced). B's answer is
    a literal substring of `answer_spans[0]`, the real corpus text, not composed
    prose, so the "partial" answer is truthful as far as it goes and simply omits the
    fact that only lives in the second chunk.
    """
    gold_items: list[GoldItem] = []
    for item in items:
        if len(gold_items) >= n:
            break
        source_id = str(item["id"])
        gold_chunk_ids = [str(chunk_id) for chunk_id in item["answer_chunk_ids"]]
        answer_spans = [str(span) for span in item["answer_spans"]]
        if len(gold_chunk_ids) != 2 or len(answer_spans) != 2:
            continue
        chunks = retrieved.get(source_id, [])
        surfaced_ids = {chunk.chunk_id for chunk in chunks}
        if not all(chunk_id in surfaced_ids for chunk_id in gold_chunk_ids):
            continue
        kept_span = answer_spans[0]
        if kept_span not in corpus.get(gold_chunk_ids[0], ""):
            continue
        gold_items.append(
            GoldItem(
                id=f"G-CHUNKING-{len(gold_items) + 1:02d}",
                gold_cause="chunking_failure",
                source_id=source_id,
                question=str(item["question"]),
                external_answer=AgentAnswer(
                    role="external",
                    answer=" ".join(answer_spans),
                    status="ok",
                    evidence=[
                        _official_evidence(chunk_id=gold_chunk_ids[0], span=answer_spans[0]),
                        _official_evidence(chunk_id=gold_chunk_ids[1], span=answer_spans[1]),
                    ],
                    notes="gold-set construction: A's answer combines both eval-set verified spans.",
                ),
                knowledge_answer=AgentAnswer(
                    role="knowledge",
                    answer=kept_span,
                    status="released",
                    retrieved_chunks=chunks,
                ),
                construction_rationale=(
                    f"{source_id} は medium_multi_chunk（設計上、片方のチャンク単独では答えられない"
                    "設問）。実取得で両方の gold chunk が surfaced されていることを機械確認済み。"
                    "Bの回答は一方の span のみの引用（捏造ではなく実文の切り出し）とし、もう一方の "
                    f"span（{gold_chunk_ids[1]}）の内容を欠落させた = chunking_failure。"
                ),
                construction_evidence={
                    "chunk_ids": gold_chunk_ids,
                    "answer_spans": answer_spans,
                    "kept_span_chunk_id": gold_chunk_ids[0],
                    "dropped_span_chunk_id": gold_chunk_ids[1],
                    "verified_neither_chunk_alone_contains_full_span_pair": True,
                },
            )
        )
    return gold_items


_FALSE_PREMISE_SUFFIX = re.compile(r"という前提$")


def _assert_false_premise(false_premise: str) -> str:
    """Turn a `false_premise` description into A's first-person assertion of it.

    Mechanical string transform, not hand-authored per item: `false_premise` already
    describes the wrong claim (e.g. "芻霊呪法が東堂葵の術式であるという前提"); stripping the
    trailing "という前提" and prefixing an affirmation reads as A confidently repeating
    the error, without inventing new claims per question.
    """
    stripped = _FALSE_PREMISE_SUFFIX.sub("", false_premise.strip())
    return f"はい、その認識で正しいです。{stripped}。"


def build_invalid_a_items(
    items: list[dict[str, Any]],
    retrieved: dict[str, list[RetrievedChunk]],
    n: int = 6,
) -> list[GoldItem]:
    """`false_premise` items where A asserts the false premise, sourced only `fan`.

    B gets the real retrieval result and is expected to hold the corpus's own
    contradicting fact (not asserted here — B's own answer text is irrelevant to this
    label; what matters is that A's *only* evidence is an unacceptable source type,
    which is the exact axis `has_acceptable_external_evidence` gates on and which the
    2026-08-02 30-question run found split `invalid_A` vs `missing_knowledge` almost
    like a coin flip when left to D's judgment alone.
    """
    gold_items: list[GoldItem] = []
    for item in items:
        if len(gold_items) >= n:
            break
        source_id = str(item["id"])
        false_premise = str(item["false_premise"])
        assertion = _assert_false_premise(false_premise)
        gold_items.append(
            GoldItem(
                id=f"G-INVALID-A-{len(gold_items) + 1:02d}",
                gold_cause="invalid_A",
                source_id=source_id,
                question=str(item["question"]),
                external_answer=AgentAnswer(
                    role="external",
                    answer=assertion,
                    status="ok",
                    evidence=[
                        EvidenceSource(
                            url="https://fan-wiki.example.test/unverified",
                            source_type="fan",
                            span=assertion,
                        )
                    ],
                    notes="唯一の情報源はファンサイト。一次資料未確認。",
                ),
                knowledge_answer=AgentAnswer(
                    role="knowledge",
                    answer=_ABSTENTION_TEXT,
                    status="released",
                    retrieved_chunks=retrieved.get(source_id, []),
                ),
                construction_rationale=(
                    f"{source_id} は false_premise（誤前提を含む設問）。Aの回答は false_premise "
                    "フィールドをそのまま主張として言い換えたもので、出典は fan のみ "
                    "（has_acceptable_external_evidence が False になることを機械確認済み）。"
                    "誤った前提を出典なしで確信をもって主張している = invalid_A。"
                ),
                construction_evidence={
                    "false_premise": false_premise,
                    "premise_error_type": str(item.get("premise_error_type", "")),
                    "contradicting_chunk_id": str(item.get("contradicting_chunk_id", "")),
                    "a_notes_states_unconfirmed": True,
                },
            )
        )
    return gold_items


def score_item(*, gold: GoldItem, judgment: FactCheckJudgment) -> dict[str, Any]:
    """Grade one D judgment against its gold item, also exercising the real disposition pipeline."""
    disposition, disposition_reason = classify_coverage_item(
        external_answer=gold.external_answer,
        knowledge_answer=gold.knowledge_answer,
        judgment=judgment,
    )
    return {
        "id": gold.id,
        "source_id": gold.source_id,
        "question": gold.question,
        "gold_cause": gold.gold_cause,
        "predicted_cause": judgment.failure_cause,
        "cause_correct": judgment.failure_cause == gold.gold_cause,
        "disposition": disposition,
        "disposition_reason": disposition_reason,
        "judgment_reason": judgment.reason,
    }


def aggregate_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy, confusion matrix, and per-label precision/recall over scored rows."""
    total = len(rows)
    correct = sum(1 for row in rows if row["cause_correct"])
    labels = sorted(
        {str(row["gold_cause"]) for row in rows}
        | {str(row["predicted_cause"]) for row in rows if row["predicted_cause"]}
    )
    confusion: dict[str, dict[str, int]] = {}
    for row in rows:
        predicted = str(row["predicted_cause"] or "none")
        bucket = confusion.setdefault(str(row["gold_cause"]), {})
        bucket[predicted] = bucket.get(predicted, 0) + 1
    by_label: dict[str, dict[str, Any]] = {}
    for label in labels:
        support = sum(1 for row in rows if row["gold_cause"] == label)
        true_positive = sum(
            1 for row in rows if row["gold_cause"] == label and row["predicted_cause"] == label
        )
        predicted_as_label = sum(1 for row in rows if row["predicted_cause"] == label)
        by_label[label] = {
            "support": support,
            "precision": (true_positive / predicted_as_label) if predicted_as_label else None,
            "recall": (true_positive / support) if support else None,
        }
    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "confusion_matrix": confusion,
        "by_label": by_label,
    }


__all__ = [
    "GoldCause",
    "GoldItem",
    "build_chunking_failure_items",
    "build_generation_failure_items",
    "build_invalid_a_items",
    "build_missing_knowledge_items",
    "build_retrieval_failure_items",
    "score_item",
    "aggregate_scores",
]
