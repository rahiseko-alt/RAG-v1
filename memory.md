# memory.md — medguide-rag エージェント記憶（MISSION + MEMORY 統合）

> 個人ポートフォリオ用の学習プロジェクト（機械学習エンジニア職応募のためのPython ML/DL成果物）。P0-P4 商用ゲートは適用除外。自治憲法 → `CLAUDE.md` / 当初計画（改変禁止）→ `docs/original-plan.md` / 進捗 → `docs/progress.md`。

## 引継ぎレポート（次セッション必読）

@docs/session-reports/2026-08-02-coverage-loop-30q-run.md
@docs/session-reports/2026-08-02-eval-set-design-research.md
@docs/session-reports/2026-08-03-classifier-accuracy.md
@docs/session-reports/2026-08-03-close-the-loop.md

> 次セッションは上記4本を **必ず先に Read** してから着手すること
> （coverage-loop-30q-runは末尾の「計り直し（3回目）」節が最終結果。冒頭の結論は覆っている。
> classifier-accuracyが判定器の精度検証の最終結果で5フェーズ計画Phase 1の成果、
> close-the-loopがPhase 2の成果）。

## P1: 現在地・引継ぎミッション（絶対に消さない）

> 直近 plan: 5フェーズ納品計画のPhase 1（D役分類器の人手検証）・Phase 2（ループを閉じる）が
> 完了した。次はPhase 3（画面とAPIの穴を埋める）。5フェーズの計画ファイルはコンテナ固有パス
> （`/root/.claude/plans/`）のため次回セッションでは失われている可能性が高い。要点は
> `docs/handoff.md` ③に転記済み。次は `docs/handoff.md` を読むこと。

- **[importance:H][2026-08-03] Phase 1（D役分類器の人手検証）完了。構成的ゴールドセット
  （`data/eval/classifier-gold-set-v1.json`・27件・`src/quality/classifier_gold.py`で生成）を
  D役サブエージェント9体に判定させ、全体正解率22/27（81.5%）——RAGECの人間一致率57.8%を上回った。
  `missing_knowledge`/`retrieval_failure`/`generation_failure`/`invalid_A`は各100%（22/22）。
  **`chunking_failure`は0/5——全件`generation_failure`に判定され、Dの理由付けは taxonomy 定義に
  忠実（各チャンクが単独で設問の一部を完結して答えていた＝分断ではなく合成の失敗）。原因は
  ゴールド構成方法が「多段推論設問」と「真のchunking_failure（ingestでの事実分断）」を混同して
  いたこと。`chunking_failure`は自動昇格対象から除外し人手確認へ回すこと（Phase 2実装時に反映）。**
  詳細は `docs/session-reports/2026-08-03-classifier-accuracy.md`。
- **[importance:H][2026-08-03] ゴールドセット構成でも「言い換え検出の欠如」「証拠を足すときの
  検証不足」という2026-08-02と同種の失敗を再現した。文字列完全一致だけの absence 判定は言い換えを
  見逃す（`_leaks_into`で語重複ベースの検出に修正）。`AgentAnswer.notes`に構築メタ情報
  （「hand-authored」等）を書くと、それがそのままD向けプロンプトに漏洩し判定を誘導する
  （notesから構築メタ情報を除去）。**証拠・入力データに何を書いても、それがプロンプトへ
  流れる経路がある限りDの判定に影響する——「監査用のメモのつもり」で書いた文字列でも
  漏洩経路を確認すること。**
- **[importance:H][2026-08-03] Phase 2（ループを閉じる）完了。`auto_classified → auto_approved
  → implemented → verified → active`を実装。改訂ドラフト生成はLLM不使用（D役の
  `fact_check.missing_knowledge`と出典を機械的に追記するのみ）。自動昇格ゲート
  `is_promotion_eligible`（`src/coverage_loop.py`）で`chunking_failure`を明示的に除外
  （Phase 1で0/5と測定されたため）。**独立エージェントの敵対検証で実バグ2件を発見・修正**：
  (1) `mark_coverage_candidate_implemented`が任意の`revision_id`を無検証で信用しており、
  無関係な検証済みリビジョンを紐付けてverify/activateを素通りさせられた
  （`config.coverage_candidate_id`の刻印・照合で修正）。
  (2) `activate_coverage_candidate`が`approve_revision`に`engine_fingerprint`/
  `structured_digest`を渡しておらず、標準の`/approve`エンドポイントと違ってエンジン陳腐化
  チェックが無条件でスキップされていた（両パラメータを渡す`QualityWorkbench.
  activate_coverage_candidate`ラッパーで修正）。**「既存の厳格な経路に委譲する」という設計
  判断だけでは不十分で、委譲先に渡す引数の完全性まで検証する必要がある。**
  失敗分類の欠落（FP3/FP7/Self-Knowledge）は意図的に見送った（分類器の一部が未検証な状況では
  taxonomy拡張は時期尚早）。詳細は `docs/session-reports/2026-08-03-close-the-loop.md`。**
