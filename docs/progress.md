# 進捗ログ

4段階の計画に沿って、セッションごとの進捗をここに追記する。当初計画の原文は [original-plan.md](original-plan.md)（改変禁止・照合用）。

## Stage 1 — Python基礎固め（pandas/numpy）

状態: 完了（2026-07-04）

- [x] 公開医療系データセット（UCI Heart Disease / Cleveland・303件）で前処理・簡易統計・可視化を一通り実施
  - 成果物: [notebooks/01-foundations.ipynb](../notebooks/01-foundations.ipynb)（解説付き）／実行済HTML `notebooks/01-foundations.html`／チャート8点 `notebooks/outputs/`（うち `00-scorecard.png` = 定説照合スコアカード）
  - データ: [data/sample/heart-disease-cleveland.csv](../data/sample/heart-disease-cleveland.csv)（出典・ライセンス・列辞書は data/sample/README.md）
  - 取得→読込→把握（shape/info/describe）→欠損処理（`?`→NaN→dropna）→統計（中央値・最頻値を主役）→可視化（分布/ヒスト/相関/箱ひげ）の流れを通した
  - 図の用語は全て日本語化。**⑦ 医学的定説との照合**節＝データの傾向を医学の定説（AHA/ACC等・出典URL付き・別途サブエージェント調査）と突き合わせ、5項目すべて向き一致を確認（相関≠因果ガード付き）。素人がコードを読まず「結論と常識の照合」で妥当性/異常を判別できる仕組み
  - 可読性再設計（マスター指示）: 照合を主役化。**照合スコアカード図**（matplotlib・PNG＝GitHubでも確実表示）を⑦に新設し、判定を Okabe-Ito 緑バッジ＋✓＋テキストの3点併記（WCAG 1.4.1・色覚/グレースケール対応）。図見出しを Assertion-Evidence（結論文）化、各図直後の照合を1行に凝縮、まとめの重複を⑦ポインタに縮約。生成器 = scratchpad `build_notebook.py`（nbformat）

## Stage 2 — 軽量モデルを1本完走させる

状態: 完了（2026-07-04）

- [x] scikit-learn のロジスティック回帰で心疾患あり/なしの二値分類を1本、学習→評価→簡易チューニングまで通した
  - 成果物: [notebooks/02-baseline-model.ipynb](../notebooks/02-baseline-model.ipynb)（解説付き）／実行済HTML `notebooks/02-baseline-model.html`／新規チャート4点 `notebooks/outputs/08〜11`（`11-coef-scorecard.png` = 係数照合スコアカード）
  - 流れ: 前処理（Stage1再現・297件）→ 特徴量Xと正解y作成（**`num` を除外しデータリーク回避**）→ 層化train/test分割 + `Pipeline(StandardScaler→LogisticRegression)` → 評価（適合率/再現率/F1/正解率/ROC-AUC＋混同行列＋ROC曲線）→ `GridSearchCV` で正則化Cを探索し交差検証で比較
  - 実測: テスト正解率 83%（TN28/FP4/FN6/TP22）・ROC-AUC 0.950。100%でない＝リーク無しを確認。医療文脈で再現率（見逃し防止）重視を明記
  - **⑤ 係数と医学的定説の照合**（核）: 標準化後の係数の符号を定説と突き合わせ、照合8項目中7項目が向き一致。**胸痛タイプ(cp)のみ⚠逆向き**＝教科書定説（典型的狭心症ほど疾患）と Cleveland データの既知の逆転（無症候の紹介バイアス・推測）を健全性チェックが炙り出した教材フックとして解説。判定は Okabe-Ito 緑/橙バッジ＋記号(✓/⚠)＋テキストの3点併記（WCAG 1.4.1）
  - 定説はサブエージェント（research-analyst）が出典付き（AHA/ACC・査読論文・URL・確度）で調査。相関≠因果／`ca` は目的変数と同一検査由来（tautological）／カテゴリの数値扱いは単純化、の但し書き付き
  - 依存追加: venv に scikit-learn 導入（requirements.txt 記載済）。生成器 = scratchpad `build_notebook_stage2.py`（nbformat）。Windows cp932 罠は `sklearn.set_config(display="text")` で回避

## Stage 3 — 登録済みナレッジRAGを構築する

状態: コアRAG＋CLI＋教材ノート＋FastAPI＋透明型RAG UI 完了。ナレッジ設定分離まで実装（2026-07-31）

