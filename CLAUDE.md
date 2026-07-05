# medguide-rag 自治憲法 v0.1

@../../docs/ops/agent-rules.md

> 本プロダクトは個人ポートフォリオ用の非商用プロジェクトである。外部リポジトリへ切り出す際は本行および vibe-base 固有の @import を削除する。クライアントが存在しないため、vibe-base 標準の P0-P4 商用ゲート（`docs/ops/product-cycle.md`）は適用除外とする（`products/kobaraku` と同様の扱い）。

## 0. プロダクト定義

| 項目 | 内容 |
|---|---|
| プロダクト名 | medguide-rag |
| 一言 | 医療ガイドラインRAG＋LLM評価パイプライン（外部公開ポートフォリオ） |
| 目的 | 就職活動（機械学習エンジニア職応募）における実務経験ギャップ（Python ML/DL）を埋める学習成果物 |
| 技術スタック | Python + LangChain/LangGraph + Chroma + FastAPI |
| 公開予定 | GitHub 個人リポジトリへ独立化（`scripts/migrate-products-to-repos.mjs` 相当の手順で切り出し） |
| 進捗管理 | `docs/progress.md`（4段階の進捗ログ） |

## 1. 最重要ルール

- 個人ポートフォリオのため、vibe-base 固有ルール（自治憲法の @import 等）への依存を最小限にする。外部リポジトリとして独立させても動くように self-contained を維持する。
- シークレット（APIキー）はコード・コミットに平文で置かない。`.env`（gitignore済・リポジトリ直下に手動作成）または環境変数のみ。必要な変数名は README.md セットアップ手順に明記する。
