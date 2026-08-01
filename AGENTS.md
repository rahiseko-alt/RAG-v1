# AGENTS.md

このファイルはリポジトリ全体の **デフォルトルール** です。すべての AI コーディングツール
（Claude Code / Codex / Cursor / Antigravity ほか）がこのファイルを参照します。

## このファイルの権威

- グローバル設定や個人の慣習とこのファイルが矛盾する場合、**常にこのファイルを優先** する。
- より深いディレクトリに `AGENTS.md` がある場合、そのディレクトリ配下では **近い方が優先**。
  書いていない項目だけ、このルートを継承する。

## このリポジトリの位置づけ：medguide-rag 専用の開発リポジトリ

**このリポジトリは `medguide-rag` の開発リポジトリである。** かつては AI コーディング用のテンプレ
（雛形）だったが、`codex/public-delivery-workbench` で開発してきた medguide-rag を取り込んだ時点で
テンプレを卒業し、**本開発専用**になった。ここで直接アプリを開発してよい。

- **medguide-rag とは**：登録済みナレッジ文書に対する RAG（検索拡張生成）と、その回答品質を
  検品・改善するワークベンチ。任意ドメインの RAG を強くするための**弱点仮説生成・検証
  ワークフロー**が現在の主題。詳細は `README.md` と `docs/current-plan-report.md`。
- テンプレ由来の仕組み（このファイル・`.claude/` のスキルとエージェント・CI・引継ぎ）は
  そのまま使い続ける。`presets/_TEMPLATE.md` は新しい案件を別リポジトリで始めるときの
  雛形として残してあるだけで、このリポジトリの運用には使わない。

**運用フロー（この順で固定）**：
1. **チェックイン**（`checkin-checkout` スキル）で `docs/handoff.md` と `memory.md` を読む。
2. 作業する。
3. **チェックアウト**（`docs/handoff.md` の上書き＋`docs/failures.md` への追記＋PR をマージまで）。

- **進捗管理は軽量**：進捗・引継ぎは `docs/handoff.md`（①今回実施 ②今回トラブル
  ③次回やる事の3項目、毎回上書き）で管理する。ただし**上書きで消えては困る
  プロジェクトの不変条件は `memory.md`** に置く（下記「セッション開始/終了の儀式」を参照）。

## 普遍ルール（言語・クラウドに依存しない）

どの案件・どの言語でも常に守る、土台のルール。

- **ユーザーへの態度・口調は常に丁寧に保つ（維持厳守）**。敬意ある丁寧語（です・ます調）で応対し、
  乱暴・ぞんざいな言葉遣い（「お前」等のぞんざいな呼称、高圧的・命令口調、見下し表現）は禁止。
  技術的に率直・簡潔であることと、丁寧であることは両立させる（率直さを口調の乱暴さの言い訳にしない）。
- 秘密情報（API キー等）はコミットしない。`.env*` は Git 管理外。
- 変更後は、**その案件で定義されたチェック**（型 / Lint / テスト）を通してからコミットする。
- 既存のコードスタイル・構成に合わせる。**指示のない大規模リファクタリングは禁止**。
- 依存ライブラリの追加やパッケージマネージャーの混在は勝手にやらない（理由を添えて提案する）。
- コミットは小さく、説明的に（1 コミット＝1 目的）。

## 検証の規律（恒久ルール）— 本人採点の禁止

作業した本人が「できました」と言っても、それは証拠にならない。

- **役割分担**：型/Lint/テスト/ビルド/URL到達など**機械で白黒つく所は CI（`.github/workflows`）**
  が判定する。
- **機械で白黒つかない所**（画面が正しい・主要フローが成功する 等）は、必要に応じて**独立サブエージェント**
  （`.claude/agents/independent-verifier.md`）に判定させる。作業した本人以外の立場で、報告を鵜呑みにせず
  自分で再実行・再観察し、敵対的に確認する。
- **evidence は偽造不能な外部事実のみ**：CI の run URL / 変更を実際に含む commit SHA / デプロイ ID /
  第三者が叩ける公開 URL。**「スクショ保存した」「レビューした」等の自己申告は証拠にしない。**
