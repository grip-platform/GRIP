---
name: standup
description: Generate a structured standup update — yesterday's commits, today's plan, and any blockers — from git history and session context.
version: 1.0.0
status: Active
triggers:
  - "/standup"
  - "standup prep"
  - "prepare standup"
  - "what did I do yesterday"
  - "generate my standup"
  - "standup update"
---

# /standup — Daily Standup Prep

Generates your standup bullets in under 10 seconds from actual git history.
No guessing. No summarising from memory.

## Usage

```
/standup              # Auto-detect yesterday + today (default)
/standup --brief      # One-liner per section (for async standups)
/standup --team       # Include a pull summary of team commits too
```

## Output Format

```
YESTERDAY
• Shipped /gcal skill — Google Calendar via DWD for any workspace user
• Fixed routing false positive: "padding" was matching "add" tier
• Vendored sqlite-lembed binary, removing dormant supply-chain risk

TODAY
• Continue RSI sprint: SOLID score at 62%, targeting improvement
• Review H046 confirmation status
• grip-mobile picomatch #2334 — blocked, monitor

BLOCKERS
• #2334 blocked on react@19 peer conflict (upstream)
```

## Execution Protocol

### Step 1 — Yesterday's work (from git)

```bash
git log -n 10000 --since="yesterday 00:00" --until="today 00:00" \
    --format="%s" --author="$(git config user.name)" 2>/dev/null
```

The explicit `-n` keeps every commit: the RTK hook's rewrite of a plain
`git log` stops at 50 when no `-n` is given (GRIP#9179), and `-n` also works
where rtk is not installed.

If that returns nothing (weekend, holiday, first session):
```bash
git log --since="3 days ago" --format="%s" \
    --author="$(git config user.name)" -10 2>/dev/null
```

Translate each commit subject into plain-language bullet:
- `feat(gcal): …` → "Shipped [description]"
- `fix(routing): …` → "Fixed [description]"
- `chore: …` → skip unless it's a sync or notable state change
- Max 5 bullets. Merge closely related commits.

### Step 2 — Today's plan (from session context)

Read `~/.claude/rules/session-context.md`. Extract **Remaining Work** items.
Present up to 3 items as "TODAY" bullets. Rephrase for standup tone
("Continue X", "Review Y", "Ship Z") rather than the raw task text.

If no session context or all items are resolved: "Continue sprint —
check `rules/session-context.md` for details."

### Step 3 — Blockers

From session context, extract any items explicitly marked as:
- "blocked", "waiting on", "pending upstream", "cannot proceed"
- GitHub issue numbers with context (e.g. "#2334 react@19 peer conflict")

If nothing is blocked: "No blockers."

### Step 4 — Output

Present as plain text, copy-pasteable into Slack, Teams, or a standup tool.
No markdown headers — just YESTERDAY / TODAY / BLOCKERS as all-caps labels.
Keep each bullet under 10 words where possible.

## Standup Etiquette Built In

- Yesterday bullets are factual (what actually shipped, not what was attempted).
- Today bullets are commitments, not wish lists. Max 3.
- Blockers name the dependency clearly (issue number, person, upstream package).
- If it's Monday, say "Last week:" instead of "Yesterday:".

## Brief Mode

`/standup --brief` produces one sentence per section:

```
Yesterday: fixed routing bug + shipped gcal skill.
Today: SOLID improvement + H046 review.
Blockers: #2334 react@19 (upstream).
```

## Team Mode

`/standup --team` additionally runs:

```bash
git log -n 10000 --since="yesterday 00:00" --format="%an: %s" \
    --all --no-merges 2>/dev/null | sort -u | head -20
```

Groups commits by author and presents a "what the team shipped" section
above your own update. Useful for async standups where you want context
before adding your own.

## Falsification

This skill fails if:
- git log returns no commits for the operator (wrong author name in git config — check `git config user.name`)
- Session context is blank (no remaining work — just says "No pending items, sprint complete")
- A "blocker" is fabricated (if it's not in session context or a GitHub issue, don't invent it)
