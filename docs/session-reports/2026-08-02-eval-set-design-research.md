# 評価セット設計のリサーチ — 30問セットは検証の道具として不備がある

- 日付: 2026-08-02
- 発端: 「質問がマニアックすぎる。検証としてやり方がおかしい」というユーザー指摘
- 方法: 3方向で公開情報を調査（評価セットの層別設計 / 評価フレームワークと失敗原因の切り分け / 実在ベンチマークの構成比）
- **出典URLは実際に取得できたページのみ。取得できなかったものは「未確認」と明記してある。**

## 結論

**指摘は正しい。`data/eval/coverage-loop-30-questions.json` は、弱点を測る道具として設計不備がある。** 3点。

1. **難問100%で対照群が無い**ため、飽和した測定値（C-2・C-3 が 10/10 で `missing_knowledge`）から何も読み取れない。
2. **答えの無い問い・誤前提の問いが0%**なので、幻覚抑止と棄権能力を測っていない。
3. **失敗原因の自動分類そのものが未検証**であり、先行研究の実測では同種の分類は6割弱しか当たらない。

## 1. 飽和は既知の設計不良である（フロア効果）

項目反応理論（IRT）をLLMベンチマークに適用した研究に、直接の記述がある。

> item discriminability is weakened by excessively high or low item difficulty … datasets that feature a balanced distribution of difficulty often exhibit higher discriminability, enabling them to more effectively distinguish between models of varying capabilities
> — https://arxiv.org/html/2505.15055v1 （arXivプレプリント）

**難度を極端に振った評価セットは、そもそも差を測る能力（弁別力）を持たない。** 10/10 の飽和はシステムの弱点ではなく、評価セット側の欠陥として説明される。

補強：*Know Your RAG*（https://arxiv.org/html/2411.19710v1 ・arXivプレプリント）は (context, query) に
`fact_single` / `summary` / `reasoning` / `unanswerable` の4ラベルを付けた実験で、
**ラベル間で最良戦略の recall が 4.8%（NaturalQ）〜42%（NewsQA）変動する**と報告している。
つまり**1種類の問いだけで測ると、システム評価そのものが歪む。**

## 2. 実在ベンチマークは「易しい問い」を主力に置いている

| ベンチマーク | 最頻タイプ | 答えの無い問い | 出典 |
|---|---|---|---|
| **CRAG**（ICLR/NeurIPS系, 4,409問） | **最も易しい Simple が 27%**（Simple系で43%、難問系は計24%） | **False Premise 12%** | https://ar5iv.labs.arxiv.org/html/2406.04744 |
| SQuAD 2.0 | — | **dev/test 約50%**（train 33%、全体約27%） | https://arxiv.org/abs/1806.03822 |
| Natural Questions | — | **51%**（実検索ログ由来） | https://aclanthology.org/Q19-1026.pdf |
| MS MARCO | Description 53% | **約35%** | https://github.com/chsasank/MSMARCOV2 |
| MultiHop-RAG | Comparison 33% | **11.78%**（幻覚検知が目的と明記） | https://ar5iv.labs.arxiv.org/html/2401.15391 |
| HotpotQA | bridge 42% / comparison 27% | 意図的には無し（分析で2%） | https://ar5iv.labs.arxiv.org/html/1809.09600 |

Ragas 公式ドキュメントの分布例も **`simple: 0.5 / multi_context: 0.4 / reasoning: 0.1`** と、
単純な問いが過半数（https://docs.ragas.io/en/v0.1.21/concepts/testset_generation.html ・OSS公式文書）。

**我々の30問は 易問0% / 答えの無い問い0% / 誤前提0% / 難問100%** で、実在ベンチマークとほぼ逆の構成だった。

> ⚠ **「易:中:難 = a:b:c」という推奨配分は、査読論文・公式文書のいずれにも存在しなかった。**
> 見つかったのは実測値のレンジと「balanced が望ましい」という定性的主張まで。**数値を断定しないこと。**

## 3. 難度は主観でなく機械的に定義する

- **HotpotQA** は easy / medium / hard を**モデルが解けたかどうか**で切り分けている（easy=ほぼsingle-hop、
  medium=当時のSOTAが解けたmulti-hop、hard=解けなかったmulti-hop）。人手の主観ではない。
- **CRAG** は難易度と**直交する2軸**を持つ：実体の人気度（head/torso/tail）と情報の変化速度
  （real-time/fast/slow/static）。飽和したスコアの中身を分解するための設計。
- **GRADE**（https://aclanthology.org/2025.findings-emnlp.236/ ・査読付きEMNLP 2025 Findings）は難度を
  **推論の深さ（＝生成側の難度）** と **クエリと根拠文書の意味的距離（＝検索側の難度）** の2軸でモデル化し、
  2次元マトリクスのセル単位で誤答率を見る。**我々の「知識の欠落か検索の失敗か」を分離したい目的に直接対応する。**