- **承認はシンプル**：変更は PR を出せば **誰でもレビュー/承認してマージ**できる。承認の階層や
  「この変更は許可が要るか」を判定する検問所は用いない。守るのは **CI（型 / Lint / テスト / ビルド）が
  緑であること** だけ。

## Testing instructions（品質チェック）

変更後は、コミット前に必ず次を緑にする。CI（`.github/workflows/ci.yml`）も**同じ内容**を回す。

```bash
uv sync --locked --extra dev        # ← --extra dev は必須（後述）
uv run ruff check src tests         # Lint（対象は src tests に限定。後述）
uv run pytest -q                    # 実テスト（e2e/slow は pytest.ini で既定除外）
uv run uvicorn src.api:app --host 127.0.0.1 --port 8010   # 起動確認（別シェルで curl）
```

- **`--extra dev` を省かない**：`ruff` / `pytest` / `mypy` は
  `[project.optional-dependencies] dev` にある。uv が自動で入れるのは `[dependency-groups]`
  だけなので、`uv sync` だけだと `ruff: command not found` になる。
- **Lint の対象は `src tests`**：`ruff check .` は `notebooks/*.ipynb` で 10 件の指摘が出る
  （未使用 import・セミコロン区切り等）。教材ノートとして意図的な書き方であり、本体コードの
  問題ではないため対象から外している。
- テストも lint も**本物**だけを置く。`echo` による見かけの成功は偽の緑として扱い、禁止。
- 新しいモジュールを足したら、そのモジュールにも実テストを用意する。

### CI から除外しているテスト（黙って除外しない）

`pytest.ini` の `addopts` で `-m "not e2e and not slow"` を既定にしている。理由と、代わりの
手動実行手順は次のとおり。**除外したまま「テストは緑」と報告しない**こと。

| マーカー | 除外理由 | 手動実行 |
|---|---|---|
| `e2e` | Playwright のブラウザバイナリと、`127.0.0.1:8010` で起動済みの uvicorn を要求する | `uv run playwright install --with-deps chromium` → 別シェルで uvicorn を起動 → `uv run pytest tests/e2e -m e2e` |
| `slow` | HuggingFace から埋め込みモデルを取得し、リポジトリ直下に `chroma/` を作る（外部ネットワーク依存） | `uv run pytest -m slow` |

### 現在 `ci-green` に入っていないゲート（既知の欠落・要対応）

「検証の規律」が求める **型チェックと依存脆弱性のゲートが、現時点では CI に入っていない**。
埋めるまでは「型は未検査」と理解して扱うこと。

- **型（mypy）**：`uv run mypy src` は現在 **40 errors in 8 files**（2026-08-01 実測）。大半は
  `WorkbenchStore.get_active_revision()` が `dict | None` を返すのに絞り込まずに添字アクセス
  している箇所。修正は移行作業の範囲を超えるため未着手。**直したうえで CI に追加すること。**
- **依存の脆弱性**：pnpm 時代の `pnpm audit --audit-level moderate` に相当するゲートが無い。
  `pip-audit` 等の導入を検討する（Dependabot は `.github/dependabot.yml` で `uv` を監視中）。

## セッション開始/終了の儀式（引継ぎ）

引継ぎは**2層**で持つ。片方だけ読むと必ず取りこぼすので、**両方読む**こと。

| 層 | ファイル | 性質 | 中身 |
|---|---|---|---|
| 揮発層 | `docs/handoff.md` | 毎回**上書き**（最新1件） | ①今回実施 ②今回トラブル ③次回やる事 |
| 不変層 | `memory.md` | **絶対に消さない**（追記・更新のみ） | 現在地、`[importance:H]` の設計決定・恒久禁止事項、プロジェクトの目的 |
| 失敗ログ | `docs/failures.md` | **append のみ**（消さない） | 日付＋事象＋根因＋教訓 |

- **セッション開始時**：`docs/handoff.md` **と** `memory.md` を読み、要約してからユーザーに提示する。
  `memory.md` が「次セッション必読」として `docs/session-reports/` の最新レポートを指している場合は
  それも読む。
