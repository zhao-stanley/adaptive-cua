"""Strict OpenAI function schemas and local argument validation."""

import json

from src.browser.tools import TOOL_ARGUMENTS

DESCRIPTIONS = {
    "observe": "Refresh full visible page structure; images are added automatically when needed.",
    "list_tabs": "List current tabs and observe the active page.",
    "navigate": "Navigate the active tab to an absolute http, https, or file URL, or about:blank.",
    "back": "Navigate back in the current tab's history.",
    "click": "Click a visible enabled element from the latest observation.",
    "type_text": "Replace the text of an editable element with the supplied text.",
    "select_option": "Select an enabled option by its value in a native select element.",
    "press_key": "Press a key or chord, e.g. Enter, Tab, or ControlOrMeta+A, in the active tab.",
    "scroll": "Scroll up, down, left, or right; optionally hover a visible element to scroll its area.",
    "click_at": "Click viewport CSS coordinates, for visual targets without usable element IDs.",
    "drag": "Drag a visible source element to a visible target element.",
    "new_tab": "Open a new tab, optionally at a URL, and activate it.",
    "switch_tab": "Activate a tab by its ID from the tabs list.",
    "close_tab": "Close the active tab; the last tab cannot be closed this way.",
}
READ_ONLY = {"observe", "list_tabs"}


def function_schema(name, description, properties):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def build_schemas():
    schemas = []
    for name, (required, optional) in TOOL_ARGUMENTS.items():
        properties = {}
        for key, kind in (required | optional).items():
            json_type = "string" if kind is str else "number"
            properties[key] = {"type": [json_type, "null"] if key in optional else json_type}
        if name == "scroll":
            properties["direction"]["enum"] = ["up", "down", "left", "right"]
        if name not in READ_ONLY:
            properties["observation_version"] = {
                "type": "integer",
                "description": "Version of the latest supplied browser observation.",
            }
        schemas.append(function_schema(name, DESCRIPTIONS[name], properties))
    schemas.append(
        function_schema(
            "request_observation",
            "Request full structure, visual crops, or a full viewport screenshot when more context is needed.",
            {"detail": {"type": "string", "enum": ["full", "crops", "screenshot"]}},
        )
    )
    schemas.append(
        function_schema(
            "inspect_region",
            "Inspect a fresh viewport region at 1–4x zoom; coordinates and dimensions are CSS pixels.",
            {
                **{key: {"type": "number"} for key in ("x", "y", "width", "height")},
                "zoom": {"type": "integer", "enum": [1, 2, 3, 4]},
            },
        )
    )
    schemas.append(
        function_schema(
            "ask_user",
            "Pause execution and ask the user for missing information.",
            {
                "question": {"type": "string"},
            },
        )
    )
    schemas.append(
        function_schema(
            "finish",
            "Report verified task completion or inability to proceed.",
            {
                "status": {"type": "string", "enum": ["completed", "blocked"]},
                "message": {"type": "string"},
            },
        )
    )
    return schemas


TOOL_SCHEMAS = build_schemas()
SCHEMAS_BY_NAME = {schema["name"]: schema["parameters"] for schema in TOOL_SCHEMAS}


def parse_arguments(name: str, arguments: str) -> dict:
    if name not in SCHEMAS_BY_NAME:
        raise ValueError(f"Unknown tool: {name}")
    values = json.loads(arguments)
    schema = SCHEMAS_BY_NAME[name]
    if not isinstance(values, dict) or set(values) != set(schema["properties"]):
        raise ValueError("Arguments must match the tool schema, including nullable optional fields.")
    for key, value in values.items():
        prop = schema["properties"][key]
        kinds = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
        matches = (
            (value is None and "null" in kinds)
            or (isinstance(value, str) and "string" in kinds)
            or (type(value) is int and "integer" in kinds)
            or (type(value) in {int, float} and "number" in kinds)
        )
        if not matches or ("enum" in prop and value not in prop["enum"]):
            raise ValueError(f"Invalid {key}.")
    if name == "ask_user" and not values["question"].strip():
        raise ValueError("Question must not be empty.")
    if name == "finish" and not values["message"].strip():
        raise ValueError("Completion message must not be empty.")
    return {key: value for key, value in values.items() if value is not None}
