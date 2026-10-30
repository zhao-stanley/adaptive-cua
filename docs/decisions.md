# Decisions for the team

These questions come up before or while building each part.
Each has options, not answers.
Record the team's choice under each question.

## Browser and capture

1. Does the layer run only in the browser first, as the proposal says, with desktop support later?
2. Which viewport size and device scale factor do all runs use?
3. Is the screenshot captured at every step, even when it is not sent, so the layer can compare it with the structure?

## Representation

1. What text format does the agent read?
   Options: an indented tree like Playwright MCP snapshots, a flat list, or JSON.
2. How are element identifiers made, and when do they reset?
   Options: reset on each navigation, or keep across the whole task.
3. Which accessibility roles are kept, and which containers are flattened?
4. How is "covered by another element" tested?
   Options: paint order from the DOM snapshot, or a hit test in the page (`document.elementFromPoint`) for each element.
5. How is visible content without a description found in the screenshot?
   Options: pixel variance per block, edge pixels, or another method.
6. What counts as a change between steps: name, value, states, position?

## Policy

1. What are the exact rules and thresholds for each escalation?
2. How many failures on the same subgoal before a full screenshot, and how is a subgoal defined?
3. Are crops sent again on later steps if the region did not change?

## Agent

1. Which model, and which effort level, for the main runs?
   The proposal also has a stretch goal with a cheaper model.
2. Does the agent build on Anthropic's computer-use demo, or use custom tools with element identifiers?
3. Does the screenshot baseline use Claude's built-in computer-use tool or the same custom tools as the other conditions?
4. How are old observations handled when the context grows?

## Evaluation

1. Which benchmark tasks, and how many?
   The proposal names about 30 OSWorld 1.0 tasks and about 10 OSWorld 2.0 tasks.
2. Where do the benchmark environments run, and who sets them up?
3. What is the cost budget, and how many repeats per task and condition?
4. How is the step-level analysis in section 4.4 run: which representations are tested at each saved step, and how is "sufficient" judged?

## Team

1. Who owns which folder?
2. Branch and review process: for example, one branch per feature and one review per pull request.
