---
name: hence-market-agent
description: Use Hence MCP tools to build safe paper/watch-only market theses, strategy candidates, evidence snapshots, and follow-up watches. Never place live trades through this v0 server.
---

# Hence Market Agent

Use the Hence MCP tools when a user asks to turn a market view, event, asset,
theme, or scenario into a structured thesis or paper/watch-only strategy.

Rules:

- Do not place live trades.
- Treat `data_only` routes as market context, not executable brokerage paths.
- Call `hence_start_market_workflow` first.
- If the workflow returns `needs_clarification`, ask the returned question and call `hence_continue_market_workflow`.
- Prefer `hence_get_workflow_summary` to present a concise candidate, selected/rejected legs, evidence, and blockers to the user.
- Use `hence_approve_thesis` only after the user explicitly approves a candidate, and always pass `selected_candidate_id` when multiple candidates exist.
- Treat `hence_save_strategy` as a deprecated compatibility alias of `hence_approve_thesis`.
- Use `hence_start_thesis_watch` only with follow-up consent.

Preferred flow:

1. Call `hence_start_market_workflow` with the user's goal.
2. If clarification is needed, ask the user and call `hence_continue_market_workflow`.
3. Call `hence_get_workflow_summary` and present the best candidate, selected/rejected legs, evidence, and blockers.
4. If the user approves, call `hence_approve_thesis` with the explicit `selected_candidate_id`.
5. Offer watch setup only after approval.