- **[importance:H][2026-08-02] 項目6（30問をA/B/Dへ流して弱点分類表を作る）は完了した（3周目で成立）。
  1周目は24/30問が判定不能、2周目でコーパス不在プローブを入れたら判定不能は0件になったが
  8件中2件が誤判定（正しい判定を壊していた）、3周目で証跡を見る順序を固定して判定者間一致30/30・
  分類表が成立。詳細は `docs/session-reports/2026-08-02-coverage-loop-30q-run.md`。**
- **[importance:H][2026-08-02] 30問の評価セットは「難問100%・対照群0%・答えの無い問い0%」という
  設計不備があった（ユーザー指摘で発覚）。実在ベンチマークとの比較調査（`docs/session-reports/
  2026-08-02-eval-set-design-research.md`）を経て、`data/eval/stratified-eval-set-v1.json`
  （100問・6層：易問/中間/難問/言い換え/答えの無い問い/誤前提）を新設。正解チャンクを持つ50問で
  LLM無しの機械的な検索評価が可能になった（`scripts/eval_set_retrieval_check.py`）。
  難問だけで固めた評価セットは弁別力を失う（フロア効果）— 評価セットを作るときは必ず対照群を含めること。**
- **[importance:H][2026-08-02・2026-08-03に検証完了] 判定器（D役の失敗原因分類）は一度も
  人手検証されていなかった。先行研究（RAGEC）の実測では同種の段階分類の人間一致率は57.8%、
  エラー型の正確度は40.3%。「判定者間一致30/30」は同一モデル・同一プロンプトを2回走らせた結果
  であり、正しさの証拠ではない（PoLLの言う合議＝異なるモデルファミリーの複数judge、には
  なっていない）。**2026-08-03に構成的ゴールドセットでの検証を実施し、全体81.5%（RAGEC比で
  良好）だが `chunking_failure` ラベルのみ未検証と判明した——詳細は上の2026-08-03エントリと
  `docs/session-reports/2026-08-03-classifier-accuracy.md`。「弱点分類表の数値を意思決定に使う
  前に検証必須」という制約は `chunking_failure` については依然として有効。**
- **[importance:H][2026-08-02・2026-08-03に解消] ループが閉じていなかった。`auto_classified`
  （追加候補）は`COVERAGE_RESOLVABLE_STATUSES`（`src/quality/store.py`）に含まれず遷移先が無い
  行き止まりで、`auto_approved`を読むコードも存在しなかった。**2026-08-03のPhase 2で
  `auto_classified → auto_approved → implemented → verified → active`を実装し解消した**
  （`WorkbenchStore.mark_coverage_candidate_implemented`/`verify_coverage_candidate`/
  `activate_coverage_candidate`、`QualityWorkbench.implement_coverage_candidate`）。
  詳細は `docs/session-reports/2026-08-03-close-the-loop.md`。
  **ただし隔離一覧UIは依然未着手**——APIは実装済みだが`src/api/static/app.js`に
  coverage/quarantineを扱うUIコードが1行も無い（Phase 3の範囲）。**
- **[importance:H][2026-08-02] 品質ゲートの決定論チェックが、部分回答＋定型留保文
  （「〜は、提供された抜粋からは特定できません」で終わる文）に引用を要求し、最も誠実な回答を
  25/30問で出荷停止していた。留保文のみ引用義務を免除するよう `src/quality/verifier.py` を修正済み
  （`is_reservation_sentence`）。ただし留保文を含んでも他に無引用の主張があれば従来どおり差し止まる
  （抜け道ではない）ことをテストで固定してある。**
