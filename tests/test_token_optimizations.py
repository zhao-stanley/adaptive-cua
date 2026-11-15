import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.agent.tools import parse_arguments
from src.browser.session import BrowserSession
from src.representation.observations import AdaptiveObservations
from src.representation.structure import visible_structure


@pytest.fixture
def browser():
    session = BrowserSession(headless=True)
    session.start()
    yield session
    session.close()


def fields(snapshot):
    return {e.name: e.id for e in snapshot.elements if e.visible and e.tag in {"input", "select"}}


def test_options_and_batch_fill(browser):
    browser.page.set_content("""<label>Name<input></label><label>Plan<select>
    <option value="">Choose</option><option value="cedar">Cedar Demo Health</option>
    <optgroup label="Unavailable" disabled><option value="old">Old plan</option></optgroup>
    <option hidden value="secret">Hidden</option></select></label>""")
    initial = browser.tools.observe()
    ids = fields(initial)
    select = next(e for e in initial.elements if e.tag == "select")
    assert select.options == [
        {"label": "Choose", "value": "", "disabled": False},
        {"label": "Cedar Demo Health", "value": "cedar", "disabled": False},
        {"label": "Old plan", "value": "old", "disabled": True},
    ]
    result = browser.tools.fill_form(
        [
            dict(action="type_text", element_id=ids["Name"], value="Alex"),
            dict(action="select_option", element_id=ids["Plan"], value="cedar"),
        ],
        initial.version,
    )
    assert result.status == "ok" and len(result.steps) == 2
    assert result.observation.version == initial.version + 1
    assert browser.page.locator("input").input_value() == "Alex"
    assert browser.page.locator("select").input_value() == "cedar"


@pytest.mark.parametrize(
    "change",
    [
        "b.style.marginTop='30px'",
        "b.value='Someone else'",
        "b.setAttribute('aria-label','New target')",
        "b.disabled=true",
    ],
)
def test_batch_stops_before_changed_field(browser, change):
    browser.page.set_content(f'''<input aria-label="First" oninput="{change}">
        <input id="b" aria-label="Second">''')
    initial = browser.tools.observe()
    ids = fields(initial)
    result = browser.tools.fill_form(
        [
            dict(action="type_text", element_id=ids["First"], value="Alex"),
            dict(action="type_text", element_id=ids["Second"], value="MUST NOT TYPE"),
        ],
        initial.version,
    )
    assert result.status == "batch_stopped" and len(result.steps) == 1
    assert browser.page.locator("#b").input_value() != "MUST NOT TYPE"


def test_invalid_batch_is_rejected_before_any_input(browser):
    browser.page.set_content('<input aria-label="Name">')
    initial = browser.tools.observe()
    action = dict(action="type_text", element_id=fields(initial)["Name"], value="Alex")
    result = browser.tools.fill_form([action, action], initial.version)
    assert result.executed is False and browser.page.locator("input").input_value() == ""
    with pytest.raises(ValueError):
        parse_arguments("fill_form", json.dumps(dict(observation_version=initial.version, actions=[action, action])))


def test_form_focus_decoration_does_not_create_image(browser):
    browser.page.set_content("""<style>input:focus{outline:3px solid orange;outline-offset:3px}</style>
        <label>Patient name<input></label>""")
    adapter = AdaptiveObservations()
    initial = browser.tools.observe()
    adapter.prepare(initial)
    result = browser.tools.execute("type_text", element_id=fields(initial)["Patient name"], text="Alex Morgan")
    prepared = adapter.prepare(result.observation)
    assert prepared.images == []


def test_payload_reduction_and_reconstructible_defaults(browser):
    browser.page.goto((Path(__file__).parents[1] / "src/eval/demo/index.html").as_uri())
    snapshot = browser.tools.observe()
    old = []
    for e in snapshot.elements:
        if e.visible:
            data = asdict(e)
            for k in ("visible", "point", "text_rects", "options", "decoration_padding"):
                data.pop(k)
            old.append(data)
    compact = visible_structure(snapshot)
    encoded = json.dumps(list(compact.values()), separators=(",", ":"))
    assert len(encoded) < len(json.dumps(old)) * 0.8
    prepared = AdaptiveObservations().prepare(snapshot)
    assert '": ' not in prepared.content()[0]["text"]
    print(f"Element JSON: {len(json.dumps(old))} -> {len(encoded)} characters; {len(prepared.images)} images")


