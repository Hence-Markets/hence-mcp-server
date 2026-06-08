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
- Ask clarifying questions when the workflow returns `needs_clarification`.
- Use `hence_approve_thesis` only after the user explicitly approves a candidate.
- Use `hence_start_thesis_watch` only with follow-up consent.

Preferred flow:

1. Call `hence_start_market_workflow` with the user's goal.
2. If clarification is needed, ask the user and call `hence_continue_market_workflow`.
3. Present candidate theses, selected/rejected legs, evidence, and blockers.
4. If the user approves, call `hence_approve_thesis`.
5. Offer watch setup only after approval.

