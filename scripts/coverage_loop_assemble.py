"""Assemble injected A/B/D answers into one coverage-loop run and aggregate it.

Design-doc step 6 asks for the fixed 30-question set to be pushed through A/B/D and
turned into a per-question-type weakness table. This script is the last stage: it
takes the three roles' outputs, injects them through the *product* code path
(`QualityWorkbench.run_revision_coverage_loop` with `allow_llm_agents=false` and
`run_knowledge_answerer=false`), and writes the run plus its aggregation.

Why injection rather than letting the API call the agents: `memory.md` records the
standing decision that the product API must not run A/C/D as LLM calls on every
request — A/B/D are injectable from subagents, humans, external tools, or prior
logs, and the API's job is candidacy, ledgering, and before/after comparison. This
run exercises exactly that path.

What each role's data is, and what it is not:

- A comes from web-searching subagents. `evidence` holds only URLs a subagent
  actually fetched; unsourced answers keep an empty evidence list rather than a
  reconstructed-from-memory citation, so D can legitimately call them `invalid_A`.
- B is half real and half substituted. Retrieval ran for real (see
  `coverage_loop_retrieve.py`), so `sources` / `retrieved_chunks` are genuine
  runtime output. Only the generation step was performed by a subagent standing in
  for the chat model, because no API key is configured. B's `status` is therefore
  `ok`, not `released`: the online quality gate needs an LLM verifier and did not
  run, and claiming a gate verdict that was never computed would be fabricated
  evidence. The deterministic half of that gate *can* run without a model, so it is
  computed here and recorded — as an observation, not as a release decision.
- D comes from judging subagents that saw A, B, and B's retrieval evidence, but not
  the corpus. That asymmetry is deliberate: see `RetrievedChunk`'s docstring for why
  absence of a fact in the surfaced chunks cannot single out `missing_knowledge`.

Usage:
    uv run python -m scripts.coverage_loop_assemble --run-dir <dir> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.quality.store import WorkbenchStore
from src.quality.verifier import _deterministic_checks
from src.quality.workbench import QualityWorkbench

# Recorded on every injected B-answer so a later reader of the ledger cannot mistake
# this for a full product run. Kept short because it is persisted per item.
B_SUBSTITUTION_NOTE = (
    "retrieval=real (e5+chroma, product retrieve stage); "
    "generation=substituted subagent (no LLM key); "
    "online quality gate not evaluated (needs LLM verifier)"
)


def normalize(question: str) -> str:
    """Match `run_coverage_loop`'s own key normalization for provided answers."""
    return " ".join(question.split())


