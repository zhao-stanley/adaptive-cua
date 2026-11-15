import copy
import io
import json

import pytest
from PIL import Image
from test_agent import ScriptedModel, call, latest_observation, make_agent, response, snapshot

from src.agent.model import ModelError
from src.agent.tools import parse_arguments
from src.representation.observations import AdaptiveObservations


def region(**changes):
    return dict(x=10.2, y=20.2, width=100, height=50, zoom=3) | changes


def test_zoom_bounds_pixels_and_refresh():
    adapter = AdaptiveObservations()
    first = adapter.prepare(snapshot(), region=region())
    visual = first.data["visuals"][0]
    assert visual["bounds"] == dict(x=10, y=20, width=101, height=51)
    assert visual["scale"] == 3
    assert Image.open(io.BytesIO(first.images[0])).size == (303, 153)
    # Local pixel (30, 60) maps to viewport (20, 40).
    assert visual["bounds"]["x"] + 30 / visual["scale"] == 20
    assert visual["bounds"]["y"] + 60 / visual["scale"] == 40
    second = adapter.prepare(snapshot(2), region=region())
    assert second.data["structure_mode"] == "delta"
    assert second.data["visuals"][0]["image_index"] == 0
    assert second.data["visuals"][0]["source_observation_version"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        dict(x=-1),
        dict(width=0),
        dict(x=float("nan")),
        dict(width=float("inf")),
        dict(zoom=True),
        dict(zoom=1.5),
        dict(zoom=5),
        dict(x=1200),
        dict(x=0, y=0, width=1280, height=720, zoom=4),
    ],
)
def test_invalid_regions_leave_baseline_intact(changes):
    adapter = AdaptiveObservations()
    adapter.prepare(snapshot())
    with pytest.raises(ValueError):
        adapter.prepare(snapshot(2), region=region(**changes))
    assert adapter.previous.version == 1


def test_inspection_dispatch_and_recovery(tmp_path):
    model = ScriptedModel(
        response(call("inspect_region", region(width=-1))),
        response(call("inspect_region", region(), "c2")),
        response(call("finish", dict(status="completed", message="Seen"), "c3")),
    )
    agent = make_agent(tmp_path, model)
    assert agent.run("Inspect", emit=lambda _: None).status == "completed"
    assert "invalid_arguments" in json.dumps(model.requests[1])
    observation = latest_observation(model.requests[2])
    assert observation["visuals"][0]["scale"] == 3
    assert all(c.args == ("observe",) for c in agent.session.tools.execute.call_args_list)
    with pytest.raises(ValueError):
        parse_arguments("inspect_region", json.dumps(region(zoom=2.5)))


class CompactModel(ScriptedModel):
    def __init__(self, *steps, fail=False, tokens=20):
        super().__init__(*steps)
        self.compactions = []
        self.fail, self.tokens = fail, tokens
        self.window = [
            dict(role="user", content="Retained goal"),
            dict(type="compaction", id="cmp1", encrypted_content="private-memory"),
        ]

    def compact(self, history, **kwargs):
        self.compactions.append(copy.deepcopy(history))
        assert kwargs["timeout"] > 0
        if self.fail:
            raise ModelError("Failed")
        return dict(output=copy.deepcopy(self.window), usage=dict(total_tokens=self.tokens))


def high_response(*outputs):
    return response(*outputs) | dict(usage=dict(input_tokens=100, output_tokens=10, total_tokens=110))


