# src/api

単一Windows PC向けのFastAPI APIと非エンジニア用ワークベンチUI。

## 起動

```bash
uvicorn src.api:app --host 127.0.0.1 --port 8010
```

ブラウザで `http://127.0.0.1:8010/` を開く。

## API

| Method | Path | 用途 |
|---|---|---|
| GET | `/health` | LLM、Langfuse、有効revisionの状態 |
| POST | `/ask` | 回答候補生成、照合、出荷判定 |
| GET/POST | `/workbench/revisions` | revision一覧・下書き作成 |
| POST | `/workbench/revisions/{id}/comparison-jobs` | 1問before/after |
| POST | `/workbench/revisions/{id}/validation-jobs` | 評価セット全件検査 |
| GET | `/workbench/jobs/{id}` | job状態・結果 |
| POST | `/workbench/revisions/{id}/approve` | 検査済みrevision承認・有効化 |
| POST | `/workbench/revisions/{id}/reject` | revision却下 |
| GET | `/workbench/adjustments` | 調整台帳 |
| GET | `/audit/traces/{trace_id}` | ローカルrunとLangfuse着弾の照合 |

`POST /ask`は`delivery_status`、nullable `answer`、`blocked_reason`、`verification`、`audit`を返す。品質条件を満たさない場合は`answer=null`。

通常運用の手順は [../../docs/workbench-guide.md](../../docs/workbench-guide.md) を参照。