- **セッション終了時**（区切りの良いタイミング）：`docs/handoff.md` の①〜③を今回の内容で上書きし、
  `memory.md` の不変条件に変化があれば**上書きせず追記・更新**して commit & push する。
- **なぜ2層か**：`docs/handoff.md` の3項目はすべて「今回のセッション」の話であり、
  「ユーザー都度承認は禁止」「製品API内で毎回LLM実行しない」のような**プロジェクト不変の設計決定**を
  書く場所が無い。①に書けば次のチェックアウトで消える。だから不変層を分けている。
- 失敗は `docs/failures.md` に積む。`docs/handoff.md` の②は今回セッションの揮発メモに留める。
- 詳細な運用（読む/書く/PR/マージまでの手順）は `checkin-checkout` スキルに従う。

### 「次にやること」が食い違ったときの優先順位

情報源が複数あるため、矛盾したら**上にあるものを正**とする。

1. `docs/session-reports/` の**最新**レポート（「次にやるべきこと」節）
2. `memory.md` の `[importance:H]` 項目
3. `docs/handoff.md` の③
4. `roadmap-state.json` / `roadmap.md` / `docs/progress.md`（更新漏れが起きやすい。**単独で信用しない**）

## 技術スタック（medguide-rag）

このリポジトリのスタックは**確定済み**。勝手に別のスタックを仮定しない。

- クラウド / ホスティング: **無し**（単一PC・ローカル運用。`127.0.0.1` 前提で
  `TrustedHostMiddleware` が許可ホストを絞っている）
- 言語 / ランタイム: **Python 3.12**（`requires-python = ">=3.12,<3.13"`、`uv.lock` は `==3.12.*`）
- フレームワーク: **FastAPI**（`src/api`）＋ **LangChain / LangGraph**（`src/rag`）
- パッケージ / 依存管理: **uv**（`pyproject.toml` ＋ `uv.lock`。依存は全て `==` で完全ピン留め）
- DB / データアクセス: **SQLite**（`src/quality/store.py` のワークベンチ台帳）＋
  **Chroma**（ベクタDB・`chroma/`）。どちらも `.gitignore` 済み
- 埋め込み / 生成: `intfloat/multilingual-e5-small`（多言語埋め込み）／生成は
  `LLM_PROVIDER` で OpenAI / Anthropic を切替
- 認証: **無し**（ローカル専用のため意図的に無効）
- IaC / デプロイ: **無し**（ローカル起動のみ）
- テスト: **pytest**（`tests/`。e2e は Playwright）
- CI: GitHub Actions（`.github/workflows/ci.yml`）
- Lint: **ruff** ／ 型: **mypy**（設定はあるが CI 未接続。「Testing instructions」の既知の欠落を参照）
- 監査ログ: **Langfuse Cloud**（任意連携。環境変数が設定されている時だけ有効）

主要コマンドは「Testing instructions」を参照。CLI は `uv run python -m src.rag.cli "質問"`。

- **CI の中身を変えるとき**：「CI が緑であること」だけは守るが、**その中身（何を実行するか）は
  このプロダクトの実コマンドに合わせて書き換えてよい**。ただし集約ゲートの job 名 `ci-green` は
  branch protection が参照する契約なので**変えない**。上流ジョブを増減させたら `needs:` と
  ゲート内の比較式の**両方**を必ず同時に直すこと（片方だけだと workflow が startup failure に
  なり、`ci-green` の check run が生成されず branch protection が永久に待ち状態になる）。

## 手順0：機械強制の有効化（リポジトリで1回だけ・**未実施**）

リポジトリ自体の統治設定。**2026-08-01 時点で `main` は `protected: false`（未適用）** であることを
GitHub API で確認済み。適用するまで、ルール文には歯が付いていない。

