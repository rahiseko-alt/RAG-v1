# src/api

FastAPIで質問応答をローカル起動できるようにするモジュール。`src/rag/` のパイプラインをHTTPエンドポイントと透明型RAG UIとして公開する。

## 起動

```bash
uvicorn src.api:app --reload
```

## エンドポイント

- `GET /health`: API、LLMキー、Langfuse監査設定、有効ナレッジ設定の状態を返す
- `POST /ask`: 質問を受け取り、回答・検索出典・監査ON/OFFを返す

`/ask` は選択中の `LLM_PROVIDER` に応じて `.env` の `OPENAI_API_KEY` または `ANTHROPIC_API_KEY` が必要。Langfuseキーが設定されている場合は、RAG実行トレースも送信される。
ナレッジを差し替える場合は `KNOWLEDGE_CONFIG_PATH` で TOML 設定ファイルを指定する。
