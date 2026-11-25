# ULTRAPROMPT — a scheduled session roster, delivered to Discord

Harness-agnostic. Any agent in any harness can execute this brief without
context from the session that wrote it. Requested by V>> 2026-09-20: *"I'm
getting behind … a /schedule /remind-me on Discord about running/idle GRIP
sessions listing all the session IDs (with descriptive current and previous
tasks — good naming with one sentence description in plain language) with links
to next-session continuation briefs."*

## The problem, measured

17 sessions are registered and ~15 `claude` processes are live. The operator
cannot tell what any of them is doing.

| Surface | What it gives | What it does NOT give |
|---|---|---|
| `~/.grip-session-mesh/presence.db` → `sessions` | `id`, `name`, `machine`, `role`, `registered_at`, `last_heartbeat` (17 rows) | **any task field at all** |
| the `name` column | `grip-session-69469-1789872835`, `.grip`, `void` | anything a human can read — PID/timestamp slugs and directory names |
| `~/.grip/state/next-session-*.md` | continuation briefs | only **1 exists**, for 17 sessions |
| `bin/grip-remind` | durable one-shot reminder → Discord + Slack via the VPS | **recurrence** — it is one-shot by design |

So three things are missing: readable names, task descriptions, and briefs.

## The join key (already verified — do not re-derive)

`presence.db.sessions.id` is `cc-<uuid>`, and the session transcript is
`~/.grip/projects/<cwd-slug>/<uuid>.jsonl`. Strip the `cc-` prefix and glob for
`<uuid>.jsonl`. That is the link from a registered session to what it has
actually been doing.

`~/.claude` is a symlink to `~/.grip`; glob under `~/.grip/projects/`.

## Build this

A script (suggested `bin/grip-session-roster`) that prints a Discord-ready
markdown roster, plus a recurring schedule that posts it.

1. **Enumerate** every row of `sessions`. Classify by `last_heartbeat` age:
   **running** (< 15 min), **idle** (< 24 h), **stale** (older). Heartbeats are
   written on tool calls, so an idle-but-alive session looks stale — say
   "last active", never "dead", and cross-check `pgrep -x claude` for a live
   process count.
2. **Derive a name and a one-sentence description per session** from the last
   turns of its transcript, in plain language. The name must say what the work
   IS, never the PID. Carry BOTH the current and the previous task, which is
   what the operator asked for and what makes the roster answer "where was I?".
3. **Link its continuation brief.** Where none exists, say so explicitly rather
   than omitting the row — a missing brief is the actionable fact.
4. **Deliver to Discord.** `mcp__grip-channel__channel_reply` with
   `platform: "discord"`, or the VPS path `bin/grip-remind` already uses.
5. **Schedule it.** `grip-remind` is one-shot; recurrence needs a cron entry or
   a launchd/systemd timer. The VPS already runs cron (`*/15 * * * *
   grip-chain-sync`), so a VPS cron entry survives the laptop being shut.

## Constraints that are not negotiable

- **Never read `state/whatsapp-history/*.jsonl`** — the operator's private
  WhatsApp.
- Transcripts contain client and personal content. The roster is a **name, a
  one-line description and a link** — never an excerpt. Run free text through
  `lib/redaction_shield.py --profile public` if anything derived from a
  transcript could reach a channel with third parties in it.
- Do not run GRIP modules under bare `python3`; use `./bin/grip-py`
  (`rules/never-system-python-for-grip.md`).
- `docs/` and `.github/workflows/` are gitignored by bare directory rules —
  `git add -f` is required for new files in either.

## Done means

- `bin/grip-session-roster` prints all 17 sessions, each with a readable name, a
  plain-English current AND previous task, and either a brief link or an explicit
  "no brief".
- A recurring job posts it to Discord and survives the laptop sleeping.
- Tests, each proved red before the fix: a session with no transcript still
  renders a row; a stale heartbeat is labelled "last active", not "dead"; no
  transcript text reaches the output beyond the derived one-liner.

## Why it was specified rather than built

The session that found all of this hit its context ceiling mid-investigation.
The mechanism above is verified, not guessed — the join key, the four surfaces
and their gaps were each measured. Building it needs a fresh window, which is
exactly the dispatch-outward case in `rules/dispatch-outward-when-context-fills.md`.

Primary Author: V>>
