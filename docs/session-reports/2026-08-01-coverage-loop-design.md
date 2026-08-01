# medguide-rag Check-out — 2026-08-01（Coverage Loop / Structured Knowledge）

- branch: `master`
- previous commit before this work: `c6335e8 feat: add quality-gated RAG workbench improvements`
- **次セッションは本ファイルを先頭から Read してから着手すること。**

## 最重要

1. 現在の主題は、呪術廻戦ナレッジそのものではなく、**任意ドメインのRAGを強くするための弱点仮説生成・検証ワークフロー**である。
2. A/B/C/Dループは「ナレッジ不足を確定する装置」ではない。検出できるのは **BがAより弱く見えた差分** であり、原因は `missing_knowledge` だけでなく `retrieval_failure` / `generation_failure` / `chunking_failure` / `invalid_A` / `ambiguous_question` などに分ける必要がある。
3. ユーザー都度承認は採用しない。処理が止まるため。方針は **自動採用 / 自動却下 / 隔離**。隔離だけ、日次・週次・任意タイミングでまとめてユーザー確認する。
4. C役（質問生成）は1人では偏るため、複数役に分ける。現行案は C-1 `因果 x マニアック`、C-2 `因果 x 複数人/組織`、C-3 `条件/例外 x 時系列/比較`。敵対検証により、今後は実ユーザーログ由来・曖昧質問・手順系・エラー系・権限系・no-answer系も追加すべき。
5. 製品API内でA/C/Dを毎回LLM実行する設計は避ける。A/B/Dはサブエージェント、人間、外部ツール、既存ログから注入できる。製品APIは候補化・台帳化・before/after比較を担う。

## 本セッションで実装したこと

### 構造化ナレッジ

- `src/structured_knowledge.py`
  - revision単位の構造化 entity / fact 保存・検索を実装。
  - 日本語検索語の揺れ・理由系クエリ向けにスコアリングを調整。
  - 「虎杖 + 簡易領域 + 習得」のような複合語を、単一語ヒットより上に置く coverage bonus を追加。
- `src/structured_extraction.py`
  - LLMで文書チャンクから汎用構造化 fact/entity を抽出する仕組みを追加。
  - OpenAI structured output のschema制約に詰まったため、最終的には通常JSON生成 + Pydantic validationに変更。
  - evidence文字列/配列の正規化、空IDの安定生成、`不明`主体factの除外を実装。
- `src/quality/workbench.py`
  - `answer_question()` が revision の structured hits を検索し、RAGへ渡す。
  - `extract_revision_structured_records()` で全体またはquery近傍チャンクを構造化抽出できる。
- `src/rag/__init__.py`
  - `structured_hits` を `RAGState` に追加。
  - 構造化hitを通常Document互換に変換し、根拠laneに流す。
  - noisyな構造化factが回答を支配したため、構造化docsは通常テキストdocsの後ろに付ける方針にした。
- `src/api/__init__.py`
  - `POST /workbench/revisions/{revision_id}/structured-records`
  - `GET /workbench/revisions/{revision_id}/structured-search`
  - `POST /workbench/revisions/{revision_id}/structured-extract`
  - `SourceHit` に structured metadata を追加。
- `src/api/static/app.js`
  - UI側で構造化Fact/Entity/Recordラベル、structured score、record idを表示。

### Coverage Loop

- `src/coverage_loop.py`
  - A/B/C/Dループの候補化ロジックを追加。
  - `CoverageQuestion`, `AgentAnswer`, `FactCheckJudgment`, `CoverageLoopItem`, `CoverageLoopResult` を定義。
  - A/B/D回答は外部注入できる。
  - `allow_llm_agents=false` のデフォルトでは A/C/D を製品API内でLLM実行しない。
  - `run_knowledge_answerer=false` のデフォルトでは Bも現行RAG実行せず、既存ログ/サブエージェント結果を注入する。
  - 外部回答がない場合の `MissingExternalAnswerer`、D判定注入用 `ProvidedFactChecker` を追加。
- `src/quality/workbench.py`
  - `run_revision_coverage_loop()` を追加。
  - デフォルトでは `questions`、`external_answers`、`knowledge_answers`、`fact_checks` が必須。
  - 明示的に `allow_llm_agents=true` または `run_knowledge_answerer=true` の時だけ内部LLM/RAGを使う。
- `src/api/__init__.py`
  - `POST /workbench/revisions/{revision_id}/coverage-loop`
  - APIキー確認は `allow_llm_agents` または `run_knowledge_answerer` の時だけ行う。
  - 入力不足は `400`。

## 実験結果

### 10問 coverage loop

1. C役がマニアック質問10問を生成。
2. A役が外部基準回答。
3. B役が現行ナレッジ限定回答。
4. D役がA/B比較。
5. `coverage-loop` APIに10問分投入。

結果:

- total_questions: 10
- add_candidates: 6
- candidate indices: 2, 4, 5, 6, 7, 8
- pass indices: 1, 3, 9, 10

追加候補になった不足:

