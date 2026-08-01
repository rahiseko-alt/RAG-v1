# medguide-rag

登録済みナレッジ文書に対する質問応答システム（RAG）と、その回答品質評価結果を集計・可視化する仕組みを組み合わせたプロジェクトです。

> **現在の状態: 単一PC向け品質改善ワークベンチ。** 回答照合、fail-closed出荷停止、SQLite版管理、before/after、全件回帰検査、承認・却下、調整台帳、Langfuse着弾確認、非エンジニア向け4タブUIまで実装済みです。現在地は [docs/current-plan-report.md](docs/current-plan-report.md)、操作と判断基準は [docs/workbench-guide.md](docs/workbench-guide.md) にまとめています。

> **注意:** このプロジェクトは学習・ポートフォリオ用デモです。現在の既定ナレッジは `config/knowledge.toml` で指定されています。原典確認や専門判断を置き換えるものではありません。

## 1. これは何をするものか

公開されている文書を検索対象にして、登録済みナレッジ内に書かれている内容を根拠付きで要約するシステムです。現在は `config/knowledge.toml` で有効ナレッジを選び、ナレッジID、本文パス、出典URL、ライセンス、評価セット、検索collection名をコード外で管理します。回答品質については、外部で作成した複数評価者の verdict JSON を集計し、HTMLレポートとして確認できる仕組みを備えています。

## 2. 作った理由

医療AI企業の機械学習エンジニア職に応募するにあたり、自分の実務経験（LLMを使ったマルチエージェント開発基盤の設計・運用、計測に基づくプロンプトチューニング）を棚卸ししたところ、「Pythonでのデータを使ったモデル開発経験」が実績として提示できないことに気づきました。このプロジェクトは、その不足を埋めるために作っています。既存の強み（複数のLLMに反証させて品質を担保する「検証パネル」という仕組みを本番運用してきた経験）を、医療文書の質問応答という新しい領域に転用できるかどうかを試すのが狙いです。

## 3. 何ができるか

- [x] サンプルナレッジ（呪術廻戦の公開情報とファン論点観測を要約した拡張リサーチナレッジ）に対する質問応答のCLIデモ
  - 実行: `python -m src.rag.cli "呪術廻戦の作者は誰ですか？"`（質問例は `config/knowledge.toml` で管理。要 `.env` の `ANTHROPIC_API_KEY` または `OPENAI_API_KEY`）
  - 出力: 質問 → 出典追跡パネル（検索チャンクの出典・類似度）→ 登録済みナレッジを根拠にした日本語回答
  - 文書に無い質問は「記載なし」と答え、幻覚を抑止
- [x] 教材ノート `notebooks/03-rag-walkthrough.ipynb`（RAGの流れを解説付きで体験）
- [x] FastAPI経由でローカル起動して質問できるデモ
- [x] ブラウザで回答・根拠・工程詳細・監査状態を見られる透明型RAG UI
- [x] 別LLM照合と、NG・判断不能・障害時の出荷停止
- [x] UIだけでナレッジ下書き、比較、回帰検査、承認・却下
- [x] SQLite調整台帳とLangfuse trace着弾確認

## 4. アーキテクチャ

詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

## 5. 技術スタック

| 領域 | 技術 |
|---|---|
| 言語 | Python |
| RAGフレームワーク | LangChain / LangGraph |
| ベクトル検索 | Chroma |
| API | FastAPI |
| 評価 | 決定論的検査 + 別LLM照合 + 回帰評価 |
| 永続化 | SQLite + 不変revisionスナップショット |
| 監査 | Langfuse Cloud + ローカルrun/event台帳 |
| 基礎データ処理 | pandas / numpy |
| 軽量モデル | scikit-learn / PyTorch |

## 6. 評価結果

- 評価データセット: `data/eval/eval_questions.json`（8問。文書内6問、文書外2問）
- 集計軸: 根拠忠実性／質問直接性／誤情報なし
- 実装済み: 外部で作成した `reports/eval/verdicts.json` を集計し、`reports/who-hearts-eval-report.html` にHTMLレポート化
- 未実装/未確認: 評価者LLMを呼び出す完全自動採点、評価者と人手サンプリングの一致率確認

## 7. 設計で工夫した点

このプロジェクトの核は、RAGそのものより「回答を表示前に検品し、悪ければ止め、調整効果を記録する」部分です。回答全文の引用検査、主張別根拠照合、別LLMの3軸判定、before/after、既存PASS質問の回帰確認を通過したrevisionだけを承認できます。

## 8. セットアップ手順

```bash
uv sync --locked --extra dev --extra notebook
```

