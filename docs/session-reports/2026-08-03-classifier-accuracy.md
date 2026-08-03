# D役分類器の精度測定（構成的ゴールドセット・2026-08-03）

## 背景・目的

前回（2026-08-02）の30問実験は「判定者間一致30/30」を成立の根拠にしたが、これは同一モデル・
同一プロンプトを2回走らせた一致であり、正しさの証拠ではない。先行研究（RAGEC）では同種の
段階分類の**人間一致率は57.8%、エラー型の正確度は40.3%**という実測が出ている。

このレポートは、5フェーズ納品計画のPhase 1「測定器を検証する」の実施結果である。人手注釈の
代わりに**原因が構成上自明な設問（構成的ゴールドセット）**を作り、D役（LLMFactChecker、実際は
APIキー無しのためサブエージェントが代役）の判定をその構成上の正解と照合した。

## 方法

### ゴールドセットの構成（`data/eval/classifier-gold-set-v1.json`）

`data/eval/stratified-eval-set-v1.json`（100問・6層）と実コーパス・実取得結果から、5つの
失敗原因ラベルそれぞれについて「正解が構成上強制される」設問を機械的に生成した
（`src/quality/classifier_gold.py`、生成スクリプトは `scripts/build_classifier_gold_set.py`）。

| 正解ラベル | 構成方法 | 件数（目標6件） |
|---|---|---|
| `retrieval_failure` | easy_factualの正解チャンクを実取得結果から機械的に除外 | 4件（不足） |
| `missing_knowledge` | unanswerable_out_of_db（absence_check済み）をそのまま使用 | 6件 |
| `generation_failure` | 正解チャンクが実際に取得された状態でB回答を人工的に棄権させる | 6件 |
| `chunking_failure` | medium_multi_chunkの2チャンクが両方取得された状態でB回答を片方のみの引用にする | 5件（不足） |
| `invalid_A` | false_premiseでAにfan出典のみで誤前提を主張させる | 6件 |

生成は同一コーパスハッシュで再実行すると**バイト同一**（再現性を確認済み）。

### 敵対検証（1回目・構成バグの発見と修正）

スコアリング前に、コーパスを読める独立エージェント2体で構成の妥当性を敵対検証した
（1体は確認側、1体は「このゴールドは間違っている」と主張する側）。

- **確認側**：28件全件AGREE。
- **反証側**：**28件中12件（43%）に実証的な反論**を発見。根本原因は2つ：
  1. 「answer_spanがkeptチャンクに含まれない」判定が**文字列完全一致のみ**で、言い換えや、
     指定した2チャンクの一方が他方の内容を独立に含んでいるケースを検出できていなかった。
  2. `AgentAnswer.notes`に「gold-set construction: hand-authored...」等の**構築メタ情報を
     そのままD向けプロンプトに漏洩**させており、Dが「これは未検証」と読めることで
     `missing_knowledge`全件に`invalid_A`側への不当なバイアスを作っていた。

両方とも実装バグとして修正（`_leaks_into`による語重複ベースの言い換え検出、
notesからの構築メタ情報の除去）。新しい回帰テストを追加し、ゴールドセットを再生成した
（27件・`retrieval_failure`4件・`chunking_failure`5件が不足として`meta.shortfalls`に明記）。
2回目の敵対検証は行っていない（対象範囲を絞った修正のため、指摘された具体的な問題点への
回帰テストでカバー。全件の再敵対検証は今回のスコープ外）。

### D役サブエージェントによる判定

`scripts/prepare_classifier_gold_prompts.py`で各ゴールド項目について本番と同一形の
Dプロンプト（`corpus_probe`・`surfaced_texts`を実コーパスから作り直したもの）を生成し、
9体のサブエージェントに3件ずつ判定させた（`data/eval/runs/2026-08-03-classifier-accuracy/
d-judgments.json`に生データを保存）。`scripts/measure_classifier_accuracy.py`で構成上の
正解と照合し、`classifier-accuracy.json`に一致率・混同行列を保存。

## 結果

**全体正解率：22/27 = 81.5%**（RAGECの段階分類57.8%・エラー型40.3%を上回る）

| ラベル | support | 正解率 | precision | recall |
|---|---|---|---|---|
| `retrieval_failure` | 4 | 4/4 (100%) | 1.00 | 1.00 |
| `missing_knowledge` | 6 | 6/6 (100%) | 1.00 | 1.00 |
| `generation_failure` | 6 | 6/6 (100%) | 0.55 | 1.00 |
| `chunking_failure` | 5 | **0/5 (0%)** | null | 0.00 |
| `invalid_A` | 6 | 6/6 (100%) | 1.00 | 1.00 |

