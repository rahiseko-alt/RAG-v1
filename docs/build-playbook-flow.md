# RAG Quality Workbench Build Playbook

ここまでの議論と実装で見えてきた、RAG品質改善ワークベンチを構築する手順と勘所をまとめる。

目的は、特定ナレッジを作り込むことではない。任意ドメインで、RAGがどの問い型に弱いかを発見し、原因を分類し、止まらず改善を回す仕組みを作ること。

## 1. 全体構築フロー

```mermaid
flowchart TD
    A["1. 対象業務を決める<br/>何に答えるRAGか<br/>何には答えないRAGか"] --> B["2. ナレッジ境界を決める<br/>公式/社内/二次情報/考察を分類<br/>出典ルールを決める"]
    B --> C["3. 最小RAGを作る<br/>ingest -> chunk -> embed -> retrieve -> generate"]
    C --> D["4. 透明化UIを作る<br/>質問直下に回答<br/>根拠/工程/判定を見える化"]
    D --> E["5. 回答ゲートを作る<br/>機械検査 + 別LLM照合<br/>NG/不明/障害は出荷停止"]
    E --> F["6. 版管理を作る<br/>revision / SHA / active / draft / reject"]
    F --> G["7. before/afterを作る<br/>同一条件で修正前後を比較"]
    G --> H["8. Coverage Loopを作る<br/>A/B/C/Dで弱点候補を発見"]
    H --> I["9. 原因分類を作る<br/>ナレッジ不足だけにしない<br/>検索/生成/chunk/A不正も分ける"]
    I --> J["10. 自動運用を作る<br/>auto approve / reject / quarantine"]
    J --> K["11. 隔離レビューを作る<br/>ユーザーは隔離だけまとめて確認"]
    K --> L["12. 回帰検査で有効化<br/>対象改善 + 既存PASS悪化なし"]
```

## 2. 勘所つき詳細フロー

```mermaid
flowchart TD
    A["対象業務定義"] --> A1{"スコープは明確か?"}
    A1 -->|NO| A2["先にno-answer範囲を決める<br/>答えない質問も評価対象にする"]
    A1 -->|YES| B["ナレッジ設計"]
    A2 --> B

    B --> B1{"出典の強さを分類したか?"}
    B1 -->|NO| B2["source_typeを定義<br/>official / internal / primary / secondary / community / speculation"]
    B1 -->|YES| C["RAG基盤"]
    B2 --> C

    C --> C1{"検索根拠を説明できるか?"}
    C1 -->|NO| C2["chunk_id / source / score / snippet を必ず保存"]
    C1 -->|YES| D["回答ゲート"]
    C2 --> D

    D --> D1{"NG回答が漏れないか?"}
    D1 -->|NO| D2["fail-closed化<br/>判断不能・timeout・JSON破損もblocked"]
    D1 -->|YES| E["透明化UI"]
    D2 --> E

    E --> E1{"何をしたか見えるか?"}
    E1 -->|NO| E2["工程ごとに目的/入力/処理/出力/判断基準を保存"]
    E1 -->|YES| F["評価質問設計"]
    E2 --> F

    F --> F1{"質問役が偏っていないか?"}
    F1 -->|YES| F2["C役を複数軸に分ける<br/>因果/条件/例外/比較/曖昧/no-answer"]
    F1 -->|NO| G["Coverage Loop"]
    F2 --> G

    G --> G1{"Bの失敗原因を分けたか?"}
    G1 -->|NO| G2["missing_knowledge / retrieval_failure / generation_failure / chunking_failure / invalid_A へ分類"]
    G1 -->|YES| H["改善候補処理"]
    G2 --> H

    H --> H1{"ユーザー承認待ちで止まるか?"}
    H1 -->|YES| H2["自動採用/自動却下/隔離へ分岐<br/>隔離だけまとめて確認"]
    H1 -->|NO| I["before/after"]
    H2 --> I

    I --> I1{"改善と副作用を同時に見たか?"}
    I1 -->|NO| I2["対象質問 + 既存PASS質問を固定条件で再評価"]
    I1 -->|YES| J["active化"]
    I2 --> J
```

## 3. 一番重要な分岐

```mermaid
flowchart TD
    A["Bが答えられない"] --> B{"本当にナレッジ不足か?"}
    B -->|ナレッジに無い| C["missing_knowledge<br/>ナレッジ追加"]
    B -->|あるが取れていない| D["retrieval_failure<br/>検索/metadata/rerank改善"]
    B -->|取れているが使えていない| E["generation_failure<br/>プロンプト/回答モード改善"]
    B -->|根拠が分断されている| F["chunking_failure<br/>chunk粒度/overlap修正"]
    B -->|Aが怪しい| G["invalid_A<br/>外部基準却下"]
    B -->|質問が複合/曖昧| H["ambiguous_question<br/>質問を分解"]
    B -->|対象外| I["out_of_scope<br/>no-answerセットへ"]
    B -->|判断不能| J["needs_quarantine<br/>隔離レビューへ"]
```

