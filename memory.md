# memory.md — medguide-rag エージェント記憶（MISSION + MEMORY 統合）

> 個人ポートフォリオ用の学習プロジェクト（機械学習エンジニア職応募のためのPython ML/DL成果物）。P0-P4 商用ゲートは適用除外。自治憲法 → `CLAUDE.md` / 当初計画（改変禁止）→ `docs/original-plan.md` / 進捗 → `docs/progress.md`。

## 引継ぎレポート（次セッション必読）

@docs/session-reports/2026-07-04-checkout-3.md

> 次セッションは上記レポートを **必ず先に Read** してから着手すること。

## P1: 現在地・引継ぎミッション（絶対に消さない）

> 直近 plan: なし（ポートフォリオ整備フェーズ・コード未編集）

- **現フェーズ: Stage 1-4 完遂。ポートフォリオ戦略＝能力単位の独立カード集（製品でなく"能力"軸でタグ化・応募時は当てはめ3〜5行のみ新規作成）。能力カード8枚（medguide4＋kosespark4）は既存ポートフォリオ `docs/deliverables/development-career-summary-technical.md` 末尾「## 能力カード」節に統合済（フォーマット・置き場・枚数の3未決点は解消済）**
- **[importance:M][2026-07-04] kosespark由来4枚（音声認識/用語補正/動画音声パイプライン/ローカルAI統合）は前回調査ベースで未再検証。案件当てはめ前に kosespark 側で実物確認が要る — plan: なし**
- **[importance:L][2026-07-04] 案件当てはめ3〜5行の作成は実際の募集要項が出た時点で着手（雛形化は不要とマスター確認済） — plan: なし**
- 4段階計画: Stage1 Python基礎[完了] → Stage2 分類[完了] → Stage3 RAG[完了] → Stage4 評価ループ[完了]
- **分岐制御（信頼性の機械強制）**: 信頼物は3層防御（定説→一次URL→独立サブAI check）必須。設計committed（`docs/branch-control-design.md`）。**vibe-base配備はマスター判断（担当AIは配備しない）**

### Stage 3 成果物（完遂済・2026-07-04）
- 題材: WHO HEARTS「Healthy-lifestyle counselling」PDF（`data/sample/`・CC BY-NC-SA 3.0 IGO・再配布可検証済・データカードは data/sample/README.md）
- `src/ingest`（pypdf＋RecursiveCharacterTextSplitter・30p→71チャンク・出典metadata）／`src/rag`（e5多言語埋め込み＋Chroma cosine＋LangGraph retrieve→generate＋ChatAnthropic日本語引用回答）／`src/rag/cli.py`／`notebooks/03-rag-walkthrough.ipynb`／`tests/test_rag.py`（3 passed）
- 核＝**出典追跡パネル**（回答が実在チャンクに根拠づくか目視＝Stage1-2定説照合のRAG版）
- 検証: ingest/埋め込み/Chroma/クロスリンガル検索は自動検証済。**生成はAPIキー未設定のため担当AI(Claude)が生成ノードとして実物チャンクから回答を作り end-to-end実演**（運動/禁煙は出典付き回答・インスリン投与量は「記載なし」で幻覚抑止を実証・マスター確認済）
- 依存: venv導入済（langchain 1.3系・要 langchain-text-splitters）。生成モデル既定 claude-opus-4-8（env `ANTHROPIC_MODEL` で上書き可）。**opus-4-8 は temperature送ると400** → ChatAnthropicに temperature渡さない
- 罠: sklearn/HF cache symlink警告は無害。chroma/・HFキャッシュ・venv・.env は gitignore

### Stage 4 着手時の申し送り（次）
- Stage3 RAGの回答品質を LLM-as-judge で自動評価（`src/eval`）。「根拠忠実性／質問直接性／誤情報なし」の複数視点採点＋人手サンプリングの二段（architecture.md 評価設計思想）
- 評価データセット（自作質問セット vs 既存医療QAベンチ流用）は未決＝Stage4で決定
- 生成の自動実行は .env の ANTHROPIC_API_KEY 設定が前提

### このプロジェクトの真の目的（毎回忘れるな）
1. 今までやらなかった開発（Python ML/DL）を実際にやる
2. **その概要と流れをマスターが理解する**（成果物は「動くコード」だけでなく、マスターが読んで流れを追える教材であること）

→ 各 Stage の成果物は解説付きノート＋実行済HTML化でマスターに「見せる」。判定はマスターが「流れが理解できた」と言えること。

