"""Full-observation helpers for traces and direct API diagnostics."""

import base64
import json

from src.browser.models import BrowserSnapshot
from src.representation.structure import visible_structure


def observation_data(snapshot: BrowserSnapshot) -> dict:
    return {
        "observation_version": snapshot.version,
        "tab_id": snapshot.tab_id,
        "url": snapshot.url,
        "title": snapshot.title,
        "viewport": snapshot.viewport,
        "tabs": snapshot.tabs,
        "elements": list(visible_structure(snapshot, compact=False).values()),
        "document_id": snapshot.document_id,
        "warnings": snapshot.warnings,
    }


def observation_content(snapshot: BrowserSnapshot) -> list[dict]:
    return [
        {"type": "input_text", "text": json.dumps(observation_data(snapshot), ensure_ascii=False)},
        {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(snapshot.screenshot).decode()},
    ]
