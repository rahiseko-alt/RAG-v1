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

from src.rag import ask, get_generation_model, get_llm_provider, is_generation_configured, make_rag  # noqa: E402
from src.observability import flush_langfuse, get_langfuse_config_error, is_langfuse_configured  # noqa: E402
from src.runtime_errors import safe_error_message  # noqa: E402


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
    try:
        state = ask(graph, question)
    except Exception as exc:
        print("エラー: 回答生成に失敗しました。", file=sys.stderr)
        print(f"詳細: {safe_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from None
    _print_sources(state["docs"])
    print("\n  【回答】")
    print("  " + state["answer"].replace("\n", "\n  "))
    print()
    flush_langfuse()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not is_generation_configured():
        provider = get_llm_provider()
        key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
        print(f"エラー: {key_name} が未設定です。リポジトリ直下に .env を作成し "
              f"{key_name}=... を設定してください（回答生成に必要）。", file=sys.stderr)
        return 2

    audit = "ON（Langfuse）" if is_langfuse_configured() else "OFF"
    langfuse_error = get_langfuse_config_error()
    if langfuse_error and langfuse_error != "LANGFUSE_ENABLED is disabled":
        print(f"警告: Langfuse監査は無効です（{langfuse_error}）。", file=sys.stderr)
    print(
        "索引を準備しています"
        f"（初回はモデル/文書の読み込みに時間がかかります・provider={get_llm_provider()}・"
        f"モデル={get_generation_model()}・監査={audit}）..."
    )
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
