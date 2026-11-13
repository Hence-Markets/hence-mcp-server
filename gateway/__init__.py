"""Hence MCP server: the door other people's AI agents use.

A small MCP server (Model Context Protocol, Streamable HTTP) in front of the Hence app
(https://app.hence.markets). It holds no keys, no database and no user state: every tool is a
read of something the app already serves to a signed-out visitor, reshaped for a model to read.
Python standard library only: nothing to install, nothing to audit but this repository.

This first slice is READ-ONLY by construction (tests/test_tools.py pins it): no tool creates,
trades, funds or moves anything. Identity, thesis creation and trade proposals arrive in later
slices and are enforced by the app, never here.
"""
