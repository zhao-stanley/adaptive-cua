"""Real Chromium tests of the browser tool contract, with self-contained pages."""

import struct

import pytest

from adaptive_cua.browser.session import BrowserSession


@pytest.fixture
def browser():
    session = BrowserSession(headless=True)
    session.start()
    yield session
    session.close()


def element(snapshot, name):
    return next(e for e in snapshot.elements if e.name == name)


def ok(result):
    assert result.status == "ok", result.error
    return result.observation


def test_checkout_capture_and_element_actions(browser):
    browser.page.set_content("""
        <h1>Checkout</h1><p style="display:none">Hidden promo</p>
        <form onsubmit="event.preventDefault(); document.querySelector('#status').textContent='Order placed'">
          <label>Name<input id="name" required></label>
          <input placeholder="ZIP code" required>
          <label>Shipping<select><option value="standard">Standard</option>
          <option value="express">Express</option></select></label>
          <button>Place order</button>
        </form><div id="status" role="status"></div><canvas width="100" height="50"></canvas>
    """)
    tools = browser.tools
    snapshot = tools.observe()
    name_id = element(snapshot, "Name").id
    assert snapshot.accessibility_tree and snapshot.dom_snapshot["documents"]
    assert snapshot.screenshot.startswith(b"\x89PNG")
    assert struct.unpack(">II", snapshot.screenshot[16:24]) == (1280, 720)
    assert not any(e.visible and e.name == "Hidden promo" for e in snapshot.elements)
    assert any(e.tag == "canvas" for e in snapshot.elements)
    snapshot = ok(tools.execute("type_text", element_id=name_id, text="Jane"))
    assert element(snapshot, "Name").id == name_id
    assert element(snapshot, "Name").value == "Jane"
    snapshot = ok(tools.execute("type_text", element_id=element(snapshot, "ZIP code").id, text="02139"))
    snapshot = ok(tools.execute("select_option", element_id=element(snapshot, "Shipping").id, value="express"))
    assert element(snapshot, "Shipping").value == "express"
    ok(tools.execute("click", element_id=element(snapshot, "Place order").id))
    assert browser.page.locator("#status").inner_text() == "Order placed"


def test_overlay_blocks_actions_until_dismissed(browser):
    browser.page.set_content("""
        <button id="delete" onclick="this.remove()">Delete message</button>
        <div style="position:fixed;inset:0;background:white;z-index:10">
          <button onclick="this.parentElement.remove()">Reject cookies</button>
        </div>
    """)
    tools = browser.tools
    snapshot = tools.observe()
    delete = element(snapshot, "Delete message")
    assert not delete.visible
    assert tools.execute("click", element_id=delete.id).status == "invalid_target"
    snapshot = ok(tools.execute("click", element_id=element(snapshot, "Reject cookies").id))
    assert element(snapshot, "Delete message").visible
    ok(tools.execute("click", element_id=delete.id))
    assert browser.page.locator("#delete").count() == 0
    assert tools.execute("click", element_id=delete.id).status == "invalid_target"


def test_navigation_expires_ids_and_back_works(browser, tmp_path):
    first, second = tmp_path / "first.html", tmp_path / "second.html"
    first.write_text("<button onclick=\"this.textContent='Clicked'\">First</button>")
    second.write_text("<button>Second</button>")
    tools = browser.tools
    snapshot = ok(tools.execute("navigate", url=first.as_uri()))
    old_id = element(snapshot, "First").id
    ok(tools.execute("navigate", url=second.as_uri()))
    assert tools.execute("click", element_id=old_id).status == "invalid_target"
    assert ok(tools.execute("back")).url == first.as_uri()


def test_removed_moved_disabled_and_stale_targets(browser):
    browser.page.set_content('<button id="target">Target</button><button disabled>Disabled</button>')
    tools = browser.tools
    snapshot = tools.observe()
    target = element(snapshot, "Target")
    assert tools.execute("click", element_id=element(snapshot, "Disabled").id).status == "invalid_target"
    browser.page.locator("#target").evaluate("e => e.style.marginLeft='100px'")
    assert tools.execute("click", element_id=target.id).status == "invalid_target"
    tools.observe()
    assert tools.execute("click", element_id=target.id, observation_version=snapshot.version).status == "invalid_target"
    browser.page.locator("#target").evaluate("e => e.remove()")
    assert tools.execute("click", element_id=target.id).status == "invalid_target"


def test_scrolling_and_coordinate_actions(browser):
    browser.page.set_content("""<div style="height:1500px"></div>
        <button onclick="this.textContent='Clicked'">Below fold</button>""")
    tools = browser.tools
    snapshot = tools.observe()
    assert not element(snapshot, "Below fold").visible
    assert tools.execute("click", element_id=element(snapshot, "Below fold").id).status == "invalid_target"
    snapshot = ok(tools.execute("scroll", direction="down", amount=1200))
    target = element(snapshot, "Below fold")
    assert target.visible
    ok(tools.execute("click_at", **target.point))
    assert browser.page.get_by_role("button").inner_text() == "Clicked"
    assert tools.execute("click_at", x=-1, y=50).status == "invalid_arguments"


