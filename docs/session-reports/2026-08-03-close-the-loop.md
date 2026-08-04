# ループを閉じる（Phase 2・2026-08-03）

## 背景・目的

5フェーズ納品計画のPhase 2。Phase 1完了時点で判明していた構造的欠落：`auto_classified`
（追加候補）が`COVERAGE_RESOLVABLE_STATUSES`に含まれず遷移先の無い**行き止まり**であり、
`auto_approved`を読むコードが存在せず、承認してもナレッジ改訂が生成されなかった。DBの
CHECK制約にある`implemented`/`verified`/`active`は書き込むコードが皆無だった。

## 実施内容

### 状態遷移の実装

`auto_classified → auto_approved → implemented → verified → active` を実装。

- `src/quality/store.py`：`COVERAGE_RESOLVABLE_STATUSES`に`auto_classified`を追加（既存の
  `resolve_coverage_candidate`をそのまま再利用できる）。新規メソッド
  `mark_coverage_candidate_implemented`・`verify_coverage_candidate`・
  `activate_coverage_candidate`を追加。`activate_coverage_candidate`は状態を直接書き換えず
  既存の`approve_revision`（検証済みの活性化メカニズム）に委譲し、別の弱い経路を作らない設計。
- `src/quality/workbench.py`：`implement_coverage_candidate`——承認済み候補から改訂ドラフトを
  作る。**LLMを一切使わない**：D役の`fact_check.missing_knowledge`（無ければA回答）と出典を
  機械的にMarkdownセクションとして既存ドキュメント末尾へ追記するだけ。
- `src/coverage_loop.py`：`is_promotion_eligible`——設計レポートの自動採用条件（改善確認・
  既存PASS非悪化）を`classify_coverage_item`の外側の昇格判定として実装。`AUTO_PROMOTABLE_CAUSES`
  で**`chunking_failure`を明示的に除外**（Phase 1で0/5と測定されたため、パスするvalidation-job
  があっても自動昇格させない）。

### 敵対検証で発見・修正した2件の実バグ

実装直後、独立エージェントに「この経路は本当に閉じているか、抜け道はないか」を敵対検証させた
（PoCスクリプトで実際に再現確認済み）。

1. **`mark_coverage_candidate_implemented`が任意の`revision_id`を無検証で信用していた**：
   候補の実際の提案内容と無関係な、既に検証済みの「別の」リビジョンを紐付け、
   verify/activateを素通りさせられた（候補の提案内容がどのリビジョンにも一度も現れないまま
   `active`になる）。修正：リビジョン作成時に`config.coverage_candidate_id`を刻印し、
   紐付け時に一致を検証。他候補が既に同じリビジョンを主張していないかも確認。
2. **`activate_coverage_candidate`が`approve_revision`呼び出し時に`engine_fingerprint`/
   `structured_digest`を渡していなかった**：標準の`/workbench/revisions/{id}/approve`
   エンドポイントは必ず両方を渡すのに、候補経由の活性化だけエンジン設定の陳腐化チェックと
   構造化データのハッシュチェックが無条件でスキップされていた。修正：両パラメータを
   `WorkbenchStore.activate_coverage_candidate`に追加し、`QualityWorkbench.
   activate_coverage_candidate`という薄いラッパーを新設して両方を自動供給する
   ——将来のAPIエンドポイントが「呼び出し側の注意力」ではなく構造的に厳格な経路の上に
   構築されるようにした。

いずれも回帰テストで固定済み。

## 意図的に対応しなかったもの

- **失敗分類の欠落（FP3/FP7/Self-Knowledge）**：設計レポートが挙げていたが、taxonomy拡張は
  D向けプロンプト・分類器双方への影響が大きく、Phase 1で分類器の一部（`chunking_failure`）が
  未検証と判明したばかりの状況で新ラベルを追加するのは時期尚早と判断した。ループを閉じる
  という本フェーズの核心（状態遷移）を優先し、taxonomy拡張はスコープ外として次回に送る。
- **API/UIからの呼び出し口**：`implement_coverage_candidate`等はPythonレベルのメソッドとして
  実装したが、FastAPIエンドポイントへの接続はPhase 3（画面とAPIの穴を埋める）の範囲。

## 証拠・再現性

- commit：`e925d11`（初期実装）・`15a537c`（敵対検証で見つかった2件の修正）。
- テスト：`tests/test_quality_store.py`に状態遷移の完走テスト1本、各状態の前提条件テスト、
  改善未確認時の遮断テスト、`chunking_failure`の昇格拒否テスト、および敵対検証で見つかった
  2件のバグそれぞれの回帰テストを追加。
- `uv run ruff check src tests` / `uv run mypy src` / `uv run pytest -q`：全て緑（159件）。
