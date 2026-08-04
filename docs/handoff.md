# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

5フェーズ納品計画の**Phase 3（画面とAPIの穴を埋め、ゲートを実証する）を完了**。
Phase 2で閉じたループを、実際に画面とAPIから使える形にした。

- **項目1・隔離一覧UI（PR #26 `16421cb`）**：`GET /workbench/coverage-candidates/{id}`、
  `.../implement`、`.../verify`、`.../activate`の4エンドポイントを追加し、
  `index.html`/`app.js`に「隔離一覧」タブ（状態別バッジ・状態別アクションボタン）を実装。
  APIパイプラインテストとPlaywright e2e（承認ボタンが実際に`/resolve`を叩くことをモック越しに
  証明）で検証済み。
- **項目2〜5（PR #27 `35b16ff`）**：
  - `CoverageLoopRequest.questions`の上限を30→200に引き上げ、100問の層別セット
    （`data/eval/stratified-eval-set-v1.json`）が投入できるようにした。
  - `structured-extract`（従来500）・`comparison-jobs`/`validation-jobs`（従来202受理後に
    ジョブ内部で非同期に失敗——`comparison`ジョブは全項目エラーでも`status="passed"`になる
    設計上の穴があった）に、`/ask` `/runs`と同じ503+明示メッセージのガードを追加。
  - explore modeの合格・不合格経路テストを追加（strict/standardは既存）。
  - `src/eval/aggregate()`が層別セット項目（`in_doc`キー無し）で`KeyError`していたのを修正し、
    `pass_rate_by_stratum`集計を追加。
- **敵対検証で実バグ2件を発見・修正（`327b381`）**：
  1. explore modeテストの1つが「authority_alignment不要」を謳いながら、使ったクレーム文言が
     たまたまfan-qualifierパターンに一致し、その経路を一度も検証していなかった
     （検証エージェントが`_release_policy`に実際に回帰を注入し、テストが検知しないことを実証）。
     非該当文言を使う専用テストケースに分離して修正。
  2. `/coverage-loop`のキーガードが`rounds>1`のケースを見落としていた——ラウンド2以降は
     `allow_llm_agents`/`run_knowledge_answerer`の値に関わらずLLM質問生成器を呼ぶため、
     両方false・キー未設定・`rounds>=2`だと生のプロバイダエラーで500になっていた
     （このPRが確立した503基準から外れる既存の穴）。ガードに`rounds>1`条件を追加して修正。

詳細は `docs/session-reports/2026-08-03-phase3-ui-and-gates.md`。

**CI evidence**：PR #26（`16421cb9`）・PR #27（`35b16ff5`）ともに`ci-green`緑でマージ済み。
品質チェック（ruff/mypy/pytest 172件、実uvicornサーバー起動下でe2e含む）は全て緑。

## ②今回トラブル

**敵対検証で見つかった2件は、どちらも「新しいテスト・新しいガードを書いた直後の
一つ抜け」という同じ形をしていた**——実装そのものではなく、検証・網羅の側に穴があった。

1. explore modeテストで、狙った条件（authority_alignment=False）を本当に発生させているか
   自分で確認せずにテストを書いた。テストが緑になることと、テストが主張どおりの経路を
   通っていることは別——既存の類似テスト（`test_word_explanation_does_not_count_as_fan_theory_label`）
   と同じ文言を使い回すべきだった。
2. `/coverage-loop`の既存ガード（`allow_llm_agents or run_knowledge_answerer`）を見て
   「これで全部か」を確認せず、新しいエンドポイント2つ（comparison/validation-jobs）だけに
   注意が向いていた。ガードを追加する作業では、**同じキー依存の入口が他に無いか
   横断的に洗い出す**（今回は`run_coverage_loop`内部の`rounds`ループを実際に読んで発覚）。

いずれも `docs/failures.md` に根因・教訓を追記済み。

## ③次回やる事

**Phase 4（ドメイン移植性と統治）に進む。** 5フェーズ計画は
`/root/.claude/plans/3-5-100-melodic-plum.md`に保存済み（Phase 1〜3の結果を反映し完了マークを
追加済み。コンテナ固有パスのため次回セッションでは失われている可能性が高く、必要ならこの
handoffと計画の要点から作り直すこと）。

1. **【Phase 4】ドメイン移植性**：`src/rag/__init__.py`の`_query_terms`/`_intent_terms`に
   `"呪術廻戦"`等がハードコード（`config/knowledge.toml`へ外出しが必要）。表記ゆれ正規化が
   `々`の展開のみ（`5mg`/`5 mg`、`COVID-19`/全角COVID-19等を別物扱い）。既定ナレッジが
   医療系（README/製品名）と呪術廻戦（実データ）で食い違っている。
2. **【Phase 4】統治**：`main`は`protected: false`のまま（手順0未実施・人の管理者権限が必要）。
   CodeQLが`ci-green`の`needs`に入っていない。pip-audit等の依存脆弱性ゲートが無い。
   `auto-merge.yml`は`--squash --delete-branch`のまま無効化されているだけ
   （マージコミット方式に直すか削除するか未決）。Dependabot滞留
   （#4は2026年12月のv3サポート終了で期限付き・最優先）。
3. **`build_report`/`_render_html`（`src/eval/__init__.py`）の`it["in_doc"]`/`it["context"]`
   直接参照は今回対象外にした**：現状呼び出し元が無いため実害は無いが、将来層別セットを
   `build_report`に接続する際は同じ修正が必要になる（Phase 3セッションレポートに記録済み）。
4. **答えの無い層12問・誤前提層8問（`data/eval/stratified-eval-set-v1.json`）はまだ未測定**
   （生成が必要なため）。上限撤廃で100問セットは投入可能になったので、次はこの実測を検討できる。
5. **`chunking_failure`の真のゴールドセットは未構成**：`medium_multi_chunk`層は多段推論の測定には
   有効だが、真のchunking_failure（1事実がチャンク境界で分断され、どちらのチャンクにも完全な
   事実が無い状態）を構成できていない。
6. **失敗分類の欠落（FP3/FP7/Self-Knowledge）**：Phase 2・3で意図的に見送った。
   taxonomy拡張の要否を改めて検討すること。
7. **Phase 5（納品検証）**：roadmap.md/original-plan.mdの二重定義解消、陳腐化した記述の一掃、
   README是正、SECURITY.md等の商用ハイジーン成果物、クリーンclone検証。Phase 1〜4完了後。
