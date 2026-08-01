# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

`codex/public-delivery-workbench` で開発してきた **medguide-rag を本リポジトリへ取り込み、
開発土台をこのリポジトリに一本化**した。テンプレを卒業し、medguide-rag 専用の開発リポジトリになった。

着手前に、移行計画を**3視点の敵対検証サブエージェント**（ルール整合性／技術リスク／運用継続性）に
かけ、当初計画の複数の致命的欠陥を潰してから実行した。主な修正：

- **CI コマンドが誤りだった**：`uv sync` は `[project.optional-dependencies] dev` を入れないため
  `ruff`/`pytest` が入らない（`--extra dev` が必須）。さらに `--extra dev` を付けると e2e テストの
  `skipif` が外れて確実に赤くなるため、`pytest.ini` で `-m "not e2e and not slow"` を既定にした。
- **`ruff` は 3 件ではなく 10 件赤**（全て `notebooks/*.ipynb`）。対象を `src tests` に限定した。
- **`ci-green` の契約**：`needs:` と比較式の 2 箇所を同時に直さないと workflow が startup failure に
  なり、check run が生成されず branch protection が永久に待ち状態になる。両方直した。
- **`auto-merge.yml` を一時無効化**：人手の承認なしに `--squash --delete-branch` を実行するため、
  13 コミットの開発履歴と原本ブランチが失われるところだった。
- **`prod-smoke.yml` を削除**：無関係な Vercel デプロイ（cc-v2-web）を検査して緑を出すだけの偽の緑。
- **引継ぎ体系の二重化を解消**：`docs/handoff.md`（3項目・毎回上書き）に `memory.md` の
  `[importance:H]` を畳むと 1 サイクルで消える。2層構造として整理し、`checkin-checkout` スキルの
  チェックイン手順を「両方読む」に変更した（これが無いと `memory.md` を読む機械的トリガが無い）。

その他：`.gitignore` を和集合にして SQLite 台帳・Chroma・venv の混入経路を塞ぎ、`codeql.yml` を
Python へ、`dependabot.yml` を uv へ変更。`roadmap-state.json` の虚偽（実装済みの Phase 3 UI を
未完了と主張していた）を訂正し、`docs/session-reports/README.md` に旧パスの読み替え表を追加した。

PR #9 はマージ済み（`e6b4a4d`、マージコミット方式）。**13 コミットは `main` の祖先として保存**され、
`c6335e8` が到達可能であることを確認した。Dependabot PR は #1・#2・#5〜#8 をクローズし、
**#3 actions/checkout と #4 codeql-action は Python 移行後も有効なので残してある**。

CI 実測（`748a3e3`）：`ci-green` **success**。install 20 秒・pytest 21 秒・uvicorn スモーク 9 秒。
事前に「cold install は 2.8GiB / 5〜12 分」と見積もっていたが、実測は 20 秒で外れた。

## ②今回トラブル

- **この環境の git プロキシは、指定ブランチ以外への push を 403 で拒否する**。そのため
  (a) `archive/codex-public-delivery-workbench` タグを作れず、(b) 旧ブランチ
  `codex/public-delivery-workbench` を削除できなかった。**旧ブランチは手動削除が必要**
  （履歴は `main` の祖先として保存済みなので、削除しても失われない）。
- **CodeQL check が赤のままマージした**（ユーザー判断）。`codeql.yml` に `python` を追加したことで
  Python 本体が初めてスキャンされ、high 16 件・medium 2 件が出た。全件コードを読んで
  **ガード済みコードに対する誤検知**と判断済み（`_resolve_source` の `is_relative_to` 封じ込め、
  `safe_name != source_name` 拒否、`revision_id` は全箇所 `uuid4().hex`）。根拠は PR #9 の
  集約コメントに記載。**アラートの dismiss は未実施**＝Security タブに残っている。
- なお `python` 単独にすると「1 configuration not found」で赤くなる。既定ブランチ側の
  `javascript-typescript` 設定が消えるため。matrix で両方走らせることで解消した。
- **`mypy src` が 34 errors**（`get_active_revision()` の `dict | None` を絞り込まずに添字アクセス
  している箇所が大半）。型ゲートは `ci-green` に入れず「既知の欠落」として AGENTS.md に明記した。

## ③次回やる事

1. **手順0 の適用（ユーザー作業・管理者権限が必要）**。`main` は `protected: false` のまま。
   `ci-green` は実際に生成済み（run 30696743483）なので、Ruleset で必須指定できる状態にある。
   `GITHUB_TOKEN` では設定できないため AI 側では実施不可。
2. **旧ブランチ `codex/public-delivery-workbench` の手動削除**（上記②の理由で自動化できず）。
3. **CodeQL アラート18件の処遇**：誤検知と判断済みなので Security タブから False positive として
   dismiss するか、そのまま残すかを決める。
4. 本題の開発：**coverage-loop の D 判定スキーマに失敗原因分類を追加**
   （`missing_knowledge` / `retrieval_failure` / `generation_failure` / `chunking_failure` /
   `ambiguous_question` / `invalid_A` / `out_of_scope` / `needs_quarantine`）。
   詳細は `docs/session-reports/2026-08-01-coverage-loop-design.md`「次にやるべきこと」。
5. 積み残し：`mypy` の 34 件を直して型ゲートを `ci-green` に追加。依存脆弱性ゲート（`pip-audit` 等）。
6. `auto-merge.yml` の再有効化（手順0 適用後、squash ではなくマージコミットを作る方式に直してから）。
