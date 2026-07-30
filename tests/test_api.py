import os
import sys

from fastapi.testclient import TestClient
from langchain_core.documents import Document

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import src.api as api  # noqa: E402


def test_health_reports_configuration(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(api, "get_llm_provider", lambda: "anthropic")
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)

    client = TestClient(api.create_app())
    res = client.get("/health")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["provider"] == "anthropic"
    assert body["generation_configured"] is False
    assert body["anthropic_configured"] is False
    assert body["openai_configured"] is False
    assert body["langfuse_configured"] is False


def test_ask_requires_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api, "get_llm_provider", lambda: "anthropic")
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)

    client = TestClient(api.create_app())
    res = client.post("/ask", json={"question": "運動はどのくらい？"})

    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_ask_requires_openai_key_when_provider_is_openai(monkeypatch):
    monkeypatch.setattr(api, "get_llm_provider", lambda: "openai")
    monkeypatch.setattr(api, "is_generation_configured", lambda: False)

    client = TestClient(api.create_app())
    res = client.post("/ask", json={"question": "運動はどのくらい？"})

    assert res.status_code == 503
    assert "OPENAI_API_KEY" in res.json()["detail"]


def test_ask_returns_answer_and_sources(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(api, "get_rag", lambda: ("graph", 71))

    def fake_ask(graph, question, **kwargs):
        assert graph == "graph"
        assert question == "運動はどのくらい？"
        assert kwargs["session_id"] == "session-1"
        return {
            "question": question,
            "answer": "週150分の中強度活動が推奨されています [1]。",
            "docs": [
                (
                    Document(
                        page_content="Adults should do at least 150 minutes of moderate-intensity physical activity.",
                        metadata={"source": "who.pdf", "page": 11, "chunk_id": 3},
                    ),
                    0.859,
                )
            ],
        }

    monkeypatch.setattr(api, "ask", fake_ask)

    client = TestClient(api.create_app())
    res = client.post(
        "/ask",
        json={"question": " 運動はどのくらい？ ", "session_id": "session-1"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["question"] == "運動はどのくらい？"
    assert body["answer"].startswith("週150分")
    assert body["chunks_indexed"] == 71
    assert body["sources"][0]["source"] == "who.pdf"
    assert body["sources"][0]["page"] == 11
    assert body["sources"][0]["score"] == 0.859
