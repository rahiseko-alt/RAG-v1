"""任意の Langfuse 監査トレース連携。

環境変数が設定されている場合だけ Langfuse の LangChain CallbackHandler を返す。
未設定なら None を返し、ローカル実行・テスト・ポートフォリオ閲覧を壊さない。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


FALSE_VALUES = {"0", "false", "no", "off"}
DEFAULT_TAGS = ["medguide-rag", "rag", "portfolio-demo"]
ALLOWED_LANGFUSE_HOSTS = {
    "cloud.langfuse.com",
    "us.cloud.langfuse.com",
    "jp.cloud.langfuse.com",
    "hipaa.cloud.langfuse.com",
}


@dataclass(frozen=True)
class AuditResult:
    trace_id: str
    status: str
    trace_url: str | None
    observation_names: tuple[str, ...] = ()


def new_trace_id() -> str:
    """Return a caller-controlled trace id safe for local persistence and Langfuse."""
    return uuid.uuid4().hex


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
    parsed_url = urlparse(base_url)
    try:
        parsed_port = parsed_url.port
    except ValueError:
        parsed_port = -1
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname not in ALLOWED_LANGFUSE_HOSTS
        or parsed_port not in {None, 443}
        or parsed_url.path not in {"", "/"}
        or parsed_url.query
        or parsed_url.fragment
    ):
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
    trace_id: str | None = None,
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

    handler_kwargs: dict[str, Any] = {}
    if trace_id:
        handler_kwargs["trace_context"] = {"trace_id": trace_id}

    return {
        "callbacks": [CallbackHandler(**handler_kwargs)],
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


def finalize_langfuse_trace(trace_id: str) -> AuditResult:
    """Flush a trace and obtain its SDK-generated URL without exposing credentials."""
    if not is_langfuse_configured():
        return AuditResult(trace_id=trace_id, status="disabled", trace_url=None)

    try:
        from langfuse import get_client

        client = get_client()
        client.flush()
        trace_url = client.get_trace_url(trace_id=trace_id)
        return AuditResult(trace_id=trace_id, status="flushed", trace_url=str(trace_url) if trace_url else None)
    except Exception:
        return AuditResult(trace_id=trace_id, status="error", trace_url=None)


def record_langfuse_quality_observations(
    *,
    trace_id: str,
    question: str,
    verification: dict[str, Any],
    delivery_status: str,
) -> str:
    """Attach verifier and release-gate evidence to the caller-controlled trace."""
    if not is_langfuse_configured():
        return "disabled"

    try:
        from langfuse import get_client

        client = get_client()
        claims = verification.get("claims") or []
        verify_observation = client.start_observation(
            trace_context={"trace_id": trace_id},
            name="verify",
            as_type="evaluator",
            input={"question": question},
            output={
                "status": verification.get("status"),
                "axes": verification.get("axes"),
                "claim_statuses": [
                    {
                        "claim_id": claim.get("claim_id"),
                        "status": claim.get("status"),
                        "evidence_ranks": [
                            evidence.get("rank")
                            for evidence in claim.get("evidence", [])
                        ],
                    }
                    for claim in claims
                ],
                "deterministic_pass": (
                    verification.get("deterministic") or {}
                ).get("all_pass"),
            },
            level="DEFAULT" if verification.get("status") == "pass" else "WARNING",
        )
        verify_observation.end()

        gate_observation = client.start_observation(
            trace_context={"trace_id": trace_id},
            name="gate",
            as_type="guardrail",
            input={
                "verification_status": verification.get("status"),
                "release_allowed": verification.get("release_allowed"),
            },
            output={
                "delivery_status": delivery_status,
                "reason_code": verification.get("reason_code"),
            },
            level="DEFAULT" if delivery_status == "released" else "WARNING",
        )
        gate_observation.end()
        return "recorded"
    except Exception:
        return "error"


def check_langfuse_trace(trace_id: str) -> AuditResult:
    """Check remote read visibility once; callers may poll this bounded endpoint."""
    if not is_langfuse_configured():
        return AuditResult(trace_id=trace_id, status="disabled", trace_url=None)

    try:
        from langfuse import get_client
        from langfuse.api import NotFoundError

        client = get_client()
        trace_url = client.get_trace_url(trace_id=trace_id)
        try:
            client.api.trace.get(trace_id, fields="core")
        except NotFoundError:
            return AuditResult(
                trace_id=trace_id,
                status="pending",
                trace_url=str(trace_url) if trace_url else None,
            )
        observations = client.api.observations.get_many(
            trace_id=trace_id,
            fields="core,basic",
            limit=100,
        )
        observed_names = list(
            dict.fromkeys(
                str(item.name)
                for item in observations.data
                if getattr(item, "name", None)
            )
        )
        canonical = ["retrieve", "generate", "verify", "gate"]
        ordered_names = [
            name for name in canonical if name in observed_names
        ] + [
            name for name in observed_names if name not in canonical
        ]
        return AuditResult(
            trace_id=trace_id,
            status="confirmed",
            trace_url=str(trace_url) if trace_url else None,
            observation_names=tuple(ordered_names),
        )
    except Exception:
        return AuditResult(trace_id=trace_id, status="error", trace_url=None)
