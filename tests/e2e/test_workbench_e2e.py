from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only when dev deps are absent.
    sync_playwright = None


BASE_URL = os.environ.get("MEDGUIDE_E2E_URL", "http://127.0.0.1:8010/")
QA_DIR = Path(__file__).resolve().parents[2] / "reports" / "qa"

# Some environments pre-install the full Chromium binary outside Playwright's own
# managed cache (e.g. at /opt/pw-browsers/chromium), separately from whatever
# @playwright/test version this project pins. Only override the launch path when
# that binary is actually present; otherwise let Playwright resolve its own
# installed browser as usual.
_PREINSTALLED_CHROMIUM = Path("/opt/pw-browsers/chromium")
_CHROMIUM_EXECUTABLE_PATH = str(_PREINSTALLED_CHROMIUM) if _PREINSTALLED_CHROMIUM.exists() else None


def _blocked_response() -> dict[str, object]:
    return {
        "question": "E2E blocked question",
        "run_id": "e2e-run",
        "delivery_status": "blocked",
        "answer": None,
        "candidate_answer": "SECRET_BLOCKED_CANDIDATE",
        "blocked_reason": "quality_gate_failed",
        "verification": {
            "status": "block",
            "axes": {"faithfulness": 1, "relevance": 2, "no_misinfo": 1},
            "claims": [
                {
                    "claim_id": "blocked-claim-1",
                    "status": "unclear",
                    "evidence": [{"rank": 1, "reason": "Evidence classification."}],
                    "reason": "Claim classified as unclear.",
                }
            ],
        },
        "audit": {"status": "flushed", "trace_id": "a" * 32, "trace_url": None},
        "sources": [
            {
                "rank": 1,
                "source": "fixture.md",
                "page": 0,
                "chunk_id": 12,
                "score": 0.861,
                "snippet": "E2E evidence fixture.",
            }
        ],
        "chunks_indexed": 12,
        "model": "e2e-model",
        "audit_enabled": True,
        "knowledge": {"title": "E2E knowledge"},
        "process_steps": [
            {
                "id": f"step-{index}",
                "title": title,
                "status": (
                    "NG"
                    if title == "回答照合"
                    else "出荷停止"
                    if title == "出荷判定"
                    else "完了"
                ),
                "purpose": f"{title}の目的",
                "input": f"{title}の入力",
                "process": f"{title}で実行した処理",
                "output": f"{title}の出力",
                "check": f"{title}の判断基準",
            }
            for index, title in enumerate(
                [
                    "質問受付",
                    "ナレッジ選択",
                    "意味検索",
                    "根拠候補確認",
                    "回答生成",
                    "回答照合",
                    "出荷判定",
                    "監査記録",
                ],
                start=1,
            )
        ],
    }


def _run_events() -> list[dict[str, object]]:
    return [
        {"id": 1, "event_type": "queued", "payload": {"summary": "queued"}},
        {"id": 2, "event_type": "running", "payload": {"summary": "running"}},
        {
            "id": 3,
            "event_type": "question_received",
            "payload": {"input": {"question_length": 20}, "answer_mode": "standard"},
        },
        {
            "id": 4,
            "event_type": "knowledge_selected",
            "payload": {"revision_id": "rev", "source_sha256": "a" * 64},
        },
        {
            "id": 5,
            "event_type": "retrieve_started",
            "payload": {"structured_hits": 0},
        },
        {
            "id": 6,
            "event_type": "generate_completed",
            "payload": {"retrieved_sources": 1, "candidate_answer_saved": True},
        },
        {
            "id": 7,
            "event_type": "verify_completed",
            "payload": {"status": "block", "claim_count": 1, "release_allowed": False},
        },
        {
            "id": 8,
            "event_type": "gate_completed",
            "payload": {"delivery_status": "blocked", "blocked_reason": "quality_gate_failed"},
        },
        {
            "id": 9,
            "event_type": "completed",
            "payload": {"delivery_status": "blocked", "blocked_reason": "quality_gate_failed"},
        },
    ]


def _sse_body(events: list[dict[str, object]]) -> str:
    return "".join(
        f"id: {event['id']}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        for event in events
    )


def _assert_base_layout(page) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    assert page.locator('[role="tab"]').count() == 5
    question = page.locator('[aria-labelledby="question-title"]').bounding_box()
    answer = page.locator('[aria-labelledby="answer-title"]').bounding_box()
    assert question is not None
    assert answer is not None
    assert question["y"] + question["height"] < answer["y"]
    assert page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    ) == 0