我々は「マニアック」という主観で難度を決めていた。**既存RAGを一度流し、正解した問いを easy、
外した問いを hard とラベルし直せば、飽和の原因が問い側か実装側かを分離できる。**

## 4. 答えの無い問いは6種類ある

**UAEval4RAG**（https://arxiv.org/pdf/2412.12300 ・査読付きACL 2025, Salesforce Research）の分類：

| # | カテゴリ | 内容 |
|---|---|---|
| 1 | Underspecified | 必要情報が欠けている（「1956年の首相は誰？」＝国が無い） |
| 2 | False-presupposition | 誤った前提に立つ |
| 3 | Nonsensical | 誤字・意味を成さない |
| 4 | Modality-limited | 扱えない入出力形式の要求 |
| 5 | Safety-concerned | 応じると害をなす |
| 6 | **Out-of-Database** | **ドメインは合っているがコーパスに答えが無い** ← 我々の `missing_knowledge` の対照群 |

同論文は我々が直面した困難そのものを明言している。

> rejection often stems from the inability to retrieve relevant context rather than a true understanding that the request should not be fulfilled

**設計上の要点**（SQuAD 2.0）：答えの無い問いは「答えられそうに見える」よう敵対的に作る。
段落に**もっともらしい答え（plausible answer）が存在する**こと。無関係な問いを混ぜても敵対性は生まれない。

**(QA)²**（https://aclanthology.org/2023.acl-long.472/ ・ACL 2023）は、通常の問いと誤前提の問いを
**両方入れる必要がある**と明示している。CREPE（ACL 2023）は自然分布で**25%が誤前提**と報告（半確認）。

## 5. 我々の失敗分類（4種）は粗く、段が1つ欠けている

**Seven Failure Points**（https://arxiv.org/abs/2401.05856 ・査読付きCAIN 2024）との対応：

| FP | 内容 | 我々の分類 |
|---|---|---|
| FP1 Missing Content | 文書から答えられない | `missing_knowledge` ✓ |
| FP2 Missed Top Ranked | 答えはあるが上位に来ない | `retrieval_failure` ✓ |
| **FP3 Not in Context** | **DBからは取得できたが、生成の文脈に入らなかった** | **無し** |
| FP4 Not Extracted | 文脈にあるが抽出できない（ノイズ・矛盾情報） | `generation_failure` ✓ |
| **FP5 Wrong Format** | 指定形式を無視 | **無し** |
| **FP6 Incorrect Specificity** | 具体的すぎ／一般的すぎ | **無し** |
| **FP7 Incomplete** | 文脈にあった情報の一部を落とした | **無し**（`generation_failure` に混ざる） |

さらに **RAGChecker**（https://arxiv.org/html/2408.08067v1 ・査読付きNeurIPS 2024 D&B）の
**Self-Knowledge**（正解だが retrieved context には無い claim＝**検索を使わずモデルの内部知識で当てた**）は
我々の4分類のどこにも入らない。**これを検出しないと、検索の弱点が統計上見えなくなる。**

同じく RAGChecker は **Relevant Noise Sensitivity**（関連chunkがあるのに誤った）と
**Irrelevant Noise Sensitivity**（無関係chunkに引きずられた）を分けている。我々は分けていない。

## 6. 最も重い指摘：失敗原因の自動分類は、最先端でも6割弱しか当たらない

**RAGEC**（https://arxiv.org/html/2510.13975v1 ・arXivプレプリント）は誤答406件を人手注釈して検証した。

| 判定対象 | 一致率 / 正確度 |
|---|---|
| 回答が誤りかどうか | **92.9%** |
| **どの段階で失敗したか** | **57.8%** |
| **エラー型の分類** | **40.3%** |

著者自身が「RAGパイプラインが生む中間出力が非常に複雑であるため、段階の特定は依然として困難」と認めている。

**我々の分類器は人手検証を一度も通していない。** 2026-08-02 の計り直しで得た「判定者間一致 30/30」は、
**同じ材料と同じ手順を与えた2名が同じ結論に至った**という意味しかなく、正しさの証拠ではない。
実際、初回の誤判定を発見したのは**コーパスを読める第三者**だった。

**先行研究が示す標準手順**：
1. 人間2名以上で数十〜数百件を注釈し、**人間同士の一致率（＝上限）を先に測る**
   （MT-Bench: 人間同士81% vs GPT-4 85% / RAGChecker: 人手間 Pearson 63.67–71.91）
2. 自作分類器と人手の一致率を、その上限と比較して報告する
3. ARES（https://aclanthology.org/2024.naacl-long.20/ ・査読付きNAACL 2024）に倣い、
   少数の人手注釈を prediction-powered inference で judge の誤差補正に使う

## 7. 「`retrieval_failure` が0件」はコーパスが小さいことの当然の帰結

**EnterpriseRAG-Bench**（https://arxiv.org/pdf/2605.05253 ）は 5,000〜511,962 文書の5段階で実測し、

