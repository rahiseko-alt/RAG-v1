import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.runtime_errors import safe_error_message  # noqa: E402
from src.observability import (  # noqa: E402
    build_langfuse_runnable_config,
    check_langfuse_trace,
    finalize_langfuse_trace,
    get_langfuse_config_error,
    is_langfuse_configured,
    record_langfuse_quality_observations,
)


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


def test_langfuse_config_rejects_prefix_lookalike_host(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-value")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-value")
    monkeypatch.setenv(
        "LANGFUSE_BASE_URL",
        "https://jp.cloud.langfuse.com.attacker.invalid",
    )

    assert is_langfuse_configured() is False
    assert (
        get_langfuse_config_error()
        == "LANGFUSE_BASE_URL must be a Langfuse Cloud region URL"
    )


def _configure_test_langfuse(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-value")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-value")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com")


def test_langfuse_handler_receives_custom_trace_context(monkeypatch):
    _configure_test_langfuse(monkeypatch)
    captured = {}

    class FakeHandler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    package = types.ModuleType("langfuse")
    package.__path__ = []
    langchain = types.ModuleType("langfuse.langchain")
    langchain.CallbackHandler = FakeHandler
    monkeypatch.setitem(sys.modules, "langfuse", package)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", langchain)

    config = build_langfuse_runnable_config(
        question="test",
        trace_id="a" * 32,
    )

    assert config is not None
    assert captured["trace_context"] == {"trace_id": "a" * 32}


def test_finalize_langfuse_flushes_and_uses_sdk_trace_url(monkeypatch):
    _configure_test_langfuse(monkeypatch)

    class FakeClient:
        def __init__(self):
            self.flushed = False

        def flush(self):
            self.flushed = True

        def get_trace_url(self, *, trace_id):
            assert self.flushed is True
            return f"https://trace.invalid/{trace_id}"

    client = FakeClient()
    package = types.ModuleType("langfuse")
    package.get_client = lambda: client
    monkeypatch.setitem(sys.modules, "langfuse", package)

    result = finalize_langfuse_trace("b" * 32)

    assert result.status == "flushed"
    assert result.trace_url == "https://trace.invalid/" + "b" * 32


def test_check_langfuse_trace_distinguishes_pending_and_confirmed(monkeypatch):
    _configure_test_langfuse(monkeypatch)

    class FakeNotFoundError(Exception):
        pass

    class FakeTraceApi:
        def __init__(self):
            self.visible = False

        def get(self, trace_id, fields):
            assert trace_id == "c" * 32
            assert fields == "core"
            if not self.visible:
                raise FakeNotFoundError()
            return {"id": trace_id}

    trace_api = FakeTraceApi()

    class FakeObservationsApi:
        @staticmethod
        def get_many(*, trace_id, fields, limit):
            assert trace_id == "c" * 32
            assert fields == "core,basic"
            assert limit == 100
            return types.SimpleNamespace(
                data=[
                    types.SimpleNamespace(name="retrieve"),
                    types.SimpleNamespace(name="verify"),
                    types.SimpleNamespace(name="gate"),
                ]
            )

    class FakeClient:
        api = types.SimpleNamespace(
            trace=trace_api,
            observations=FakeObservationsApi(),
        )

        @staticmethod
        def get_trace_url(*, trace_id):
            return f"https://trace.invalid/{trace_id}"

    package = types.ModuleType("langfuse")
    package.get_client = lambda: FakeClient()
    api_package = types.ModuleType("langfuse.api")
    api_package.NotFoundError = FakeNotFoundError
    monkeypatch.setitem(sys.modules, "langfuse", package)
    monkeypatch.setitem(sys.modules, "langfuse.api", api_package)

    pending = check_langfuse_trace("c" * 32)
    trace_api.visible = True
    confirmed = check_langfuse_trace("c" * 32)

    assert pending.status == "pending"
    assert confirmed.status == "confirmed"
    assert confirmed.observation_names == ("retrieve", "verify", "gate")


def test_quality_observations_share_the_caller_trace(monkeypatch):
    _configure_test_langfuse(monkeypatch)
    captured = []

    class FakeObservation:
        def __init__(self, item):
            self.item = item

        def end(self):
            self.item["ended"] = True

    class FakeClient:
        def start_observation(self, **kwargs):
            captured.append(kwargs)
            return FakeObservation(captured[-1])

    package = types.ModuleType("langfuse")
    package.get_client = lambda: FakeClient()
    monkeypatch.setitem(sys.modules, "langfuse", package)

    result = record_langfuse_quality_observations(
        trace_id="d" * 32,
        question="test question",
        verification={
            "status": "pass",
            "axes": {"faithfulness": 2, "relevance": 2, "no_misinfo": 2},
            "claims": [
                {
                    "claim_id": "claim-1",
                    "status": "supported",
                    "evidence": [{"rank": 1}],
                }
            ],
            "deterministic": {"all_pass": True},
            "release_allowed": True,
            "reason_code": None,
        },
        delivery_status="released",
    )

    assert result == "recorded"
    assert [item["name"] for item in captured] == ["verify", "gate"]
    assert all(item["trace_context"] == {"trace_id": "d" * 32} for item in captured)
    assert all(item["ended"] is True for item in captured)
