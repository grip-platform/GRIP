"""The flags that make a ``claude -p`` call text-only: no built-in tool and no MCP server.

A council or roundtable seat that runs Claude Code as a subprocess is asked for an
answer, never for an action. ``--allowedTools ""`` does not deliver that: it only
pre-approves tools. The model still sees every tool, and the operator's
settings.json approves many Bash commands on its own, so such a seat can run them.

Measured 2026-11-11 on Claude Code 2.1.319 with claude-haiku-5-5, asked to run
``echo GRIP_PROBE_7`` through Bash: with ``--allowedTools ""`` alone the call took
two turns and answered ``GRIP_PROBE_7``, so the command ran; with these flags it
took one turn, ran nothing, and its prompt shrank from about 138K to 99.6K tokens
because no tool definitions were sent. The same day the /ultrathink trio's Fable
seat, launched with ``--allowedTools ""``, ran Bash and ripgrep in a GRIP worktree
until its 300-second timeout ended the call.

``--tools ""`` removes every built-in tool (``claude --help``: 'Use "" to disable
all tools'), and ``--strict-mcp-config`` with no ``--mcp-config`` loads no MCP
server. That is a capability lock, not a prompt setting
(rules/untrusted-content-capability-lock.md): an unknown tool name could only
leave fewer tools, never more. ``lib/headless_run.py`` and
``lib/lexicon/tokencount.py`` reached the same conclusion for their own calls.
"""

TEXT_ONLY_FLAGS = ("--tools", "", "--strict-mcp-config")
