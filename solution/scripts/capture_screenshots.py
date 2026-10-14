"""Drive the GUI with a headless browser and capture demo screenshots into docs/screenshots/.

Requires the stack to be running (scripts/run_local.sh or docker compose) and
``pip install playwright && python -m playwright install chromium``.

    python scripts/capture_screenshots.py --url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify "
    "likely contributing factors, retrieve the relevant operating procedure, and provide recommended "
    "actions with source evidence."
)


def ask(page: Page, text: str) -> None:
    before = page.locator(".msg-bot:not(.loading)").count()
    page.fill("#input", text)
    page.click("#send")
    # locator waits (the app's CSP forbids string eval, so no wait_for_function)
    page.locator(".msg-bot:not(.loading)").nth(before).wait_for(timeout=60000)
    page.wait_for_timeout(300)


def tab(page: Page, name: str) -> None:
    page.click(f".tab[data-tab='{name}']")
    page.wait_for_timeout(200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--out", default="docs/screenshots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(args.url)
        page.wait_for_selector("#status-mcp.pill-ok", timeout=20000)
        page.screenshot(path=str(out / "01-home.png"))

        ask(page, ACCEPTANCE)
        page.screenshot(path=str(out / "02-acceptance-answer.png"))
        tab(page, "investigation")
        page.screenshot(path=str(out / "03-investigation-panel.png"))
        tab(page, "sources")
        page.screenshot(path=str(out / "04-citations.png"))
        tab(page, "trace")
        page.locator("#pane-trace details.trace > summary").nth(0).click()
        page.screenshot(path=str(out / "05-mcp-trace.png"))
        tab(page, "retrieval")
        page.screenshot(path=str(out / "06-retrieval.png"))
        tab(page, "tools")
        page.locator("#pane-tools details.trace > summary").nth(0).click()
        page.screenshot(path=str(out / "07-tool-discovery.png"))

        ask(page, "Which alarm has the highest priority in EastRefinery, and why?")
        ask(page, "Are the API recommendations consistent with the maintenance manual?")
        tab(page, "investigation")
        page.screenshot(path=str(out / "08-conflict-detection.png"))

        # degraded scenario: correlation endpoint outage injected through the demo controls
        if page.locator("#tab-demo:not(.hidden)").count():
            tab(page, "demo")
            page.get_by_text("Outage: correlation endpoint down").click()
            page.wait_for_timeout(300)
            ask(page, "Investigate recurring alarms for Boiler Feed Pump 101 over the last 90 days")
            tab(page, "trace")
            failed = page.locator("details.trace:has(.st-error)")
            if failed.count():
                failed.nth(0).locator("summary").click()
                failed.nth(0).scroll_into_view_if_needed()
            page.screenshot(path=str(out / "09-degraded-partial-failure.png"))
            tab(page, "demo")
            page.click("#fault-clear")

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(args.url)
        mobile.wait_for_selector("#status-mcp.pill-ok", timeout=20000)
        ask(mobile, "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.")
        mobile.screenshot(path=str(out / "10-mobile.png"), full_page=False)
        browser.close()

    if errors:
        print("Browser console errors:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    print(f"screenshots written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
