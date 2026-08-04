"""Build a construction-verified gold set for the coverage-loop D-role classifier.

Phase 1 of the delivery plan (`docs/session-reports/`, `memory.md` [importance:H]
2026-08-02 "判定器は一度も人手検証されていない"): before any weakness-classification
number is trusted for a decision, the classifier that produces it needs to be checked
against a KNOWN-correct answer, not against itself. Two human annotators are not
available in this environment, so each item's correct `FailureCause` is instead
forced by construction — real retrieval results and real corpus text, deliberately
sliced or paired with a synthetic A/B answer — via `src/quality/classifier_gold.py`.

This script does the one part that module deliberately stays free of: talking to the
real e5+Chroma index (same bootstrap `scripts/coverage_loop_retrieve.py` uses) to get
real `RetrievedChunk` results and real corpus chunk text, then hands them to the
`build_*_items` functions.

The only hand-authored content in the whole gold set lives in `SYNTHETIC_A_ANSWERS`
below (used only for the `missing_knowledge` label — no live web search is available
in this environment to source A's answer for questions the corpus cannot answer).
Every other label's A/B content is either a verbatim excerpt of the real corpus or a
mechanical transform of one (see `src/quality/classifier_gold.py` docstrings).

Usage:
    uv run python -m scripts.build_classifier_gold_set \
        --questions data/eval/stratified-eval-set-v1.json \
        --out data/eval/classifier-gold-set-v1.json \
        --per-label 6

Run twice on an unchanged corpus and the output is byte-identical (reproducibility
completion condition, Phase 1 plan) — nothing here reads wall-clock time or randomness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.quality.classifier_gold import (
    GoldItem,
    build_chunking_failure_items,
    build_generation_failure_items,
    build_invalid_a_items,
    build_missing_knowledge_items,
    build_retrieval_failure_items,
)
from src.quality.store import WorkbenchStore
from src.quality.workbench import (
    QualityWorkbench,
    RevisionCorpusProber,
    retrieved_chunks_from_sources,
)
from src.coverage_loop import RetrievedChunk
from src.rag import engine_fingerprint, get_or_build_index

from scripts.coverage_loop_retrieve import retrieve_documents


# The one hand-authored piece of this gold set (see module docstring). Each answer is
# a short, plausibly-sourced claim about the JJK franchise from outside the demo
# corpus; its real-world accuracy is not what `missing_knowledge` tests — see
# `build_missing_knowledge_items`'s docstring for why that does not weaken the label.
SYNTHETIC_A_ANSWERS: dict[str, str] = {
    "U-01": "五条悟の誕生日は12月7日とされています。",
    "U-05": "パンダは核を3つ持つとされています。",
    "U-09": "魔虚羅は同じ攻撃を8回受けると適応が完了するとされています。",
    "U-10": "TVアニメ『呪術廻戦』第1期の監督は朴性厚（パク・ソンフ）氏が務めたとされています。",
    "U-11": "劇場版『呪術廻戦 0』の興行収入は日本国内で138億円超と報じられています。",
    "U-12": "単行本第1巻の発売日は2018年3月2日とされています。",
}


def _retrieve_for(vectorstore: Any, questions: list[dict[str, Any]]) -> dict[str, list[RetrievedChunk]]:
    retrieved: dict[str, list[RetrievedChunk]] = {}
    for question in questions:
        source_id = str(question["id"])
        documents = retrieve_documents(vectorstore, str(question["question"]).strip())
        _private, public_sources = QualityWorkbench._evidence(documents)
        retrieved[source_id] = retrieved_chunks_from_sources(public_sources)
    return retrieved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--per-label", type=int, default=6)
    args = parser.parse_args()

    payload = json.loads(args.questions.read_text(encoding="utf-8"))
    questions = payload["questions"]
    by_stratum: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        by_stratum.setdefault(str(question["stratum"]), []).append(question)

    easy = by_stratum.get("easy_factual", [])
    # retrieval_failure eligibility is the strict one (rank >= 2, no literal-or-
    # paraphrase leak into any kept chunk — see `build_retrieval_failure_items`), so
    # it gets the larger share of the easy_factual pool. generation_failure's bar is
    # much looser (gold chunk surfaced + its span present verbatim) and reliably
    # clears its target from a small pool, confirmed by the 2026-08-03 run.
    generation_split = max(0, len(easy) - 6)
    retrieval_failure_candidates = easy[:generation_split]
    generation_failure_candidates = easy[generation_split:]
    medium = by_stratum.get("medium_multi_chunk", [])
    unanswerable = by_stratum.get("unanswerable_out_of_db", [])
    false_premise = by_stratum.get("false_premise", [])

    store = WorkbenchStore()
    fingerprint = engine_fingerprint()
    revision = store.bootstrap_active_revision(engine_fingerprint=fingerprint)
    revision_id = str(revision["id"])
    source_path = Path(str(revision["source_path"]))
    persist_dir = store.runtime_root / "revisions" / revision_id / "indexes" / fingerprint
    vectorstore, chunks_indexed = get_or_build_index(
        source_path, persist_dir, collection_name=f"revision_{revision_id}"
    )
    prober = RevisionCorpusProber(source_path)
    corpus = {chunk.chunk_id: chunk.text for chunk in prober.corpus()}

    needed = retrieval_failure_candidates + generation_failure_candidates + medium + unanswerable + false_premise
    retrieved = _retrieve_for(vectorstore, needed)

    gold_items: list[GoldItem] = [
        *build_retrieval_failure_items(
            retrieval_failure_candidates, retrieved, corpus, n=args.per_label
        ),
        *build_missing_knowledge_items(
            unanswerable, retrieved, synthetic_a_answers=SYNTHETIC_A_ANSWERS, n=args.per_label
        ),
        *build_generation_failure_items(
            generation_failure_candidates, retrieved, corpus, n=args.per_label
        ),
        *build_chunking_failure_items(medium, retrieved, corpus, n=args.per_label),
        *build_invalid_a_items(false_premise, retrieved, n=args.per_label),
    ]

    shortfalls = {}
    for cause in ("retrieval_failure", "missing_knowledge", "generation_failure", "chunking_failure", "invalid_A"):
        count = sum(1 for item in gold_items if item.gold_cause == cause)
        if count < args.per_label:
            shortfalls[cause] = count

    out_payload: dict[str, Any] = {
        "meta": {
            "revision_id": revision_id,
            "source_sha256": revision["source_sha256"],
            "engine_fingerprint": fingerprint,
            "chunks_indexed": chunks_indexed,
            "eval_set": str(args.questions),
            "per_label": args.per_label,
            "generated_by": "scripts.build_classifier_gold_set",
            "shortfalls": shortfalls,
        },
        "items": [item.model_dump() for item in gold_items],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(out_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {**out_payload["meta"], "total_items": len(gold_items)}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
