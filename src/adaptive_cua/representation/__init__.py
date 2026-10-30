"""The structured representation and the visual fallback (proposal sections 3.2 and 3.3).

To build:
- Build the base representation from the accessibility tree, and fill in
  missing names and states from DOM attributes.
- Give each element an identifier the agent can use to target actions.
- Remove or flag elements the user cannot see: hidden by style, outside the
  viewport, or covered by another element.
- Find the elements added, removed, or changed since the previous observation.
- Find visible content the structure does not describe (canvas, icon-only
  buttons, images without alt text, embedded frames) and crop those areas.

To decide as a team: see docs/decisions.md, "Representation".
"""
