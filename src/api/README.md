# src/api

FastAPIで質問応答をローカル起動できるようにするモジュール。`src/rag/` のパイプラインをHTTPエンドポイントとして公開する。

## 起動

```bash
uvicorn src.api:app --reload
```

## エンドポイント

- `GET /health`: API、LLMキー、Langfuse監査設定の状態を返す
- `POST /ask`: 質問を受け取り、回答・検索出典・監査ON/OFFを返す

`/ask` は `.env` の `ANTHROPIC_API_KEY` が必要。Langfuseキーが設定されている場合は、RAG実行トレースも送信される。
OpenAIで回答生成する場合は `LLM_PROVIDER=openai` と `OPENAI_API_KEY` を設定する。
