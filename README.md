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

`uv run pytest` checks that Chromium starts and answers Chrome DevTools Protocol (CDP) requests.
To call the Claude API later, copy `.env.example` to `.env` and add a key.

## Layout

Each folder matches a part of the proposal.
Each `__init__.py` says what the part is for; nothing is implemented yet.

| Folder | Proposal section | Purpose |
|---|---|---|
| `src/adaptive_cua/browser/` | 4.1 | Drive Chromium; capture the accessibility tree, DOM snapshot, and screenshot |
| `src/adaptive_cua/representation/` | 3.2, 3.3 | Structured representation, visibility filters, changes between steps, missing-content detection |
| `src/adaptive_cua/policy/` | 3.3 | Choose the observation level at each step |
| `src/adaptive_cua/agent/` | 3.3, 4.1 | Agent loop and actions |
| `src/adaptive_cua/eval/` | 4.2-4.4 | Conditions, tasks, metrics, step-level analysis |
| `fixtures/pages/` | | Small local pages for development |
| `tests/` | | Tests |
| `docs/decisions.md` | | Questions for the team to decide before building |

## Tools

- Python 3.12 with [uv](https://docs.astral.sh/uv/) for dependencies.
- Playwright for the browser, from the proposal.
- The Anthropic Python SDK for the agent, from the proposal.
- pytest for tests and ruff for lint and formatting (`uv run ruff check .`, `uv run ruff format .`).

Add other dependencies with `uv add <package>` when the team needs them.
