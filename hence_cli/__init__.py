"""The `hence` command: Hence from a terminal.

A thin client of the Hence MCP server. It calls the same tools an AI agent would, over the
same endpoint, so anything the server learns to do is available here without a new release
(`hence tools`, `hence call`). Standard library only.

Three rules it keeps:
  - A failure is said on stderr with a non-zero exit, and nothing is printed on stdout. A script
    that pipes this command can never read an error as data.
  - A figure that was not read is shown as "n/a", never as zero.
  - A credential is sent only to the server it was issued for, only over https (or to this
    machine), and is never printed.
"""
VERSION = '0.1.0'
