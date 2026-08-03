# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

5フェーズ納品計画の**Phase 1（測定器＝D役分類器の人手検証）を完了**。判定不能を減らすための
実装ではなく、判定そのものが正しいかを初めて構成的ゴールドセットで検証した。

- **構成的ゴールドセットの新設**（`data/eval/classifier-gold-set-v1.json`）：`src/quality/
  classifier_gold.py` を新設し、5つの失敗原因ラベル（`retrieval_failure`/`missing_knowledge`/
  `generation_failure`/`chunking_failure`/`invalid_A`）それぞれについて「正解が構成上強制される」
  設問を、実コーパス・実取得結果から機械的に生成した（`scripts/build_classifier_gold_set.py`）。
  人手注釈の代わりに、正解チャンクを機械的に除外する・両チャンクが取得された状態でB回答を
  片方の引用のみにする等の構成方法を使用。同一コーパスハッシュで再実行するとバイト同一
  （再現性確認済み）。
- **敵対検証で構成バグ2件を発見・修正**：コーパスを読める独立エージェント2体（確認側・反証側）
  にゴールドセットの妥当性を敵対検証させたところ、確認側は28件全件AGREEだったが、反証側は
  **28件中12件（43%）に実証的な反論**を発見した。根本原因は2つ：(1) 「answer_spanがkeptチャンクに
  含まれない」判定が文字列完全一致のみで言い換えを検出できていなかった、(2) `AgentAnswer.notes`
  に構築メタ情報（「hand-authored, not live web search」等）をそのまま書いており、それがDプロンプトに
  漏洩して`missing_knowledge`全件に`invalid_A`側への不当なバイアスを作っていた。両方とも実装バグ
  として修正（`_leaks_into`による語重複ベースの言い換え検出を追加、notesから構築メタ情報を除去）。
  回帰テストを追加し、ゴールドセットを27件に再生成（`retrieval_failure`4件・`chunking_failure`5件が
  目標6件に届かず`meta.shortfalls`に明記）。
- **D役サブエージェント9体で27件を判定・採点**：`scripts/prepare_classifier_gold_prompts.py`で
  本番と同一形のDプロンプトを生成し、サブエージェントが3件ずつ判定、`scripts/
  measure_classifier_accuracy.py`で採点。**全体正解率22/27（81.5%）**、RAGECの人間一致率57.8%を
  上回った。`missing_knowledge`/`retrieval_failure`/`generation_failure`/`invalid_A`は
  各100%（22/22）。
- **`chunking_failure`が0/5だった原因を特定**：Dは5件全てを一貫して`generation_failure`と判定し、
  その理由は taxonomy 定義に忠実だった（各チャンクが単独で設問の一部を完結して答えており、
  分断ではなく合成の失敗）。**Dの誤りではなく、私のゴールド構成方法が「多段推論設問」と
  「真のchunking_failure（ingestでの事実分断）」を混同していた**という発見。
  `docs/session-reports/2026-08-03-classifier-accuracy.md` に詳細と、Phase 2への推奨
  （`chunking_failure`は自動昇格させず人手確認に回す）を記載。`FactCheckJudgment`の
  docstringにも測定結果を追記済み。

**CI evidence**：commit `7f04b8d`（構成バグ修正）・`cb0b3ec`（測定結果）。品質チェック
（ruff/mypy/pytest 148件）は全て緑。

## ②今回トラブル

**測定器を作る道具（ゴールドセット生成スクリプト）自体にも、測定対象と同じ種類の欠陥を
2つ持ち込んでいた**——両方とも敵対検証で発覚。

1. **言い換え検出の欠如**：文字列完全一致だけでは言い換えを見逃す、という2026-08-02の教訓
   （語一致プローブの限界）を、今回は自分がゴールドセット構成ロジックで再現していた。
2. **構築メタ情報のプロンプト漏洩**：ゴールド項目のA役回答に「これは構築用の手作りテキストです」
   という注記を書いたところ、それがそのままDへのプロンプトに含まれ、Dの判定を不当に誘導していた。
   証拠を足すときにその証拠が何を意味するか検証せずにプロンプトへ流し込む、というパターンの
   再発（形は違うが2026-08-02の「証跡から言えない結論をD向けプロンプトに書いた」と同種）。

いずれも `docs/failures.md` に追記済み。

## ③次回やる事

**Phase 2（ループを閉じる）に進む。** 5フェーズ計画は `/root/.claude/plans/3-5-100-melodic-plum.md`
に保存済み（Phase 1の結果を反映し完了マークを追加済み。コンテナ固有パスのため次回セッションでは
失われている可能性が高く、必要ならこのhandoffと計画の要点から作り直すこと）。

1. **【最優先・Phase 2】ループを閉じる**：`auto_classified`が`COVERAGE_RESOLVABLE_STATUSES`
   （`src/quality/store.py:48`）に含まれておらず行き止まり。`auto_approved`を読むコードも無く、
   承認してもナレッジ改訂が生成されない。DBのCHECK制約にある`implemented`/`verified`/`active`は
   書き込むコードが皆無。**実装時は、今回`chunking_failure`が未検証と判明したことを反映し、
   `failure_cause == "chunking_failure"`の候補は自動昇格対象から除外し常に人手確認へ回すこと。**
2. **隔離一覧UIを作る**（Phase 3）：API（`GET /workbench/revisions/{id}/coverage-candidates`等）は
   実装済みだが `src/api/static/app.js` にcoverage/quarantineを扱うコードが1行も無い。
3. **`CoverageLoopRequest.questions`の上限30を外す**（`src/api/__init__.py:183`、Phase 3）。
   100問セットが一度に投入できない。
4. **統治**（Phase 4）：`main`は`protected: false`のまま（手順0未実施・人の管理者権限が必要）。
   CodeQLが`ci-green`の`needs`に入っていない。pip-audit等の依存脆弱性ゲートが無い。
   Dependabot滞留（#4は2026年12月のv3サポート終了で期限付き）。
5. **ドメイン移植性**（Phase 4）：`src/rag/__init__.py`の`_query_terms`に`"呪術廻戦"`等がハードコード。
   表記ゆれ正規化が`々`の展開のみ（`5mg`/`5 mg`等を別物扱い）。医療ドメインへ移す前に要修正。
6. **答えの無い層12問・誤前提層8問（`data/eval/stratified-eval-set-v1.json`）はまだ未測定**
   （生成が必要なため）。
7. **`chunking_failure`の真のゴールドセットは未構成**：`medium_multi_chunk`層は多段推論の測定には
   有効だが、真のchunking_failure（1事実がチャンク境界で分断され、どちらのチャンクにも完全な
   事実が無い状態）を構成できていない。ingestが実際に分断した実例を探すか、意図的に
   chunk_size未満の位置に境界を作る構成方法が必要。