### 【重要・全Stage踏襲】素人が妥当性を判断できる3点セット（マスター指示・2026-07-04確立）
Stage1で確立。**Stage2以降のノートも必ずこの型を踏襲する**:
1. **図・表の用語は日本語**（素人はまず読めることが前提）
2. **結論（データの傾向）と医学的定説の照合**を文章で載せる（向きが一致→分析健全／逆向き→バグを疑える）
3. **定説はサブエージェント（research-analyst等）に出典付きで調査**させる（AHA/ACC・査読論文限定・URL必須・確度明記）
- 狙い: 素人はPython(途中経過)を読めないが、**ゴール=結論から途中経過の妥当性/異常を判別**できる（＝健全性チェック）
- 必須ガード: **相関≠因果**を明記（データは定説を「証明」しない）／医療助言でない旨の注記／定説は争いのない教科書レベルに限定
- Stage1実績: 5項目（年齢/性別/最大心拍数/ST低下/コレステロール）すべて定説と向き一致

### Stage 1 成果物（完遂済）
- `notebooks/01-foundations.ipynb`（解説付き・UCI Heart Disease/Cleveland 303件）。生成器は nbformat スクリプト（旧 scratchpad `build_notebook.py`・再構築時はこれを編集して再生成）
- 実行検証: `jupyter nbconvert --to html --execute` EXIT 0・図8点 `notebooks/outputs/`・日本語フォント(Yu Gothic)描画OK
- 定説照合の可読性再設計済（マスター指示 2026-07-04）: ⑦に**照合スコアカード図** `00-scorecard.png`（matplotlib・PNG＝GitHub公開でも装飾が剥がれない）を主役配置。判定は Okabe-Ito 緑 `#009E73`＋✓＋テキストの3点併記（色単独依存を回避・WCAG 1.4.1）。図見出しは Assertion-Evidence（結論文）化・各図直後の照合は1行凝縮・順序は現状維持（照合は末尾）。→ この見せ方は [[skill-design-from-master-view]] 的に Stage2 以降も踏襲
- 環境: `products/medguide-rag/venv/`（Stage1依存のみ導入・.gitignore済）。Stage2以降(torch/langchain等)は各Stage着手時に追加
- データ: `data/sample/heart-disease-cleveland.csv`（出典UCI ID45・CC BY 4.0・列辞書は data/sample/README.md）

### Stage 2 成果物（完遂済・2026-07-04）
- `notebooks/02-baseline-model.ipynb`（解説付き・実行済HTML化）。生成器は scratchpad `build_notebook_stage2.py`（nbformat・再構築時はこれを編集して再生成）
- ロジスティック回帰で心疾患二値分類を1本完走: 前処理再現→X/y作成（**`num` 除外でリーク回避**）→層化分割+`Pipeline(StandardScaler→LogisticRegression)`→評価（適合率/再現率/F1/正解率/ROC-AUC＋混同行列図08＋ROC曲線図09）→`GridSearchCV`でC探索（図10）
- 実測: テスト正解率83%（TN28/FP4/FN6/TP22）・ROC-AUC 0.950（リーク無し確認）
- **係数照合スコアカード図11**（主役）: 係数符号を定説と動的照合。7/8一致・**cp のみ⚠逆向き**（教科書 vs Cleveland データの既知逆転＝紹介バイアス。健全性チェックが炙り出す教材フック）。緑/橙バッジ＋記号＋文字の3点併記
- venv に scikit-learn 導入済。**cp932 罠**: sklearn の Pipeline HTML repr が estimator.js を cp932 で読み落ちる → `sklearn.set_config(display="text")` で回避（ノート内に埋込）。matplotlib bold 欠落警告は `logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)` で抑制

### Stage 3 着手時の申し送り（次）
- 医療文書RAG。公開医療ガイドライン文書の選定 → チャンク分割・埋め込み・Chroma格納 → LangGraphで検索→生成フロー → CLI/FastAPIデモ（original-plan.md Stage3 参照）
- requirements.txt に langchain/langgraph/langchain-anthropic/chromadb 記載済・venv 未導入 → 着手時 `pip install`
- 教材の型（3点セット）は踏襲だが、RAG は「定説照合」でなく「回答根拠の出典追跡」に発展させる想定（Stage4 評価ループに接続）

## P3: 失敗事例

（なし）

## メモ

- マスター規律「平均値のみ禁止・中央値/最頻値を実数で」→ Stage1ノート統計節に反映済。以降のStageでも踏襲
- Windows cp932 の罠 → ファイルIOは `encoding='utf-8'` 明示（ノート内で実施済）
