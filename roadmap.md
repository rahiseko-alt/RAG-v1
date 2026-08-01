# medguide-rag Roadmap

作成日: 2026-08-01

## ゴール

単一Windows PCで非エンジニアが、質問、リアルタイム工程確認、検品、調整、比較、回帰検査、承認、隔離確認、バックアップまで行える、再現可能なRAG品質改善ワークベンチにする。

## Milestone 1: 真実固定と整合性修復

対象Phase: 0, 1

完了条件:

- 現状レポートが内部βとして正しく書かれている。
- 正本一覧、ロードマップ、次セッション引継ぎが存在する。
- 構造化ナレッジがrevision不変スナップショットとして扱われる。
- active revision、検査済revision、承認済revisionへ無承認で構造化recordを差し替えられない。
- validation結果とapproval判定に構造化digestが含まれる。

## Milestone 2: 再現可能な実行基盤

対象Phase: 2, 4

完了条件:

- Python 3.12の依存が`pyproject.toml`とlockで固定される。
- 重いML importが遅延化され、通常ユニットテストが60秒以内を目標に短縮される。
- Python Playwrightへ統一し、親ディレクトリのNode依存をなくす。
- migration、worker lease、heartbeat、retry、cancel、backup/restore、診断bundleがある。

## Milestone 3: 実工程可視化

対象Phase: 3

完了条件:

- `POST /runs`が202と`run_id`を返す。
- `GET /runs/{id}`で状態、公開可能回答、判定、manifestを返す。
- `GET /runs/{id}/events`でSSE配信する。
- UIは「何をするか」ではなく「何をしたか」を工程別に表示する。
- NG工程は自動展開し、候補回答はイベントへ漏れない。

## Milestone 4: 評価とCoverage Loop

対象Phase: 5, 6, 7

完了条件:

- qrels、期待主張、answerability、問い型、危険度を持つ評価schemaがある。
- 検索、回答、棄却、judge校正を分離して測る。
- Judge-human一致率、Cohen's kappa、重大false-passを測れる。
- Coverage Loopの候補が原因分類、重複排除、自動採否、隔離UIへ流れる。
- 採用候補も通常revision検査を通さない限りactive化できない。

## Milestone 5: 納品・公開可能化

対象Phase: 8, 9

完了条件:

- Langfuseは既定OFFで、停止してもローカル監査を失わない。
- 初回設定、鍵管理、バックアップUI、Windows起動停止、PyInstaller one-folder配布がある。
- README、SECURITY、CONTRIBUTING、CHANGELOG、NOTICE、CI、Dependabot、CodeQL、secret scan、SBOMがある。
- sanitizedな公開repoを新規cloneして検証できる。
- ナレッジ本文とライセンス判断は公開直前ゲートで確認する。

## 進め方

Solが各Phaseの仕様確定と敵対レビュー、Terraが1 Phaseずつ実装、verify-agentが受け入れ条件を機械検証する。各Phaseは独立コミットにして、`roadmap-state.json`とセッションレポートへ結果・未解決・次Phaseを残す。
