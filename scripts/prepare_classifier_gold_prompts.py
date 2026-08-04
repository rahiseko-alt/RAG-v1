"""Build the exact D-role prompt for each classifier gold item, for a subagent to judge.

No LLM API key is available, so — exactly as the 2026-08-02 30-question run did for
the production coverage loop — a subagent stands in for D. What makes this a fair
test of the *classifier*, not of the subagent's general reasoning, is that each
subagent must see byte-identical input to what `LLMFactChecker.check` would send a
real model: this script calls `build_fact_check_prompt` directly (the same function
`LLMFactChecker.check` now calls, see `src/coverage_loop.py`), after rebuilding
`corpus_probe`/`surfaced_texts` fresh from the real corpus — gold items are stored
without those two fields on purpose (see `GoldItem` docstring), matching the
anti-forgery rule `run_coverage_loop` itself enforces on every injected loop.

Usage:
    uv run python -m scripts.prepare_classifier_gold_prompts \
        --gold-set data/eval/classifier-gold-set-v1.json \
        --out-dir <run-dir>

Writes `<run-dir>/d-prompts.json` = `{gold_item_id: prompt_dict}` (not committed — it
duplicates corpus text, same reason `retrieval-context.json` isn't committed). A D-role
subagent reads one or more prompts and writes `d-out-<id>.json` =
`{"<id>": {"external_status": ..., "knowledge_status": ..., "same_answer": ...,
"failure_cause": ..., "missing_knowledge": ..., "reason": ...}}`, the same shape
`coverage_loop_assemble.py` already expects and glob-merges from D subagents.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.coverage_loop import AgentAnswer, build_fact_check_prompt
from src.quality.store import WorkbenchStore
from src.quality.workbench import RevisionCorpusProber


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-set", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.gold_set.read_text(encoding="utf-8"))
    items = payload["items"]

    store = WorkbenchStore()
    revision = store.bootstrap_active_revision()
    prober = RevisionCorpusProber(Path(str(revision["source_path"])))

    prompts: dict[str, Any] = {}
    for item in items:
        gold_id = str(item["id"])
        question = str(item["question"])
        external_answer = AgentAnswer.model_validate(item["external_answer"])
        knowledge_answer = AgentAnswer.model_validate(item["knowledge_answer"])
        probe = prober.probe(
            question=question, external_answer=external_answer, knowledge_answer=knowledge_answer
        )
        surfaced_texts = prober.surfaced_texts(knowledge_answer=knowledge_answer)
        knowledge_with_evidence = knowledge_answer.model_copy(
            update={"corpus_probe": probe, "surfaced_texts": surfaced_texts}
        )
        prompts[gold_id] = build_fact_check_prompt(
            question=question,
            external_answer=external_answer,
            knowledge_answer=knowledge_with_evidence,
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "d-prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"gold_set": str(args.gold_set), "prompts_written": len(prompts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
