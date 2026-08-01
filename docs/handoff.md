# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

**`codex/public-delivery-workbench` の medguide-rag を本リポジトリへ取り込み、開発土台を `main` に
一本化した。** テンプレを卒業し、medguide-rag 専用の開発リポジトリになった。

着手前に移行計画を**3視点の敵対検証サブエージェント**（ルール整合性／技術リスク／運用継続性）に
かけ、実行すれば確実に失敗する誤りを潰してから実行した。潰した主なもの：

- `uv sync` では `ruff`/`pytest` が入らない（`[project.optional-dependencies] dev` は `--extra dev` 必須）
- それを直すと e2e テストの `skipif` が外れて別の赤が出る → `pytest.ini` の `addopts` で既定除外
- `ruff` の赤は記録の 3 件ではなく**実測 10 件**（全て `notebooks/*.ipynb`）→ 対象を `src tests` に限定
- `auto-merge.yml` が `--squash --delete-branch` を自動実行し、13 コミットの履歴と原本ブランチを
  消すところだった → 一時無効化
- `.gitignore` を片側に寄せると SQLite 台帳・Chroma が公開リポに入る経路ができる → 和集合に
- 引継ぎ体系の二重化（`docs/handoff.md` に `memory.md` の `[importance:H]` を畳むと 1 サイクルで消える）
  → 2層構造として整理し、`checkin-checkout` スキルを「両方読む」に変更

**マージ済み**：PR #9（移行・`e6b4a4d`）と PR #16（引継ぎ更新・`e7734e4`）。どちらもマージコミット
方式。13 コミットは `main` の祖先として保存され、`c6335e8` が到達可能であることを確認済み。

**マージ後の確認結果**：

- `main` は `"protected": true`（適用前は `false`）。旧ブランチ `codex/public-delivery-workbench` は
  削除済み（API と `git fetch --prune` の両方で確認）
- **`dependabot.yml` の uv 移行が実際に効いている**：`dependabot/uv/*` の PR が6本自動生成された
  （anthropic / langgraph / sentence-transformers / uvicorn / dev-dependencies、ラベル `python:uv`）。
  移行前は Python 依存が完全に監視外だった
- CI 実測（`748a3e3`）：`ci-green` success。install 20 秒・pytest 21 秒・uvicorn スモーク 9 秒。
  事前見積もり「cold install 2.8GiB / 5〜12 分」は外れた

## ②今回トラブル

- **この環境の git プロキシは、指定ブランチ以外への push を 403 で拒否する**（`docs/failures.md`
  に記録済み）。タグ作成もブランチ削除もできず、ユーザーの手動作業になった。**履歴の保全を
  タグに依存する計画を立てないこと。**
- **CodeQL check が赤のままマージした**（ユーザー判断）。`codeql.yml` に `python` を追加したことで
  Python 本体が初めてスキャンされ、high 16 件・medium 2 件が出た。全件コードを読んで
  **ガード済みコードに対する誤検知**と確認済み（`_resolve_source` の `is_relative_to` 封じ込め、
  `safe_name != source_name` 拒否、`revision_id` は全箇所 `uuid4().hex`、テスト2件は
  URL サニタイズですらないアサーション）。根拠は PR #9 の集約コメントに記載。
  - なお `python` 単独にすると「1 configuration not found」で赤くなる（既定ブランチ側の
    `javascript-typescript` 設定が消えるため）。**matrix で両方走らせて解消**した。
- **未検証が2点残っている**：(a) CodeQL アラートの dismiss 状況は API に読み取り手段が無く確認不可、
  (b) Ruleset の内訳（`Require a pull request before merging` が入っているか）も同様。
  `protected: true` は「Ruleset が Active で main を対象にしている」ことしか示さない。

## ③次回やる事

1. **本題：`coverage-loop` の D 判定スキーマに失敗原因分類を追加する**
   （`missing_knowledge` / `retrieval_failure` / `generation_failure` / `chunking_failure` /
   `ambiguous_question` / `invalid_A` / `out_of_scope` / `needs_quarantine`）。
   敵対検証で「現設計のままでは採用不可」とされた根本原因がここで、後続の台帳・自動採否・
   隔離UIがすべてこの分類に依存する。詳細は
   `docs/session-reports/2026-08-01-coverage-loop-design.md`「次にやるべきこと」の 1〜7。
2. **Dependabot PR 8 本の処理**：`dependabot/uv/*` 6 本＋ `actions/checkout` ＋ `codeql-action`。
   特に `astral-sh/setup-uv 6→7`（#14）は `.github/workflows/ci.yml` を触るので、CI が緑のままかを
   実際の run で確認してからマージすること。
3. **積み残しのゲート**：`mypy src` の 34 件を直して型ゲートを `ci-green` に追加。
   依存脆弱性ゲート（`pip-audit` 等）の導入。どちらも AGENTS.md に「既知の欠落」として明記済み。
4. **`auto-merge.yml` の再有効化**：手順0 適用後、squash ではなくマージコミットを作る方式に
   直してから `on:` を戻す。現在は `workflow_dispatch` のみ。
5. 未検証2点（②参照）の確認：Ruleset に `Require a pull request` が入っているか、
   CodeQL 18 件を dismiss したか。どちらも GitHub の画面で目視するしかない。