環境変数はリポジトリ直下に `.env` を作成し、以下を設定してください（`.env` はgitignore対象・コミットしない）。Stage 3・4（RAG・評価）で使用します。

```
LLM_PROVIDER=openai
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_MODEL=claude-opus-4-8
OPENAI_MODEL=gpt-5.6-sol
KNOWLEDGE_CONFIG_PATH=config/knowledge.toml
```

回答生成は `LLM_PROVIDER=openai` または `LLM_PROVIDER=anthropic` で切り替えられます。既定はOpenAIです。

Langfuse Cloud でRAGの監査ログ（質問・検索/生成トレース・モデル呼び出し）を残す場合は、同じ `.env` に以下も設定します。未設定なら監査送信は無効のまま通常実行できます。

```
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENABLED=false
```

Langfuseを使う場合だけ `LANGFUSE_ENABLED=true` にします。日本リージョンのプロジェクトを作った場合は `LANGFUSE_BASE_URL=https://jp.cloud.langfuse.com` に変更します。

### Stage 1 の実行（Python基礎固めノート）

pandas/numpy/matplotlib によるデータ前処理・統計・可視化の解説ノートです。

```bash
# ノートをブラウザで開いて対話的に実行
jupyter lab notebooks/01-foundations.ipynb

# もしくは全セルを実行してHTML化（生成物: notebooks/01-foundations.html）
cd notebooks
jupyter nbconvert --to html --execute 01-foundations.ipynb
```

初回実行時に UCI Heart Disease データを `data/sample/` へ自動取得します（既に存在すれば再取得しません）。生成される図は `notebooks/outputs/` に保存されます。

Stage 3 のRAG CLIは以下で実行できます。

```bash
python -m src.rag.cli "呪術廻戦の作者は誰ですか？"
```

検索対象を変える場合は、新しいナレッジ用の TOML を作り、`.env` の `KNOWLEDGE_CONFIG_PATH=...` を差し替えます。PDF、Markdown、テキストに対応しています。

```toml
[knowledge]
id = "client_knowledge"
title = "顧客向けナレッジ"
source_path = "data/sample/client-knowledge.md"
collection = "knowledge_client_20260731"
source_url = "https://example.com/source"
license = "確認済みライセンス"
checked_at = "2026-07-31"
eval_set = "data/eval/client-questions.json"
example_question = "この文書の対象は何ですか？"
expected_terms = ["期待される語"]
```

FastAPIデモは以下で起動できます。

```bash
uv run uvicorn src.api:app --host 127.0.0.1 --port 8010
```

- `GET /`: 4タブ品質改善ワークベンチ
- `POST /ask`: 質問、回答照合、出荷判定、監査記録
- `POST /runs`: 非同期run作成
- `GET /runs/{id}` / `GET /runs/{id}/events`: run状態とSSE工程イベント
- 品質管理API一覧: [src/api/README.md](src/api/README.md)

主要UIフローのPlaywright E2Eは、サーバー起動中に次で実行する。

```bash
uv run pytest tests/e2e -m e2e
```

## 進捗

4段階の計画で進めています。当初計画の原文は [docs/original-plan.md](docs/original-plan.md)（改変禁止）、進捗は [docs/progress.md](docs/progress.md) に記録しています。

| Stage | 内容 | 状態 |
|---|---|---|
| 1 | Python基礎固め（pandas/numpy） | 完了 |
| 2 | 軽量モデルを1本完走（scikit-learn/PyTorch） | 完了 |
| 3 | 登録済みナレッジRAG構築（LangChain/LangGraph/Chroma） | 完了 |
| 4 | 評価ループ移植（LLM-as-judge） | 完了 |
| 5 | Langfuse監査ログ連携 | 任意連携追加済（認証確認済み・画面着弾確認は未記録） |
| 6 | LLMプロバイダ切替 | Anthropic / OpenAI 切替対応 |
| 7 | ナレッジ設定分離 | `config/knowledge.toml` で本文・出典・評価セット・collectionを管理 |
| 8 | 品質改善ワークベンチ | 回答照合、出荷停止、版管理、比較・回帰、承認、台帳、非エンジニアUI |

## 監査ログ

Langfuse の環境変数が正しく設定されている場合、`retrieve`、`generate`、`verify`、`gate`を同じtrace IDへ送信し、flush後にCloud APIで着弾確認します。キー未設定時やキー形式不正時はローカルのみで動きます。2026-07-31に実runとCloud traceの`confirmed`照合を確認済みです。

## ライセンス

MIT License。詳細は [LICENSE](LICENSE) を参照してください。