勘所はここ。Bが弱いからといって、すぐナレッジを足してはいけない。

RAG改善で失敗しやすいのは、検索やchunkが悪いだけなのに、本文を増やして解決しようとすること。これはナレッジ肥大化と検索ノイズを生む。

## 4. Coverage Loop構築フロー

```mermaid
flowchart TD
    A["C役を設計"] --> B["問い型を分散<br/>C-1 因果 x マニアック<br/>C-2 因果 x 複数人/組織<br/>C-3 条件/例外 x 時系列/比較"]
    B --> C["質問セット生成"]
    C --> D["A回答<br/>外部基準<br/>出典URL/source_type/evidence_span必須"]
    C --> E["B回答<br/>現行ナレッジのみ<br/>retrieved chunks/scores/citations必須"]
    D --> F["D判定"]
    E --> F
    F --> G["原因分類<br/>ナレッジ不足/検索失敗/生成失敗/chunk失敗/A不正/曖昧/対象外"]
    G --> H{"処理方針"}
    H -->|高信頼| I["auto_approved"]
    H -->|明確に不要| J["auto_rejected"]
    H -->|判断不能| K["auto_quarantined"]
    I --> L["修正実装"]
    L --> M["before/after + 回帰検査"]
    M --> N{"PASS?"}
    N -->|YES| O["active"]
    N -->|NO| K
```

## 5. UIを作る時の勘所

```mermaid
flowchart LR
    A["質問欄"] --> B["回答欄<br/>質問直下"]
    B --> C["回答検品結果"]
    C --> D["根拠照合<br/>主張 x chunk"]
    D --> E["処理工程<br/>accordion"]
    E --> F["ナレッジ調整/履歴"]
```

UIでやってはいけないこと:

- 処理工程に「何をするか」だけを書く。
- 類似度スコアだけで正確性を判断させる。
- NG時に候補回答を見せる。
- 根拠パネルをただのsource一覧にする。
- 非エンジニアにchunkやcommitを直接触らせる。

UIで必ず出すこと:

| 表示 | 理由 |
|---|---|
| 回答は質問直下 | 結果確認の視線移動を減らす |
| 出荷状態 | 回答可能/出荷停止/処理失敗を明確にする |
| 主張別根拠 | どの文がどのchunkに支えられているか見る |
| 工程accordion | 普段は簡潔、必要時だけ詳細を見る |
| NG工程の自動展開 | 止まった理由をすぐ分かるようにする |
| 調整履歴 | 何を変えたら結果がどう変わったか残す |

## 6. 構築順序の正解

```mermaid
flowchart TD
    A["Step 1<br/>最小RAG"] --> B["Step 2<br/>根拠と工程の可視化"]
    B --> C["Step 3<br/>回答照合Hook"]
    C --> D["Step 4<br/>出荷停止ゲート"]
    D --> E["Step 5<br/>revision / 台帳"]
    E --> F["Step 6<br/>before/after / 回帰検査"]
    F --> G["Step 7<br/>Coverage Loop"]
    G --> H["Step 8<br/>原因分類と自動分岐"]
    H --> I["Step 9<br/>隔離レビューUI"]
    I --> J["Step 10<br/>構造化ナレッジと検索改善"]
```

重要なのは、Coverage Loopを最初に作らないこと。

先に、Bが何を検索し、何を根拠に、なぜ止まったかが見える状態を作る。これがないと、A/B/Dで差分が出ても、ナレッジ不足なのか検索失敗なのか分からない。

## 7. 完成形の判定基準

```mermaid
flowchart TD
    A["完成判定"] --> B{"危ない回答を止められるか"}
    A --> C{"止めた理由を説明できるか"}
    A --> D{"どこを調整すべきか分類できるか"}
    A --> E{"調整前後を同一条件で比較できるか"}
    A --> F{"副作用を検出できるか"}
    A --> G{"ユーザー確認待ちで止まらないか"}
    A --> H{"隔離だけまとめて確認できるか"}
```

この7つを満たして初めて、納品できる仕組みに近づく。

単にRAGが回答するだけでは足りない。回答、検品、停止、原因分類、調整、比較、回帰、隔離運用までつながっていることが重要。

