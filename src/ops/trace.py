"""Per-task JSONL events and screenshots; no credentials or reasoning content."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.agent.observation import observation_data
from src.browser.models import BrowserSnapshot
from src.ops.database import RunDatabase


class RunTrace:
    def __init__(self, root: Path):
        self.path = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
        self.path.mkdir(parents=True)
        self.snapshots = 0
        self.closed = False
        self.database = RunDatabase(root / "runs.sqlite3")
        self.database.create_run(self.path.name, self.path, datetime.now(UTC).isoformat())

    def event(self, kind: str, **data):
        record = {"time": datetime.now(UTC).isoformat(), "event": kind, **data}
        self.database.append(self.path.name, record)
        with (self.path / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    def close(self):
        if not self.closed:
            self.event("close")
            self.closed = True

    def observation(self, snapshot: BrowserSnapshot, prepared=None):
        self.snapshots += 1
        filename = f"observation-{self.snapshots:04d}.png"
        (self.path / filename).write_bytes(snapshot.screenshot)
        self.event("observation", screenshot=filename, **observation_data(snapshot))
        if prepared is not None:
            text = json.dumps(prepared.data, ensure_ascii=False, separators=(",", ":"))
            image_files = []
            for index, png in enumerate(prepared.images):
                image_file = f"sent-{self.snapshots:04d}-{index:02d}.png"
                (self.path / image_file).write_bytes(png)
                image_files.append(image_file)
            self.event(
                "observation_sent",
                image_files=image_files,
                image_bytes=sum(len(png) for png in prepared.images),
                text_characters=len(text),
                text_bytes=len(text.encode("utf-8")),
                **prepared.data,
            )