- **[importance:H][2026-08-01] 項目7（B回答の取得証跡の配線）は実装済み（PR #22）。**
- **[importance:H][2026-08-02] 取得証跡（`RetrievedChunk`）が積極的に証明できるのは一方向だけ：surfaced chunk が事実を含むのに B が失敗 → `generation_failure`。「surfaced chunk に事実が無い」ことは `missing_knowledge` / `retrieval_failure` / `chunking_failure` のいずれとも等しく整合し、D はコーパスを渡されないため区別できない。証跡だけから `missing_knowledge` を選ばせない（`needs_quarantine` に落とす）。この飛躍を一度自分で持ち込んで指摘された（`docs/failures.md` 2026-08-02）。**
- **[importance:H][2026-08-02] 実験の入力（設問セット・固定パラメータ）は必ず版管理下のファイルに置く。before/after 比較は固定評価セットが前提で、毎回生成し直すと両側が比較不能になる。前回30問セットをレポート要約だけで済ませて失った（`docs/failures.md` 2026-08-02）。**
- **[importance:H][2026-08-02] 型ゲート（`uv run mypy src`）は `ci-green` に接続済み・エラー 0。0 を維持すること。`type: ignore` は `src/rag/__init__.py` の 2 箇所のみで、langchain の pydantic 別名との食い違い（実行時は正しい）。`ignore[call-arg]` は呼び出し全体に掛かるため、その穴は `tests/test_rag.py::test_langchain_constructor_kwargs_still_exist` が埋めている。**このテストを消さないこと。** 詳細は `AGENTS.md`「型ゲート（mypy）」節。**
- **[importance:H][2026-08-01] ユーザー都度承認は禁止方針。処理が止まるため。通常候補は自動採用/自動却下/隔離に分岐し、隔離だけ日次・週次・任意タイミングでまとめてユーザー確認する。**
- **[importance:H][2026-08-01] 製品API内でA/C/Dを毎回LLM実行しない。A/B/D結果はサブエージェント、人間、外部ツール、既存ログから注入可能にする。製品APIは候補化・台帳化・before/after比較を担う。**
- **[importance:M][2026-08-01] C役は3人化済み設計: C-1 因果xマニアック、C-2 因果x複数人/組織、C-3 条件/例外x時系列/比較。敵対検証により実ユーザーログ由来・曖昧質問・手順系・エラー系・権限系・no-answer系も追加すべき。**
- **[importance:M][2026-08-01] 10問coverage loop実験では10問中6件が追加候補。弱点は情報量だけでなく、因果の橋・条件・例外・比較・前提設定の不足。30問質問生成までは完了、A/B/D全実行は未完。**

- **現フェーズ: Stage 1-4 完遂。ポートフォリオ戦略＝能力単位の独立カード集（製品でなく"能力"軸でタグ化・応募時は当てはめ3〜5行のみ新規作成）。能力カード8枚（medguide4＋kosespark4）は既存ポートフォリオ `docs/deliverables/development-career-summary-technical.md` 末尾「## 能力カード」節に統合済（フォーマット・置き場・枚数の3未決点は解消済）。**⚠ この統合先は分割元の vibe-base 側にあり、本リポジトリには存在しない**（本リポには `docs/capability-cards.html` の閲覧ビューのみ）**
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
- 実行検証: `jupyter nbconvert --to html --execute` EXIT 0・図8点・日本語フォント(Yu Gothic)描画OK
  - 図の実体は **`notebooks/01-foundations.html` に base64 で埋め込み済み**（追跡下）。単体 PNG を置く
    `notebooks/outputs/` は `.gitignore` 対象で**リポジトリには無い**（要るならノートを再実行して生成）
- 定説照合の可読性再設計済（マスター指示 2026-07-04）: ⑦に**照合スコアカード図** `00-scorecard.png`（matplotlib・PNG＝GitHub公開でも装飾が剥がれない）を主役配置。判定は Okabe-Ito 緑 `#009E73`＋✓＋テキストの3点併記（色単独依存を回避・WCAG 1.4.1）。図見出しは Assertion-Evidence（結論文）化・各図直後の照合は1行凝縮・順序は現状維持（照合は末尾）。→ この見せ方は [[skill-design-from-master-view]] 的に Stage2 以降も踏襲
- 環境: リポジトリ直下の `venv/`（.gitignore済。当時のパスは `products/medguide-rag/venv/`）。現在の依存管理は uv（`uv sync --locked --extra dev`）
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
