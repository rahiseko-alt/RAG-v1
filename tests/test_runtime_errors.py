import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.runtime_errors import safe_error_message  # noqa: E402
from src.observability import get_langfuse_config_error, is_langfuse_configured  # noqa: E402


def test_safe_error_message_redacts_common_api_keys():
    exc = RuntimeError(
        "bad keys sk-proj-abc123456789 pk-lf-abc123456789 sk-lf-abc123456789"
    )

    msg = safe_error_message(exc)

    assert "sk-proj-abc" not in msg
    assert "pk-lf-abc" not in msg
    assert "sk-lf-abc" not in msg
    assert msg.count("[redacted-key]") == 3


def test_langfuse_config_rejects_non_langfuse_key_prefixes(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "not-a-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "not-a-secret-key")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com")

    assert is_langfuse_configured() is False
    assert get_langfuse_config_error() == "LANGFUSE_PUBLIC_KEY must start with 'pk-lf-'"