def test_compaction_keeps_window_pairs_calls_and_resets_visuals(tmp_path):
    model = CompactModel(
        high_response(call("observe", {})), response(call("finish", dict(status="completed", message="Done"), "c2"))
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    assert agent.run("Goal", emit=lambda _: None).status == "completed"
    assert model.requests[1][:2] == model.window
    compact_input = model.compactions[0]
    assert any(i.get("type") == "function_call_output" and i["call_id"] == "c1" for i in compact_input)
    fresh = latest_observation(model.requests[1])
    assert fresh["structure_mode"] == "full" and fresh["reset_visuals"]
    assert fresh["visuals"] == []  # Text-only baseline needs no screenshot.
    assert agent.total_tokens == 140
    assert "private-memory" not in (agent.trace.path / "events.jsonl").read_text()


def test_compaction_failure_keeps_history_and_disables_retries(tmp_path):
    model = CompactModel(
        high_response(call("observe", {})),
        high_response(call("observe", {}, "c2")),
        response(call("finish", dict(status="completed", message="Done"), "c3")),
        fail=True,
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    assert agent.run("Goal", emit=lambda _: None).status == "completed"
    assert len(model.compactions) == 1
    assert model.requests[1][: len(model.compactions[0])] == model.compactions[0]


def test_compaction_usage_stops_next_model_request(tmp_path):
    model = CompactModel(high_response(call("observe", {})), tokens=100)
    agent = make_agent(tmp_path, model, compact_after_tokens=100, max_tokens=200)
    assert agent.run("Goal", emit=lambda _: None).status == "limit"
    assert len(model.requests) == 1
    assert agent.total_tokens == 210


def test_compaction_disabled_and_followup_memory(tmp_path):
    model = CompactModel(
        high_response(call("ask_user", dict(question="Name?"))),
        response(call("finish", dict(status="completed", message="Done"), "c2")),
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=0)
    agent.run("Goal", emit=lambda _: None)
    agent.run("Jane", emit=lambda _: None)
    assert not model.compactions
    assert "Jane" in json.dumps(model.requests[1]) and "Goal" in json.dumps(model.requests[1])


def test_compaction_sdk_roundtrip(monkeypatch):
    import httpx2
    from openai import OpenAI

    from src.agent.config import AgentConfig
    from src.agent.model import OpenAIModel

    captured = []
    window = [dict(type="compaction", id="cmp1", encrypted_content="opaque")]

    def transport(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx2.Response(
            200,
            json=dict(
                id="cmp-response",
                object="response.compaction",
                created_at=0,
                output=window,
                usage=dict(input_tokens=100, output_tokens=10, total_tokens=110),
            ),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    model = OpenAIModel(AgentConfig("test"))
    model.client.close()
    model.client = OpenAI(
        api_key="test-not-real", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))
    )
    try:
        result = model.compact([dict(role="user", content="Task")], timeout=5)
        assert result["output"] == window
        assert captured[0][0] == "/v1/responses/compact"
        assert captured[0][1]["model"] == "test"
        assert captured[0][1]["input"] == [dict(role="user", content="Task")]
    finally:
        model.close()


def test_browser_inspection_and_compaction(tmp_path):
    from src.agent.config import AgentConfig
    from src.agent.loop import Agent
    from src.browser.session import BrowserSession

    session = BrowserSession(headless=True)
    session.start()
    try:
        session.page.set_content('<canvas width="100" height="100"></canvas>')
        session.page.locator("canvas").evaluate(
            "c => {let x=c.getContext('2d'); x.fillStyle='red'; x.fillRect(0,0,100,100)}"
        )
        model = CompactModel(
            high_response(call("inspect_region", region(x=10, y=20, width=20, height=20))),
            response(call("finish", dict(status="completed", message="Inspected"), "c2")),
        )
        agent = Agent(session, model, AgentConfig("test", trace_dir=tmp_path, compact_after_tokens=100))
        assert agent.run("Inspect red canvas", emit=lambda _: None).status == "completed"
        tool_output = next(i for i in model.compactions[0] if i.get("type") == "function_call_output")
        assert any(p["type"] == "input_image" for p in tool_output["output"])
        assert latest_observation(model.requests[1])["level"] == "crops"
        assert latest_observation(model.requests[1])["visuals"][0]["image_index"] == 0
    finally:
        session.close()


def test_compaction_on_followup_retains_latest_user_input(tmp_path):
    model = CompactModel(
        high_response(call("ask_user", dict(question="Name?"))),
        response(call("finish", dict(status="completed", message="Done"), "c2")),
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    assert agent.run("Fill form", emit=lambda _: None).status == "awaiting_user"
    assert agent.run("Jane", emit=lambda _: None).status == "completed"
    assert "Jane" in json.dumps(model.compactions[0])
    assert any(i.get("type") == "function_call_output" for i in model.compactions[0])


def test_interrupted_compaction_preserves_history(tmp_path):
    model = CompactModel(high_response(call("observe", {})))

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    model.compact = interrupt
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    with pytest.raises(KeyboardInterrupt):
        agent.run("Goal", emit=lambda _: None)
    assert "Goal" in json.dumps(agent.history)
    assert agent.history[-1]["type"] == "function_call_output"
    assert agent.observations.previous is None


def test_invalid_compaction_window_keeps_history(tmp_path):
    model = CompactModel(
        high_response(call("observe", {})), response(call("finish", dict(status="completed", message="Done"), "c2"))
    )
    model.window = []
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    assert agent.run("Goal", emit=lambda _: None).status == "completed"
    assert agent.compaction_disabled
    assert model.requests[1][: len(model.compactions[0])] == model.compactions[0]
