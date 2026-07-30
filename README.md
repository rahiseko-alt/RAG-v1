# medguide-rag

医療ガイドライン文書に対する質問応答システム（RAG）と、その回答品質評価結果を集計・可視化する仕組みを組み合わせたプロジェクトです。

> **現在の状態: 実証プロトタイプ。** OpenAI対応RAG、FastAPI最小API、Langfuse連携経路、評価結果の集計/HTML化まで実装済みです。CLIでのOpenAI回答生成とLangfuse認証は確認済みですが、Langfuse画面でのtrace着弾確認、実uvicorn経由の `/ask` E2E、非エンジニア向けUI、工程診断、調整台帳は未実装です。現在地と計画は [docs/current-plan-report.md](docs/current-plan-report.md) にまとめています。

> **注意:** このプロジェクトは学習・ポートフォリオ用デモです。医療判断、診断、治療、投薬、緊急時対応には使用できません。

## 1. これは何をするものか

公開されている医療ガイドライン文書を検索対象にして、文書内に書かれている内容を根拠ページ付きで要約するシステムです。個別症状への医療助言ではなく、「登録済み文書のどこに何が書かれているか」を確認する学習用デモとして扱います。回答品質については、外部で作成した複数評価者の verdict JSON を集計し、HTMLレポートとして確認できる仕組みを備えています。

## 2. 作った理由

医療AI企業の機械学習エンジニア職に応募するにあたり、自分の実務経験（LLMを使ったマルチエージェント開発基盤の設計・運用、計測に基づくプロンプトチューニング）を棚卸ししたところ、「Pythonでのデータを使ったモデル開発経験」が実績として提示できないことに気づきました。このプロジェクトは、その不足を埋めるために作っています。既存の強み（複数のLLMに反証させて品質を担保する「検証パネル」という仕組みを本番運用してきた経験）を、医療文書の質問応答という新しい領域に転用できるかどうかを試すのが狙いです。

## 3. 何ができるか

- [x] サンプル文書（WHO HEARTS 生活習慣カウンセリング）に対する質問応答のCLIデモ
  - 実行: `python -m src.rag.cli "成人は運動をどのくらい行うべきですか？"`（要 `.env` の `ANTHROPIC_API_KEY` または `OPENAI_API_KEY`）
  - 出力: 質問 → 出典追跡パネル（検索チャンクの出典ページ・類似度）→ 文書を根拠にした日本語回答（引用付き）
  - 日本語質問→英語文書のクロスリンガル検索。文書に無い質問は「記載なし」と答え幻覚を抑止
- [x] 教材ノート `notebooks/03-rag-walkthrough.ipynb`（RAGの流れを解説付きで体験）
- [x] FastAPI経由でローカル起動して質問できるデモ
- [ ] デモのスクリーンショット / GIF

## 4. アーキテクチャ

詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

## 5. 技術スタック

| 領域 | 技術 |
|---|---|
| 言語 | Python |
| RAGフレームワーク | LangChain / LangGraph |
| ベクトル検索 | Chroma |
| API | FastAPI |
| 評価 | verdict JSON の集計/HTML化（LLM-as-judge運用は外部/手動プロセス） |
| 基礎データ処理 | pandas / numpy |
| 軽量モデル | scikit-learn / PyTorch |

## 6. 評価結果

- 評価データセット: `data/eval/eval_questions.json`（8問。文書内6問、文書外2問）
- 集計軸: 根拠忠実性／質問直接性／誤情報なし
- 実装済み: 外部で作成した `reports/eval/verdicts.json` を集計し、`reports/who-hearts-eval-report.html` にHTMLレポート化
- 未実装/未確認: 評価者LLMを呼び出す完全自動採点、評価者と人手サンプリングの一致率確認

## 7. 設計で工夫した点

このプロジェクトの核は、RAGそのものより「回答品質をどう検証可能にするか」の部分です。現時点では、出典追跡、監査トレース経路、評価結果の集計/HTML化まで実装しています。今後は、非エンジニア向けUI、工程別診断、調整台帳、before/after比較を追加し、回答品質を継続改善できる形へ発展させます。

## 8. セットアップ手順

```bash
python -m venv venv
source venv/bin/activate  # Windowsは venv\Scripts\activate
pip install -r requirements.txt
```

環境変数はリポジトリ直下に `.env` を作成し、以下を設定してください（`.env` はgitignore対象・コミットしない）。Stage 3・4（RAG・評価）で使用します。

```
LLM_PROVIDER=openai
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_MODEL=claude-opus-4-8
OPENAI_MODEL=gpt-5.6-sol
```

回答生成は `LLM_PROVIDER=openai` または `LLM_PROVIDER=anthropic` で切り替えられます。既定はOpenAIです。

Langfuse Cloud でRAGの監査ログ（質問・検索/生成トレース・モデル呼び出し）を残す場合は、同じ `.env` に以下も設定します。未設定なら監査送信は無効のまま通常実行できます。

```
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENABLED=true
```

日本リージョンのプロジェクトを作った場合は `LANGFUSE_BASE_URL=https://jp.cloud.langfuse.com` に変更します。

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
python -m src.rag.cli "成人は運動をどのくらい行うべきですか？"
```

FastAPIデモは以下で起動できます。

```bash
uvicorn src.api:app --reload
```

- `GET /health`: 設定状態の確認
- `POST /ask`: 質問応答（回答・出典・監査ON/OFFを返す）

## 進捗

4段階の計画で進めています。当初計画の原文は [docs/original-plan.md](docs/original-plan.md)（改変禁止）、進捗は [docs/progress.md](docs/progress.md) に記録しています。

| Stage | 内容 | 状態 |
|---|---|---|
| 1 | Python基礎固め（pandas/numpy） | 完了 |
| 2 | 軽量モデルを1本完走（scikit-learn/PyTorch） | 完了 |
| 3 | 医療文書RAG構築（LangChain/LangGraph/Chroma） | 完了 |
| 4 | 評価ループ移植（LLM-as-judge） | 完了 |
| 5 | Langfuse監査ログ連携 | 任意連携追加済（認証確認済み・画面着弾確認は未記録） |
| 6 | LLMプロバイダ切替 | Anthropic / OpenAI 切替対応 |

## 監査ログ

Langfuse の環境変数が正しく設定されている場合、CLI実行時に LangGraph / LangChain の callback 経路でトレース送信を試みます。`src/observability.py` が任意連携を担当し、キー未設定時やキー形式不正時はローカルのみで動きます。Langfuse認証チェックは通過済みですが、画面上でのtrace着弾確認は別途記録が必要です。

## ライセンス

MIT License。詳細は [LICENSE](LICENSE) を参照してください。
