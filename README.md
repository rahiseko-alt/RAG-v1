# medguide-rag

登録済みナレッジ文書に対する質問応答システム（RAG）と、その回答品質評価結果を集計・可視化する仕組みを組み合わせたプロジェクトです。

> **現在の状態: 単一PC向け品質改善ワークベンチ。** 回答照合、fail-closed出荷停止、SQLite版管理、before/after、全件回帰検査、承認・却下、調整台帳、Langfuse着弾確認、非エンジニア向け4タブUIまで実装済みです。現在地は [docs/current-plan-report.md](docs/current-plan-report.md)、操作と判断基準は [docs/workbench-guide.md](docs/workbench-guide.md) にまとめています。

> **注意:** このプロジェクトは学習・ポートフォリオ用デモです。現在の既定ナレッジは `config/knowledge.toml` で指定されています。原典確認や専門判断を置き換えるものではありません。

## 1. これは何をするものか

公開されている文書を検索対象にして、登録済みナレッジ内に書かれている内容を根拠付きで要約するシステムです。現在は `config/knowledge.toml` で有効ナレッジを選び、ナレッジID、本文パス、出典URL、ライセンス、評価セット、検索collection名をコード外で管理します。回答品質については、外部で作成した複数評価者の verdict JSON を集計し、HTMLレポートとして確認できる仕組みを備えています。

## 2. 作った理由

RAGは「それらしい回答」を返すことは簡単でも、**その回答が信頼できるかを機械的に示すのは難しい**という問題があります。本プロジェクトは、複数のLLMに互いを反証させて品質を担保する「検証パネル」——実務で設計・運用してきた仕組み——を、文書質問応答の検品に転用できるかを検証するために作っています。

そのため主題は検索精度そのものではなく、**回答を利用者に見せる前に検品し、根拠が不足していれば出荷を止め、弱点を台帳に記録して改善が実測できたものだけを採用する**というループの構築にあります。同時に、Python でのデータ処理・モデル開発・評価設計を一通り自分の手で通すことも目的の一つです（`notebooks/` の Stage 1・2 がその記録です）。

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
- [x] 弱点の発見から改善確認までを一周させる coverage loop（下記 §6）
  - 質問生成 → 外部基準回答と自ナレッジ回答の突き合わせ → 失敗原因の分類 → 台帳化 →
    改訂ドラフト生成 → before/after 検証 → 改善が実測できたものだけ採用
  - 判定不能なものは自動採用せず**隔離**し、UIの「隔離一覧」タブで人がまとめて確認する

> **なぜデモ用ナレッジが医療文書ではないのか。** 既定の検索対象は、ライセンスが明確で
> 事実密度が高く、正誤を第三者が検証しやすい公開情報（アニメ作品の設定）にしてあります。
> このプロジェクトの主題は特定ドメインの知識ではなく**ドメインに依存しない検品の仕組み**であり、
> 検索・検品の弱点は題材が身近なほど読み手が正しさを判定しやすいためです。
> 医療文書（WHO HEARTS ガイドライン）も `data/sample/` に同梱しており、
> `config/knowledge.toml` の差し替えのみで切り替えられます（コード変更は不要です）。

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

## 6. 評価結果（実測値）

自作した仕組みを「動きました」で終わらせず、**計測できるものは計測し、できないものは未検証と書く**方針で進めています。

### 失敗原因分類器の精度：22/27（81.5%）

回答の失敗原因（知識不足／検索失敗／生成失敗／外部回答が不正 等）を分類する判定器が、そもそも当てになるのかを検証しました。判定器が信用できないまま台帳を運用すると、誤った候補をナレッジに取り込むだけで有害になるためです。

- 手法：人手注釈の代わりに、**原因が構成上自明な設問**を実データから機械生成した「構成的ゴールドセット」（27件・`data/eval/classifier-gold-set-v1.json`）と照合
- 結果：全体 **81.5%**。先行研究 RAGEC の実測（同種の段階分類で人間一致率57.8%、エラー型正確度40.3%）を上回りました
- ただし `chunking_failure` ラベルのみ **0/5 で未検証**。ゴールドセットの構成方法に概念的誤りがあったためで、隠さず記録し、**このラベルは自動採用の対象から除外**しています
- 詳細：[docs/session-reports/2026-08-03-classifier-accuracy.md](docs/session-reports/2026-08-03-classifier-accuracy.md)

### 評価セットの設計是正

