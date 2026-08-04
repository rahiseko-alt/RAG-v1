# Phase 3（画面とAPIの穴を埋め、ゲートを実証する）完了報告

2026-08-03。5フェーズ納品計画（`docs/handoff.md` 参照）のPhase 3を完了した。
Phase 1（測定器の検証）・Phase 2（ループを閉じる）は完了済みで、Phase 3は
「Phase 2で閉じたループを、実際に画面とAPIから使える形にする」ことが主題。

## やったこと

### 項目1：隔離一覧UI（PR #26・マージ済み `16421cb`）

- バックエンド：`GET /workbench/coverage-candidates/{id}`、
  `POST .../implement`、`.../verify`、`.../activate` の4エンドポイントを追加
  （既存の404/409パターンに準拠。`/activate`成功時は`clear_rag_cache()`も呼ぶ）。
- フロントエンド：`index.html`に「隔離一覧」タブ（5枚目）、`app.js`に状態別バッジ・
  状態別アクションボタン（承認/却下/実装/検証/活性化）と台帳連携ロジックを追加。
  既存のCSSクラス・イベント委譲パターンを流用し、新規スタイルは追加していない。
- テスト：API層のパイプラインテスト（`auto_classified→active`の全経路を実HTTPで通す、
  `chunking_failure`が`/verify`で409拒否されることを固定）と、Playwright e2e
  （承認ボタンが実際に`/resolve`を正しいbodyで叩くことをモック越しに証明。
  タブ数が5枚になったことも固定）。

### 項目2〜5：残りのゲート実証（PR #27・マージ済み `35b16ff`）

- **30問上限の撤廃**：`CoverageLoopRequest.questions`のPydantic `max_length`を
  30→200に引き上げ（30という数字は2026-08-02の30問実験の名残で、他にこの数字を
  前提にしたコードは無いことを確認済み）。100問の層別セット
  （`data/eval/stratified-eval-set-v1.json`）が投入できるようになった。
  境界はPydanticモデル単体のテストで直接固定（200件は受理・201件は`ValidationError`）。
- **キーガードの503統一**：`structured-extract`（従来はキー未設定で汎用500に落ちていた）、
  `comparison-jobs`/`validation-jobs`（従来は202受理後、キー未設定がジョブ内部で
  非同期に判明する設計で、しかも`comparison`ジョブは全項目がエラーでも
  `status="passed"`になる——`_evaluate_revision`が項目単位で例外を握り潰すため）に、
  `/ask` `/runs`と同じ503+明示メッセージのガードを追加。
  `structured-extract`は`wb.structured_extractor is None`のときだけガードする
  （テスト用に注入した非LLM実装はそもそもキーを必要としないため、無条件ガードは誤り）。
- **explore モードの合格経路テスト**：strict/standardは既存テストで比較済みだったが、
  explore単体の合格・不合格経路テストが無かったので追加（`tests/test_quality_verifier.py`）。
- **eval集計の層別セット対応**：`src/eval/aggregate()`が`it["in_doc"]`を直接参照して
  層別セットの項目（`in_doc`キーを持たない）で`KeyError`になっていたのを`.get()`に修正し、
  `stratum`/`expected_behavior`のパススルーと`pass_rate_by_stratum`集計を追加。
  `build_report`/`_render_html`側の同種の直接参照は意図的に対象外とした
  （リポジトリ全体を検索してもこの2関数を呼ぶコードが存在せず、影響範囲が無いため）。

## 敵対検証で見つかった実バグ2件（PR #27・修正済み `327b381`）

独立エージェントに「キーガード統一・explore modeテストは本当に主張どおりか」を
敵対検証させたところ、以下2件の実バグが見つかった。

1. **explore modeテストが自分の主張を証明していなかった**：
   `test_explore_mode_passes_with_low_axis_scores_and_fan_only_evidence`は
   「explore modeはauthority_alignmentを要求しない」ことを証明すると謳っていたが、
   使ったクレーム文言がたまたま`FAN_QUALIFIER_PATTERN`に一致し、
   `authority_alignment`が`True`になっていた——つまりそのテストは
   authority_alignment=Falseの経路を一度も通していなかった。
   検証エージェントが`_release_policy`のexploreブランチに実際に
   `and authority_alignment`を追加する回帰を注入して再実行し、
   2件のテストが両方とも通ってしまうことを確認して実証した。
   修正：`test_word_explanation_does_not_count_as_fan_theory_label`と同じ
   非該当文言を使う専用テストケースを追加し、`authority_alignment is False`かつ
   `release_allowed is True`（explore）／`release_allowed is False`（standard）を
   直接アサートするよう分離した。
2. **`/coverage-loop`のキーガードに`rounds>1`の抜け**：
   `run_coverage_loop`（`src/coverage_loop.py`）はラウンド2以降、
   `allow_llm_agents`/`run_knowledge_answerer`の値に関わらず必ず
   LLM質問生成器を呼ぶ（ラウンド0だけが呼び出し元のseed_questionsを使う）。
   エンドポイント側のガードは`allow_llm_agents or run_knowledge_answerer`しか
   見ておらず、両方falseで`rounds=2`以上・キー未設定の場合、生の
   プロバイダエラーで500になっていた（このPRが確立した「503+明示メッセージ」の
   基準から外れる、既存の穴）。修正：ガードに`or request.rounds > 1`を追加。

いずれも回帰テストで固定済み。

## 品質チェック・CI

- `uv run ruff check src tests` / `uv run mypy src`（0 errors）/
  `uv run pytest -q -m "not slow"`（実uvicornサーバー起動下・e2e含む）：
  最終172件全て緑。
- PR #26・#27ともに`ci-green`緑を確認後、マージコミット方式（squashしない）で
  `main`にマージ。

## 対応しなかったもの（意図的・スコープ外）

- `build_report`/`_render_html`の`it["in_doc"]`/`it["context"]`直接参照：
  呼び出し元がリポジトリ内に存在しないため今回は対象外とした。将来、層別セットを
  `build_report`に接続する際は同じ修正が必要になる（潜在的な罠として記録）。
- Phase 4以降（統治・ドメイン移植性・未測定strata・`chunking_failure`ゴールドセット・
  失敗分類の欠落）は未着手。`docs/handoff.md`③に引き継ぐ。

## CI evidence

- PR #26: https://github.com/rahiseko-alt/RAG-v1/pull/26（マージコミット `16421cb9`）
- PR #27: https://github.com/rahiseko-alt/RAG-v1/pull/27（マージコミット `35b16ff5`）
- 実装commit：`72711d8`（隔離一覧UI）・`0304a15`（項目2〜5実装）・
  `327b381`（敵対検証で見つかった2件の修正）
