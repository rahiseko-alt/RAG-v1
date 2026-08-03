"""Score D-role subagent judgments against the classifier gold set (Phase 1).

Loads `data/eval/classifier-gold-set-v1.json`, glob-merges `d-out-*.json` from a
run-dir (subagents that read `scripts.prepare_classifier_gold_prompts`'s output and
returned a judgment per gold item — the same `d-out-*.json` naming convention
`scripts/coverage_loop_assemble.py` already expects from D subagents), scores each
with `src.quality.classifier_gold.score_item` (which also runs the real
`classify_coverage_item`, so the disposition pipeline is exercised, not just the raw
cause label), and aggregates accuracy + a confusion matrix.

A gold item with no D output is a hard error, not a silent skip — an incomplete run
must not be able to report an inflated accuracy over whatever subset happened to be
judged.

Usage:
    uv run python -m scripts.measure_classifier_accuracy \
        --gold-set data/eval/classifier-gold-set-v1.json \
        --run-dir <dir with d-out-*.json> \
        --out-dir data/eval/runs/<date>-classifier-accuracy
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.coverage_loop import FactCheckJudgment
from src.quality.classifier_gold import GoldItem, aggregate_scores, score_item


def load_merged(run_dir: Path, pattern: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in sorted(run_dir.glob(pattern)):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-set", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    gold_payload = json.loads(args.gold_set.read_text(encoding="utf-8"))
    gold_items = [GoldItem.model_validate(item) for item in gold_payload["items"]]
    judgments = load_merged(args.run_dir, "d-out-*.json")

    missing = [item.id for item in gold_items if item.id not in judgments]
    if missing:
        raise SystemExit(f"missing D output for gold items: {missing}")

    rows = [
        score_item(gold=item, judgment=FactCheckJudgment.model_validate(judgments[item.id]))
        for item in gold_items
    ]
    summary = aggregate_scores(rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "classifier-accuracy.json").write_text(
        json.dumps(
            {
                "gold_set": str(args.gold_set),
                "gold_set_meta": gold_payload.get("meta", {}),
                "summary": summary,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
