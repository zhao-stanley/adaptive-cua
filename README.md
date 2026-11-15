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

`uv run pytest` checks browser capture, actions, session isolation, adaptive observations, and the agent loop.
Tests use simulated model responses and real Chromium; no API key or paid calls are required.

For the live agent, set `OPENAI_API_KEY` and `OPENAI_MODEL` in your environment or
in a local `.env` using `.env.example` as a template. Choose a model your API project
can access that supports image input and function calling. No model is selected
implicitly. Environment variables override `.env` values.

## Terminal chat

```bash
uv run adaptive-cua
# Or run without a browser window:
uv run adaptive-cua --headless
# Override the configured model:
uv run adaptive-cua --model YOUR_MODEL_ID
```

After configuration succeeds, the first task message opens a fresh Chromium browser.
The OpenAI model receives the current page and calls one browser tool at a time.
Follow-up messages share the same browser and model conversation. The agent can
ask a clarification question, report completion, or explain why it cannot proceed.
For example, enter: `Open https://example.com and tell me the page heading.`
Manual `/tool` and `/observe` commands remain usable without API credentials.

- `/new`: close the current browser, clear the conversation, and open a fresh browser.
- `/observe`: capture and print the current page's visible element references and tabs.
- `/tool NAME {JSON arguments}`: execute a browser tool manually.
- `/help`: show commands.
- `/quit` or Ctrl+D: exit and close the browser.
- Ctrl+C: interrupt and return to the chat prompt.

Each task uses an isolated browser context with a 1280×720 viewport. Cookies and
other browser state do not carry over to the next task. The browser stays open
between messages until `/new` or exit, including after the agent reports completion.
`/new` also clears model history and the cumulative token counter.

## Agent loop and limits

The agent uses the Responses API with custom function tools. By default it sends
full visible structure initially, then element changes, adding images when needed.
Raw DOM/accessibility capture data and hidden elements are not sent to the model.
Browser capture still takes a full screenshot every step for visual detection and
debugging; capture does not imply sending that screenshot to the model.

Browser actions require the latest observation version. Failed or uncertain actions
return a fresh observation when possible; they are never automatically retried.
The model uses `ask_user` to pause for input and `finish` to report `completed` or
`blocked`. A text-only model reply also returns control to the CLI. Completion is
the model's assessment based on browser evidence, not an independent task evaluator.

Ctrl+C interrupts execution and returns to the prompt with the browser open.
If an action was interrupted, its outcome is recorded as uncertain; the next
message starts with a fresh observation. Pending tool calls are paired with
interruption results so resuming does not replay them.

| Option | Default | Scope |
|---|---|---|
| `--max-steps` | 30 | Model requests per user message |
| `--max-seconds` | 300 | Time budget per user message |
| `--max-tokens` | 100000 | Reported input + output tokens across the task |
| `--max-output-tokens` | 4096 | Maximum output tokens per request |
| `--api-timeout` | 60 | Timeout for a single model request |
| `--trace-dir` | `runs` | Root directory for local task traces |
| `--observation-mode` | `adaptive` | Use `full` to send full structure and a screenshot every step |
| `--compact-after-tokens` | 24000 | Reported context size triggering compaction; 0 disables it |
| `--full-refresh-interval` | 12 | Send a full structure after this many deltas; 0 disables periodic refreshes |

Time and task-token limits are checked between steps: in-flight browser operations
can finish after the time budget, and an API response can exceed the remaining
task-token budget through its input usage. Once a limit is reached no further
browser action is dispatched. API requests use bounded timeouts and no automatic
retries. Step/time limits reset on follow-up; the task-token limit resets on `/new`.

Each agent task creates a directory under `runs/` containing `events.jsonl` and
numbered observation PNGs. `observation-*.png` files are complete captures;
`sent-*.png` files are the actual crops/screenshots included in model observations.
`observation_sent` events record the exact text payload, image filenames, chosen
detail level, and reasons. Events also include messages, tool arguments/results,
usage, timings, and pause reasons. These traces contain task
and page content. Credentials and model reasoning content are not logged.
Trace directories are excluded from Git. They are for inspection, not restartable
browser checkpoints; conversation continuity currently lasts only within the CLI process.

## Adaptive observations

The representation layer keeps a complete visible element table in application
memory. Visible body text is included alongside names, values, states, and bounds,
so instructions and status messages do not require screenshots just to be read.

