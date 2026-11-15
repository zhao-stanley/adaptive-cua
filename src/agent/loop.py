"""Sequential, resumable browser-agent loop with bounded model requests."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from src.agent.config import AgentConfig
from src.agent.model import ModelError, OpenAIModel
from src.agent.tools import parse_arguments
from src.browser.session import BrowserSession
from src.ops.trace import RunTrace
from src.representation.observations import AdaptiveObservations
from src.representation.visual import ObservationError, inspection_region


@dataclass(frozen=True)
class AgentOutcome:
    status: str
    message: str


class Agent:
    def __init__(self, session: BrowserSession, model, config: AgentConfig):
        self.session = session
        self.model = model
        self.config = config
        self.history: list[dict] = []
        self.total_tokens = 0
        self.context_tokens = 0
        self.compaction_floor = 0
        self.compaction_rebase_pending = False
        self.deadline = None
        self.compaction_disabled = False
        self.observations = AdaptiveObservations(
            mode=config.observation_mode,
            full_refresh_interval=config.full_refresh_interval,
        )
        self.trace = RunTrace(config.trace_dir)
        self.trace.event("start", model=config.model, observation_mode=config.observation_mode)

    def close(self):
        try:
            self.model.close()
        finally:
            self.trace.close()

    def _repair_pending_calls(self):
        """Keep history valid if interruption happens between response and tool output."""
        answered = {item["call_id"] for item in self.history if item.get("type") == "function_call_output"}
        pending = [
            item for item in self.history if item.get("type") == "function_call" and item["call_id"] not in answered
        ]
        for call in pending:
            self.history.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(
                        {
                            "status": "interrupted",
                            "executed": None,
                            "error": "Observe before continuing; this action must not be automatically replayed.",
                        }
                    ),
                }
            )

    def _output(
        self, call: dict, data: dict, snapshot=None, *, action=None, arguments=None, requested="auto", region=None
    ):
        content = [{"type": "input_text", "text": json.dumps(data)}]
        prepared = None
        if snapshot is not None:
            prepared = self.observations.prepare(
                snapshot,
                action=action,
                arguments=arguments,
                status=data["status"],
                requested=requested,
                region=region,
            )
            content.extend(prepared.content())
        self.history.append({"type": "function_call_output", "call_id": call["call_id"], "output": content})
        self.trace.event("tool_result", call_id=call["call_id"], name=call["name"], **data)
        if snapshot is not None:
            self.trace.observation(snapshot, prepared)
        return prepared

    def _dispatch(self, call: dict, emit: Callable[[str], None]) -> AgentOutcome | None:
        try:
            args = parse_arguments(call["name"], call["arguments"])
        except (ValueError, TypeError) as exc:
            self._output(call, {"status": "invalid_arguments", "error": str(exc), "executed": False})
            return None
        name = call["name"]
        if name == "ask_user":
            self._output(call, {"status": "awaiting_user"})
            return AgentOutcome("awaiting_user", args["question"])
        if name == "finish":
            self._output(call, {"status": args["status"]})
            return AgentOutcome(args["status"], args["message"])
        emit(f"[action] {name}")
        started = time.monotonic()
        requested = args["detail"] if name == "request_observation" else ("full" if name == "observe" else "auto")
        result = (
            self.session.tools.fill_form(
                **args,
                deadline=self.deadline,
                emit_step=lambda data: self.trace.event("batch_action", call_id=call["call_id"], **data),
            )
            if name == "fill_form"
            else self.session.tools.execute("observe")
            if name in {"request_observation", "inspect_region"}
            else self.session.tools.execute(name, **args)
        )
        if result.observation is None:
            # Supply a fresh view for recovery, without repeating the failed action.
            refreshed = self.session.tools.execute("observe")
            result.observation = refreshed.observation
        region = args if name == "inspect_region" and result.observation is not None else None
        if region is not None:
            try:
                inspection_region(region, result.observation.viewport)
            except ValueError as exc:
                region = None
                result.status, result.error = "invalid_arguments", str(exc)
        prepared = self._output(
            call,
            {
                "status": result.status,
                **({"steps": result.steps} if result.steps else {}),
                "executed": result.executed,
                "error": result.error,
                "latency_seconds": time.monotonic() - started,
            },
            result.observation,
            action=name,
            arguments=args,
            requested=requested,
            region=region,
        )
        emit(f"[result] {result.status}")
        if prepared is not None:
            emit(f"[observation] {prepared.data['level']} ({len(prepared.images)} images)")
        return None

    def _compact_if_needed(self, deadline, emit):
        threshold = self.config.compact_after_tokens
        if not threshold or self.compaction_disabled or self.context_tokens < max(threshold, self.compaction_floor):
            return
        answered = {item["call_id"] for item in self.history if item.get("type") == "function_call_output"}
        if any(item.get("type") == "function_call" and item["call_id"] not in answered for item in self.history):
            raise ModelError("Cannot compact history with unanswered tool calls.")
        emit("[memory] Compacting task history")
        started = time.monotonic()
        try:
            compacted = self.model.compact(self.history, timeout=min(self.config.api_timeout, deadline - started))
        except ModelError:
            self.compaction_disabled = True
            self.trace.event("compaction_failed")
            emit("[memory] Compaction failed; keeping history. Automatic compaction disabled until /new.")
            return
        usage = compacted.get("usage") or {}
        self.total_tokens += usage.get("total_tokens", 0)
        output = compacted.get("output")
        if not isinstance(output, list) or not output or not any(i.get("type") == "compaction" for i in output):
            self.compaction_disabled = True
            self.trace.event("compaction_failed", reason="invalid_output", usage=usage)
            emit("[memory] Invalid compaction response; keeping history.")
            return
        # Keep the provider's complete replacement window, including retained messages.
        self.history = list(output)
        self.compaction_rebase_pending = True
        before_tokens = self.context_tokens
        self.context_tokens = 0
        # Avoid compacting every turn if the provider retains a large baseline.
        self.compaction_floor = max(threshold, (usage.get("output_tokens") or 0) + threshold // 2)
        self.observations.reset()
        self.trace.event(
            "compaction",
            usage=usage,
            total_tokens=self.total_tokens,
            context_tokens_before=before_tokens,
            next_compaction_threshold=self.compaction_floor,
            latency_seconds=time.monotonic() - started,
        )
        fresh = self.session.tools.execute("observe")
        if fresh.observation is None:
            raise ObservationError("History compacted, but browser refresh failed; send a follow-up to retry.")
        prepared = self.observations.prepare(fresh.observation, force_full=True)
        self.history.append({"role": "user", "content": prepared.content()})
        self.trace.observation(fresh.observation, prepared)
        emit("[memory] Task history compacted; fresh browser baseline supplied")

    def _run(self, message: str, emit: Callable[[str], None]) -> AgentOutcome:
        deadline = time.monotonic() + self.config.max_seconds
        self.deadline = deadline
        if self.total_tokens >= self.config.max_tokens:
            return AgentOutcome("limit", "Task token limit reached. Use /new to start a fresh task.")
        self.trace.event("user", message=message)
        initial = self.session.tools.execute("observe")
        if initial.observation is None:
            return AgentOutcome("error", f"Could not observe the browser: {initial.error}")
        prepared = self.observations.prepare(initial.observation, force_full=True)
        self.trace.observation(initial.observation, prepared)
        emit(f"[observation] {prepared.data['level']} ({len(prepared.images)} images)")
        self.history.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}] + prepared.content(),
            }
        )
        for step in range(1, self.config.max_steps + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.total_tokens >= self.config.max_tokens:
                return AgentOutcome(
                    "limit", "Execution limit reached. Browser remains open; inspect it before continuing."
                )
            self._compact_if_needed(deadline, emit)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.total_tokens >= self.config.max_tokens:
                return AgentOutcome("limit", "Execution limit reached during compaction. Browser remains open.")
            emit(f"[model] Step {step}/{self.config.max_steps}")
            started = time.monotonic()
            response = self.model.respond(
                self.history,
                timeout=min(self.config.api_timeout, remaining),
                max_output_tokens=min(self.config.max_output_tokens, self.config.max_tokens - self.total_tokens),
            )
            usage = response.get("usage") or {}
            self.total_tokens += usage.get("total_tokens", 0)
            self.context_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if self.compaction_rebase_pending:
                self.compaction_floor = max(
                    self.config.compact_after_tokens,
                    self.context_tokens + self.config.compact_after_tokens // 2,
                )
                self.compaction_rebase_pending = False
            emit(
                f"[tokens] Context {self.context_tokens:,}; cumulative {self.total_tokens:,}/{self.config.max_tokens:,}"
            )
            outputs = response.get("output", [])
            calls = [item for item in outputs if item.get("type") == "function_call"]
            text = "\n".join(
                part["text"]
                for item in outputs
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            self.trace.event(
                "response",
                response_id=response.get("id"),
                status=response.get("status"),
                usage=usage,
                total_tokens=self.total_tokens,
                latency_seconds=time.monotonic() - started,
                text=text,
                calls=calls,
            )
            if response.get("status") != "completed":
                return AgentOutcome(
                    "error", "The model response was incomplete or failed; no returned actions were run."
                )
            # Preserve all output items, including encrypted reasoning needed by the next request.
            self.history.extend(outputs)
            if not calls:
                if text:
                    return AgentOutcome("reply", text)
                return AgentOutcome("error", "The model returned no action or user-facing reply.")
            if len(calls) != 1:
                for call in calls:
                    self._output(
                        call,
                        {
                            "status": "rejected",
                            "error": "Request exactly one tool per response.",
                            "executed": False,
                        },
                    )
                continue
            call = calls[0]
            if time.monotonic() >= deadline or self.total_tokens >= self.config.max_tokens:
                self._output(call, {"status": "cancelled", "error": "Execution limit reached.", "executed": False})
                return AgentOutcome("limit", "Execution limit reached before the next action. Browser remains open.")
            history_size = len(self.history)
            try:
                outcome = self._dispatch(call, emit)
            except BaseException:
                # A follow-up must never replay an action or leave an unmatched tool call.
                if len(self.history) == history_size:
                    self.history.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps(
                                {
                                    "status": "interrupted",
                                    "executed": None,
                                    "error": "Execution interrupted. Observe before deciding what to do next.",
                                }
                            ),
                        }
                    )
                raise
            if outcome is not None:
                return outcome
        return AgentOutcome("limit", "Step limit reached. Browser remains open; send a follow-up to continue.")

    def run(self, message: str, emit: Callable[[str], None] = print) -> AgentOutcome:
        try:
            outcome = self._run(message, emit)
        except KeyboardInterrupt:
            self.observations.reset()
            self._repair_pending_calls()
            self.trace.event("interrupted")
            raise
        except ModelError as exc:
            outcome = AgentOutcome("error", str(exc))
        except ObservationError as exc:
            self._repair_pending_calls()
            self.observations.reset()
            outcome = AgentOutcome("error", str(exc))
        except BaseException:
            self.observations.reset()
            self._repair_pending_calls()
            raise
        self.trace.event("pause", status=outcome.status, message=outcome.message)
        return outcome


def create_agent(session: BrowserSession, config: AgentConfig | None = None) -> Agent:
    config = config or AgentConfig()
    model = OpenAIModel(config)
    try:
        return Agent(session, model, config)
    except BaseException:
        model.close()
        raise
