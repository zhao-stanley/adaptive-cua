# Adaptive State Representations for Computer-Use Agents

We build a representation layer that sits between a browser and a computer-use agent. At each step, the CUA decides how much page state to send to the model in a few different forms: only the changes, the full element structure (DOM, accessibility tree), cropped images, or a full screenshot.

## Setup

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

## Quickstart

Start the demo site (a fictional medication prior-authorization form) in one terminal:

```bash
uv run serve src/eval/demo/index.html
```

Start the agent in another:

```bash
uv run adaptive-cua              # add --headless to hide the browser window
```

Example task prompt: 

> Go to http://127.0.0.1:8000. Submit the demo medication prior authorization for Alex Morgan using the fictional chart. Review the details, submit, and report the confirmation ID and status.

## How it works

### Agent loop

Each task runs in an isolated 1280×720 browser context. The agent uses the OpenAI Responses API and repeats the following until it stops:

1. Send the model the latest observation.
2. The model calls exactly one tool.
3. Run the tool and capture a new observation.

The loop ends when the model calls `finish` (`completed` or `blocked`), calls `ask_user`, or replies with text only. Completion is the model's own judgment, not an independent check.

### Tools

Browser tools: `observe`, `list_tabs`, `navigate`, `back`, `click`, `type_text`, `select_option`, `press_key`, `scroll`, `click_at`, `drag`, `new_tab`, `switch_tab`, `close_tab`.

Agent tools:

- `fill_form`: up to five `type_text`/`select_option` operations in one call. Each field is verified, and the batch stops on any uncertainty.
- `request_observation`: ask for more detail (`full`, `crops`, or `screenshot`).
- `inspect_region`: zoomed crop of a viewport region (`x`, `y`, `width`, `height`, `zoom` 1–4).
- `ask_user`, `finish`: hand control back to the user.

Actions must reference the latest observation version. Element IDs are stable within a document and expire on navigation. Actions are rejected if the target is hidden, covered, disabled, offscreen, or moved. Failed or uncertain actions are never retried automatically; they return a fresh observation instead.

Element actions cover the main document and open shadow DOM. Iframe contents require coordinate actions (`click_at`).

### Representation

Each step starts from one browser snapshot: URL, title, tabs, visible elements (role, name, value, state, text, bounds), and a screenshot. `src/representation/` turns it into the observation the model sees:

- **Structure** (`structure.py`): visible elements keyed by ID, with empty wrappers and default fields removed. Each later step sends a delta of `added`, `updated`, and `removed` elements against a `base_version`.
- **Visuals** (`visual.py`): pixels the DOM does not explain (canvas, iframe, video, unlabeled icons, drawn regions) are sent as cropped images. Crops carry bounds so the model can map them to click coordinates. Unchanged crops are not resent. If there are more than four crops or they cover over half the viewport, a full screenshot is sent instead.
- **Assembly** (`observations.py`): combines structure and visuals into one JSON text block plus 0–4 images. The `level` field says what was sent (`structure`, `changes`, `crops`, or `screenshot`), and `reasons` says why.

### Policy

`src/policy/adaptive.py` chooses the detail level with deterministic rules and no extra model calls. It starts with the cheapest option and escalates on these signals:

| Situation                                                 | Observation                   |
| --------------------------------------------------------- | ----------------------------- |
| First step, follow-up, navigation, tab or viewport change | Full structure                |
| Every `full_refresh_interval` deltas                      | Full structure                |
| Routine action on the same page                           | Delta                         |
| Action error, or an action with no visible effect         | Full structure                |
| Two errors, or the same action twice with no effect       | Full structure and screenshot |
| Model requests more detail                                | The requested level           |

Focusing a field, typing a value it already holds, and key presses are not counted as no-effect failures. These are heuristics, not a semantic check: backgrounds and animations can trigger unneeded crops, and subtle visual content can be missed.

### Memory

History keeps tool calls, results, and reasoning. When the last reported context size reaches `compact_after_tokens`, the agent compacts it with OpenAI Responses compaction between tool calls. It then supplies a fresh full structure and only the images currently needed, and resets delta and image references. Compaction tokens count toward the task budget.

Memory lasts only for the current session and does not persist across different task runs.

## Logs and metrics

Each task writes a folder `runs/<timestamp>-<id>/` and logs to `runs/runs.sqlite3`.

| Artifact            | Contents                                                                   |
| ------------------- | -------------------------------------------------------------------------- |
| `events.jsonl`      | Every event: messages, tool calls and results, usage, timing, observations |
| `observation-N.png` | Full screenshot captured at step N                                         |
| `sent-N-K.png`      | Crops or screenshots actually sent to the model                            |
| `runs` table        | Run ID, timestamps, model, observation mode, status, total tokens          |
| `events` table      | Ordered events with kind, call ID, observation version, JSON payload       |

Key events:

- `response`: token usage and latency per model call.
- `observation_sent`: what the model received.
- `tool_result`, `batch_action`: action outcomes.

Inspect runs with:

```bash
uv run python -m src.ops                  # list runs
uv run python -m src.ops --run RUN_ID     # events for one run
```