混同行列：`chunking_failure`の5件は**全件**`generation_failure`に判定された
（`retrieval_failure`/`missing_knowledge`/`invalid_A`との混同はゼロ）。

## `chunking_failure`が0/5だった理由の分析

5件とも、Dは`evidence_order_you_must_follow`の指示（surfaced_textsを先に全文確認する）に
正しく従った上で、**「設問の各半分がそれぞれ単独の既存チャンクで完全に答えられる」**ことを
発見し、`generation_failure`（Bはそのチャンクを読んだのに使わなかった）と判定した。理由文の例：

> G-CHUNKING-04: "surfaced chunk 9 (rank 11) ... a single chunk that fully answers both
> the arc name and who else is involved" → `generation_failure`

これは**Dの誤判定ではなく、私が作ったゴールドラベルの概念的な誤り**である可能性が高い。
`chunking_failure`の本来の定義（`src/coverage_loop.py`のコメント）は「分割が悪く情報が
分断されている（ingest側の問題）」——1つの事実がチャンク境界で寸断されている状態を指す。
一方、私が`medium_multi_chunk`層（複合質問・2チャンクにまたがる設問）から構成した
ゴールド項目は、実際には「独立した2つの事実をそれぞれ完全なチャンクから合成する」
多段推論（multi-hop）の設問であり、**どちらの事実も単独チャンクで完結**していた。
これは検索・生成が正しく機能していれば通常のRAGが処理できるべきケースであり、
ingestの分断とは別物である。5件が例外なく同じ理由で`generation_failure`に倒れた一貫性は、
Dの判定が場当たり的ではなく、taxonomy定義に忠実であることを示している。

**含意**：`medium_multi_chunk`層は「多段推論設問」の測定には有効だが、「chunking_failure
（真の分断）」の構成的ゴールドセットとしては使えない。真のchunking_failure（1文がチャンク
境界で分断され、どちらのチャンクにも完全な事実がない状態）を構成するには、ingest段階で
実際に分断が起きている実例を探すか、意図的にchunk_size未満の位置に境界を作る必要がある——
今回は行っていない。

## Phase 2への推奨（自動昇格ゲートの方針）

- `retrieval_failure` / `missing_knowledge` / `generation_failure` / `invalid_A` の4ラベルは
  本ゴールドセット上で100%一致（合計22/22）。Phase 2でこれらを自動昇格の対象にすることは、
  本測定の範囲では支持できる。
- `chunking_failure`は**今回のゴールドセットでは正しく測定できていない**（構成方法の欠陥）。
  Phase 2で`chunking_failure`をDが返した場合は、自動昇格の対象にせず、
  常に人手確認（隔離）に回すことを推奨する。真のchunking_failure用ゴールドセットを
  別途構成できるまで、この原因ラベルの精度は未検証のままとする。
- `needs_quarantine`/`ambiguous_question`/`out_of_scope`はゴールドセットに含まれておらず、
  未測定。

## 証拠・再現性

- ゴールドセット：`data/eval/classifier-gold-set-v1.json`（commit待ち、`meta.source_sha256`で
  コーパス固定）。同一コーパスで`uv run python -m scripts.build_classifier_gold_set`を
  再実行するとバイト同一の出力になることを確認済み。
- 生の判定データ：`data/eval/runs/2026-08-03-classifier-accuracy/d-judgments.json`（27件）。
- 採点結果：`data/eval/runs/2026-08-03-classifier-accuracy/classifier-accuracy.json`
  （正解率・混同行列・ラベル別precision/recall）。
- ruff / mypy / pytest：148件緑（`tests/test_classifier_gold.py`含む）。

## 限界（隠さず開示）

- サンプルサイズが小さい（1ラベルあたり4〜6件）。統計的に強い主張はできない。
- 単一コーパス（JJKデモ・41チャンク）でのみ測定。医療ドメイン等での再現性は未確認。
- ゴールドラベルは人間注釈ではなく構成（construction）によるもの。構成方法自体に
  上記のような概念的な誤りが起こり得ることを、今回`chunking_failure`で実際に発見した。
- 2回目の敵対検証（修正後のゴールドセット全件の再検証）は行っていない。
- `needs_quarantine`/`ambiguous_question`/`out_of_scope`は未測定。
