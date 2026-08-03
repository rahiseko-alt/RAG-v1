"""Score the retrieval stage against ground-truth answer chunks, with no LLM in the loop.

Every question in the stratified set records which chunk holds its answer, so the one
question the previous evaluation could never answer objectively — *did retrieval return
the chunk that contains the answer?* — becomes a string comparison. That matters because
`retrieval_failure` had been decided by a judge reading someone else's summary of what
retrieval produced; here it is a fact about the retrieved id list.

Two things fall out of that:

- **Mechanical difficulty labels.** HotpotQA splits easy/medium/hard by whether models
  solved the item rather than by how hard it felt to the author. `retrieval_rank` gives
  the same thing for the retrieval stage: rank 1 is easy, unretrieved is hard, and the
  label is reproducible from the pinned corpus hash instead of asserted.
- **A control group.** If the easy stratum — questions built from single chunks — does
  not come back near the top, the problem is the retriever, not the questions. Without
  that baseline a hard-only set cannot tell a weak system from a hard question, which is
  what the 2026-08-02 30-question run ran into.

Unanswerable and false-premise questions have no answer chunk by construction and are
skipped for recall; what they measure (abstention, premise correction) needs a generator.

Usage:
    uv run python -m scripts.eval_set_retrieval_check \
        --questions data/eval/stratified-eval-set-v1.json --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.quality.store import WorkbenchStore
from src.quality.workbench import RevisionCorpusProber
from src.rag import engine_fingerprint, get_or_build_index

from scripts.coverage_loop_retrieve import retrieve_documents


def answer_chunk_ids(question: dict[str, Any]) -> list[str]:
    if question.get("answer_chunk_ids"):
        return [str(value) for value in question["answer_chunk_ids"]]
    if question.get("answer_chunk_id") is not None:
        return [str(question["answer_chunk_id"])]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.questions.read_text(encoding="utf-8"))
    questions = payload["questions"]

    store = WorkbenchStore()
    fingerprint = engine_fingerprint()
    revision = store.bootstrap_active_revision(engine_fingerprint=fingerprint)
    revision_id = str(revision["id"])
    source_path = Path(str(revision["source_path"]))
    prober = RevisionCorpusProber(source_path)
    corpus = {chunk.chunk_id: chunk.text for chunk in prober.corpus()}
    vectorstore, chunks_indexed = get_or_build_index(
        source_path,
        store.runtime_root / "revisions" / revision_id / "indexes" / fingerprint,
        collection_name=f"revision_{revision_id}",
    )

    rows: list[dict[str, Any]] = []
    for question in questions:
        text = str(question["question"]).strip()
        gold = answer_chunk_ids(question)
        documents = retrieve_documents(vectorstore, text)
        retrieved = [str(document.metadata.get("chunk_id")) for document, _score in documents]
        ranks = {
            chunk_id: retrieved.index(chunk_id) + 1
            for chunk_id in gold
            if chunk_id in retrieved
        }
        # The gold chunk still has to contain the recorded span, or the "ground truth" is
        # only an assertion by whoever wrote the question.
        spans = question.get("answer_spans") or (
            [question["answer_span"]] if question.get("answer_span") else []
        )
        span_ok = all(
            any(span in corpus.get(chunk_id, "") for chunk_id in gold) for span in spans
        )
        rows.append(
            {
                "id": question["id"],
                "stratum": question["stratum"],
                "gold_chunk_ids": gold,
                "retrieved_count": len(retrieved),
                "gold_ranks": ranks,
                "best_rank": min(ranks.values()) if ranks else None,
                "all_gold_retrieved": bool(gold) and len(ranks) == len(set(gold)),
                "span_verified": span_ok,
                # HotpotQA-style: the label comes from what happened, not from intent.
                "measured_difficulty": (
                    None
                    if not gold
                    else "easy"
                    if ranks and min(ranks.values()) <= 3
                    else "medium"
                    if ranks
                    else "hard"
                ),
            }
        )

    scored = [row for row in rows if row["gold_chunk_ids"]]
    summary = {
        "revision_id": revision_id,
        "source_sha256": revision["source_sha256"],
        "engine_fingerprint": fingerprint,
        "chunks_indexed": chunks_indexed,
        "questions": len(rows),
        "with_gold_chunks": len(scored),
        "span_verified": sum(row["span_verified"] for row in scored),
        "recall_all_gold": sum(row["all_gold_retrieved"] for row in scored),
        "difficulty_counts": {
            level: sum(row["measured_difficulty"] == level for row in scored)
            for level in ("easy", "medium", "hard")
        },
        "by_stratum": {},
    }
    for row in scored:
        bucket = summary["by_stratum"].setdefault(
            row["stratum"], {"n": 0, "recall": 0, "easy": 0, "medium": 0, "hard": 0}
        )
        bucket["n"] += 1
        bucket["recall"] += int(row["all_gold_retrieved"])
        bucket[row["measured_difficulty"]] += 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "retrieval-check.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
