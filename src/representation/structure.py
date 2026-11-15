"""Canonical visible structure and lossless element-level changes."""

from dataclasses import asdict

from src.browser.models import BrowserSnapshot


def visible_structure(snapshot: BrowserSnapshot, *, compact=True) -> dict[str, dict]:
    result = {}
    for element in snapshot.elements:
        if not element.visible:
            continue
        data = asdict(element)
        for key in ("visible", "point", "text_rects", "decoration_padding"):
            data.pop(key)
        data["bounds"] = {key: round(value, 1) for key, value in data["bounds"].items()}
        if not compact:
            result[element.id] = data
            continue
        # Empty structural wrappers contribute no user-visible information.
        if (
            not element.name
            and not element.text
            and element.role in {"", "generic", "none"}
            and element.tag in {"div", "span", "section", "main", "body", "html"}
        ):
            continue
        if data["text"] == data["name"]:
            data.pop("text")
        for key in ("name", "role", "value", "text", "states", "options"):
            if key in data and not data[key]:
                data.pop(key)
        if data["enabled"]:
            data.pop("enabled")
        if not data["editable"]:
            data.pop("editable")
        result[element.id] = data
    return result


def diff(previous: dict[str, dict], current: dict[str, dict]) -> dict:
    return {
        "added": [value for key, value in current.items() if key not in previous],
        "updated": [value for key, value in current.items() if key in previous and value != previous[key]],
        "removed": [key for key in previous if key not in current],
    }


def meaningful_change(previous: dict[str, dict], current: dict[str, dict]) -> bool:
    # Focus alone is not evidence that a submit/delete/etc. operation succeeded.
    def without_focus(records):
        return {
            key: {**value, "states": {k: v for k, v in value.get("states", {}).items() if k != "focused"}}
            for key, value in records.items()
        }

    return without_focus(previous) != without_focus(current)


def page_key(snapshot: BrowserSnapshot) -> tuple:
    return (
        snapshot.tab_id,
        snapshot.document_id,
        snapshot.url,
        snapshot.viewport["width"],
        snapshot.viewport["height"],
    )