> as the corpus grows, top-10 local cosine similarity rises while Recall@10 declines for both BM25 and vector search

と報告している。**小さいコーパスでは Recall が高く出やすく、検索の失敗が観測されない。**
41チャンクで `retrieval_failure` が0件だったのは、システムが強いからではない。

対策：評価用に無関係文書を大量に混ぜて検索難度を上げる、またはコーパスサイズを段階的に変えて指標の変化を見る。
**RGB**（https://ar5iv.labs.arxiv.org/html/2309.01431 ・査読付きAAAI 2024）は正解文書を必ず含めた上で
ノイズ混入率を 0 / 0.2 / 0.4 / 0.6 / 0.8 と振る設計で、**ノイズ比0が事実上の対照群（positive control）**になる。

## 8. LLM-as-a-judge のバイアス対策（我々に無いもの）

- **位置バイアス**：MT-Bench（https://arxiv.org/pdf/2306.05685 ・査読付きNeurIPS 2023 D&B）は
  A/Bの順序を入れ替えて2回呼び、両方で勝った時のみ勝ちとする（不一致は引き分け）。
- **自己選好**：GPT-4 は自分を約10%、Claude-v1 は約25%優遇（ただし論文自身は断定を避けている）。
  **判定に使うモデルと生成に使うモデルが同一なら要注意。**
- **合議**：**PoLL**（https://arxiv.org/pdf/2404.18796 ）は**異なるモデルファミリーの小型モデルを複数**並べる方式で、
  単一の大型judgeより人手相関が高く、コストは7分の1以下、intra-model bias も低減。
  **我々の「同一プロンプトで同一モデルを2回」は、この意味では合議になっていない**（相関した誤りを検出できない）。
- **数値・数学の採点は特に不得手**：GPT-4 は自力で解ける問題でも、提示された誤答に引きずられて誤採点する。

## 9. 合成評価データの限界

*Can we Evaluate RAGs with Synthetic Data?*（https://arxiv.org/html/2508.11758v1 ）：

- **検索設定のチューニングには使える**（ランキング一貫性 Kendall's τ = 0.44〜0.75）
- **生成モデルの比較には使えない**（多くの組合せで人手ベンチと**負の相関**＝選好が逆転）
- 原因：合成質問は「より具体的・技術的」だが実ユーザーは「より一般的・曖昧」／生成に使ったLLMを有利にする文体バイアス／
  **質問・chunk・参照解の表層的な語彙重複が増え、検索課題が実際より簡単に見える**

**我々の30問はLLM（C役）が生成した合成データである。** 弱点仮説が「生成モデルを変えるべき」という結論に
向かう場合、合成データだけを根拠にするのは危険である。

## 次にやるべきこと（この調査からの帰結）

1. **設問セットを作り直す。** 現行30問は「難問」層としてそのまま残し（版管理済みなので破棄しない）、
   下記を足して層別する。**比率は先行研究に推奨値が無いため、実測レンジを参考に決め、根拠を明記すること。**
   - **易しい層**：コーパスのチャンクから構成的に作り、答えの所在が保証される問い（対照群／positive control）
   - **答えの無い層**：Out-of-Database（ドメインは合うがコーパスに無い）を、**答えられそうに見える形で**作る
   - **誤前提の層**：作中に存在しない設定を前提にした問い
   - **表記ゆれ・言い換えの層**：同じ事実を別の言い方で問う
2. **難度ラベルを機械的に付け直す。** 現行RAGを流し、正解＝easy / 不正解＝hard で再ラベルする（HotpotQA方式）。
3. **分類器を人手検証する。** 人間2名で数十件を注釈して上限を測り、自作分類器の一致率をそれと比較する。
   **これをやるまで、弱点分類表の数値を意思決定に使わない。**
4. **失敗分類に FP3（取得したが文脈に入らなかった）と FP7（文脈にあったのに落とした）と
   Self-Knowledge（検索を使わず内部知識で答えた）を足すか、足さない理由を書く。**
5. **`retrieval_failure` を測るなら、無関係文書を混ぜて検索難度を上げる**（RGB のノイズ比方式）。

## この調査自体の限界

- **「易:中:難」の推奨配分、「答えの無い問いを何割入れるか」の推奨値は、どの出典にも無かった。**
  実測レンジ（答えの無い問い 約12%〜51%）のみ。数値を決めるのは我々の判断であり、その根拠を書く責任がある。
- arXiv・ACL Anthology の直接PDFは WebFetch で本文抽出に失敗することが多く、
  `ar5iv.labs.arxiv.org/html/` および `arxiv.org/html/` のHTML版を使った。一部は未確認のまま残っている
  （MHTS の難易度定義、RAGBench の内訳、UAEval4RAG の human agreement の具体値、
  Landis & Koch の kappa 基準の原典）。
- ベンダーブログ（Snowflake は TruLens 保有企業、Galileo、Evidently AI、Google Cloud）は利害関係があるため、
  数値の根拠としては査読論文を優先した。