当初の30問セットは「難問100%・対照群0%・答えの無い問い0%」という設計不備があり、弁別力を失っていました（フロア効果）。実在ベンチマークを調査のうえ、6層100問の層別セット（`data/eval/stratified-eval-set-v1.json`：易問25／複数チャンク15／言い換え10／難問30／答えの無い問い12／誤前提8）に作り直しています。うち正解チャンクを持つ50問は、**LLMを呼ばずに機械的な検索評価**ができます。

### 現時点で未検証・未実装のもの

- 評価者LLMを呼び出す完全自動採点（現在は外部で作成した verdict JSON を集計する形）
- 評価者と人手サンプリングの一致率
- 層別セットのうち「答えの無い問い」12問・「誤前提」8問の実測（生成の実行が必要なため）
- 実運用規模のコーパスでの検索評価（現在のコーパスは41チャンクで、検索失敗が構造的に観測されにくい）

## 7. 設計で工夫した点

このプロジェクトの核は、RAGそのものより「回答を表示前に検品し、悪ければ止め、調整効果を記録する」部分です。回答全文の引用検査、主張別根拠照合、別LLMの3軸判定、before/after、既存PASS質問の回帰確認を通過したrevisionだけを承認できます。

## 8. 開発の進め方（検証の規律）

**「作った本人が動きましたと言っても、それは証拠にならない」**を運用ルールにしています。ルールの本文は [AGENTS.md](AGENTS.md)、実際に運用した記録は以下に残しています。

- **機械で白黒がつく所は CI が判定する。** 型（mypy・エラー0）／Lint（ruff）／テスト（pytest）／起動スモークを集約ゲート `ci-green` にまとめ、これが緑でなければマージしません。
- **機械で判定できない所は、独立したサブエージェントに敵対的に検証させる。** 実装した本人ではない立場で、報告を鵜呑みにせず再実行・再観察させます。これで実際にバグが見つかっています。例：
  - ナレッジ改訂の紐付けが未検証で、無関係な検証済みリビジョンを差し替えて承認を素通りできた
  - 追加したテストが、docstring で謳っていた条件を実際には一度も通っていなかった（回帰を注入して無検知を実証）
  - キー未設定ガードに、多段実行時だけ通り抜ける経路が残っていた
- **失敗は消さずに積む。** 事象・根因・教訓の形式で [docs/failures.md](docs/failures.md) に追記し続けています（上書きしません）。
- **セッションの引継ぎを2層で持つ。** 揮発層 [docs/handoff.md](docs/handoff.md)（毎回上書き）と不変層 [memory.md](memory.md)（消さない設計決定）を分け、区切りごとの詳細は [docs/session-reports/](docs/session-reports/) に残しています。

## 9. セットアップ手順

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
| 4 | 評価ループ移植（LLM-as-judge） | 集計・レポートまで完了。**評価者LLMの自動呼び出しは未実装**（§6参照） |
| 5 | Langfuse監査ログ連携 | 任意連携追加済（認証確認済み・画面着弾確認は未記録） |
| 6 | LLMプロバイダ切替 | Anthropic / OpenAI 切替対応 |
| 7 | ナレッジ設定分離 | `config/knowledge.toml` で本文・出典・評価セット・collectionを管理 |
| 8 | 品質改善ワークベンチ | 回答照合、出荷停止、版管理、比較・回帰、承認、台帳、非エンジニアUI |

現在は、上記の土台の上で **coverage loop（弱点を見つけて直し、改善を実測して採用する一周）** を主題に開発しています。

| Phase | 内容 | 状態 |
|---|---|---|
| 1 | 失敗原因分類器の精度検証 | 完了（22/27・81.5%。`chunking_failure` のみ未検証） |
| 2 | ループを閉じる（候補→改訂→検証→採用の状態遷移） | 完了 |
| 3 | 隔離一覧UI・APIの穴埋め・品質ゲートの実証 | 完了 |
| 4 | ドメイン移植性と統治（既定ナレッジの外出し・依存脆弱性ゲート・branch protection） | 未着手 |
| 5 | 納品検証（ドキュメント整合・クリーンcloneでの再現確認） | 未着手 |

## 監査ログ

Langfuse の環境変数が正しく設定されている場合、`retrieve`、`generate`、`verify`、`gate`を同じtrace IDへ送信し、flush後にCloud APIで着弾確認します。キー未設定時やキー形式不正時はローカルのみで動きます。2026-07-31に実runとCloud traceの`confirmed`照合を確認済みです。

## ライセンス

MIT License。詳細は [LICENSE](LICENSE) を参照してください。
