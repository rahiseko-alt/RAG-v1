# medguide-rag 現在地と公開・納品可能版計画

作成日: 2026-08-01

## 現在地

現状は「機能豊富な内部β」です。回答のfail-closed、revision、before/after、回帰検査、SQLite台帳、Langfuse、4タブUIは存在します。ただし、公開・納品可能版としては未完成です。

実測ベースの確認事項:

| 項目 | 現状 |
|---|---|
| pytest | 84 passed / 約145秒 / warning 1件 |
| ruff | 3件FAIL |
| UI | 4タブと工程アコーディオンあり |
| 回答ゲート | NG候補をAPI/UI/CLIへ出さないfail-closedあり |
| 版管理 | source revisionとvalidation/approvalあり |
| Langfuse | trace記録経路あり |

公開・納品阻害要因:

| 項目 | 問題 |
|---|---|
| 構造化ナレッジ | 承認なしでactive revisionへ差し替え可能 |
| 工程表示 | 実イベントではなく、同期処理後の表示置換が中心 |
| Coverage Loop | 原因分類、永続台帳、自動採否、隔離UIが未完成 |
| 評価 | Recall/Precision/no-answer/Judge校正が未測定 |
| 再現可能性 | pyproject/lock/CI/自己完結E2Eが未整備 |
| 納品運用 | バックアップ、復旧、Windows配布物、初期設定UIが未完成 |
| 公開準備 | SECURITY/NOTICE/SBOM/secret scan/依存監査が未整備 |

## 完成形

完成形は、単一Windows PCで非エンジニアが、質問、リアルタイム工程確認、検品、調整、比較、回帰検査、承認、隔離確認、バックアップまで行える、再現可能なRAG品質改善ワークベンチです。

通常処理は自動化し、ユーザー確認は隔離項目の一括確認だけに限定します。

```text
質問
-> 検索
-> 回答候補
-> 機械検査
-> 別LLM照合
-> PASSのみ表示
-> 修正候補分類
-> before/after
-> 回帰検査
-> 承認
-> active化
```

NG、判定不能、検品エラー、timeout、JSON破損はすべて出荷停止です。

## フェーズ表

| Phase | 目的 | 実装内容 | 完了条件 | 現在地 |
|---|---|---|---|---|
| 0 | 真実固定 | 専用ブランチ、現状レポート訂正、正本一覧、ロードマップ、引継ぎ様式 | 現状の能力と未完成項目に矛盾がない | 完了 |
| 1 | 整合性修復 | 構造化entity/factをrevision不変スナップショット化し、承認fingerprintへ追加 | 無承認でactive回答が変化しない。改変後承認は409 | 完了 |
| 2 | 再現可能化 | Python 3.12、pyproject、lock、依存分離、Python Playwright | 新規環境をlockで再現。lint/type/testがPASS | 完了 |
| 3 | 実工程可視化 | 永続run、追記専用工程event、SSE配信、UI追跡 | 500ms以内に実イベント表示。再読込後も追跡 | 部分実装 |
| 4 | 耐障害運用 | migration、worker lease/heartbeat/retry/cancel、backup/restore、診断bundle | 強制終了後も重複runなしで再開。復元後SHA一致 | 未完 |
| 5 | 評価基盤 | qrels、期待主張、answerability、問い型、危険度、検索/回答分離評価 | Recall@5>=0.90、nDCG@5>=0.75等を測定 | 未完 |
| 6 | Judge校正 | frozen人手ラベル、混同行列、macro-F1、Cohen's kappa、rubric hash | 一致率>=80%、kappa>=0.70、重大false-pass 0 | 未完 |
| 7 | Coverage Loop完成 | 失敗分類、永続台帳、自動採否、隔離UI | 根拠不足422、不正遷移409、重複収束 | 未完 |
| 8 | 非エンジニア納品 | 初回設定、Windows起動停止、鍵管理、Langfuse既定OFF、PyInstaller | PythonなしWindowsでCLIなし運用 | 未完 |
| 9 | GitHub公開 | sanitized repo、README、SECURITY、CI、Dependabot、CodeQL、SBOM | 新規cloneから検証PASS。秘密0件。Scorecard 8+ | 未完 |

## 正本

| 文書 | 役割 |
|---|---|
| `roadmap.md` | 公開・納品可能版へ進める最上位ロードマップ |
| `roadmap-state.json` | 次セッションが機械的に現在地を読める状態ファイル |
| `docs/system-readme.md` | 製品の仕組み説明 |
| `docs/system-flow.md` | 解説付き処理フロー図 |
| `docs/build-playbook-flow.md` | 何もないディレクトリから同系統を作るための手順 |
| `docs/session-reports/2026-08-01-coverage-loop-design.md` | Coverage Loop設計思想の引継ぎ |

## 次にやること

次の最優先はPhase 3のUI接続です。バックエンドには`POST /runs`、`GET /runs/{id}`、`GET /runs/{id}/events`と追記event保存を入れました。残りはUIを`/ask`同期表示から`/runs`作成 + EventSource購読へ切り替え、工程表示を実イベント由来にすることです。
