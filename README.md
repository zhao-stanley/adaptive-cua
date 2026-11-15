# Adaptive State Representations for Computer-Use Agents

Code for the 6.1850 project by Jocelyn Zheng, Jeffrey Li, and Stanley Zhao.

The project adds a representation layer between a browser and a computer-use agent.
At each step, the layer decides how much of the page state to send: changes only, the full structure, cropped images, or a full screenshot.
See the project proposal for the full design.

## Setup

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

`uv run pytest` checks browser capture, actions, target validation, and session isolation in Chromium.
No API key is required yet: the CLI exposes browser tools, but model calls are not connected.

## Terminal chat

```bash
uv run adaptive-cua
# Or run without a browser window:
uv run adaptive-cua --headless
```

The first task message opens a fresh Chromium browser. Follow-up messages stay in
the same task and are retained in memory. Agent execution will be connected in a
later milestone; messages currently receive an explicit placeholder reply.

- `/new`: close the current browser, clear the conversation, and open a fresh browser.
- `/observe`: capture and print the current page's visible element references and tabs.
- `/tool NAME {JSON arguments}`: execute a browser tool manually.
- `/help`: show commands.
- `/quit` or Ctrl+D: exit and close the browser.
- Ctrl+C: interrupt and return to the chat prompt.

Each task uses an isolated browser context with a 1280×720 viewport. Cookies and
other browser state do not carry over to the next task. The browser stays open
between messages until `/new` or exit. Conversations are not yet saved to disk.

## Browser observations and actions

For example, inside the CLI:

```text
/tool navigate {"url":"https://example.com"}
/observe
/tool click {"element_id":"<copy an ID from the observation>"}
/tool type_text {"element_id":"<input ID>","text":"Jane Doe"}
/tool select_option {"element_id":"<select ID>","value":"express"}
/tool scroll {"direction":"down","amount":500}
/tool new_tab {"url":"https://example.com"}
/tool switch_tab {"tab_id":"t1"}
```

Available tools: `observe`, `list_tabs`, `navigate`, `back`, `click`, `type_text`,
`select_option`, `press_key`, `scroll`, `click_at`, `drag`, `new_tab`, `switch_tab`,
and `close_tab`. `type_text` replaces the existing field text. `select_option`
accepts an option's value. `drag` takes `element_id` and `target_element_id`.
`scroll` optionally takes an `element_id` to target a scrollable area.
`press_key` takes a Playwright key name such as `Enter` or `ControlOrMeta+A`.
`click_at` takes viewport CSS-pixel coordinates `x` and `y`.

The Python interface is `session.tools.observe()` and
`session.tools.execute(tool_name, **arguments)`. Observations contain:

- Raw accessibility and DOM snapshots for later representation processing.
- Element IDs, accessible names, roles, values, states, bounds, and visibility.
- A viewport PNG as bytes (`snapshot.screenshot`), plus URL, title, tabs, and version.

The terminal prints visible elements; screenshots remain available through the
Python interface. Raw snapshots may include hidden DOM data and are internal
capture artifacts, not the future model-facing representation.

IDs persist for the same DOM node within a document and expire across navigation,
task resets, or an observation of another tab. Actions reject detached, hidden,
covered, offscreen, disabled, or moved targets. After scrolling or page changes,
use the returned observation. An optional `observation_version` rejects actions
based on an older capture. Successful actions include a fresh observation;
errors include a status and explanation. A browser error can have an uncertain
action outcome (`executed=None`); observe before retrying it.

Current limits: element actions cover the main document and open shadow DOM.
Iframe boundaries are reported, but their contents require coordinate actions.
Visibility uses five hit-test points and is a heuristic for complex shapes.
Capture is sequential, not atomic; navigation during capture is rejected.
Browser JavaScript dialogs use Playwright's default automatic dismissal.
No adaptive filtering, changes-only observations, or visual crops are implemented yet.

## Layout

Each folder matches a part of the proposal.
The CLI, browser lifecycle, observations, and actions are implemented. The remaining packages
contain descriptions of the planned components.

| Folder | Proposal section | Purpose |
|---|---|---|
| `src/adaptive_cua/browser/` | 4.1 | Drive Chromium; capture the accessibility tree, DOM snapshot, and screenshot |
| `src/adaptive_cua/representation/` | 3.2, 3.3 | Structured representation, visibility filters, changes between steps, missing-content detection |
| `src/adaptive_cua/policy/` | 3.3 | Choose the observation level at each step |
| `src/adaptive_cua/agent/` | 3.3, 4.1 | Agent loop and actions |
| `src/adaptive_cua/eval/` | 4.2-4.4 | Conditions, tasks, metrics, step-level analysis |
| `tests/` | | Self-contained browser scenarios and CLI tests |
| `docs/decisions.md` | | Questions for the team to decide before building |

## Tools

- Python 3.12 with [uv](https://docs.astral.sh/uv/) for dependencies.
- Playwright for the browser, from the proposal.
- OpenAI custom tools are planned for the agent; the current dependency list still includes the original Anthropic SDK.
- pytest for tests and ruff for lint and formatting (`uv run ruff check .`, `uv run ruff format .`).

Add other dependencies with `uv add <package>` when the team needs them.