| Situation | Observation |
|---|---|
| First step, user follow-up, navigation/reload, tab or viewport change | Full visible structure |
| Routine action on the same page | Added, updated, and removed elements |
| Canvas, unnamed icon control, or unexplained visual region | Structure/delta plus positioned crops |
| More than four crops or crops cover over half the viewport | Full viewport image |
| Action error, or action with no observed effect | Fresh full structure |
| Two action errors, or the same action twice with no observed effect | Full viewport image and structure |
| Progress resumes | Return to changes and any needed crops |

Each delta has a `base_version`; updated records completely replace the previous
record for that ID. Page metadata accompanies every update. A same-URL reload
still resets the baseline because the document identity changes. Periodic full
structure refreshes help reestablish the current state without extra model calls.

Visual detection flags known visual elements and scans unrepresented screenshot
areas using pixel variance. Coverage uses visible text rectangles and named
controls, not the bounding box of an entire named container. Nearby crop regions
are merged and clipped to the viewport. Crops preserve CSS-pixel scale: add their
origin to crop-local coordinates to obtain click coordinates.

Unchanged images are not attached again as new observations: their visual IDs reference an earlier observation
in the existing model history. Changed or moved crops are sent again; disappeared
regions are invalidated. Fresh task/context baselines resend required images.
The model can call `request_observation` with `detail` set to `full`, `crops`, or
`screenshot`; explicit visual requests resend imagery. This is an agent-level tool,
while manual `/observe` continues to show the browser's raw visible element list.

These are deterministic heuristics, not a semantic sufficiency test. Backgrounds,
complex layouts, or animations can cause unnecessary imagery; subtle visual
content can be missed. A lack of change only increases detail, and does not mark
an action as failed. Focusing an input, entering its existing value, and keyboard
actions are not treated as repeated no-effect failures. Read-only observations
do not erase failure history; progress or a new page resets escalation.

Task memory lives within the current session and is cleared by `/new`. History
preserves tool results and reasoning until the latest reported input + output
context reaches `--compact-after-tokens` (default 24000; 0 disables compaction).
Between completed tool calls, the agent uses OpenAI Responses compaction and keeps
the entire returned context. It then supplies fresh full structure and a full
screenshot, resetting delta and image references. Compaction usage counts toward
the task token budget; encrypted memory is not written to traces. If compaction
fails, history is retained and automatic compaction is disabled until `/new`.
The selected model must support compaction. The threshold is based on the last
reported usage, not a prediction of the next observation's size; configure it
below the model's context limit with room for new observations. Memory is not
persisted across tasks or process restarts.

For targeted inspection, the model can call:

```json
{"x": 100, "y": 80, "width": 200, "height": 120, "zoom": 3}
```

with `inspect_region`. This captures a fresh viewport and returns a magnified
crop, plus the current structure/delta. All arguments are required. Regions must
fit inside the viewport; zoom is an integer from 1–4 and output is limited to four
million pixels. Fractional bounds round outward; returned bounds are authoritative.
Map image coordinates to clicks with `viewport_x = bounds.x + image_x / scale`
(and likewise for y). Magnification resizes existing pixels; it does not zoom the
browser or create additional detail. This is an agent observation tool, not a
manual `/tool` browser action.

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
- Element IDs, accessible names, visible text, roles, values, states, bounds, and visibility.
- A viewport PNG as bytes (`snapshot.screenshot`), plus URL, title, tabs, and version.

The terminal prints visible elements; screenshots remain available through the
Python interface. Raw snapshots may include hidden DOM data and are internal
capture artifacts, not the model-facing representation.

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

## Layout

Each folder matches a part of the proposal.
The CLI, browser lifecycle, observations, actions, OpenAI agent loop, and adaptive
representation/policy are implemented. Evaluation remains a placeholder.

| Folder | Proposal section | Purpose |
|---|---|---|
| `src/browser/` | 4.1 | Drive Chromium; capture the accessibility tree, DOM snapshot, and screenshot |
| `src/representation/` | 3.2, 3.3 | Structured representation, visibility filters, changes between steps, missing-content detection |
| `src/policy/` | 3.3 | Choose the observation level at each step |
| `src/agent/` | 3.3, 4.1 | Agent loop and actions |
| `src/eval/` | 4.2-4.4 | Conditions, tasks, metrics, step-level analysis |
| `tests/` | | Self-contained browser scenarios and CLI tests |
| `docs/decisions.md` | | Questions for the team to decide before building |

## Tools

- Python 3.12 with [uv](https://docs.astral.sh/uv/) for dependencies.
- Playwright for the browser, from the proposal.
- OpenAI's Python SDK for the Responses API, and python-dotenv for local configuration.
- Pillow for screenshot inspection and lossless image crops.
- pytest for tests and ruff for lint and formatting (`uv run ruff check .`, `uv run ruff format .`).

Add other dependencies with `uv add <package>` when the team needs them.
