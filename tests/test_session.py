from unittest.mock import Mock

import pytest

from src.browser.session import BrowserSession


def test_new_task_gets_fresh_browser_and_clears_storage():
    session = BrowserSession(headless=True)
    try:
        session.start()
        previous = session.browser
        session.context.add_cookies([{"name": "task", "value": "one", "url": "https://example.com"}])
        session.messages.append("first task")
        session.start()
        assert not previous.is_connected()
        assert session.active
        assert session.page.url == "about:blank"
        assert session.context.cookies() == []
        assert session.messages == []
    finally:
        session.close()
    assert not session.active
    session.close()


@pytest.mark.parametrize("failure", [RuntimeError("launch failed"), KeyboardInterrupt()])
def test_partial_launch_releases_runtime(monkeypatch, failure):
    runtime = Mock()
    runtime.chromium.launch.side_effect = failure
    monkeypatch.setattr("src.browser.session.sync_playwright", lambda: Mock(start=lambda: runtime))
    session = BrowserSession()
    with pytest.raises(type(failure)):
        session.start()
    runtime.stop.assert_called_once()
    assert not session.active


def test_runtime_stops_even_if_browser_close_fails():
    session = BrowserSession()
    session.browser = Mock()
    session.browser.close.side_effect = RuntimeError("close failed")
    runtime = session._playwright = Mock()
    with pytest.raises(RuntimeError):
        session.close()
    runtime.stop.assert_called_once()
    assert not session.active
