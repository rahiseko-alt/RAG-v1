"""任意の Langfuse 監査トレース連携。

環境変数が設定されている場合だけ Langfuse の LangChain CallbackHandler を返す。
未設定なら None を返し、ローカル実行・テスト・ポートフォリオ閲覧を壊さない。
"""
from __future__ import annotations

import os
from typing import Any


FALSE_VALUES = {"0", "false", "no", "off"}
DEFAULT_TAGS = ["medguide-rag", "rag", "portfolio-demo"]


def get_langfuse_config_error() -> str | None:
    """Return a human-readable Langfuse config issue, or None when usable."""
    enabled = os.getenv("LANGFUSE_ENABLED", "true").strip().lower()
    if enabled in FALSE_VALUES:
        return "LANGFUSE_ENABLED is disabled"

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    base_url = os.getenv("LANGFUSE_BASE_URL", "")

    if not public_key or not secret_key:
        return "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required"
    if not public_key.startswith("pk-lf-"):
        return "LANGFUSE_PUBLIC_KEY must start with 'pk-lf-'"
    if not secret_key.startswith("sk-lf-"):
        return "LANGFUSE_SECRET_KEY must start with 'sk-lf-'"
    if not base_url.startswith(("https://cloud.langfuse.com", "https://us.cloud.langfuse.com", "https://jp.cloud.langfuse.com", "https://hipaa.cloud.langfuse.com")):
        return "LANGFUSE_BASE_URL must be a Langfuse Cloud region URL"
    return None


def is_langfuse_configured() -> bool:
    """Langfuse Cloud/Server へ送信できる最低限の設定があるかを返す。"""
    error = get_langfuse_config_error()
    if error == "LANGFUSE_ENABLED is disabled":
        return False
    return error is None


def build_langfuse_runnable_config(
    *,
    question: str,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """LangGraph/LangChain invoke に渡す Langfuse callback config を組み立てる。

    Langfuse SDK が未導入なのにキーだけ設定されている場合は、監査が有効化されたつもりで
    記録されない事故を避けるため、明示的にエラーにする。
    """
    if not is_langfuse_configured():
        return None

    try:
        from langfuse.langchain import CallbackHandler
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Langfuse credentials are configured, but the 'langfuse' package is not installed. "
            "Run: pip install langfuse"
        ) from exc

    merged_tags = list(dict.fromkeys([*(tags or DEFAULT_TAGS)]))
    metadata: dict[str, Any] = {
        "langfuse_tags": merged_tags,
        "source": "medguide-rag",
        "question": question,
    }
    if session_id:
        metadata["langfuse_session_id"] = session_id
    if user_id:
        metadata["langfuse_user_id"] = user_id

    return {
        "callbacks": [CallbackHandler()],
        "metadata": metadata,
    }


def flush_langfuse() -> None:
    """短命CLIプロセスで Langfuse の未送信トレースを送信しきる。"""
    if not is_langfuse_configured():
        return

    try:
        from langfuse import get_client
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Langfuse credentials are configured, but the 'langfuse' package is not installed. "
            "Run: pip install langfuse"
        ) from exc

    get_client().flush()