def test_agent_batches_fields_in_one_model_turn_and_logs_each_action(browser, tmp_path):
    from test_agent import ScriptedModel, call, latest_observation, response

    from src.agent.config import AgentConfig
    from src.agent.loop import Agent

    browser.page.set_content("<label>First<input></label><label>Second<input></label>")

    def choose(history):
        obs = latest_observation(history)
        inputs = [e for e in obs["elements"] if e["tag"] == "input"]
        return response(
            call(
                "fill_form",
                dict(
                    observation_version=obs["observation_version"],
                    actions=[dict(action="type_text", element_id=e["id"], value=e["name"] + " value") for e in inputs],
                ),
            )
        )

    def finish(history):
        obs = latest_observation(history)
        assert all(e["value"] == e["name"] + " value" for e in obs["elements"] if e["tag"] == "input")
        return response(call("finish", dict(status="completed", message="Both fields filled"), "c2"))

    agent = Agent(browser, ScriptedModel(choose, finish), AgentConfig("test", trace_dir=tmp_path))
    try:
        assert agent.run("Fill fields", emit=lambda _: None).status == "completed"
        assert len(agent.model.requests) == 2
        events = agent.trace.database.events(agent.trace.path.name)
        completed = [json.loads(e["payload_json"]) for e in events if e["kind"] == "batch_action"]
        assert sum(e["status"] == "ok" for e in completed) == 2
    finally:
        agent.close()


def test_compaction_preserves_sqlite_audit_and_uses_text_baseline(tmp_path):
    from test_agent import call, make_agent, response
    from test_inspection_memory import CompactModel, high_response

    model = CompactModel(
        high_response(call("observe", {})), response(call("finish", dict(status="completed", message="Done"), "c2"))
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    try:
        assert agent.run("Remember this task", emit=lambda _: None).status == "completed"
        rows = agent.trace.database.events(agent.trace.path.name)
        assert any(e["kind"] == "user" and "Remember this task" in e["payload_json"] for e in rows)
        assert any(e["kind"] == "compaction" for e in rows)
        assert not any("private-memory" in e["payload_json"] for e in rows)
        assert agent.history[:2] == model.window
    finally:
        agent.close()


def test_batch_stops_on_navigation(browser):
    # Native select dispatches change synchronously; verify navigation prevents remaining fields.
    browser.page.set_content("""<label>Plan<select onchange="location.href='about:blank#changed'">
        <option value="a">A</option><option value="b">B</option></select></label><input aria-label="Second">""")
    initial = browser.tools.observe()
    ids = fields(initial)
    result = browser.tools.fill_form(
        [
            dict(action="select_option", element_id=ids["Plan"], value="b"),
            dict(action="type_text", element_id=ids["Second"], value="MUST NOT TYPE"),
        ],
        initial.version,
    )
    # Same-document URL changes are navigation too.
    assert result.status == "batch_stopped"
    assert browser.page.get_by_label("Second").input_value() == ""


def test_large_retained_context_does_not_compact_every_turn(tmp_path):
    from test_agent import call, make_agent
    from test_inspection_memory import CompactModel, high_response

    model = CompactModel(
        high_response(call("observe", {})),
        high_response(call("observe", {}, "c2")),
        high_response(call("observe", {}, "c3")),
        high_response(call("finish", dict(status="completed", message="Done"), "c4")),
    )
    agent = make_agent(tmp_path, model, compact_after_tokens=100)
    try:
        assert agent.run("Goal", emit=lambda _: None).status == "completed"
        assert len(model.compactions) == 1
    finally:
        agent.close()
