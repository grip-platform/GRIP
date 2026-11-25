---
name: eod
description: End-of-day wrap-up — what shipped today, what carries over to tomorrow, and an optional Slack post to close the day.
version: 1.0.0
status: Active
triggers:
  - "/eod"
  - "end of day"
  - "wrap up today"
  - "what did I ship today"
  - "close the day"
  - "day summary"
  - "end of day summary"
---

# /eod — End-of-Day Wrap-Up

One command to close the day: what shipped, what carries over, and an optional
Slack post. The symmetric complement to `/morning`.

## Usage

```
/eod                  # Full wrap-up (commits + remaining work)
/eod --brief          # One-liner per section (async digest mode)
/eod --slack          # Post summary to #daily-chat after generating
/eod --save           # Also run /save to persist session state
```

## Output Example

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  End of day — Tuesday 22 Apr. Good work today.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SHIPPED TODAY
  • WhatsApp self-reply loop fixed — outgoing texts no longer echo back
  • /morning, /standup, /recap, /week-ahead skills added to GRIP
  • Gemini 2.5 Flash — multi-model analysis via new provider
  5 commits · 2 PRs merged

CARRYING OVER
  1. Upstream CC issues #39342, #41787 — monitor, no action needed
  2. Dependabot vulnerabilities (2 high, 2 moderate) — review via GitHub UI

METRICS
  Session quality: 0.814 · Genome: Gen 208, 375 genes
```

## Execution Protocol

### Step 1 — What shipped today

```bash
# keep -n: the RTK hook's rewrite of a plain git log stops at 50 commits (GRIP#9179)
git log -n 10000 --since="today 00:00" --format="%s" \
    --author="$(git config user.name)" --no-merges 2>/dev/null
```

Working directory: `~/.claude` (the GRIP repo). If commits span multiple repos,
check the currently active project directory too.

Translate each commit into a past-tense plain-language bullet (same rules as `/recap`):
- `feat(...)` → "... added / shipped / enabled"
- `fix(...)` → "... fixed / resolved"
- `chore(...)` → skip unless a notable state change
- Max 7 bullets. Merge closely related commits.

Show a tally line: "N commits · M PRs merged"

### Step 2 — Carrying over

Read `~/.claude/rules/session-context.md`. Extract **Remaining Work** items
that were NOT completed today (i.e., not addressed in today's commits).

Present as a numbered list. Max 5 items. If nothing remains: "Nothing carrying
over — clean slate for tomorrow."

If remaining work is stale (>3 days since last update), flag it:
"Note: remaining items are N days old — verify these are still relevant."

### Step 3 — Metrics (optional, omit if data unavailable)

Read last line from `~/.claude/learning/delight_v2.jsonl` for session quality.
Read genome generation and gene count from `~/.claude/data/genome-shared.json`.

Show a compact metrics line if data is available. Omit silently if files are
missing or unreadable.

### Step 4 — Motivational close

One short sentence appropriate to what was shipped. Examples:
- "Solid day — 5 commits shipped and the daily workflow suite is complete."
- "Good progress — the WhatsApp fix closes a loop that was open since Gen 481."
- "Short day but H046 is confirmed and the pipeline is green."

Keep it factual and brief. No empty praise.

### Step 5 — Slack post (--slack flag)

When `--slack` is passed, post the summary to `#daily-chat` (C0EXAMPLE01)
via the Slack MCP tool. Format as plain text — no Block Kit needed for EOD.

```
*End of day — 22 Apr*

Shipped: [bullets]
Carrying over: [items or "nothing"]
```

Skip silently if Slack MCP is unavailable.

### Step 6 — Save (--save flag)

When `--save` is passed, run `/save` after generating the EOD summary to
persist session state. Present the EOD summary first, then run /save.

## Brief Mode

`/eod --brief` condenses to three lines:

```
Shipped: WhatsApp fix + 4 daily-workflow skills (5 commits).
Carrying over: CC issues #39342/#41787 (monitor only).
Tomorrow: Dependabot vulnerabilities — review via GitHub UI.
```

## Design Decisions

- **Commits over memory**: EOD accuracy depends on git log, not recall. The
  summary reflects what was actually committed, not what was intended.
- **Carrying over from session-context**: this is the canonical cross-session
  handoff file, not project-specific memory.
- **Metrics optional**: DELIGHT and genome data enrich the EOD when available
  but the skill degrades gracefully to just commits + remaining work.
- **No web lookups**: GRIP-First Tier 0-2 only. EOD must be fast (<10 seconds).

## Falsification

This skill fails if:
- EOD bullets contradict the actual git log (fabricated work)
- Remaining work includes items that were in fact closed by today's commits
- The Slack post goes to the wrong channel (must be `#daily-chat`, not `#grip-updates`)
- The metrics line shows data from a different day (always read fresh, never cached)
