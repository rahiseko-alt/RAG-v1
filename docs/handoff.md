# 引継ぎメモ（handoff）

セッションをまたぐ**揮発的な引継ぎメモ**。**このファイルは毎回上書き**（最新1件だけを保持する）。
過去の失敗の蓄積は `docs/failures.md`（append-only・消さない）を見る。
**プロジェクト不変の設計決定・恒久禁止事項は `memory.md`**（絶対に消さない）を見ること。

## ①今回実施

**Phase 3（画面とAPIの穴を埋め、ゲートを実証する）を完了**し、続けて
**リポジトリを公開ポートフォリオとして成立する状態に整え、`AGENTS.md` の手順0（機械強制）を完了**した。

### Phase 3

- **隔離一覧UI**（PR #26 `16421cb`）：`GET .../coverage-candidates/{id}`・`.../implement`・
  `.../verify`・`.../activate` の4エンドポイントと、`index.html`/`app.js` の「隔離一覧」タブ。
- **項目2〜5**（PR #27 `35b16ff`）：`CoverageLoopRequest.questions` の上限 30→200（100問の層別
  セットが投入可能に）、`structured-extract`/`comparison-jobs`/`validation-jobs` のキーガードを
  `/ask` `/runs` と同じ 503+明示メッセージへ統一、explore mode の合格経路テスト追加、
  `src/eval/aggregate()` の層別セット対応。
- **敵対検証で実バグ2件を発見・修正**（`327b381`）：(1) explore mode テストの1つが
  「authority_alignment 不要」を謳いながら、使ったクレーム文言が fan-qualifier パターンに一致し
  その経路を一度も検証していなかった（回帰注入で無検知を実証）。(2) `/coverage-loop` のキーガードが
  `rounds>1` を見落としていた。

### 公開ポートフォリオ化と統治（詳細は `docs/session-reports/2026-08-04-public-portfolio-and-governance.md`）

- **メール流出の停止**：原因は想定と違い、個人メール28件は**全て GitHub 生成のマージコミット**で、
  ローカルの `git config` では防げないものだった。アカウント設定
  （Keep my email addresses private）で塞ぎ、あわせて **CI にコミット衛生ガードを追加**。
  設定前後のマージコミットのメール変化を実測して効果を確認済み。
- **手順0の完了**：Secret Protection / Push protection を有効化。`main` に branch protection を
  適用し `ci-green` を必須化。**赤い PR のマージが実際にブロックされた**ことで、飾りでないことも
  実証された。
- **公開向けの整備**：README の自己矛盾解消と実測値の掲載（未検証項目も明示列挙）、
  memory.md の文体、ローカルパス除去、`AGENTS.md` に「公開リポジトリとしての規律」を新設。
- **Dependabot 8件を全マージ**（期限付きの CodeQL v3→v4 を最優先）。
- **陳腐化した記述を7箇所一掃**（`roadmap-state.json` の mypy「34 errors・未接続」等）。数値は
  依存更新後に測り直した実測値。

**CI evidence**：PR #26 `16421cb` / #27 `35b16ff` / #28 `567e963` / #29 `caa2545` /
#30 `ef97e48` / #31 `f5542a0`、Dependabot 8件。全て `ci-green` 緑でマージコミット方式。
最終確認：ruff pass / mypy 0 errors・18 files / pytest 170 passed・3 deselected。

## ②今回トラブル

**自分で新しいガードを入れるたびに、そのガード自身に穴を作った。** 今回2件とも、実装ではなく
**検証・網羅の側**の抜けだった。

1. **explore mode テストが自分の主張を証明していなかった**（Phase 3）。狙った条件
   （authority_alignment=False）を本当に発生させているか確認せずにテストを書き、フィクスチャの
   文言がたまたま別のパターンに一致して条件が成立していなかった。
2. **コミット衛生ガードが Dependabot PR を全て赤にした**（今回）。許可パターンのローカル部を
   `[A-Za-z0-9._-]` としたが、bot のアドレスは `49699333+dependabot[bot]@users.noreply.github.com`
   で角括弧を含む。Dependabot に触る直前に実データを確認して発覚し、PR #30 で修正。

いずれも `docs/failures.md` に事象・根因・教訓を追記済み。

## ③次回やる事

**Phase 4（ドメイン移植性）に進む。** 統治側（手順0・Dependabot・branch protection）は今回で
完了したため、Phase 4 の残りは移植性と依存監査である。

1. **【最優先・Phase 4】ドメイン用語のハードコードを外に出す**：`src/rag/__init__.py` の
   `_query_terms` の `stop_terms` に `"呪術廻戦"`、`_intent_terms` に `術式/領域/声優/作者` が
   直書きされている。`config/knowledge.toml` へ外出しする。コーパスプローブ
   （`src/coverage_loop.py`）がこれを継承している問題も同時に解消する。
2. **表記ゆれ正規化**：現在は `々` の展開のみ。`5mg`/`5 mg`、`COVID-19`/全角、大小文字が別物に
   なる。医療ドメインでは致命的なので、全角半角・単位前空白・大小文字の正規化を追加する。
3. **既定ナレッジの整合**：製品名は medguide（医療）だが既定コーパスは呪術廻戦。README には
   「ドメイン非依存の検品の仕組みが主題だから」という理由を明記済みだが、1 が終われば
   医療系（同梱済みの WHO HEARTS PDF）を既定にし、呪術廻戦をデモ用プリセットへ降格できる。
4. **依存脆弱性ゲート（pip-audit）を `ci-green` に追加**：`AGENTS.md` が「既知の欠落」と
   明記している唯一の項目。**⚠ 上流ジョブを増やす場合は `needs:` とゲート内の比較式の両方を
   必ず同時に直すこと**（片方だけだと startup failure になり `ci-green` の check run が生成されず、
   branch protection が永久に待ち状態になる）。今回のコミット衛生は、この危険を避けるため
   新規ジョブではなく `quality` ジョブのステップとして追加してある。
5. **CodeQL を `ci-green` の `needs` に追加**（現在は赤でも通る）。
6. **`build_report`/`_render_html`（`src/eval/__init__.py`）の `it["in_doc"]`/`it["context"]`
   直接参照**：現状呼び出し元が無いため実害は無いが、層別セットを接続する際は同じ修正が要る。
7. **答えの無い層12問・誤前提層8問は未測定**（生成が必要）。上限撤廃で100問投入が可能になった。
8. **`chunking_failure` の真のゴールドセットは未構成**（Phase 1 で概念的誤りと判明）。
9. **失敗分類の欠落（FP3/FP7/Self-Knowledge）**：Phase 2・3 で意図的に見送り。要否を再検討する。
10. **Phase 5（納品検証）**：roadmap.md と original-plan.md の二重定義解消、SECURITY.md /
    CONTRIBUTING.md / CHANGELOG.md / NOTICE の新設、SBOM、クリーンclone検証。

### 公開に関する既知の残り（対応済み・要判断ではない）

- **Git 履歴には過去分（個人メール28件・旧記述）が残る。** 消すには履歴書き換えが必要だが、
  ハンドルとメールの文字列が同じで隠す実益が薄く、PR のレビュー履歴を痛めるため**現状維持**と
  判断済み。実施する場合は新規リポジトリではなく `git filter-repo` で同一リポジトリを書き換える。
- **WHO の PDF は CC BY-NC-SA 3.0 IGO（非営利条件）**。現在の非営利ポートフォリオ用途は適合だが、
  商用に転じる場合は条件違反になる。
