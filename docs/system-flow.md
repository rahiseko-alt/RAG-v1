# medguide-rag Flow Diagram

この文書は、medguide-rag の処理工程をフロー図で説明します。

## 1. 全体フロー

```mermaid
flowchart TD
    A["ユーザー質問<br/>入力: 質問文<br/>出力: 正規化質問 + run ID"] --> B["ナレッジ選択<br/>入力: active revision<br/>出力: source SHA + revision ID"]
    B --> C["検索<br/>入力: 質問 + revision<br/>処理: vector / BM25 / neighbor / rerank / structured hits<br/>出力: 根拠候補チャンク"]
    C --> D["回答生成<br/>入力: 根拠候補 + プロンプト<br/>出力: 引用付き回答候補"]
    D --> E["機械検査<br/>入力: 回答候補 + 取得根拠<br/>処理: 引用番号、空回答、根拠なし断定を検査<br/>出力: deterministic checks"]
    E --> F["別LLM照合<br/>入力: 主張 + 根拠チャンク<br/>処理: 支持あり / 矛盾 / 判断不能<br/>出力: 主張別判定"]
    F --> G{"出荷判定<br/>全主張支持 + 3軸PASS?"}
    G -->|PASS| H["回答表示<br/>delivery_status: released<br/>answer: 表示"]
    G -->|NG / timeout / 判定不能| I["出荷停止<br/>delivery_status: blocked<br/>answer: null<br/>停止理由を表示"]
    H --> J["監査記録<br/>run / trace / sources / verification を保存"]
    I --> J
    J --> K["調整台帳<br/>問題工程・原因分類・before/afterへ接続"]
```

### 読み方

| 工程 | 何をしたかを見る場所 | 止まる主な理由 |
|---|---|---|
| ユーザー質問 | 正規化後質問、run ID | 空質問、長すぎる質問 |
| ナレッジ選択 | revision ID、source SHA | revision不整合、source hash不一致 |
| 検索 | 取得チャンク、順位、score、snippet | 根拠0件、検索対象違い |
| 回答生成 | 回答候補、引用番号 | LLM障害、形式破損 |
| 機械検査 | 引用検査、根拠なし断定検査 | 壊れた引用、根拠なし断定 |
| 別LLM照合 | 主張別 support / contradiction / unclear | 矛盾、判断不能、JSON破損 |
| 出荷判定 | released / blocked | NG、timeout、検査エラー |
| 監査記録 | trace ID、Langfuse状態 | Langfuse未設定、送信失敗 |

## 2. ナレッジ改善フロー

```mermaid
flowchart TD
    A["回答結果<br/>released / blocked / unclear"] --> B["原因分類<br/>missing_knowledge<br/>retrieval_failure<br/>generation_failure<br/>chunking_failure<br/>invalid_A<br/>ambiguous_question<br/>out_of_scope"]
    B --> C{"修正対象は何か?"}
    C -->|ナレッジ不足| D["ナレッジ追加<br/>fact / entity / text / source"]
    C -->|検索失敗| E["検索改善<br/>BM25 / metadata / rerank / top-k"]
    C -->|chunk不良| F["chunk設計修正<br/>分割粒度 / overlap / source構造"]
    C -->|生成失敗| G["プロンプト/回答モード調整"]
    C -->|A不正| H["外部基準却下"]
    D --> I["新revision作成"]
    E --> I
    F --> I
    G --> I
    H --> Z["auto_rejected"]
    I --> J["before/after比較<br/>同一条件で対象質問を再実行"]
    J --> K{"改善したか?"}
    K -->|NO| L["auto_rejected / auto_quarantined"]
    K -->|YES| M["回帰検査<br/>既存PASS質問が悪化しないか"]
    M --> N{"副作用なし?"}
    N -->|YES| O["active化<br/>有効revisionへ"]
    N -->|NO| P["auto_quarantined<br/>まとめてユーザー確認"]
```

### 重要な考え方

悪かった原因をすべて「ナレッジ不足」にしないこと。

RAGの失敗は、ナレッジ追加ではなく検索、chunk、プロンプトで直すべき場合があります。

## 3. Coverage Loop