@pytest.mark.e2e
@pytest.mark.skipif(sync_playwright is None, reason="playwright dev dependency is not installed")
def test_workbench_main_layout_and_blocked_answer_do_not_leak_candidate() -> None:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=_CHROMIUM_EXECUTABLE_PATH)
        try:
            desktop = browser.new_page(viewport={"width": 1280, "height": 900})
            response = _blocked_response()
            events = _run_events()
            desktop.route(
                "**/runs",
                lambda route: route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps(
                        {"run_id": "e2e-run", "status": "queued", "events_url": "/runs/e2e-run/events"},
                        ensure_ascii=False,
                    ),
                ),
            )
            desktop.route(
                "**/runs/e2e-run/events",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body=_sse_body(events),
                ),
            )
            desktop.route(
                "**/runs/e2e-run",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "run_id": "e2e-run",
                            "status": "completed",
                            "revision_id": "rev",
                            "question": "E2E blocked question",
                            "answer_mode": "standard",
                            "response": response,
                            "events": events,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            _assert_base_layout(desktop)
            desktop.get_by_label("質問", exact=True).fill("E2E blocked question")
            desktop.get_by_role("button", name="回答を作成").click()
            desktop.locator("#answer-state").filter(has_text="出荷停止").wait_for()

            assert desktop.locator("#process-steps details").count() == 8
            assert desktop.locator("#process-steps details[open]").count() == 2
            assert "SECRET_BLOCKED_CANDIDATE" not in desktop.locator("body").inner_text()

            for tab_name in ["ナレッジ調整", "比較・承認", "台帳・監査", "隔離一覧"]:
                desktop.get_by_role("tab", name=tab_name).click()
                assert desktop.get_by_role("tabpanel", name=tab_name).is_visible()
            desktop.get_by_role("tab", name="質問・検品").click()
            desktop.screenshot(
                path=str(QA_DIR / "workbench-desktop.jpg"),
                type="jpeg",
                quality=85,
                full_page=True,
            )

            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            _assert_base_layout(mobile)
            assert mobile.evaluate("() => window.innerWidth") == 390
            mobile.screenshot(
                path=str(QA_DIR / "workbench-mobile.jpg"),
                type="jpeg",
                quality=85,
                full_page=True,
            )
        finally:
            browser.close()


def _quarantined_candidate(status: str = "auto_quarantined") -> dict[str, object]:
    return {
        "id": "e2e-candidate-1",
        "revision_id": "rev",
        "question": "E2E隔離候補の質問",
        "intent": "",
        "disposition": "quarantined" if status == "auto_quarantined" else "add_candidate",
        "failure_cause": "needs_quarantine",
        "candidate_reason": "",
        "status": status,
        "status_reason": "判定不能のため保留" if status == "auto_quarantined" else "ワークベンチから承認",
        "implemented_revision_id": None,
        "created_at": "2026-08-03T00:00:00.000+00:00",
        "updated_at": "2026-08-03T00:00:00.000+00:00",
        "external_answer": {"role": "external", "answer": "A の回答", "evidence": []},
        "knowledge_answer": {"role": "knowledge", "answer": "記載がありません"},
        "fact_check": {"external_status": "pass", "knowledge_status": "abstain", "reason": ""},
    }


@pytest.mark.e2e
@pytest.mark.skipif(sync_playwright is None, reason="playwright dev dependency is not installed")
def test_quarantine_tab_resolves_a_candidate_through_the_real_ledger_endpoint() -> None:
    """The quarantine screen must actually drive the ledger, not just repaint locally.

    `/workbench/revisions` is left unmocked (hits the real running server) so the
    revision-select really reflects the active revision; only the coverage-candidate
    endpoints are mocked, and the resolve mock asserts the exact request body the UI
    sent — proving the approve button really calls the resolve endpoint with
    `status: auto_approved`, not just changing a badge client-side.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=_CHROMIUM_EXECUTABLE_PATH)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            calls = {"list": 0, "resolve_body": None}

            def fulfill_candidates(route):
                calls["list"] += 1
                status = "auto_approved" if calls["list"] > 1 else "auto_quarantined"
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"items": [_quarantined_candidate(status)]}, ensure_ascii=False),
                )

            def fulfill_resolve(route):
                calls["resolve_body"] = json.loads(route.request.post_data or "{}")
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_quarantined_candidate("auto_approved"), ensure_ascii=False),
                )

            page.route("**/coverage-candidates", fulfill_candidates)
            page.route("**/coverage-candidates/*/resolve", fulfill_resolve)

            page.goto(BASE_URL, wait_until="networkidle")
            page.get_by_role("tab", name="隔離一覧").click()

            candidate_list = page.locator("#quarantine-list")
            candidate_list.get_by_text("E2E隔離候補の質問").wait_for()
            assert candidate_list.get_by_text("隔離（要確認）").is_visible()

            page.get_by_role("button", name="承認").click()
            candidate_list.get_by_text("承認済み").wait_for()

            assert calls["resolve_body"] == {
                "status": "auto_approved",
                "reason": "ワークベンチから承認",
            }
            assert calls["list"] >= 2
        finally:
            browser.close()
