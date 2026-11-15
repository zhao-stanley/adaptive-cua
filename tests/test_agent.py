import copy
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from adaptive_cua.agent.config import AgentConfig, ConfigurationError
from adaptive_cua.agent.loop import Agent
from adaptive_cua.agent.model import ModelError, OpenAIModel
from adaptive_cua.agent.observation import observation_content
from adaptive_cua.agent.tools import TOOL_SCHEMAS, parse_arguments
from adaptive_cua.browser.models import ActionResult, BrowserSnapshot, Element
from adaptive_cua.browser.session import BrowserSession


def snapshot(version=1):
    png = io.BytesIO()
    Image.new("RGB", (1280, 720), "white").save(png, format="PNG")
    return BrowserSnapshot(
        version,
        "t1",
        "about:blank",
        "Test",
        {"width": 1280, "height": 720},
        [
            Element("e1", "button", "button", "Visible", "", {}, True, True, False, None),
            Element("hidden", "button", "button", "SECRET", "", {}, False, True, False, None),
        ],
        png.getvalue(),
        [{"secret": "RAW AX"}],
        {"secret": "RAW DOM"},
        [],
    )


def call(name, arguments, call_id="c1"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def response(*outputs, status="completed", tokens=10):
    return {"id": "r1", "status": status, "output": list(outputs), "usage": {"total_tokens": tokens}}


def latest_observation(history):
    state, latest = {}, None
    for item in history:
        content = item.get("content", item.get("output", []))
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "input_text":
                try:
                    data = json.loads(part["text"])
                except ValueError:
                    continue
                if isinstance(data, dict) and "observation_version" in data:
                    if data.get("structure_mode", "full") == "full":
                        state = {element["id"]: element for element in data["elements"]}
                    else:
                        assert latest is not None and data["base_version"] == latest["observation_version"]
                        for key in data["removed"]:
                            state.pop(key)
                        for element in data["added"] + data["updated"]:
                            state[element["id"]] = element
                    latest = {**data, "elements": list(state.values())}
    assert latest is not None, "No observation supplied"
    return latest


class ScriptedModel:
    def __init__(self, *steps):
        self.steps = iter(steps)
        self.requests = []
        self.closed = False

    def respond(self, history, **kwargs):
        self.requests.append(copy.deepcopy(history))
        step = next(self.steps)
        if isinstance(step, BaseException):
            raise step
        return step(history) if callable(step) else step

    def close(self):
        self.closed = True


def make_agent(tmp_path, model, **limits):
    session = Mock(spec=BrowserSession)
    session.tools.execute.return_value = ActionResult("ok", snapshot())
    return Agent(session, model, AgentConfig("test-model", trace_dir=tmp_path, **limits))


def test_clarification_resumes_with_paired_calls_and_reasoning(tmp_path):
    reasoning = {"type": "reasoning", "id": "rs1", "summary": [], "encrypted_content": "opaque"}
    model = ScriptedModel(
        response(reasoning, call("ask_user", {"question": "What name?"})),
        response(call("finish", {"status": "completed", "message": "Done"}, "c2")),
    )
    agent = make_agent(tmp_path, model)
    assert agent.run("Fill the form", emit=lambda _: None).status == "awaiting_user"
    assert agent.run("Jane", emit=lambda _: None).status == "completed"
    assert reasoning in model.requests[1]
    assert any(item.get("call_id") == "c1" and item["type"] == "function_call_output" for item in model.requests[1])
    assert agent.total_tokens == 20
    events = (agent.trace.path / "events.jsonl").read_text()
    assert "opaque" not in events and "SECRET" not in events and "RAW DOM" not in events
    assert len(list(agent.trace.path.glob("*.png"))) == 2
    agent.close()
    assert model.closed


def test_observation_excludes_raw_and_hidden_content():
    content = observation_content(snapshot())
    assert "Visible" in content[0]["text"]
    assert "SECRET" not in content[0]["text"] and "RAW AX" not in content[0]["text"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_unknown_and_malformed_tools_return_errors_without_execution(tmp_path):
    bad = call("click", {"element_id": "e1", "observation_version": 1})
    bad["arguments"] = "{bad"
    model = ScriptedModel(
        response(bad),
        response(call("shell", {})),
        response(
            call(
                "finish",
                {
                    "status": "blocked",
                    "message": "Cannot proceed",
                },
                "c3",
            )
        ),
    )
    agent = make_agent(tmp_path, model)
    assert agent.run("Task", emit=lambda _: None).status == "blocked"
    assert [c.args[0] for c in agent.session.tools.execute.call_args_list] == ["observe"]
    assert "invalid_arguments" in json.dumps(model.requests[1])


def test_multiple_actions_are_rejected_as_a_batch(tmp_path):
    model = ScriptedModel(
        response(
            call("click", {"element_id": "e1", "observation_version": 1}),
            call("press_key", {"key": "Enter", "observation_version": 1}, "c2"),
        ),
        response(call("finish", {"status": "blocked", "message": "Stopped"}, "c3")),
    )
    agent = make_agent(tmp_path, model)
    agent.run("Task", emit=lambda _: None)
    agent.session.tools.execute.assert_called_once_with("observe")
    outputs = [i for i in model.requests[1] if i.get("type") == "function_call_output"]
    assert len(outputs) == 2


def test_failed_action_refreshes_without_repeating(tmp_path):
    model = ScriptedModel(
        response(call("click", {"element_id": "old", "observation_version": 1})),
        response(call("finish", {"status": "blocked", "message": "Target missing"}, "c2")),
    )
    agent = make_agent(tmp_path, model)
    agent.session.tools.execute.side_effect = [
        ActionResult("ok", snapshot()),
        ActionResult("invalid_target", error="stale"),
        ActionResult("ok", snapshot(2)),
    ]
    agent.run("Task", emit=lambda _: None)
    assert [c.args[0] for c in agent.session.tools.execute.call_args_list] == ["observe", "click", "observe"]
    assert latest_observation(model.requests[1])["observation_version"] == 2


def test_interrupted_action_is_paired_and_never_replayed(tmp_path):
    model = ScriptedModel(
        response(call("click", {"element_id": "e1", "observation_version": 1})),
        response(call("finish", {"status": "blocked", "message": "Paused"}, "c2")),
    )
    agent = make_agent(tmp_path, model)
    agent.session.tools.execute.side_effect = [
        ActionResult("ok", snapshot()),
        KeyboardInterrupt(),
        ActionResult("ok", snapshot(2)),
    ]
    with pytest.raises(KeyboardInterrupt):
        agent.run("Task", emit=lambda _: None)
    assert agent.run("Continue", emit=lambda _: None).status == "blocked"
    assert [c.args[0] for c in agent.session.tools.execute.call_args_list].count("click") == 1
    assert "interrupted" in json.dumps(model.requests[1])


@pytest.mark.parametrize("limits,steps", [({"max_steps": 1}, 1), ({"max_tokens": 5}, 0)])
def test_limits_stop_before_extra_actions(tmp_path, limits, steps):
    model = ScriptedModel(response(call("click", {"element_id": "e1", "observation_version": 1})))
    agent = make_agent(tmp_path, model, **limits)
    assert agent.run("Task", emit=lambda _: None).status == "limit"
    assert [c.args[0] for c in agent.session.tools.execute.call_args_list].count("click") == steps
    assert len(model.requests) == 1
    assert any(i.get("type") == "function_call_output" for i in agent.history)


def test_deadline_stops_before_request(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, ScriptedModel())
    clock = iter([0, 1000])
    monkeypatch.setattr("adaptive_cua.agent.loop.time.monotonic", lambda: next(clock))
    assert agent.run("Task", emit=lambda _: None).status == "limit"
    assert not agent.model.requests


def test_incomplete_response_does_not_execute_or_poison_history(tmp_path):
    model = ScriptedModel(response(call("click", {}), status="incomplete"))
    agent = make_agent(tmp_path, model)
    assert agent.run("Task", emit=lambda _: None).status == "error"
    agent.session.tools.execute.assert_called_once_with("observe")
    assert not any(i["type"] == "function_call" for i in agent.history if "type" in i)


def test_api_error_returns_to_user(tmp_path):
    agent = make_agent(tmp_path, ScriptedModel(ModelError("Request failed")))
    assert agent.run("Task", emit=lambda _: None).message == "Request failed"


def test_config_and_missing_credentials(monkeypatch):
    with pytest.raises(ConfigurationError):
        AgentConfig("")
    with pytest.raises(ConfigurationError):
        AgentConfig("test", max_seconds=float("nan"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        OpenAIModel(AgentConfig("test"))


def test_schema_contract():
    for schema in TOOL_SCHEMAS:
        params = schema["parameters"]
        assert schema["strict"] and params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
    assert parse_arguments(
        "scroll",
        json.dumps(
            {
                "direction": "down",
                "amount": None,
                "element_id": None,
                "observation_version": 1,
            }
        ),
    ) == {"direction": "down", "observation_version": 1}
    with pytest.raises(ValueError):
        parse_arguments("click", json.dumps({"element_id": "e1"}))


def test_adapter_request_preserves_protocol(monkeypatch):
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(model_dump=lambda **kwargs: response())
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    monkeypatch.setattr("adaptive_cua.agent.model.OpenAI", lambda **kwargs: client)
    model = OpenAIModel(AgentConfig("configured-model"))
    history = [{"role": "user", "content": observation_content(snapshot())}]
    model.respond(history, timeout=12, max_output_tokens=100)
    request = client.responses.create.call_args.kwargs
    assert request["input"] is history
    assert request["model"] == "configured-model"
    assert request["store"] is False and request["parallel_tool_calls"] is False
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["timeout"] == 12 and request["max_output_tokens"] == 100


def browser_call(name, target_name=None, **args):
    def choose(history):
        current = latest_observation(history)
        arguments = dict(args, observation_version=current["observation_version"])
        if target_name:
            arguments["element_id"] = next(e["id"] for e in current["elements"] if e["name"] == target_name)
        return response(call(name, arguments, f"c{len(history)}"))

    return choose


def test_real_browser_checkout_driven_by_model_responses(tmp_path):
    page = tmp_path / "checkout.html"
    page.write_text("""<form onsubmit="event.preventDefault();document.querySelector('h2').textContent='Order placed'">
        <label>Name<input required></label><input placeholder="ZIP" required>
        <label>Shipping<select><option value="standard">Standard</option>
        <option value="express">Express</option></select></label>
        <button>Place order</button></form><h2></h2>""")

    def finish(history):
        assert any(e["name"] == "Order placed" for e in latest_observation(history)["elements"])
        return response(call("finish", {"status": "completed", "message": "The page confirms Order placed."}, "done"))

    model = ScriptedModel(
        browser_call("navigate", url=page.as_uri()),
        browser_call("type_text", "Name", text="Jane"),
        browser_call("type_text", "ZIP", text="02139"),
        browser_call("select_option", "Shipping", value="express"),
        browser_call("click", "Place order"),
        finish,
    )
    session = BrowserSession(headless=True)
    session.start()
    agent = Agent(session, model, AgentConfig("test", trace_dir=tmp_path / "runs"))
    try:
        result = agent.run("Complete checkout for Jane, ZIP 02139, express shipping.", emit=lambda _: None)
        assert result.status == "completed"
        assert session.page.locator("h2").inner_text() == "Order placed"
        assert session.page.locator("select").input_value() == "express"
        assert len(model.requests) == 6
        assert len(list(agent.trace.path.glob("observation-*.png"))) == 6
    finally:
        agent.close()
        session.close()


def test_real_browser_inbox_driven_by_model_responses(tmp_path):
    session = BrowserSession(headless=True)
    session.start()
    session.page.set_content("""<button onclick="this.remove()">Delete spam</button>
        <div style="position:fixed;inset:0;background:white">
        <button onclick="this.parentElement.remove()">Reject cookies</button></div>""")
    model = ScriptedModel(
        browser_call("click", "Reject cookies"),
        browser_call("click", "Delete spam"),
        response(call("finish", {"status": "completed", "message": "Spam deleted."}, "done")),
    )
    agent = Agent(session, model, AgentConfig("test", trace_dir=tmp_path / "runs"))
    try:
        assert agent.run("Delete spam", emit=lambda _: None).status == "completed"
        assert session.page.get_by_role("button", name="Delete spam").count() == 0
        assert "Delete spam" not in [e["name"] for e in latest_observation(model.requests[0])["elements"]]
    finally:
        agent.close()
        session.close()


def test_real_sdk_serializes_text_and_image_tool_results(monkeypatch):
    import httpx2
    from openai import OpenAI

    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                **response(call("ask_user", {"question": "Which URL?"})),
                "object": "response",
                "created_at": 0,
                "model": "test",
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    model = OpenAIModel(AgentConfig("test"))
    model.client.close()
    model.client = OpenAI(
        api_key="test-not-real", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))
    )
    history = [
        call("observe", {}),
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": observation_content(snapshot()),
        },
    ]
    try:
        result = model.respond(history, timeout=5, max_output_tokens=100)
        assert result["output"][0]["name"] == "ask_user"
        assert captured[0]["input"][1]["output"][1]["type"] == "input_image"
        assert captured[0]["tools"][0]["type"] == "function"
    finally:
        model.close()


def test_model_can_explicitly_request_screenshot_with_adaptive_default(tmp_path):
    model = ScriptedModel(
        response(call("request_observation", {"detail": "screenshot"})),
        response(call("finish", {"status": "blocked", "message": "Paused"}, "c2")),
    )
    agent = make_agent(tmp_path, model)
    agent.run("Inspect the page", emit=lambda _: None)
    assert not any(p.get("type") == "input_image" for item in model.requests[0] for p in item.get("content", []))
    result = next(item for item in model.requests[1] if item.get("type") == "function_call_output")
    assert any(part.get("type") == "input_image" for part in result["output"])
    events = [json.loads(line) for line in (agent.trace.path / "events.jsonl").read_text().splitlines()]
    sent = [event for event in events if event["event"] == "observation_sent"]
    assert [event["level"] for event in sent] == ["structure", "screenshot"]
    assert (agent.trace.path / sent[1]["image_files"][0]).exists()
