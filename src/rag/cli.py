"""src/rag/cli.py — 登録済みナレッジ RAG の CLI デモ。

使い方（リポジトリ直下＝products/medguide-rag で実行）:
    python -m src.rag.cli "このナレッジの対象は何ですか？"       # 1問だけ
    python -m src.rag.cli                                          # 対話モード（空行/quitで終了）

各回答の前に「出典追跡」（検索されたチャンクの出典ページと類似度）を表示し、
回答が実在の文書箇所に根拠づいていることを目視できるようにする。
OPENAI_API_KEY / ANTHROPIC_API_KEY は .env または環境変数から読む（コードに平文で置かない）。
"""
from __future__ import annotations

import os
import sys

# `python src/rag/cli.py` 直接起動でも import 解決できるよう、プロジェクト直下を path に追加
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.quality import QualityWorkbench, WorkbenchStore  # noqa: E402
from src.rag import engine_fingerprint, get_generation_model, get_llm_provider, is_generation_configured  # noqa: E402
from src.observability import get_langfuse_config_error, is_langfuse_configured  # noqa: E402
from src.runtime_errors import safe_error_message  # noqa: E402


def _print_sources(sources: list[dict]) -> None:
    print("  ── 出典追跡（検索された文書箇所）─────────────")
    for item in sources:
        snippet = " ".join(str(item["snippet"]).split())[:90]
        print(
            f"  [{item['rank']}] 類似度 {item['score']:.3f} | "
            f"{item['source']} p.{item['page']}"
        )
        print(f"      {snippet}…")
    print("  ─────────────────────────────────────────────")


def _answer_one(workbench: QualityWorkbench, question: str, *, answer_mode: str = "strict") -> None:
    try:
        outcome = workbench.answer_question(question=question, answer_mode=answer_mode)
    except Exception as exc:
        print("エラー: 回答生成に失敗しました。", file=sys.stderr)
        print(f"詳細: {safe_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from None
    _print_sources(outcome["sources"])
    if outcome["delivery_status"] == "released":
        print("\n  【回答】")
        print("  " + str(outcome["answer"]).replace("\n", "\n  "))
    else:
        print("\n  【回答保留】")
        print("  品質検証を通過しなかったため、候補回答は表示しません。")
        print(f"  run_id: {outcome['run_id']}")
    print()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    answer_mode = "strict"
    if "--mode" in argv:
        index = argv.index("--mode")
        try:
            answer_mode = argv[index + 1]
        except IndexError:
            print("エラー: --mode には strict / standard / explore を指定してください。", file=sys.stderr)
            return 2
        del argv[index : index + 2]
    if answer_mode not in {"strict", "standard", "explore"}:
        print("エラー: --mode は strict / standard / explore のいずれかです。", file=sys.stderr)
        return 2

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
        f"モデル={get_generation_model()}・回答モード={answer_mode}・監査={audit}）..."
    )
    workbench = QualityWorkbench(
        WorkbenchStore(engine_fingerprint=engine_fingerprint())
    )
    _graph, _vs, n = workbench.prepare_revision()
    print(f"準備完了: {n} チャンクを検索対象にしています。\n")

    if argv:  # 1問モード
        _answer_one(workbench, " ".join(argv), answer_mode=answer_mode)
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
        _answer_one(workbench, q, answer_mode=answer_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
