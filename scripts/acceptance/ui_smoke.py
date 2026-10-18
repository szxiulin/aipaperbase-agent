"""Browser smoke test for the v0.7 shell: ask / area map / papers, drill-down, theme, overflow.

Requires the demo server and Playwright (dev-only):
  .venv/bin/python -m backend.api.server --port 8767 --database data/database/catalog.sqlite
  .venv/bin/python scripts/acceptance/ui_smoke.py --base-url http://127.0.0.1:8767

Writes output/acceptance/ui_smoke.json and exits non-zero on failure.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROUTES = ["ask", "map", "map/cv_lowlevel", "map/cv_lowlevel/cv_lowlevel-d15-video", "papers", "about", "maintain"]
WIDTHS = [1440, 1200, 1024, 900, 768, 620, 480, 390]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8767")
    ap.add_argument("--out", type=Path, default=Path("output/acceptance/ui_smoke.json"))
    ap.add_argument("--screenshots", type=Path, default=None)
    a = ap.parse_args()

    result = {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
              "base_url": a.base_url, "widths": WIDTHS, "errors": [], "overflow": [], "checks": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda exc: result["errors"].append(str(exc)))
        page.goto(a.base_url, wait_until="networkidle")
        result["checks"].append(("nav", page.locator(".site-links .nav-item").count() == 3))
        page.wait_for_selector("#chat-messages .chat-suggest button", timeout=15000)
        result["checks"].append(("chat_empty_state", page.locator("#chat-messages .chat-suggest button").count() == 4))
        result["checks"].append(("chat_sidebar", page.locator("#chat-new-button").is_visible()))
        page.click(".settings-button")
        result["checks"].append(("settings_dialog", page.locator("#settings-overlay:not(.hidden) #settings-form").is_visible()))
        key_value = page.locator("#settings-form input[name=api_key]").input_value()
        result["checks"].append(("settings_key_not_echoed", key_value == ""))
        page.keyboard.press("Escape")
        result["checks"].append(("settings_closes", page.locator("#settings-overlay.hidden").count() == 1))
        page.click("#lang-toggle [data-lang=en]")
        page.wait_for_timeout(400)
        result["checks"].append(("english_ui", page.locator(".nav-item.active").inner_text().strip() == "Ask"
                                 and "AI research" in page.locator("#chat-messages h1").inner_text()))
        page.click("#lang-toggle [data-lang=zh]")
        page.wait_for_timeout(300)

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for route in ROUTES:
                page.evaluate(f"location.hash = '#/{route}'")
                page.wait_for_timeout(700)
                metrics = page.evaluate("({w: innerWidth, doc: document.documentElement.scrollWidth})")
                if metrics["doc"] > metrics["w"] + 1:
                    result["overflow"].append({"width": width, "page": route, **metrics})

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("location.hash = '#/map'")
        page.wait_for_selector(".tile", timeout=15000)
        result["checks"].append(("treemap_tiles", page.locator(".tile").count() == 21))
        page.locator(".tile").first.click()
        page.wait_for_selector(".dir-row", timeout=15000)
        result["checks"].append(("area_drill", "/map/" in page.url and page.locator("#map-crumbs a").count() == 1))
        page.locator(".dir-row").first.click()
        page.wait_for_selector("#dir-papers .paper-row", timeout=15000)
        result["checks"].append(("direction_drill", page.locator("#map-crumbs a").count() == 2))
        page.go_back()
        page.wait_for_selector(".dir-row", timeout=15000)
        result["checks"].append(("back_button", page.locator("#map-crumbs a").count() == 1))

        page.set_viewport_size({"width": 1440, "height": 1000})
        before = page.evaluate("document.documentElement.dataset.theme")
        page.click("#theme-toggle")
        after = page.evaluate("document.documentElement.dataset.theme")
        result["checks"].append(("theme_toggle", before != after))
        page.reload(wait_until="networkidle")
        result["checks"].append(("theme_persist", page.evaluate("document.documentElement.dataset.theme") == after))

        page.evaluate("location.hash = '#/papers/agents'")
        page.wait_for_function("document.querySelector('#paper-count').innerText.includes('2,639')", timeout=15000)
        result["checks"].append(("papers_area_filter", True))
        page.evaluate("location.hash = '#/maintain'")
        page.wait_for_timeout(700)
        page.get_by_role("button", name="研究资料", exact=True).click()
        page.wait_for_timeout(700)
        result["checks"].append(("research_tab", page.locator("#research-view").is_visible()))

        if a.screenshots:
            a.screenshots.mkdir(parents=True, exist_ok=True)
            for route in ("ask", "map"):
                page.evaluate(f"location.hash = '#/{route}'")
                page.wait_for_timeout(1200)
                page.screenshot(path=str(a.screenshots / f"ui-{route}.png"))
        browser.close()

    failed_checks = [name for name, ok in result["checks"] if not ok]
    result["passed"] = not result["errors"] and not result["overflow"] and not failed_checks
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(f"ui smoke: {'PASS' if result['passed'] else 'FAIL'}; "
          f"errors={len(result['errors'])} overflow={len(result['overflow'])} failed={failed_checks}")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
