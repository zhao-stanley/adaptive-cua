"""Checks that the development setup works: Chromium starts and CDP answers."""

from playwright.sync_api import sync_playwright


def test_chromium_returns_an_accessibility_tree():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('<button type="button">Place order</button>')
        cdp = page.context.new_cdp_session(page)
        nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
        browser.close()
    names = {(n.get("name") or {}).get("value") for n in nodes}
    assert "Place order" in names