0. **機械強制を有効化（以下は実地検証済みの正確な手順）** —
   既定ブランチ `main` に
   branch protection を掛け「CI が緑でないとマージ不可」にする。**クローンで運ばれない唯一のサーバー側設定**で、
   これを掛けて初めてルール文に歯が付く（未設定だと、赤い PR や AI の auto-merge が main に入り得る）。
   - **手順0-a：リポジトリを Public にする（前提）** — GitHub 無料プランの **Private では branch protection /
     Ruleset は強制されない**（設定画面に「Team org にしないと効かない」旨の警告が出る＝実地で確認済み）。
     公開したくないなら歯止めは掛からないので、代わりに `auto-merge` を無効化し、マージは必ず人手で行う。
   - **手順0-b：`main` に「`ci-green` が緑でないとマージ不可」を掛ける。経路は2つあり、Public ならどちらでも効く**
     （どちらか一方でよい。**GitHub の現行 UI は既定で (A) Rulesets に誘導する**ので、そのまま (A) を使うのが迷いが少ない）。
     - **(A) Rulesets（現行 UI の既定・推奨）**：Settings → **Rules → Rulesets** → 「New ruleset」→「New branch ruleset」。
       **ハマりどころ4点（ここを外すと"作ったのに効かない"状態になる＝実地で確認済み）**：
       1. **Enforcement status を『Active』にする**（既定は **Disabled** のまま。Disabled だと一切適用されない）。
       2. **Target branches → 「Add target」→「Include default branch」を選ぶ**（未設定だと画面上部に
          「does not target any resources / **Applies to 0 targets**」と出て適用されない。1 target = `main` になればOK）。
       3. **「Require status checks to pass」にチェック → 検索欄で `ci-green` を追加** → 緑の「**Create**」で保存。
       4. **「Require a pull request before merging」にもチェックし、Required approvals は `0` のままにする**
          （承認人数を上げない）。**これが無いと、上の3点を掛けても `main` への直 push で CI を丸ごと
          迂回できてしまう**（実地で確認済み＝最重要）。
     - **(B) 従来の Branch protection（代替）**：Settings → 左メニュー **『Branches』** →「Add branch protection rule」→
       **Branch name pattern に `main`**（空だと Create が押せない）→「Require status checks to pass before merging」に
       チェック → 検索欄で **`ci-green`** →「Require a pull request before merging」にもチェック（Required approvals は
       classic API/UIでは 0 を選べないため、代わりに Required approvals を **1** にするか、(A) Rulesets を使う → 一番下の緑「**Create**」。
     - ⚠ **(A)(B) いずれも無料 Private では強制されない**（理由・対処は上記手順0-aを参照）。
   - **手順0-c：Secret Protection / Push protection を有効化する** — Settings → **Security → Code security**
     （旧 Security and analysis）→「Secret Protection」「Push protection」をそれぞれ **Enable**（Public repo なら無料）。
     未有効だと、誤って API キー等をコミットしても検知・ブロックされない（実地で確認済み＝ボタンが「Enable」表示なら未有効）。
     依存の自動更新（Dependabot version updates）は `.github/dependabot.yml` が同梱済み
     （`uv` と `github-actions` を監視）なので追加設定は不要。
   - **エンジニア（任意）**：gh CLI 認証済みなら `bash scripts/setup.sh` で `ci-green` を一括適用も可
     （Public 前提は同じ）。
   - ⚠ GitHub Actions の自動トークン（`GITHUB_TOKEN`）には branch protection を変える権限が無く、workflow
     ボタンでの自動化は不可（過去に試みて startup failure。`docs/failures.md` 参照）。必ず人の管理者権限で行う。
   - ⚠ **適用の順番**：先に CI を書き換えて `ci-green` という名前の check run が**実際に生成された**
     ことを確認してから、必須チェックに指定する。存在しない context 名を先に必須指定すると、PR が
     「Waiting for status to be reported」のまま永久に止まる。
   - ⚠ **`.github/workflows/auto-merge.yml` は現在一時無効化してある**（人手の承認なしに
     `--squash --delete-branch` を実行し、開発履歴が失われるため）。手順0 を適用し、squash ではなく
     マージコミットを作る方式に直してから再有効化すること。

## 配下ごとの調整（オーバーライド）

サブディレクトリに `AGENTS.md` を置くと、そこでルールを上書きできる
（継承の優先順位は冒頭「このファイルの権威」を参照）。現時点で配置しているものは無い。

- 別リポジトリで新しい案件を始めるときの雛形: `presets/_TEMPLATE.md`
  （このリポジトリの運用には使わない）