def test_tabs_switching_and_cleanup(browser):
    browser.page.set_content("<button>Original</button>")
    tools = browser.tools
    initial = tools.observe()
    old_id = element(initial, "Original").id
    new = ok(tools.execute("new_tab"))
    assert len(new.tabs) == 2 and new.tab_id != initial.tab_id
    assert tools.execute("click", element_id=old_id).status == "invalid_target"
    snapshot = ok(tools.execute("switch_tab", tab_id=initial.tab_id))
    assert element(snapshot, "Original").id == old_id
    snapshot = ok(tools.execute("close_tab"))
    assert len(snapshot.tabs) == 1
    assert tools.execute("close_tab").status == "invalid_target"
    browser.start()
    assert browser.tools is not tools
    assert browser.tools.execute("click", element_id=old_id).status == "invalid_target"


def test_arguments_validated_before_input(browser):
    browser.page.set_content('<input aria-label="Name" value="untouched">')
    tools = browser.tools
    target = element(tools.observe(), "Name")
    assert tools.execute("type_text", element_id=target.id).status == "invalid_arguments"
    assert tools.execute("type_text", element_id=target.id, text=123).status == "invalid_arguments"
    assert browser.page.locator("input").input_value() == "untouched"
    assert tools.execute("navigate", url="javascript:alert(1)").status == "invalid_arguments"
    assert tools.execute("not_a_tool").status == "invalid_arguments"


def test_frame_boundary_and_open_shadow_dom(browser):
    browser.page.set_content("""<div id="host"></div><iframe srcdoc="<button>Inside frame</button>"></iframe>""")
    browser.page.locator("#host").evaluate("""e => {
        const button = document.createElement('button');
        button.textContent = 'Shadow';
        button.onclick = () => button.textContent = 'Done';
        e.attachShadow({mode:'open'}).append(button);
    }""")
    tools = browser.tools
    snapshot = tools.observe()
    assert snapshot.warnings
    assert any(e.tag == "iframe" for e in snapshot.elements)
    ok(tools.execute("click", element_id=element(snapshot, "Shadow").id))
    assert browser.page.get_by_role("button", name="Done").count() == 1


def test_keypress_and_drag(browser):
    browser.page.set_content("""<input aria-label="Name"><div style="display:flex;gap:50px">
        <div id="source" style="width:100px;height:100px;background:red">Source</div>
        <div id="target" role="button" aria-label="Drop" style="width:100px;height:100px;background:blue"
        onmouseup="this.textContent='Dropped'">Target</div></div>
        <script>document.querySelector('#source').setAttribute('role','button');
        document.querySelector('#source').setAttribute('aria-label','Drag');</script>""")
    tools = browser.tools
    snapshot = tools.observe()
    snapshot = ok(tools.execute("click", element_id=element(snapshot, "Name").id))
    ok(tools.execute("press_key", key="A"))
    assert browser.page.locator("input").input_value() == "A"
    snapshot = tools.observe()
    ok(tools.execute("drag", element_id=element(snapshot, "Drag").id, target_element_id=element(snapshot, "Drop").id))
    assert browser.page.locator("#target").inner_text() == "Dropped"


def test_dialogs_do_not_block_and_popups_are_listed(browser):
    browser.page.set_content("""<button onclick="alert('Notice')">Alert</button>
        <a href="about:blank" target="_blank">Open tab</a>""")
    tools = browser.tools
    snapshot = tools.observe()
    snapshot = ok(tools.execute("click", element_id=element(snapshot, "Alert").id))
    with browser.page.expect_popup():
        snapshot = ok(tools.execute("click", element_id=element(snapshot, "Open tab").id))
    assert len(ok(tools.execute("list_tabs")).tabs) == 2


def test_executed_action_is_not_reported_as_failed_if_capture_fails(browser, monkeypatch):
    from playwright.sync_api import Error

    browser.page.set_content("<button onclick=\"this.textContent='Done'\">Click</button>")
    tools = browser.tools
    target = element(tools.observe(), "Click")

    def fail():
        raise Error("capture failed")

    monkeypatch.setattr(tools, "observe", fail)
    result = tools.execute("click", element_id=target.id)
    assert result.executed and result.status == "observation_error"
    assert browser.page.get_by_role("button").inner_text() == "Done"


def test_cli_manual_tools(browser, tmp_path):
    import json

    from adaptive_cua.cli import run_chat

    page = tmp_path / "chat.html"
    page.write_text("<title>CLI test</title><button>Visible button</button>")
    commands = iter(
        [
            "/tool observe []",
            "/tool observe {broken}",
            "/tool navigate " + json.dumps({"url": page.as_uri()}),
            "/observe",
            '/tool click {"name":"wrong argument"}',
            "/quit",
        ]
    )
    output = []
    run_chat(browser, read=lambda prompt: next(commands), write=output.append)
    assert any("CLI test" in line for line in output)
    assert any("Visible button" in line for line in output)
    assert sum("Invalid tool arguments" in line for line in output) == 2
    assert any("invalid_arguments" in line for line in output)
    assert not browser.active
