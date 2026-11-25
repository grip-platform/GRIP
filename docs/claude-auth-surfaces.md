# Claude Code auth surfaces — in-memory session vs on-disk credential

Origin: issue #3972 (2026-07-13, live observation). A running Claude Code REPL
kept reasoning normally **while every status check and fresh spawn reported
"Not logged in · Please run /login."** That combination is **expected
behaviour, not a bug** — this page is the durable mental model so no future
session re-diagnoses it.

## TL;DR

> You're logged out on paper, but the app that's already running never
> re-checks paper. It read your login once at startup, put the key in its
> pocket, and uses the pocket copy ever since. Deleting the paper copy does
> not empty the pocket.

A live process's **in-memory session token** and the **on-disk credential**
are two different surfaces. Only *new* launches read the disk.

## The three auth surfaces

| Surface | Reads credential from | Behaviour when the on-disk copy is absent |
|---|---|---|
| **Running REPL** (the session talking to you) | Memory — loaded ONCE at process start | Keeps working until the token expires or the process restarts |
| **Fresh spawns** (`claude`, `claude -p`, relay/batch jobs, status checks, `/login` prompts) | Disk — keychain `Claude Code-credentials` / `~/.claude/.credentials.json` (config-dir-scoped when `CLAUDE_CONFIG_DIR` is set) | Honestly report "Not logged in" / exit 1 |
| **Shell mode** (`!`-prefixed commands) | Neither — local bash, no Anthropic call in the loop | No effect — nothing in this path reads the credential |

## Operational implications

- **Live but not re-loginable.** The current session survives; the *next*
  fresh launch reads the empty drawer and needs `/login`. Re-run `/login` at
  a natural break — it repopulates the DISK for future spawns; the live
  REPL's in-memory token is unaffected (and needs nothing).
- **Spawned surfaces need the drawer, not the pocket.** The VPS relay's
  `claude -p`, the Mac drainer, batch jobs — each is a fresh process reading
  disk. Never infer a host's spawn-auth health from one live REPL on it
  (verify-canonical corollary). Evidence for the scoping: PR #3969
  (`9a8bc372`) fixed a live drainer failure whose commit documents the
  mechanism — a login made under `CLAUDE_CONFIG_DIR` lives in a
  config-dir-scoped keychain entry, so a spawn without that variable exits 1
  with "Not logged in" in the result JSON.
- **"Drive GRIP from any terminal" is real but drawer-gated.** `claude -p` /
  the `grip` CLI work from any terminal *iff* that host's on-disk credential
  is populated.
- **Safety rails stay live in this state.** During the ORIGINAL incident
  (evidence log: issue #3972) the env-var-protection guardrail blocked an
  `$ANTHROPIC_API_KEY` probe while the status said "logged out" — the gates
  do not depend on auth state. Note: the repro below re-creates the auth
  split only; it does not exercise the guardrail.

## Repro

1. Start `claude` with a valid credential (populates memory + disk).
2. Remove the on-disk credential (keychain entry / `.credentials.json`)
   **without** restarting the process.
3. Observe: the running REPL keeps working; `! claude …` and status checks
   report "Not logged in"; `/login` is requested but not required for the
   live session.

## Untested boundaries (falsification stubs)

Documented edges the repro does NOT cover — each would sharpen or refute the
model above if exercised:

- **Process death timing.** Kill the REPL *after* removing the credential but
  *before* any further API call — the in-memory token dies with the process
  either way, but this would confirm no shutdown path re-persists it to disk.
- **Multi-path credential cache.** With `CLAUDE_CONFIG_DIR` unset, remove
  ONLY the keychain entry (leave `.credentials.json`) — if the live session
  or a status check silently falls back to the file, "the running process
  never re-checks disk" is too strong for the multi-path cache and this doc
  must be narrowed.

## Decision record (from #3972)

Classified as expected Claude Code behaviour; documented rather than escalated
as a bug. Rejected alternatives: filing upstream (standard process-memory
behaviour), forcing the API-key bucket (unnecessary — the in-memory session
works), and inspecting the key past the guardrail (forbidden,
`rules/no-guardrail-evasion.md`). Full observation log, evidence, and the
memory-chain of beliefs: issue #3972. Mechanism-only by design — no account
or credential values (`rules/no-sensitive-info-in-grip.md`).
