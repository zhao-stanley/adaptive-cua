"""Bounded browser tools. CDP captures state; Playwright performs UI input.

Element actions target main-document nodes, including open shadow DOM. Frames are
reported as visual boundaries; coordinate actions can interact with their pixels.
"""

import math
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import CDPSession, Error, Page

from adaptive_cua.browser.models import ActionResult, BrowserSnapshot, Element
from adaptive_cua.browser.session import BrowserSession

GEOMETRY = Path(__file__).with_name("geometry.js").read_text()

# Required and optional tool arguments. Validate before performing any UI input.
TOOL_ARGUMENTS = {
    "observe": ({}, {}),
    "list_tabs": ({}, {}),
    "back": ({}, {}),
    "close_tab": ({}, {}),
    "navigate": ({"url": str}, {}),
    "new_tab": ({}, {"url": str}),
    "switch_tab": ({"tab_id": str}, {}),
    "click": ({"element_id": str}, {}),
    "type_text": ({"element_id": str, "text": str}, {}),
    "select_option": ({"element_id": str, "value": str}, {}),
    "press_key": ({"key": str}, {}),
    "click_at": ({"x": (int, float), "y": (int, float)}, {}),
    "drag": ({"element_id": str, "target_element_id": str}, {}),
    "scroll": ({"direction": str}, {"amount": (int, float), "element_id": str}),
}


class TargetError(ValueError):
    pass


