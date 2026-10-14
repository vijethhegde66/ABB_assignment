"""GUI end-to-end test with a headless browser (skipped when Playwright/Chromium is not installed)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


def test_gui_chat_shows_answer_citations_and_trace(http_stack, browser) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(http_stack.copilot_url)
    page.wait_for_selector("#status-mcp.pill-ok", timeout=20000)
    page.locator(".example").first.click()  # acceptance scenario example
    page.locator(".msg-bot:not(.loading)").first.wait_for(timeout=60000)

    assert "Alarm investigation" in page.locator(".msg-bot .md").first.inner_text()
    assert page.locator(".msg-bot .ref-d").count() > 0  # document citations rendered as chips
    page.click(".tab[data-tab='sources']")
    assert page.locator("#pane-sources .item").count() >= 2
    page.click(".tab[data-tab='trace']")
    assert page.locator("#pane-trace details.trace").count() >= 8
    page.locator("#pane-trace details.trace > summary").first.click()
    assert "Underlying Alarm API calls" in page.locator("#pane-trace").inner_text()
    page.click(".tab[data-tab='tools']")
    page.wait_for_selector("#pane-tools details.trace")
    assert page.locator("#pane-tools details.trace").count() == 17
    assert not errors, errors
