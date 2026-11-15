from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as PlaywrightError

from adaptive_cua.agent.loop import AgentOutcome
from adaptive_cua.browser.session import BrowserSession
from adaptive_cua.cli import run_chat


def reader(*events):
    pending = iter(events)

    def read(prompt):
        event = next(pending)
        if isinstance(event, BaseException):
            raise event
        return event

    return read


def fake_agent(session):
    agent = Mock()
    agent.run.return_value = AgentOutcome("reply", "Ready")
    return agent


def test_chat_preserves_followups_and_resets_task():
    session = Mock(spec=BrowserSession)
    session.active = False
    session.messages = []
    histories = []

    def start():
        histories.append(list(session.messages))
        session.messages.clear()
        session.active = True

    session.start.side_effect = start
    output = []
    agents = []

    def factory(session):
        agent = fake_agent(session)
        agents.append(agent)
        return agent

    run_chat(
        session, read=reader("first", "followup", "/new", "second", "/quit"), write=output.append, agent_factory=factory
    )
    assert histories == [[], ["first", "followup"]]
    assert session.messages == ["second"]
    session.close.assert_called_once()
    assert len(agents) == 2
    assert [call.args[0] for call in agents[0].run.call_args_list] == ["first", "followup"]
    assert [call.args[0] for call in agents[1].run.call_args_list] == ["second"]
    for agent in agents:
        agent.close.assert_called_once()


@pytest.mark.parametrize("ending", ["/quit", EOFError()])
def test_interrupt_and_commands_do_not_launch_browser(ending):
    session = Mock(spec=BrowserSession)
    output = []
    run_chat(session, read=reader("", "/help", "/unknown", KeyboardInterrupt(), ending), write=output.append)
    session.start.assert_not_called()
    session.close.assert_called_once()
    assert any("Interrupted" in line for line in output)


def test_unexpected_error_still_closes_session():
    session = Mock(spec=BrowserSession)
    with pytest.raises(RuntimeError):
        run_chat(session, read=reader(RuntimeError("input failed")), write=lambda text: None)
    session.close.assert_called_once()


def test_failed_browser_start_can_be_retried():
    session = Mock(spec=BrowserSession)
    session.active = False
    session.messages = []
    session.start.side_effect = [PlaywrightError("launch failed"), None]
    output = []
    run_chat(session, read=reader("first", "retry", "/quit"), write=output.append, agent_factory=fake_agent)
    assert session.start.call_count == 2
    assert session.messages == ["retry"]
    assert any("Browser error" in line for line in output)
    session.close.assert_called_once()
