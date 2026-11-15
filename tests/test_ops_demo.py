import json
from pathlib import Path

from src.browser.session import BrowserSession
from src.ops.database import RunDatabase
from src.ops.trace import RunTrace


def test_sqlite_trace_lifecycle_and_usage(tmp_path):
    trace = RunTrace(tmp_path)
    trace.event("start", model="test-model", observation_mode="adaptive")
    trace.event("user", message="Patient's demo task")
    trace.event("response", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    trace.event("tool_result", call_id="c1", status="ok", executed=True)
    trace.event("compaction", usage={"total_tokens": 7})
    trace.event("pause", status="completed", message="Done")
    trace.close()
    trace.close()
    db = RunDatabase(tmp_path / "runs.sqlite3")
    run = db.runs()[0]
    assert run["status"] == "completed" and run["closed_at"]
    assert run["total_tokens"] == 22
    assert run["model"] == "test-model"
    events = db.events(run["run_id"])
    lines = [json.loads(line) for line in (trace.path / "events.jsonl").read_text().splitlines()]
    assert len(events) == len(lines) == 7
    for row, record in zip(events, lines, strict=True):
        assert row["kind"] == record.pop("event")
        assert row["time"] == record.pop("time")
        assert json.loads(row["payload_json"]) == record
    assert db.events("' OR 1=1 --") == []


def test_sqlite_runs_are_isolated_and_resume(tmp_path):
    first, second = RunTrace(tmp_path), RunTrace(tmp_path)
    first.event("pause", status="awaiting_user")
    second.event("pause", status="error")
    first.event("user", message="Continue")
    rows = {r["run_id"]: r for r in first.database.runs()}
    assert rows[first.path.name]["status"] == "running"
    assert rows[second.path.name]["status"] == "error"
    assert len(first.database.events(first.path.name)) == 2


def test_demo_submission_validation_reset_and_screenshot(tmp_path):
    session = BrowserSession(headless=True)
    session.start()
    try:
        page = session.page
        page.goto((Path(__file__).parents[1] / "src/eval/demo/index.html").as_uri())
        page.screenshot(path=str(tmp_path / "demo-initial.png"))
        page.get_by_label("Patient name", exact=True).fill("Wrong name")
        page.get_by_label("Date of birth (YYYY-MM-DD)").fill("1990-04-12")
        page.get_by_label("Member ID", exact=True).fill("DEMO-48271")
        page.get_by_label("Insurance plan", exact=True).select_option("cedar")
        page.get_by_role("button", name="Continue to medication").click()
        page.get_by_label("Requested medication", exact=True).select_option("relief")
        page.get_by_label("Quantity (tablets)").fill("30")
        page.get_by_label("Days supply", exact=True).fill("30")
        page.get_by_label("Diagnosis", exact=True).select_option("a")
        page.get_by_label("Previous therapy outcome").select_option("inadequate")
        page.get_by_label("Reason for request").fill("Symptoms persisted after 8 weeks of DemoFirst.")
        page.get_by_role("button", name="Review request").click()
        page.get_by_role("button", name="Submit demo request").click()
        assert "Confirm the request" in page.get_by_role("alert").inner_text()
        page.get_by_role("checkbox").check()
        page.get_by_role("button", name="Submit demo request").click()
        assert "does not match" in page.get_by_role("alert").inner_text()
        page.get_by_label("Patient name", exact=True).fill("Alex Morgan")
        page.get_by_role("button", name="Continue to medication").click()
        page.get_by_role("button", name="Review request").click()
        page.get_by_role("checkbox").check()
        page.get_by_role("button", name="Submit demo request").click()
        assert "PA-DEMO-1042" in page.get_by_role("status").inner_text()
        assert "Pending demo review" in page.get_by_role("status").inner_text()
        page.screenshot(path="/tmp/adaptive-cua-demo.png")
        page.get_by_role("button", name="Reset demo").click()
        assert page.get_by_label("Patient name", exact=True).input_value() == ""
        assert page.get_by_role("status").is_hidden()
    finally:
        session.close()
