# medguide-rag

医療ガイドライン文書に対する質問応答システム（RAG）と、その回答品質を複数視点のLLMで自動評価する仕組みを組み合わせたプロジェクトです。

> **現在の状態: スキャフォールド段階。** ディレクトリ構成とドキュメントのみ用意した状態で、RAGパイプライン・モデル学習・評価ループの実装はこれから行います。進捗は [docs/progress.md](docs/progress.md) に記録します。

## 1. これは何をするものか

公開されている医療ガイドライン文書を検索対象にして、「この症状にはどんな対応が推奨されているか」といった質問に対し、根拠となる文書箇所を示しながら回答するシステムです。回答の品質は人手だけでなく、複数の異なる視点を持つLLMに自動で採点させる評価ループでチェックします。

## 2. 作った理由

医療AI企業の機械学習エンジニア職に応募するにあたり、自分の実務経験（LLMを使ったマルチエージェント開発基盤の設計・運用、計測に基づくプロンプトチューニング）を棚卸ししたところ、「Pythonでのデータを使ったモデル開発経験」が実績として提示できないことに気づきました。このプロジェクトは、その不足を埋めるために作っています。既存の強み（複数のLLMに反証させて品質を担保する「検証パネル」という仕組みを本番運用してきた経験）を、医療文書の質問応答という新しい領域に転用できるかどうかを試すのが狙いです。

## 3. 何ができるか

- [x] サンプル文書（WHO HEARTS 生活習慣カウンセリング）に対する質問応答のCLIデモ
  - 実行: `python -m src.rag.cli "成人は運動をどのくらい行うべきですか？"`（要 `.env` の `ANTHROPIC_API_KEY`）
  - 出力: 質問 → 出典追跡パネル（検索チャンクの出典ページ・類似度）→ 文書を根拠にした日本語回答（引用付き）
  - 日本語質問→英語文書のクロスリンガル検索。文書に無い質問は「記載なし」と答え幻覚を抑止
- [x] 教材ノート `notebooks/03-rag-walkthrough.ipynb`（RAGの流れを解説付きで体験）
- [ ] FastAPI経由でローカル起動して質問できるデモ（Stage 3 後続サブステップ）
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
| 評価 | LLM-as-judge（Anthropic Claude API） |
| 基礎データ処理 | pandas / numpy |
| 軽量モデル | scikit-learn / PyTorch |

## 6. 評価結果（Stage4完了後に追記）

- [ ] 評価データセットの件数・作り方
- [ ] LLM-as-judgeスコアの分布
- [ ] 人手サンプリングとの一致率

## 7. 設計で工夫した点

このプロジェクトの核は、RAGそのものより「回答品質をどう継続的に検証するか」の部分です。既存の業務（Claude Codeのマルチエージェント基盤）で、実装物を正当性・セキュリティ・仕様適合・UXの4視点を持つ複数のLLMに反証させ、重大な問題があればコミット前にブロックする仕組みを運用してきました。この考え方を、RAGの回答品質評価（LLM-as-judgeによる自動評価＋人手サンプリングの二段構え）に転用します。詳細は実装後に [docs/architecture.md](docs/architecture.md) に追記します。

## 8. セットアップ手順

```bash
python -m venv venv
source venv/bin/activate  # Windowsは venv\Scripts\activate
pip install -r requirements.txt
```

環境変数はリポジトリ直下に `.env` を作成し、以下を設定してください（`.env` はgitignore対象・コミットしない）。Stage 3・4（RAG・評価）で使用します。

```
ANTHROPIC_API_KEY=
```

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

Stage 2 以降の実行コマンドは、`src/` の実装が進み次第この節に追記します。

## 進捗

4段階の計画で進めています。当初計画の原文は [docs/original-plan.md](docs/original-plan.md)（改変禁止）、進捗は [docs/progress.md](docs/progress.md) に記録しています。

| Stage | 内容 | 状態 |
|---|---|---|
| 1 | Python基礎固め（pandas/numpy） | 完了 |
| 2 | 軽量モデルを1本完走（scikit-learn/PyTorch） | 未着手 |
| 3 | 医療文書RAG構築（LangChain/LangGraph/Chroma） | 未着手 |
| 4 | 評価ループ移植（LLM-as-judge） | 未着手 |

## ライセンス

MIT License。詳細は [LICENSE](LICENSE) を参照してください。
