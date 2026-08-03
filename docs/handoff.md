# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

5フェーズ納品計画の**Phase 2（ループを閉じる）を完了**。Phase 1で判明していた
「`auto_classified`が行き止まり」という構造的欠落を解消した。

- **状態遷移の実装**：`auto_classified → auto_approved → implemented → verified → active` を
  実装（`src/quality/store.py`の新規メソッド`mark_coverage_candidate_implemented`/
  `verify_coverage_candidate`/`activate_coverage_candidate`、`src/quality/workbench.py`の
  `implement_coverage_candidate`）。改訂ドラフト生成は**LLM不使用**——D役の
  `fact_check.missing_knowledge`（無ければA回答）と出典を機械的にMarkdownセクションとして
  追記するだけ。`activate_coverage_candidate`は状態を直接書き換えず既存の`approve_revision`
  （検証済みの活性化メカニズム）に委譲し、別の弱い経路を作らない設計とした。
- **自動昇格ゲートの実装**：`src/coverage_loop.py`に`is_promotion_eligible`を新設。設計レポートの
  自動採用条件（改善確認・既存PASS非悪化）を`classify_coverage_item`の外側の昇格判定として実装。
  `AUTO_PROMOTABLE_CAUSES`で**`chunking_failure`を明示的に除外**（Phase 1で0/5と測定されたため、
  validation-jobが通っても自動昇格させない）。
- **敵対検証で実バグ2件を発見・修正**：独立エージェントに「この経路は本当に閉じているか、
  抜け道はないか」を敵対検証させ、PoCスクリプトで実際に再現確認された。
  1. `mark_coverage_candidate_implemented`が任意の`revision_id`を無検証で信用しており、
     候補の実際の提案内容と無関係な、既に検証済みの別リビジョンを紐付けてverify/activateを
     素通りさせられた（候補の提案内容がどのリビジョンにも一度も現れないまま`active`になる）。
     修正：リビジョン作成時に`config.coverage_candidate_id`を刻印し紐付け時に一致を検証。
  2. `activate_coverage_candidate`が`approve_revision`呼び出し時に`engine_fingerprint`/
     `structured_digest`を渡しておらず、標準の`/workbench/revisions/{id}/approve`
     エンドポイントと違ってエンジン陳腐化チェック等が無条件でスキップされていた。
     修正：両パラメータを追加し、`QualityWorkbench.activate_coverage_candidate`という
     薄いラッパーを新設して常に両方を自動供給する形にした。
  いずれも回帰テストで固定済み。
- **意図的に対応しなかったもの**：失敗分類の欠落（FP3/FP7/Self-Knowledge）はtaxonomy拡張の
  影響範囲が大きく、分類器の一部（`chunking_failure`）が未検証な状況では時期尚早と判断し
  次回に送った。API/UIからの呼び出し口はPhase 3の範囲として未着手。

詳細は `docs/session-reports/2026-08-03-close-the-loop.md`。

**CI evidence**：commit `e925d11`（実装）・`15a537c`（敵対検証の修正）・`90e0c80`（レポート）。
品質チェック（ruff/mypy/pytest 159件）は全て緑。

## ②今回トラブル

**ループを閉じる実装そのものに、防ぐべき種類の脆弱性を2つ持ち込んでいた**——両方とも
自分から敵対検証にかけて発覚（ユーザー指摘ではなく自発的な検証）。

1. `implement`ステップで「このリビジョンは本当にこの候補から作られたものか」を検証していなかった
   ——ID を受け取ったらその出自を信用するのではなく機械的に照合する、という原則がここでも
   必要だった。
2. 新しい活性化経路（`activate_coverage_candidate`）を作るとき、既存の厳格な経路
   （`/approve`エンドポイント）が渡している引数を全部渡しているか確認しなかった
   ——「既存のメカニズムに委譲する」という設計判断自体は正しかったが、**委譲先に渡す引数の
   完全性**まで検証していなかった。

いずれも `docs/failures.md` に根因・教訓を追記済み。

## ③次回やる事

**Phase 3（画面とAPIの穴を埋め、ゲートを実証する）に進む。** 5フェーズ計画は
`/root/.claude/plans/3-5-100-melodic-plum.md`に保存済み（Phase 1・2の結果を反映し完了マークを
追加済み。コンテナ固有パスのため次回セッションでは失われている可能性が高く、必要ならこの
handoffと計画の要点から作り直すこと）。

1. **【最優先・Phase 3】隔離一覧UIを作る**：API（`GET /workbench/revisions/{id}/coverage-candidates`等）は
   実装済みだが `src/api/static/app.js` にcoverage/quarantineを扱うコードが1行も無い。
   Phase 2で実装した`implement_coverage_candidate`/`verify_coverage_candidate`/
   `activate_coverage_candidate`をFastAPIエンドポイントとして公開する作業も未着手。
2. **`CoverageLoopRequest.questions`の上限30を外す**（`src/api/__init__.py:183`）。
   100問セットが一度に投入できない。
3. **キーガードの漏れを塞ぐ**：`structured-extract`はキー未設定で500になる。
   `comparison-jobs`/`validation-jobs`は202受理後にジョブがerrorに落ちる。
   `/ask` `/runs`と同様に503+明示メッセージへ統一する。
4. **品質ゲートが「鍵を入れれば通る」ことの証明**：strict/standard/exploreの全モードで
   偽検証器を使った合格経路テストを追加する。
5. **統治**（Phase 4）：`main`は`protected: false`のまま（手順0未実施・人の管理者権限が必要）。
   CodeQLが`ci-green`の`needs`に入っていない。pip-audit等の依存脆弱性ゲートが無い。
   Dependabot滞留（#4は2026年12月のv3サポート終了で期限付き）。
6. **ドメイン移植性**（Phase 4）：`src/rag/__init__.py`の`_query_terms`に`"呪術廻戦"`等がハードコード。
   表記ゆれ正規化が`々`の展開のみ（`5mg`/`5 mg`等を別物扱い）。医療ドメインへ移す前に要修正。
7. **答えの無い層12問・誤前提層8問（`data/eval/stratified-eval-set-v1.json`）はまだ未測定**
   （生成が必要なため）。
8. **`chunking_failure`の真のゴールドセットは未構成**：`medium_multi_chunk`層は多段推論の測定には
   有効だが、真のchunking_failure（1事実がチャンク境界で分断され、どちらのチャンクにも完全な
   事実が無い状態）を構成できていない。
9. **失敗分類の欠落（FP3/FP7/Self-Knowledge）**：Phase 2で意図的に見送った。
   taxonomy拡張の要否を改めて検討すること。