class BrowserTools:
    """Create one instance per BrowserSession; no provider or model dependencies."""

    def __init__(self, session: BrowserSession):
        self.session = session
        self.version = 0
        self._tabs: dict[Page, str] = {}
        self._clients: dict[Page, CDPSession] = {}
        self._targets: dict[str, tuple[Page, int, int, Element]] = {}
        self._next_tab = 0
        self._prefix = uuid4().hex[:12]

    def close(self):
        for client in self._clients.values():
            try:
                client.detach()
            except Error:
                pass
        self._clients.clear()
        self._tabs.clear()
        self._targets.clear()

    @property
    def page(self) -> Page:
        if not self.session.active or self.session.context is None:
            raise TargetError("No active browser session.")
        if self.session.page is None or self.session.page.is_closed():
            pages = self.session.context.pages
            if not pages:
                raise TargetError("No open tabs. Open a new tab.")
            self.session.page = pages[-1]
        return self.session.page

    def _client(self, page: Page) -> CDPSession:
        if page not in self._clients:
            client = page.context.new_cdp_session(page)
            client.send("DOM.enable")
            client.send("Accessibility.enable")
            self._clients[page] = client
        return self._clients[page]

    def tabs(self) -> list[dict[str, str]]:
        if self.session.context is None:
            raise TargetError("No active browser session.")
        for page in list(self._tabs):
            if page.is_closed():
                self._tabs.pop(page)
                self._clients.pop(page, None)
        result = []
        for page in self.session.context.pages:
            if page not in self._tabs:
                self._next_tab += 1
                self._tabs[page] = f"t{self._next_tab}"
            result.append({"id": self._tabs[page], "url": page.url, "title": page.title()})
        return result

    def _document(self, client: CDPSession) -> int:
        return client.send("DOM.getDocument", {"depth": 0})["root"]["backendNodeId"]

    def _on_node(self, client, backend, function, arguments=None):
        obj = client.send("DOM.resolveNode", {"backendNodeId": backend})["object"]["objectId"]
        try:
            reply = client.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": obj,
                    "functionDeclaration": function,
                    "arguments": [{"value": value} for value in (arguments or [])],
                    "returnByValue": True,
                },
            )
            if "exceptionDetails" in reply:
                raise TargetError("The target could not be inspected or operated on.")
            return reply["result"].get("value")
        finally:
            try:
                client.send("Runtime.releaseObject", {"objectId": obj})
            except Error:
                pass

    def observe(self) -> BrowserSnapshot:
        page = self.page
        tabs = self.tabs()
        client = self._client(page)
        document = self._document(client)
        ax = client.send("Accessibility.getFullAXTree")["nodes"]
        dom = client.send(
            "DOMSnapshot.captureSnapshot",
            {
                "computedStyles": ["display", "visibility", "opacity"],
                "includeDOMRects": True,
                "includePaintOrder": True,
            },
        )
        semantic = {}
        for node in ax:
            if not node.get("ignored") and node.get("backendDOMNodeId"):
                semantic.setdefault(node["backendDOMNodeId"], node)
        elements, targets = [], {}
        nodes = dom["documents"][0]["nodes"]
        for backend, node_type in zip(nodes["backendNodeId"], nodes["nodeType"], strict=True):
            if node_type != 1:
                continue
            try:
                info = self._on_node(client, backend, GEOMETRY)
            except Error:
                continue  # Dynamic pages may remove nodes during capture.
            if not info:
                continue
            node = semantic.get(backend, {})
            role = node.get("role", {}).get("value", "")
            name = node.get("name", {}).get("value", "") or info["name"]
            if (
                not role
                and not info["text"]
                and info["tag"]
                not in {
                    "input",
                    "button",
                    "select",
                    "textarea",
                    "canvas",
                    "iframe",
                    "img",
                    "a",
                    "svg",
                    "video",
                }
            ):
                continue
            element_id = f"{self._prefix}-{self._tabs[page]}-d{document}-e{backend}"
            element = Element(
                element_id,
                info["tag"],
                role,
                name,
                info["value"],
                info["bounds"],
                info["visible"],
                info["enabled"],
                info["editable"],
                info["point"],
                {p["name"]: p["value"].get("value") for p in node.get("properties", [])},
                info["text"],
                info["text_rects"],
            )
            elements.append(element)
            targets[element_id] = (page, document, backend, element)
        screenshot = page.screenshot(type="png", full_page=False, scale="css", timeout=5000)
        if self._document(client) != document:
            raise TargetError("Page navigated during capture; observe again.")
        self._targets = targets
        self.version += 1
        return BrowserSnapshot(
            self.version,
            self._tabs[page],
            page.url,
            page.title(),
            page.viewport_size,
            elements,
            screenshot,
            ax,
            dom,
            tabs,
            ["Frame contents require coordinate actions; element IDs cover the main document only."]
            if len(page.frames) > 1
            else [],
            document_id=f"{self._prefix}-{self._tabs[page]}-d{document}",
        )

    def _target(self, element_id: str):
        if element_id not in self._targets:
            raise TargetError("Unknown or expired element ID; observe again.")
        page, document, backend, previous = self._targets[element_id]
        if page != self.page or self._document(self._client(page)) != document:
            raise TargetError("Element belongs to an old document or another tab; observe again.")
        try:
            info = self._on_node(self._client(page), backend, GEOMETRY)
        except Error as exc:
            raise TargetError("Element was removed; observe again.") from exc
        if not info or not info["visible"]:
            raise TargetError("Element is detached, hidden, offscreen, or covered; observe or scroll first.")
        if not info["enabled"]:
            raise TargetError("Element is disabled.")
        if any(abs(info["bounds"][key] - previous.bounds[key]) > 2 for key in previous.bounds):
            raise TargetError("Element moved since observation; observe again.")
        return backend, info

    def _point(self, x, y):
        viewport = self.page.viewport_size
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
            raise ValueError("Coordinates must be finite numbers.")
        if not (0 <= x < viewport["width"] and 0 <= y < viewport["height"]):
            raise ValueError("Coordinates must be inside the viewport in CSS pixels.")

    def _url(self, url):
        if not isinstance(url, str) or (url != "about:blank" and urlparse(url).scheme not in {"http", "https", "file"}):
            raise ValueError("Use an http, https, file URL, or about:blank.")
        return url

    def _perform(self, name, args):
        page = self.page
        if name == "navigate":
            page.goto(self._url(args["url"]), wait_until="domcontentloaded", timeout=10000)
        elif name == "back":
            page.go_back(wait_until="domcontentloaded", timeout=10000)
        elif name in {"click", "type_text", "select_option"}:
            backend, info = self._target(args["element_id"])
            if name == "click":
                page.mouse.click(**info["point"])
            elif name == "type_text":
                if not info["editable"]:
                    raise TargetError("Target is not an editable text field.")
                page.mouse.click(**info["point"])
                focused = self._on_node(
                    self._client(page),
                    backend,
                    """function() {
                    const active = this.getRootNode().activeElement;
                    return active === this || this.contains(active);
                }""",
                )
                if not focused:
                    raise TargetError("Target did not receive focus; text was not entered.")
                page.keyboard.press("ControlOrMeta+A")
                page.keyboard.insert_text(args["text"])
            else:
                if info["tag"] != "select":
                    raise TargetError("Target is not a native select element.")
                selected = self._on_node(
                    self._client(page),
                    backend,
                    """function(value) {
                    const option = Array.from(this.options).find(o => o.value === value);
                    if (!option || option.disabled || option.parentElement.disabled) return false;
                    this.value = value;
                    this.dispatchEvent(new Event('input', {bubbles:true}));
                    this.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }""",
                    [args["value"]],
                )
                if not selected:
                    raise TargetError("No enabled option with that value.")
        elif name == "click_at":
            self._point(args["x"], args["y"])
            page.mouse.click(args["x"], args["y"])
        elif name == "drag":
            _, source = self._target(args["element_id"])
            _, target = self._target(args["target_element_id"])
            page.mouse.move(**source["point"])
            page.mouse.down()
            try:
                page.mouse.move(**target["point"], steps=10)
            finally:
                page.mouse.up()
        elif name == "press_key":
            page.keyboard.press(args["key"])
        elif name == "scroll":
            amount = args.get("amount", 500)
            if not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= 5000:
                raise ValueError("Scroll amount must be between 0 and 5000 CSS pixels.")
            directions = {"up": (0, -amount), "down": (0, amount), "left": (-amount, 0), "right": (amount, 0)}
            if args["direction"] not in directions:
                raise ValueError("Direction must be up, down, left, or right.")
            if args.get("element_id"):
                _, info = self._target(args["element_id"])
                page.mouse.move(**info["point"])
            else:
                page.mouse.move(page.viewport_size["width"] / 2, page.viewport_size["height"] / 2)
            page.mouse.wheel(*directions[args["direction"]])
            page.wait_for_timeout(100)  # Wheel dispatch returns before scrolling completes.
        elif name == "switch_tab":
            self.tabs()
            target = next((p for p, tab in self._tabs.items() if tab == args["tab_id"]), None)
            if target is None:
                raise TargetError("Unknown tab ID.")
            self.session.page = target
            target.bring_to_front()
        elif name == "new_tab":
            url = self._url(args.get("url", "about:blank"))
            self.session.page = page.context.new_page()
            self.session.page.goto(url, wait_until="domcontentloaded", timeout=10000)
        elif name == "close_tab":
            if len(page.context.pages) <= 1:
                raise TargetError("Cannot close the last tab; end the task instead.")
            page.close()
            self.session.page = self.session.context.pages[-1]
        else:
            raise ValueError(f"Unknown browser tool: {name}")

    def execute(self, name: str, /, *, observation_version: int | None = None, **arguments) -> ActionResult:
        """Dispatch a named tool and return its outcome plus fresh browser state."""
        executed = False
        dispatched = False
        try:
            if name not in TOOL_ARGUMENTS:
                raise ValueError(f"Unknown browser tool: {name}")
            required, optional = TOOL_ARGUMENTS[name]
            if set(required) - arguments.keys():
                raise ValueError(f"Missing arguments: {sorted(set(required) - arguments.keys())}")
            allowed = required | optional
            if arguments.keys() - allowed.keys():
                raise ValueError(f"Unexpected arguments: {sorted(arguments.keys() - allowed.keys())}")
            for key, value in arguments.items():
                if isinstance(value, bool) or not isinstance(value, allowed[key]):
                    raise ValueError(f"Invalid type for {key}.")
            if observation_version is not None and observation_version != self.version:
                raise TargetError("Observation version is stale; observe again.")
            if name not in {"observe", "list_tabs"}:
                dispatched = True
                self._perform(name, arguments)
                executed = True
            return ActionResult("ok", self.observe(), executed=executed)
        except TargetError as exc:
            return ActionResult(
                "observation_error" if executed else "invalid_target", error=str(exc), executed=executed
            )
        except (ValueError, TypeError, KeyError) as exc:
            return ActionResult("invalid_arguments", error=str(exc), executed=executed)
        except Error as exc:
            # Timeouts can happen after an input or navigation has already taken effect.
            return ActionResult(
                "observation_error" if executed else "browser_error",
                error=str(exc),
                executed=True if executed else (None if dispatched else False),
            )
