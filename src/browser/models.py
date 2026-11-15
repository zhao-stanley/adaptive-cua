"""Browser-facing records, independent of the model API."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Element:
    id: str
    tag: str
    role: str
    name: str
    value: str
    bounds: dict[str, float]
    visible: bool
    enabled: bool
    editable: bool
    point: dict[str, float] | None
    states: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    text_rects: list[dict[str, float]] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    decoration_padding: float = 2


@dataclass
class BrowserSnapshot:
    version: int
    tab_id: str
    url: str
    title: str
    viewport: dict[str, int]
    elements: list[Element]
    screenshot: bytes
    accessibility_tree: list[dict[str, Any]]
    dom_snapshot: dict[str, Any]
    tabs: list[dict[str, str]]
    warnings: list[str] = field(default_factory=list)
    document_id: str | None = None


@dataclass
class ActionResult:
    status: str
    observation: BrowserSnapshot | None = None
    error: str | None = None
    # An observation failure must not imply that an already executed action failed.
    # None means a browser error left the action's outcome uncertain.
    executed: bool | None = False
    steps: list[dict] = field(default_factory=list)
