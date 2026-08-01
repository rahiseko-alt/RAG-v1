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


def _assert_base_layout(page) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    assert page.locator('[role="tab"]').count() == 4
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
        browser = playwright.chromium.launch(headless=True)
        try:
            desktop = browser.new_page(viewport={"width": 1280, "height": 900})
            desktop.route(
                "**/ask",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_blocked_response(), ensure_ascii=False),
                ),
            )
            _assert_base_layout(desktop)
            desktop.get_by_label("質問", exact=True).fill("E2E blocked question")
            desktop.get_by_role("button", name="回答を作成").click()
            desktop.locator("#answer-state").filter(has_text="出荷停止").wait_for()

            assert desktop.locator("#process-steps details").count() == 8
            assert desktop.locator("#process-steps details[open]").count() == 2
            assert "SECRET_BLOCKED_CANDIDATE" not in desktop.locator("body").inner_text()

            for tab_name in ["ナレッジ調整", "比較・承認", "台帳・監査"]:
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