```mermaid
flowchart TD
    C0["C役: 質問生成<br/>目的: 弱点を突く問いを作る"] --> C1["C-1<br/>因果 x マニアック"]
    C0 --> C2["C-2<br/>因果 x 複数人/組織"]
    C0 --> C3["C-3<br/>条件/例外 x 時系列/比較"]
    C1 --> Q["質問セット"]
    C2 --> Q
    C3 --> Q

    Q --> A["A役: 外部基準回答<br/>入力: 質問<br/>出力: answer + source_url + source_type + evidence_span"]
    Q --> B["B役: 現行ナレッジ回答<br/>入力: 質問 + current revision<br/>出力: answer + retrieved chunks + scores + citations"]
    A --> D["D役: 比較判定<br/>A/Bを比較し原因分類"]
    B --> D
    D --> R{"判定"}
    R -->|A妥当 + B不十分| X["改善候補"]
    R -->|A不正 / 対象外| Y["auto_rejected"]
    R -->|判断不能| Z["auto_quarantined"]
    X --> W["原因分類別に修正ルートへ"]
```

### Dが出すべき分類

**実装済み**（2026-08-01）：`src/coverage_loop.py` の `FactCheckJudgment.failure_cause` と
`classify_coverage_item()`。ここに書かれている8分類はスキーマの型（`FailureCause`）として存在する。
下表の「次の行き先」列（自動採用/自動却下/隔離への振り分けロジック、台帳への永続化、
quarantine UI）はまだ未実装（`docs/handoff.md` の次回やる事を参照）。

| 分類 | 意味 | 次の行き先 |
|---|---|---|
| `missing_knowledge` | 必要情報がナレッジにない | ナレッジ追加 |
| `retrieval_failure` | あるのに検索できていない | retriever改善 |
| `generation_failure` | 根拠はあるが回答に使えていない | prompt/回答モード改善 |
| `chunking_failure` | 根拠が分割で壊れている | chunk設計修正 |
| `invalid_A` | 外部基準が弱い/誤り | 却下 |
| `ambiguous_question` | 質問が曖昧 | 質問分解 |
| `out_of_scope` | 対象外 | no-answerセット |
| `needs_quarantine` | 自動判断不能 | 隔離 |

## 4. 自動運用と隔離

```mermaid
stateDiagram-v2
    [*] --> candidate
    candidate --> auto_classified: D判定 + 原因分類
    auto_classified --> auto_approved: 出典強い + 改善見込みあり
    auto_classified --> auto_rejected: 対象外 / A不正 / 重複
    auto_classified --> auto_quarantined: 判断不能 / judge割れ / 副作用あり
    auto_approved --> implemented: 修正反映
    implemented --> verified: before/after + 回帰PASS
    verified --> active: 有効化
    auto_rejected --> [*]
    auto_quarantined --> user_batch_review: 隔離だけまとめて確認
    user_batch_review --> auto_approved: 採用
    user_batch_review --> auto_rejected: 却下
    user_batch_review --> auto_quarantined: 保留
```

### なぜ隔離だけユーザー確認にするのか

都度確認にすると改善が止まります。

そのため、通常候補は自動で進め、判断不能だけ隔離します。

ユーザーは、全件を見る作業者ではなく、例外だけを見る監査役です。

## 5. before/after 承認フロー

```mermaid
sequenceDiagram
    participant U as UI/API
    participant S as WorkbenchStore
    participant R as RAG Engine
    participant V as Verifier
    participant A as Audit/Langfuse

    U->>S: 新revision作成
    U->>R: before質問をactive revisionで実行
    R->>V: 回答候補を照合
    V-->>S: before結果保存
    U->>R: after質問をcandidate revisionで実行
    R->>V: 回答候補を照合
    V-->>S: after結果保存
    S->>S: before/after差分 + 回帰悪化判定
    S->>A: run/trace/verification保存
    alt 改善 + 回帰悪化なし
        S-->>U: 承認可能
    else 改善なし or 回帰悪化
        S-->>U: 承認不可 / 隔離
    end
```

### 承認条件

| 条件 | 必須理由 |
|---|---|
| 対象質問が改善 | 修正目的を満たしたか |
| 既存PASS質問が悪化しない | 副作用防止 |
| 同一条件で比較 | 偶然の差分を避ける |
| trace保存 | 後から監査できる |

## 6. UIで見せるべき順序

```text
1. 稼働状態・使用ナレッジ
2. 質問欄
3. 回答欄
4. 回答検品結果
5. 根拠照合
6. 処理工程
7. ナレッジ調整・履歴
```

回答欄は質問欄の直下に置きます。

処理工程は通常は工程名と状態だけを見せ、展開時に以下を表示します。

- 目的
- 入力
- 実行した処理
- 処理後の出力
- 判断基準

NG工程は自動展開します。

