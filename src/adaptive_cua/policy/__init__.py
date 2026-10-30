"""The policy that decides how much detail to send at each step (proposal section 3.3).

To build:
- Choose among the four levels: changes since the previous step, the full
  structured representation, cropped images, and a full screenshot.
- Escalate on missing content, on an action that changes nothing, or on
  repeated failures; return to changes only after a successful action.

To decide as a team: see docs/decisions.md, "Policy".
"""
