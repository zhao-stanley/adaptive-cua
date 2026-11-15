"""Deterministic escalation signals, with no extra model requests."""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    full_structure: bool
    screenshot: bool
    reasons: list[str]


class AdaptivePolicy:
    def __init__(self):
        self.failures = 0
        self.stalls = 0
        self.last_action = None

    def decide(
        self, *, reset, changed, visual_changed, action, arguments, status, requested, checkpoint, previous_structure
    ) -> Decision:
        reasons = []
        if reset:
            self.failures = self.stalls = 0
            self.last_action = None
            reasons.append("new_observation_baseline")
        full = reset or checkpoint or requested in {"full", "screenshot"}
        screenshot = requested == "screenshot"
        if checkpoint:
            reasons.append("periodic_structure_refresh")
        if requested != "auto":
            reasons.append(f"requested_{requested}")
        if action and status != "ok":
            self.failures += 1
            full = True
            reasons.append("action_error")
            if self.failures >= 2:
                screenshot = True
                reasons.append("repeated_action_errors")
        elif action and action not in {"observe", "list_tabs", "request_observation"}:
            self.failures = 0
            target = previous_structure.get(arguments.get("element_id"), {})
            expected_noop = (
                action == "click"
                and target.get("editable", False)
                or action == "type_text"
                and target.get("value") == arguments.get("text")
                or action == "select_option"
                and target.get("value") == arguments.get("value")
            )
            signature = (
                action,
                json.dumps({k: v for k, v in arguments.items() if k != "observation_version"}, sort_keys=True),
            )
            if (
                not reset
                and not changed
                and not visual_changed
                and not expected_noop
                and action in {"click", "type_text", "select_option", "drag"}
            ):
                self.stalls = self.stalls + 1 if signature == self.last_action else 1
                full = True
                reasons.append("no_observed_effect")
                if self.stalls >= 2:
                    screenshot = True
                    reasons.append("repeated_action_without_effect")
            else:
                self.stalls = 0
            self.last_action = signature
        return Decision(full, screenshot, reasons)
