"""src/rag/cli.py — 医療ガイドライン RAG の CLI デモ。

使い方（リポジトリ直下＝products/medguide-rag で実行）:
    python -m src.rag.cli "運動は週にどのくらい推奨されますか？"   # 1問だけ
    python -m src.rag.cli                                          # 対話モード（空行/quitで終了）

各回答の前に「出典追跡」（検索されたチャンクの出典ページと類似度）を表示し、
回答が実在の文書箇所に根拠づいていることを目視できるようにする。
ANTHROPIC_API_KEY は .env または環境変数から読む（コードに平文で置かない）。
"""
from __future__ import annotations

import os
import sys

# `python src/rag/cli.py` 直接起動でも import 解決できるよう、プロジェクト直下を path に追加
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.rag import ANTHROPIC_MODEL, ask, make_rag  # noqa: E402


def _print_sources(docs: list) -> None:
    print("  ── 出典追跡（検索された文書箇所）─────────────")
    for i, (d, score) in enumerate(docs, start=1):
        page = d.metadata.get("page", "?")
        src = d.metadata.get("source", "?")
        snippet = " ".join(d.page_content.split())[:90]
        print(f"  [{i}] 類似度 {score:.3f} | {src} p.{page}")
        print(f"      {snippet}…")
    print("  ─────────────────────────────────────────────")


def _answer_one(graph, question: str) -> None:
    state = ask(graph, question)
    _print_sources(state["docs"])
    print("\n  【回答】")
    print("  " + state["answer"].replace("\n", "\n  "))
    print()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("エラー: ANTHROPIC_API_KEY が未設定です。リポジトリ直下に .env を作成し "
              "ANTHROPIC_API_KEY=... を設定してください（回答生成に必要）。", file=sys.stderr)
        return 2

    print(f"索引を準備しています（初回はモデル/文書の読み込みに時間がかかります・モデル={ANTHROPIC_MODEL}）...")
    graph, _vs, n = make_rag()
    print(f"準備完了: {n} チャンクを検索対象にしています。\n")

    if argv:  # 1問モード
        _answer_one(graph, " ".join(argv))
        return 0

    # 対話モード
    print("質問を入力してください（空行または quit で終了）。")
    while True:
        try:
            q = input("質問> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q or q.lower() in {"quit", "exit"}:
            break
        _answer_one(graph, q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