- [x] 既定の検索対象を `config/knowledge.toml` で管理。本文、出典URL、ライセンス、評価セット、Chroma collection、例示質問をコード外へ分離
- [x] 対象文書を選定: **WHO HEARTS「Healthy-lifestyle counselling」**（30ページ・CC BY-NC-SA 3.0 IGO・再配布可をライセンス検証）。`data/sample/who-hearts-healthy-lifestyle-counselling.pdf`（データカードは data/sample/README.md）
- [x] チャンク分割・埋め込み・Chroma格納: `src/ingest`（pypdf＋RecursiveCharacterTextSplitter・30ページ→71チャンク・出典metadata付き）／`src/rag`（多言語埋め込み intfloat/multilingual-e5-small・Chroma cosine・`chroma/`に永続化）
- [x] LangGraphで検索→生成フロー: `src/rag` の StateGraph（retrieve→generate）。生成は OpenAI / Anthropic を `LLM_PROVIDER` で切替可能。既定は OpenAI（`gpt-5.6-sol`）で、文脈のみ根拠に日本語回答＋引用
- [x] CLIデモ: `src/rag/cli.py`（`python -m src.rag.cli "質問"`）。質問→出典追跡パネル→日本語回答
- [x] 教材ノート: `notebooks/03-rag-walkthrough.ipynb`（解説→コード→出力・出典追跡パネル＝Stage1-2「定説照合」のRAG版）
- [x] 軽量テスト: `tests/test_rag.py`（チャンク化・クロスリンガル検索）3 passed
- [x] FastAPI エンドポイント（`src/api/`）。`GET /health` と `POST /ask` を実装し、Langfuse監査もRAG実行経由で連携
- [x] 透明型RAG UI（`GET /`）。質問、処理工程タイムライン、工程別の目的・入力・処理・出力・判断目安、回答、根拠パネル、検索スコア注意書き、監査ON/OFF表示を実装
- [ ] 教材ノートの nbconvert 実行＋HTML化＋スクショ（生成セルは `.env` の ANTHROPIC_API_KEY 設定時に実行）

### 検証
- ingest: 30ページ→71チャンク・metadata（source/page/chunk_id）正常・英語抽出クリーン
- 埋め込み＋Chroma＋検索: **日本語質問→英語文書のクロスリンガル検索が成立**（「運動はどのくらい」→p.11 physical activity・類似度0.859）
- 生成: 2026-07-30 に OpenAI `gpt-5.6-sol` で CLI 実行し、WHO p.11 を根拠に運動量の推奨回答が生成されることを確認済み。2026-07-31 に既定ナレッジを呪術廻戦Wikipedia要約へ変更
- FastAPI: `GET /health` と `POST /ask` の実uvicorn経由E2Eを確認済み。`POST /ask` は回答、出典、ナレッジ情報、工程詳細を返す

## Stage 4 — 評価ループを移植する

状態: 完了（2026-07-04）

- [x] 評価データセット作成: `data/eval/eval_questions.json`（8問・文書内6＋文書外2・期待付き）
- [x] 評価結果の集計/HTML化: `src/eval`（prepare＝検索でアイテム化／aggregate＝評価者間の中央値・最頻値・pass率・割れ集計／HTMLレポート）。評価者LLMの呼び出し自体はこのモジュール内で完全自動化しておらず、外部で作成した verdict JSON を集計する
- [x] **幻覚検出の実証**: 意図的な誤答を1件混入→独立3評価者が全員「根拠忠実性0」で検出（要確認判定）。文書外の適切な拒否は高評価。pass 7/8・全体中央値2.0
- [x] 評価結果を信頼物レポート化: `reports/who-hearts-eval-report.html`（出典付き）＋実行記録 `reports/eval/`
- [ ] 人手サンプリングとの一致率（評価者満場一致＝割れ0のため未実施・任意）
- 受注証明の手法提案書: `reports/medical-rag-method-proposal.html`（Stage1-4を「確立した手法」として提案・ココナラ医療AI案件向け）

## 監査ログ — Langfuse Cloud 任意連携

状態: 同一trace送信・Cloud着弾確認済み（2026-07-31）

- [x] `src/observability.py` を追加し、`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` が設定されている時だけ Langfuse CallbackHandler を有効化
- [x] `src/rag.ask()` から LangGraph / LangChain 実行時の callback config を渡すように変更
- [x] `.env.example` と README に Langfuse Cloud 用の環境変数を追記
- [x] Langfuse SDK `auth_check=True` を確認
- [x] 実run `25cf3bb75e6d425c82d63785ab25276d` とtrace `d7c8d9a3c3fc4d3fb2d62b70e139a13d`を照合し、Cloud APIの`confirmed`を確認
- [x] `retrieve` / `generate` / `verify` / `gate`を同一trace IDで記録する実装を追加

## Stage 5 — RAG品質改善ワークベンチ

状態: 単一PC向け初期納品範囲を実装（2026-07-31）

- [x] SQLiteへrevision、run、job、event、adjustment、traceを永続化
- [x] 決定論的引用検査と別LLMの主張別・3軸照合
- [x] NG、判断不能、検証障害をfail-closedで出荷停止
- [x] ナレッジ下書き、却下、全件検査、承認、有効化
- [x] before/afterと既存PASS質問の回帰検査
- [x] 4タブUI、質問直下の回答欄、8工程アコーディオン
- [x] 調整台帳とLangfuse着弾poll
- [x] 非エンジニア向け [workbench-guide.md](workbench-guide.md)

## LLMプロバイダ切替 — OpenAI対応

状態: 追加（2026-07-30）

- [x] `LLM_PROVIDER=openai|anthropic` で回答生成モデルを切替可能にした
- [x] OpenAI利用時は `OPENAI_API_KEY` / `OPENAI_MODEL` を使用
- [x] 既定はOpenAI（`gpt-5.6-sol`）