def load_merged(run_dir: Path, pattern: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in sorted(run_dir.glob(pattern)):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/eval/coverage-loop-30-questions.json"),
    )
    args = parser.parse_args()

    question_set = json.loads(args.questions.read_text(encoding="utf-8"))
    retrieval = json.loads((args.run_dir / "retrieval-sources.json").read_text(encoding="utf-8"))
    retrieval_by_id = {item["id"]: item for item in retrieval["items"]}
    a_answers = load_merged(args.run_dir, "a-out-*.json")
    b_answers = load_merged(args.run_dir, "b-out-*.json")
    d_judgments = load_merged(args.run_dir, "d-out-*.json")

    missing = [
        question["id"]
        for question in question_set["questions"]
        if question["id"] not in a_answers
        or question["id"] not in b_answers
        or question["id"] not in d_judgments
    ]
    if missing:
        raise SystemExit(f"missing A/B/D output for: {missing}")

    questions: list[str] = []
    external_answers: dict[str, Any] = {}
    knowledge_answers: dict[str, Any] = {}
    fact_checks: dict[str, Any] = {}
    gate_checks: dict[str, Any] = {}

    for entry in question_set["questions"]:
        question_id = entry["id"]
        question = normalize(entry["question"])
        questions.append(question)

        a_raw = a_answers[question_id]
        external_answers[question] = {
            "answer": a_raw.get("answer"),
            "status": a_raw.get("status", "ok"),
            "confidence": a_raw.get("confidence"),
            "evidence": a_raw.get("evidence", []),
            "notes": a_raw.get("notes", ""),
            "model": "subagent_external_answerer",
        }

        retrieved = retrieval_by_id[question_id]
        b_raw = b_answers[question_id]
        knowledge_answers[question] = {
            "answer": b_raw.get("answer"),
            "status": "ok",
            "sources": retrieved["sources"],
            "retrieved_chunks": retrieved["retrieved_chunks"],
            "notes": f"{B_SUBSTITUTION_NOTE}; {b_raw.get('notes', '')}".strip(),
            "model": "subagent_generation_node",
        }
        # The deterministic half of the online gate (citation presence, valid ranks,
        # per-sentence coverage) needs no model, so it is a real signal even though
        # the semantic half could not run. Recorded separately from `status` so it is
        # never mistaken for a release decision.
        checks = _deterministic_checks(str(b_raw.get("answer") or ""), retrieved["sources"])
        gate_checks[question_id] = {
            "deterministic_pass": all(check["passed"] for check in checks),
            "failed_checks": [check["id"] for check in checks if not check["passed"]],
        }

        fact_checks[question] = d_judgments[question_id]

    store = WorkbenchStore()
    workbench = QualityWorkbench(store)
    revision = store.bootstrap_active_revision(engine_fingerprint=workbench.fingerprint())
    result = workbench.run_revision_coverage_loop(
        str(revision["id"]),
        focus=question_set.get("description"),
        questions=questions,
        external_answers=external_answers,
        knowledge_answers=knowledge_answers,
        fact_checks=fact_checks,
        rounds=1,
        max_questions_per_round=len(questions),
        allow_llm_agents=False,
        run_knowledge_answerer=False,
        persist=True,
    )

    by_question = {normalize(item["question"]): item for item in result["items"]}
    rows = []
    for entry in question_set["questions"]:
        item = by_question[normalize(entry["question"])]
        rows.append(
            {
                "id": entry["id"],
                "role": entry["role"],
                "prior_10q_gap": entry["prior_10q_gap"],
                "disposition": item["disposition"],
                "failure_cause": item["fact_check"].get("failure_cause"),
                "external_status": item["fact_check"].get("external_status"),
                "knowledge_status": item["fact_check"].get("knowledge_status"),
                "same_answer": item["fact_check"].get("same_answer"),
                "a_evidence_count": len(external_answers[normalize(entry["question"])]["evidence"]),
                "a_source_types": sorted(
                    {
                        str(source.get("source_type") or "unknown")
                        for source in external_answers[normalize(entry["question"])]["evidence"]
                    }
                ),
                "b_deterministic_pass": gate_checks[entry["id"]]["deterministic_pass"],
                "b_failed_checks": gate_checks[entry["id"]]["failed_checks"],
                "candidate_id": item.get("candidate_id"),
                "ledger_status": item.get("ledger_status"),
                "missing_knowledge": item["fact_check"].get("missing_knowledge", ""),
                "reason": item["fact_check"].get("reason", ""),
            }
        )

    def tally(keys: list[str], predicate: Any = None) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = {}
        for row in rows:
            if predicate is not None and not predicate(row):
                continue
            bucket = table.setdefault(str(row[keys[0]]), {})
            value = str(row[keys[1]])
            bucket[value] = bucket.get(value, 0) + 1
        return table

    aggregation = {
        "total_questions": result["total_questions"],
        "add_candidates": result["add_candidates"],
        "disposition_counts": result["disposition_counts"],
        "cause_counts": result["cause_counts"],
        "disposition_by_role": tally(["role", "disposition"]),
        "cause_by_role": tally(["role", "failure_cause"]),
        # New findings must be countable apart from ground the prior 10-question loop
        # already covered, otherwise the same gap is banked twice.
        "disposition_by_role_new_only": tally(
            ["role", "disposition"], lambda row: not row["prior_10q_gap"]
        ),
        "prior_gap_dispositions": tally(
            ["prior_10q_gap", "disposition"]
        ),
        "a_source_type_counts": dict(
            Counter(
                source_type
                for row in rows
                for source_type in (row["a_source_types"] or ["none"])
            )
        ),
        "b_deterministic_pass_counts": dict(
            Counter("pass" if row["b_deterministic_pass"] else "fail" for row in rows)
        ),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "coverage-loop-run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "coverage-loop-summary.json").write_text(
        json.dumps({"aggregation": aggregation, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(aggregation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
