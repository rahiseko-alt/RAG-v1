"""src/eval — RAG回答品質の評価ループ（LLM-as-judge・独立サブAI）。

原計画 Stage 4。検証パネル（複数視点のLLM反証レビュー）の考え方を RAG 回答評価へ転用する。
- 生成者（回答を作るAI）と評価者（採点するAI）を**分離**（自己採点しない＝独立性が信頼性の要）。
- 各回答を**複数の独立した評価者サブAI**が3軸で採点し、評価が割れた項目は人手サンプリングへ。
- 判定は API でなくサブエージェント（このPC上の独立Claude）が行う。

本モジュールは決定的な部分（検索によるアイテム化・スコア集計・レポート描画）を担い、
非決定的な生成・採点はオーケストレータ（サブエージェント）が行って verdict を渡す。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

# 注: src.rag（langchain/torch 等の重い依存）は prepare_items でのみ必要なため遅延 import。
# 集計・レポート描画は src.rag 非依存で高速に動く。

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = PRODUCT_ROOT / "data" / "eval" / "eval_questions.json"

# 評価3軸（検証パネル転用・1回答を異なる観点で採点）。スコアは 0-2（0=不可 / 1=一部 / 2=良）
AXES = [
    ("faithfulness", "根拠忠実性", "回答が検索文脈に支持されるか（幻覚でないか）"),
    ("relevance", "質問直接性", "質問に正面から答えているか"),
    ("no_misinfo", "誤情報なし", "誤情報が無いか／文書外は適切に『記載なし』と答えるか"),
]
AXIS_KEYS = [a[0] for a in AXES]
PASS_THRESHOLD = 2  # 各軸の中央値がこの値以上で合格


def load_questions(path: str | Path = QUESTIONS_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["questions"]


def prepare_items(top_k: int | None = None) -> list[dict]:
    """各質問を Chroma で検索し、出典付き文脈を添えた評価アイテムにする。"""
    from src.rag import TOP_K, get_or_build_index  # 遅延 import（重い依存）

    top_k = TOP_K if top_k is None else top_k
    vs, _ = get_or_build_index()
    items = []
    for q in load_questions():
        hits = vs.similarity_search_with_relevance_scores(q["question"], k=top_k)
        ctx = [
            {"page": d.metadata.get("page"), "score": round(float(s), 3), "text": d.page_content.strip()}
            for d, s in hits
        ]
        items.append({**q, "context": ctx})
    return items


def dump_items(out_path: str | Path, top_k: int | None = None) -> int:
    items = prepare_items(top_k=top_k)
    Path(out_path).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


def _mode(vals: list) -> int:
    """最頻値（複数モード時は最小値）。空なら 0。"""
    if not vals:
        return 0
    c = Counter(vals)
    top = max(c.values())
    return min(k for k, v in c.items() if v == top)


def aggregate(items: list[dict], verdicts: dict) -> dict:
    """独立評価者の verdict を集計。

    verdicts 形式: { judge_id: { item_id: {"faithfulness":int, "relevance":int, "no_misinfo":int, "comment":str} } }
    各アイテム×各軸で、評価者間の**中央値・最頻値**を出す（平均は使わない）。
    評価が割れた（スコア幅 >=2）軸は disagreement として列挙し人手サンプリングへ回す。
    """
    judges = list(verdicts.keys())
    per_item = []
    for it in items:
        row = {"id": it["id"], "question": it["question"], "in_doc": it["in_doc"]}
        axes = {}
        disagree = []
        for ak in AXIS_KEYS:
            # 評価者JSONに軸キーが欠けていても落ちないよう 0（未採点扱い）で補完
            scores = [verdicts[j][it["id"]].get(ak, 0) for j in judges if it["id"] in verdicts.get(j, {})]
            axes[ak] = {
                "scores": scores,
                "median": statistics.median(scores) if scores else 0,
                "mode": _mode(scores),
            }
            if scores and (max(scores) - min(scores)) >= 2:
                disagree.append(ak)
        row["axes"] = axes
        row["pass"] = all(axes[ak]["median"] >= PASS_THRESHOLD for ak in AXIS_KEYS)
        row["disagree"] = disagree
        per_item.append(row)

    overall = {}
    for ak in AXIS_KEYS:
        meds = [r["axes"][ak]["median"] for r in per_item]
        overall[ak] = {"median": statistics.median(meds) if meds else 0, "mode": _mode(meds)}

    n = len(per_item)
    npass = sum(1 for r in per_item if r["pass"])
    return {"per_item": per_item, "overall": overall, "pass_rate": [npass, n], "judges": judges}


def build_report(items_path, answers_path, verdicts_path, out_html, human_sampling=None) -> str:
    """items / answers / verdicts JSON からHTML評価レポートを生成する（信頼物・出典付き）。"""
    items = json.loads(Path(items_path).read_text(encoding="utf-8"))
    answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    verdicts = json.loads(Path(verdicts_path).read_text(encoding="utf-8"))
    agg = aggregate(items, verdicts)
    html = _render_html(items, answers, verdicts, agg, human_sampling or {})
    Path(out_html).write_text(html, encoding="utf-8")
    return out_html


def _render_html(items, answers, verdicts, agg, human_sampling) -> str:
    axis_ja = {a[0]: a[1] for a in AXES}
    item_by_id = {it["id"]: it for it in items}
    npass, n = agg["pass_rate"]

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    # 全体サマリ行
    ov_rows = "".join(
        f"<tr><td>{esc(axis_ja[ak])}</td><td class='num'>{agg['overall'][ak]['median']}</td>"
        f"<td class='num'>{agg['overall'][ak]['mode']}</td></tr>"
        for ak in AXIS_KEYS
    )

    # アイテム別
    item_blocks = []
    for r in agg["per_item"]:
        it = item_by_id[r["id"]]
        ans = answers.get(r["id"], "(未生成)")
        pages = "・".join(f"p.{c['page']}" for c in it["context"])
        axis_cells = "".join(
            f"<td class='num'>{r['axes'][ak]['median']}"
            f"<span class='sub'>（各評価者: {', '.join(map(str, r['axes'][ak]['scores']))}）</span></td>"
            for ak in AXIS_KEYS
        )
        badge = "<span class='pass'>合格</span>" if r["pass"] else "<span class='fail'>要確認</span>"
        dis = ("<div class='dis'>⚠ 評価が割れた軸: "
               + "・".join(axis_ja[a] for a in r["disagree"]) + "（人手サンプリング対象）</div>") if r["disagree"] else ""
        planted = "<div class='planted'>※このアイテムは<strong>意図的な誤答</strong>を混入した幻覚検出テスト</div>" if it.get("planted_wrong") else ""
        indoc = "文書内" if it["in_doc"] else "文書外（正解＝記載なし）"
        item_blocks.append(f"""
        <div class="qa">
          <div class="qhead"><span class="qid">{esc(r['id'])}</span> {esc(r['question'])} {badge}
            <span class="tag">{indoc}</span></div>
          {planted}
          <div class="ans"><b>回答:</b> {esc(ans)}</div>
          <table class="axtbl"><thead><tr><th>根拠忠実性</th><th>質問直接性</th><th>誤情報なし</th></tr></thead>
            <tbody><tr>{axis_cells}</tr></tbody></table>
          <div class="trace"><b>出典追跡（採点の根拠文脈）:</b> {esc(pages)}</div>
          {dis}
        </div>""")

    hs_block = ""
    if human_sampling:
        rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in human_sampling.items())
        hs_block = f"""<h2>人手サンプリング（評価者との一致）</h2>
        <table class="kv"><tbody>{rows}</tbody></table>"""

    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RAG回答品質 評価レポート — WHO HEARTS</title>
<style>
:root{{--ink:#1f2933;--muted:#5b6670;--line:#e3e8ee;--bg:#f6f8fa;--card:#fff;--accent:#0b6b5b;--warn:#b45309;--red:#b3261e;}}
*{{box-sizing:border-box}} body{{margin:0;font-family:"Yu Gothic","Meiryo",system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.7}}
.wrap{{max-width:900px;margin:0 auto;padding:36px 20px 72px}}
h1{{font-size:1.5rem;border-bottom:3px solid var(--accent);padding-bottom:12px}}
h2{{font-size:1.12rem;border-left:5px solid var(--accent);padding-left:10px;margin-top:32px}}
.method{{background:#e6f2ef;border:1px solid #cfe6e0;border-radius:10px;padding:14px 18px;font-size:.92rem}}
table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:10px 0}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:.9rem;vertical-align:top}}
th{{background:#eef3f7;color:var(--muted);font-weight:600}}
.num{{text-align:center;font-weight:700;font-size:1.05rem}}
.num .sub{{display:block;font-weight:400;font-size:.72rem;color:var(--muted)}}
.summary{{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 18px;min-width:150px}}
.kpi b{{display:block;font-size:1.6rem;color:var(--accent)}}
.qa{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}}
.qhead{{font-weight:700;margin-bottom:8px}} .qid{{background:var(--accent);color:#fff;border-radius:5px;padding:1px 7px;font-size:.8rem;margin-right:6px}}
.tag{{font-size:.76rem;color:var(--muted);border:1px solid var(--line);border-radius:12px;padding:1px 8px;margin-left:6px}}
.pass{{color:var(--accent);font-weight:700;margin-left:6px}} .fail{{color:var(--warn);font-weight:700;margin-left:6px}}
.ans{{background:#fafbfc;border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0;font-size:.9rem}}
.axtbl{{margin:6px 0}} .axtbl th{{text-align:center;font-size:.82rem}}
.trace{{font-size:.82rem;color:var(--muted);margin-top:6px}}
.dis{{color:var(--warn);font-size:.85rem;margin-top:6px}}
.planted{{color:var(--red);font-size:.85rem;margin:4px 0}}
footer{{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);font-size:.83rem;color:var(--muted)}}
@media(max-width:600px){{.wrap{{padding:22px 14px}}}}
</style></head><body><div class="wrap">
<h1>RAG回答品質 評価レポート</h1>
<p>対象: WHO HEARTS「Healthy-lifestyle counselling」に対する Stage 3 RAG の回答を、<b>独立した複数の評価者サブAI</b>が3軸で採点。</p>
<div class="method"><b>評価方法（独立性の担保）:</b> 回答を作る<b>生成者AIと採点する評価者AIを分離</b>し（自己採点しない）、各回答を{esc(len(agg['judges']))}名の独立評価者が
「根拠忠実性／質問直接性／誤情報なし」の3軸で 0-2 点採点。評価者間は<b>中央値・最頻値</b>で集計（平均は使わない）。評価が割れた項目は人手サンプリングへ回す。
各採点は検索文脈（出典ページ）を根拠にした意味評価。<b>幻覚検出力の実証のため、意図的な誤答を1件混入</b>している。</div>

<div class="summary">
  <div class="kpi"><b>{npass}/{n}</b>合格アイテム</div>
  <div class="kpi"><b>{esc(len(agg['judges']))}</b>独立評価者</div>
  <div class="kpi"><b>3</b>評価軸</div>
</div>

<h2>全体スコア（評価者間の中央値・最頻値）</h2>
<table><thead><tr><th>評価軸</th><th>中央値</th><th>最頻値</th></tr></thead><tbody>{ov_rows}</tbody></table>

<h2>アイテム別 採点</h2>
{''.join(item_blocks)}

{hs_block}

<footer>
<p>スコア: 0=不可 / 1=一部 / 2=良。合格＝全3軸の評価者間中央値が2以上。数値は中央値・最頻値の実数（平均は不使用）。</p>
<p>評価は独立サブAIによる複数視点採点であり、最終判断は人手サンプリングで担保する（検証パネル思想の転用）。対象文書 © WHO 2018, CC BY-NC-SA 3.0 IGO。医療助言ではない。</p>
</footer>
</div></body></html>"""
