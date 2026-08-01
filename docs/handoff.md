# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

`docs/session-reports/2026-08-01-coverage-loop-design.md`「次にやるべきこと」の項目1〜5を実装した。

- **項目1（PR #18・別セッションでマージ済み）**：`coverage-loop` の D 判定に失敗原因分類
  （`missing_knowledge` / `retrieval_failure` / `generation_failure` / `chunking_failure` /
  `ambiguous_question` / `invalid_A` / `out_of_scope` / `needs_quarantine`）を追加。
- **項目2（PR #19）**：`src/coverage_loop.py` の `AgentAnswer` に evidence metadata
  （`EvidenceSource`: url/source_type/span/updated_at、`RetrievedChunk`: chunk_id/score/citation/rank、
  `confidence`）を追加。デフォルト空のオプションフィールドで既存呼び出し元は無変更。
- **項目3〜5（PR #19）**：`src/quality/store.py` に `coverage_candidates` テーブルを追加し、
  `run_revision_coverage_loop()` が各候補を自動保存するようにした（`persist: bool = True` 既定オン）。
  `classify_coverage_item` の disposition を初期状態へマッピング：
  `add_candidate -> auto_classified` / `rejected -> auto_rejected` / `quarantined -> auto_quarantined` /
  `no_gap -> no_gap`。`GET /workbench/revisions/{id}/coverage-candidates`（status フィルタ対応）と
  `POST /workbench/coverage-candidates/{id}/resolve`（`auto_quarantined -> auto_approved/auto_rejected`
  のみ許可）を追加。ローカル `uvicorn` を実際に起動し、coverage-loop → 一覧 → resolve（409になる
  ケース含む）まで curl で手元検証済み。

PR #19 はマージコミット方式でマージ済み（`1bce374`）。CI（`ci-green`）は緑を確認してからマージした。

## ②今回トラブル

無し。ただし設計判断を1つ明記しておく：**`add_candidate` はあえて `auto_classified` 止まりで
`auto_approved` にはしていない。** 設計文書の自動採用条件案は before/after 改善確認（項目6・未実装）
も必須にしており、それを飛ばして自動承認すると「本人採点」と同じ誤りになるため。項目6を実装する
までは、この保守的な挙動を変えないこと。

## ③次回やる事

1. **本題：`docs/session-reports/2026-08-01-coverage-loop-design.md`「次にやるべきこと」の
   項目6・7**：
   - 6: 30問セット（C-1/C-2/C-3、計30問）をA/B/Dへ流し、問い型別弱点分類表を作る。
   - 7: `missing_knowledge` と `retrieval_failure` を分けるため、B回答時の retrieved chunks /
     scores / citations を必ず保存する（`RetrievedChunk` スキーマは項目2で用意済み・配線は未着手）。
   - 7が終わると、before/after改善確認（項目6の実行結果）を使って `auto_classified -> auto_approved`
     の自動昇格ロジックを実装できるようになる。これが今回あえてやらなかった部分。
2. **積み残しのゲート**（`memory.md` からも継続）：`mypy src` の型エラーを直して型ゲートを
   `ci-green` に追加。依存脆弱性ゲート（`pip-audit` 等）の導入。
3. **Dependabot PR の処理**：前回セッションから積み残っている `dependabot/uv/*` 等。
4. **`auto-merge.yml` の再有効化**：手順0 適用後、squash ではなくマージコミットを作る方式に
   直してから `on:` を戻す。現在は `workflow_dispatch` のみ。
5. quarantine 一覧の UI（`src/api/static/app.js`）は今回未着手。API（項目5）のみ実装済み。