| No | 不足分類 | 不足内容 |
|---:|---|---|
| 2 | 因果 | 天元同化失敗 -> 呪霊に近い存在化 -> 羂索に利用される -> 死滅回游/結界運用 |
| 4 | 因果/条件 | 甚爾戦後の覚醒 -> 反転術式 -> 無下限常時運用、六眼の低燃費化 |
| 5 | 条件/例外 | 伏魔御厨子の開いた領域、約200m、解/捌の対象別効果 |
| 6 | 条件/例外 | 呪霊操術の取り込み条件、等級差、主従関係呪霊の扱い |
| 7 | 例外/比較 | 秤の大当たり中の自動反転術式と通常反転術式の差 |
| 8 | 因果/前提 | 双子が呪術的に一人扱いされるため真希の天与呪縛が未完成だった前提 |

### 30問質問生成

C役を3人化:

| role | axes | generated |
|---|---|---:|
| C-1 | 因果 x マニアック | 10問 |
| C-2 | 因果 x 複数人/組織 | 10問 |
| C-3 | 条件/例外 x 時系列/比較 | 10問 |

目的は「問いの多様化」と「どの問い型に弱いかの集計」。まだこの30問はA/B/D全実行まではしていない。次に実行するなら、30問をA/B/Dへ流し、弱点分類表を作る。

## 敵対検証の結果

サブエージェントに敵対検証を依頼。結論:

- **修正条件付きで採用可。現設計のまま採用は不可。**
- A/B/C/Dループは「ナレッジ不足検出器」ではなく、**RAG弱点仮説生成・検証ワークフロー**と呼ぶべき。
- Dが「Aが良い、Bが弱い」から直ちに「ナレッジ追加」と判定すると誤検知を量産する。

必須修正:

- D判定を `missing_knowledge` 一択にしない。
- 失敗原因分類:
  - `missing_knowledge`
  - `retrieval_failure`
  - `generation_failure`
  - `chunking_failure`
  - `ambiguous_question`
  - `invalid_A`
  - `out_of_scope`
  - `needs_quarantine`
- A回答には出典URL、出典種別、根拠span、更新日、confidenceを必須化。
- B回答には取得chunk、score、引用箇所、検索順位を必須化。
- D判定は順序入れ替え、複数judge、隔離ルールを入れる。
- before/after比較は固定コーパス版、固定設定、固定評価セットで行う。
- 台帳は `candidate -> auto_classified -> auto_approved / auto_rejected / auto_quarantined -> implemented -> verified -> active` にする。

## 自動運用方針

ユーザー都度関与は無し。

```text
通常候補
-> 自動判定
-> 自動採用 / 自動却下 / 隔離
-> 隔離だけまとめてユーザー確認
```

自動採用条件案:

- Aが複数ソースで支持される。
- Aのsource_typeが `official / primary / reliable_secondary`。
- Dが原因分類を出す。
- 原因が `missing_knowledge / retrieval_failure / chunking_failure / generation_failure` のいずれか。
- before/afterで対象質問が改善。
- 既存PASS質問が悪化しない。

自動却下条件案:

- Aが非公式1件のみ。
- Aが考察・推測。
- A/Bの質問解釈が違う。
- 対象スコープ外。
- 重複候補。
- 出典URLなし。
- 根拠spanなし。

隔離条件案:

- Aは強そうだが公式性が弱い。
- 複数judgeが割れた。
- before/afterで改善したが副作用がある。
- 検索改善かナレッジ追加か分類不能。
- 追加するとナレッジ方針が変わる。

## 次にやるべきこと

1. `coverage-loop` のD判定スキーマを失敗原因分類つきに拡張する。
2. A/B/D入力に evidence metadata を必須化する。
3. coverage台帳をSQLiteに保存する。
4. `auto_approved / auto_rejected / auto_quarantined` の状態遷移を実装する。
5. quarantine一覧API/UIを追加する。ユーザーが見るのは隔離だけ。
6. 30問セットをA/B/Dへ流し、問い型別弱点分類を作る。
7. `missing_knowledge` と `retrieval_failure` を分けるため、B回答時の retrieved chunks / scores / citations を必ず保存する。

## 検証

- `venv\Scripts\python -m pytest tests/test_api.py::test_coverage_loop_marks_external_pass_b_abstention_as_add_candidate -q`
  - PASS
- `venv\Scripts\python -m pytest tests/test_api.py -q`
  - `18 passed, 1 warning`
- `venv\Scripts\python -m pytest -q`
  - `84 passed, 1 warning`
- `pytest.ini` に `testpaths = tests` を追加。理由: runtimeにcloneした外部評価リポジトリ `data/runtime/tool-evals/llm-graph-builder` のpytestまで探索され、製品外テストcollectionで失敗したため。`data/runtime/` はgitignore済みでコミット対象外。

## 注意

- `.env`、APIキー、Langfuse秘密鍵は記録・Git差分に含めない。
- `.claude/` と `docs/portfolio-alignment-inventory.md` は今回の作業外の未追跡ファイル。コミット対象に含めない。
- `data/runtime/tool-evals/llm-graph-builder` はNeo4j LLM Graph Builder評価用にcloneしたruntime領域。コミット対象外。
- 現行APIサーバーは `http://127.0.0.1:8010/`。モデルは健康確認時点で `gpt-5.6-luna`、Langfuse configured。
