"""Checks that the development setup works: Chromium starts and CDP answers."""

from pathlib import Path

from playwright.sync_api import sync_playwright

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"


def test_chromium_opens_a_fixture_page_and_returns_an_accessibility_tree():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto((FIXTURES / "checkout.html").as_uri())
        cdp = page.context.new_cdp_session(page)
        nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
        browser.close()
    names = {(n.get("name") or {}).get("value") for n in nodes}
    assert "Place order" in names
