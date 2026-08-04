"""src/eval 集計ロジックのテスト（決定的・サブAI/埋め込み不要）。

- 中央値・最頻値・pass判定・割れ（disagreement）検出を合成 verdict で検証。
- 幻覚検出の要: 全評価者が faithfulness=0 を付けた回答が pass=False になること。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.eval import AXIS_KEYS, aggregate  # noqa: E402


def _verdict(f, r, m):
    return {"faithfulness": f, "relevance": r, "no_misinfo": m, "comment": ""}


def test_hallucination_flagged_fail():
    items = [{"id": "q2", "question": "…", "in_doc": True}]
    # 3評価者が全員 faithfulness=0・no_misinfo=0（幻覚を検出）
    verdicts = {
        "j1": {"q2": _verdict(0, 2, 0)},
        "j2": {"q2": _verdict(0, 2, 0)},
        "j3": {"q2": _verdict(0, 2, 0)},
    }
    agg = aggregate(items, verdicts)
    row = agg["per_item"][0]
    assert row["axes"]["faithfulness"]["median"] == 0
    assert row["pass"] is False
    assert agg["pass_rate"] == [0, 1]


def test_all_good_pass():
    items = [{"id": "q1", "question": "…", "in_doc": True}]
    verdicts = {j: {"q1": _verdict(2, 2, 2)} for j in ("j1", "j2", "j3")}
    agg = aggregate(items, verdicts)
    row = agg["per_item"][0]
    assert row["pass"] is True
    assert row["disagree"] == []
    for ak in AXIS_KEYS:
        assert row["axes"][ak]["median"] == 2


def test_aggregate_accepts_stratified_items_without_in_doc():
    """`data/eval/stratified-eval-set-v1.json` items use `stratum`/`expected_behavior`
    instead of the legacy `in_doc` flag; aggregate must not KeyError on them, and
    should report a per-stratum pass rate breakdown."""
    items = [
        {"id": "E-01", "question": "…", "stratum": "easy_factual"},
        {"id": "U-01", "question": "…", "stratum": "unanswerable_out_of_db", "expected_behavior": "abstain"},
    ]
    verdicts = {
        "j1": {"E-01": _verdict(2, 2, 2), "U-01": _verdict(0, 2, 0)},
        "j2": {"E-01": _verdict(2, 2, 2), "U-01": _verdict(0, 2, 0)},
    }

    agg = aggregate(items, verdicts)

    assert agg["pass_rate"] == [1, 2]
    assert agg["pass_rate_by_stratum"] == {
        "easy_factual": [1, 1],
        "unanswerable_out_of_db": [0, 1],
    }
    rows = {row["id"]: row for row in agg["per_item"]}
    assert rows["E-01"]["in_doc"] is None
    assert rows["U-01"]["expected_behavior"] == "abstain"


def test_disagreement_detected():
    items = [{"id": "q1", "question": "…", "in_doc": True}]
    # faithfulness が 0 と 2 に割れる → 幅>=2 で disagreement
    verdicts = {
        "j1": {"q1": _verdict(0, 2, 2)},
        "j2": {"q1": _verdict(2, 2, 2)},
        "j3": {"q1": _verdict(2, 2, 2)},
    }
    agg = aggregate(items, verdicts)
    row = agg["per_item"][0]
    assert "faithfulness" in row["disagree"]
    assert row["axes"]["faithfulness"]["median"] == 2  # 中央値は多数側
