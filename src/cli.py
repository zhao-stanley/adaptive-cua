"""Terminal chat and manual browser tools for the OpenAI agent."""

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError

from src.agent.config import AgentConfig, ConfigurationError
from src.agent.loop import Agent, create_agent
from src.browser.session import BrowserSession

HELP = "Commands: /new, /observe, /tool NAME {JSON arguments}, /help, /quit. Ctrl+C interrupts; Ctrl+D exits."


def run_chat(
    session: BrowserSession,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    agent_factory: Callable[[BrowserSession], Agent] = create_agent,
) -> int:
    write("Adaptive CUA — terminal chat")
    write("Describe your task to the agent, or use /tool and /observe to operate the browser manually.")
    write(HELP)
    agent = None
    try:
        while True:
            try:
                message = read("You > ").strip()
                if not message:
                    continue
                if message == "/quit":
                    break
                if message == "/help":
                    write(HELP)
                    continue
                if message == "/new":
                    if agent is not None:
                        agent.close()
                        agent = None
                    session.start()
                    write("[browser] Started a fresh task. Previous conversation cleared.")
                    continue
                if message == "/observe" or message.startswith("/tool "):
                    try:
                        parts = message.split(maxsplit=2)
                        name = "observe" if message == "/observe" else parts[1]
                        arguments = json.loads(parts[2]) if len(parts) == 3 else {}
                        if not isinstance(arguments, dict):
                            raise ValueError("Tool arguments must be a JSON object.")
                    except ValueError as exc:
                        write(f"Invalid tool arguments: {exc}")
                        continue
                    if not session.active:
                        if agent is not None:
                            agent.close()
                            agent = None
                        session.start()
                    result = session.tools.execute(name, **arguments)
                    write(f"[tool] {result.status}" + (f": {result.error}" if result.error else ""))
                    if result.observation:
                        snapshot = result.observation
                        write(f"[page] {snapshot.tab_id} v{snapshot.version}: {snapshot.title} — {snapshot.url}")
                        for tab in snapshot.tabs:
                            write(f"[tab] {tab['id']}: {tab['title']} — {tab['url']}")
                        for element in snapshot.elements:
                            if element.visible:
                                write(f"[{element.id}] {element.role or element.tag} {element.name!r}")
                        for warning in snapshot.warnings:
                            write(f"[note] {warning}")
                    continue
                if message.startswith("/"):
                    write("Unknown command. " + HELP)
                    continue
                if not session.active:
                    if agent is not None:
                        agent.close()
                        agent = None
                if agent is None:
                    agent = agent_factory(session)
                    write(f"[trace] {agent.trace.path}")
                if not session.active:
                    session.start()
                    write("[browser] Started a fresh task.")
                session.messages.append(message)
                outcome = agent.run(message, emit=write)
                write(f"Agent > {outcome.message}")
                write(f"[agent] {outcome.status}")
            except KeyboardInterrupt:
                write("\nInterrupted. Back at the chat prompt; use /new to reset or /quit to exit.")
            except EOFError:
                break
            except PlaywrightError as exc:
                write(f"Browser error: {exc}")
                write("Check Chromium is installed with `uv run playwright install chromium`, then retry.")
            except (ConfigurationError, OSError) as exc:
                write(f"Configuration or local file error: {exc}")
    finally:
        try:
            if agent is not None:
                agent.close()
        finally:
            session.close()
    write("Session closed. Goodbye.")
    return 0


def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(description="Chat with an OpenAI model controlling an isolated browser.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium without a visible window.")
    args = parser.parse_args()
    try:
        return run_chat(
            BrowserSession(headless=args.headless),
            agent_factory=lambda session: create_agent(
                session,
                AgentConfig(),
            ),
        )
    except PlaywrightError as exc:
        print(f"Browser cleanup failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
