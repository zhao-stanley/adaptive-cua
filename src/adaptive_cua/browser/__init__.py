"""Browser control and state capture (proposal section 4.1).

To build:
- Start or attach to a Chromium browser with Playwright.
- At each step, capture what the representation layer needs through the Chrome
  DevTools Protocol: the accessibility tree, a DOM snapshot with the screen
  position of each element, and a screenshot.

To decide as a team: see docs/decisions.md, "Browser and capture".
"""
