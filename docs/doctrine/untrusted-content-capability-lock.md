# Untrusted-Content Capability Lock: annex

The narrative behind `rules/untrusted-content-capability-lock.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-05-17. A `grip-anywhere` analysis of
`BeehiveInnovations/pal-mcp-server` surfaced its P1 #417 (untrusted remote
MCP prompt → spawned sub-CLI run with `--permission-mode acceptEdits` /
`--dangerously-bypass-approvals-and-sandbox` → arbitrary host write). An
audit found GRIP's own VPS `relay-dispatcher` was the **same structural
class**: `lib/relay_dispatcher.py::build_argv` spawned
`claude -p --dangerously-skip-permissions` with the untrusted inbound
channel text as the prompt, `cwd=~/.claude`. Fixed by GRIP #2891
(`--tools "Read,Grep,Glob"` capability allowlist + Goodhart-resistant
test). This rule generalises the lesson so the class cannot recur
silently.
