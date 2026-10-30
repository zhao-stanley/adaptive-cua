"""The agent loop and actions (proposal sections 3.3 and 4.1).

To build:
- The loop: observation in, model call, actions out, repeat.
- Actions that target an element identifier or a screen position.
- Before each action in a batch, check that the target is still visible and in
  the same place.

To decide as a team: see docs/decisions.md, "Agent".
"""
