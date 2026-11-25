---
name: gcal
description: Fetch Google Calendar events for any CodeTonight workspace user via Domain-Wide Delegation, with GRIP-First memory enrichment and plain-language action recommendations.
version: 1.0.0
status: Active
triggers:
  - "/gcal"
  - "check my calendar"
  - "check [name]'s calendar"
  - "what's on my calendar"
  - "what does [name] have on"
---

# /gcal — Google Calendar

## Usage

```
/gcal                              # Your own calendar, next 7 days
/gcal fabio                        # Fabio's calendar (resolved via facts/team.md)
/gcal development@codetonight.co.za      # Any workspace email, next 7 days
/gcal --days 1                     # Today only (morning brief mode)
/gcal fabio --days 3               # Fabio, next 3 days
```

## Execution Protocol

### Step 1 — Resolve subject email

1. If no arg given: read `~/.claude/facts/identity.md` for the operator's email.
2. If a short name is given (e.g. `fabio`): resolve to full email via `~/.claude/facts/team.md`.
3. If a full email is given: use directly.

### Step 2 — Fetch raw events (Python)

```bash
python3 ~/.claude/skills/gcal/gcal.py <resolved_email> [--days N]
```

The script auto-detects a Python environment that has `google-auth` and
`google-api-python-client` installed. If the current Python lacks them it
searches PATH, then versioned Homebrew binaries, then `/usr/bin/python3`,
and re-execs into the first one that works. If none found, it prints a
clear install instruction and exits.

### Step 3 — GRIP-First memory enrichment

Before synthesising the summary, apply GRIP-First retrieval tiers for each
distinct meeting name and each external attendee domain (non-`@codetonight.co.za`):

- **Tier 0 (session context)**: What do we already know about this meeting from this session?
- **Tier 1 (KONO)**: Semantic search — `"[meeting name]"`, `"[company/domain]"`,
  `"[project name if inferable]"`. Stop here unless tier 1 returns nothing useful.
- Do NOT escalate to Tier 3 (Grep) or Tier 4 (Explore) for calendar enrichment.

Use whatever context surfaces to add one plain-English sentence of background
per meeting. Do not fabricate context — if KONO returns nothing, omit the
background sentence.

### Step 4 — Synthesise plain-language summary

Rules:
- Group by day (e.g. "Today", "Tomorrow", "Wednesday 22 Apr").
- For each event: one sentence on what it is + one sentence action recommendation.
- Flag time conflicts (two events at the same time slot).
- Surface free windows ("Your afternoon is clear").
- **Action recommendation heuristics** (apply semantically, not as a lookup table):
  - External client meeting → check recent work, prepare a status update or demo notes
  - Recurring internal standup → no special prep needed, just status update ready
  - One-off internal meeting with description → summarise description into prep note
  - Interview / hiring → nothing unless description gives candidate name/role
  - All-day event (conference, travel) → flag as context for the day's meetings
- Output: British English, plain language. No KONO codes, no hypothesis IDs, no jargon.

## Auth

| Setting | Value |
|---------|-------|
| SA | `grip-meet-absorb-sa@your-gcp-project-id.iam.gserviceaccount.com` |
| SA client ID | `115168037007162836229` |
| DWD scope | `https://www.googleapis.com/auth/calendar.readonly` |
| ADC | `gcloud auth application-default login` — laurie@ must be the active account |

DWD scopes are managed in Google Workspace Admin Console → Security → API Controls
→ Domain-wide delegation → client ID `115168037007162836229`.

If auth fails with `unauthorized_client`: the scope is missing from the DWD list
— add `https://www.googleapis.com/auth/calendar.readonly` in the Admin Console.

If auth fails with `Reauthentication needed`: run `gcloud auth application-default login`.

## Morning Brief Mode

`/gcal --days 1` is optimised for the start-of-day brief:

- Today's events only, in chronological order
- Lead with the first thing happening and how far away it is
- Action items surfaced before the event list, not after
- Free time windows noted for planning purposes

## Falsification

This skill fails if:
- DWD scope removed from Admin Console (clear auth error surfaced, re-add instruction given)
- `google-auth` absent from all detected Python envs (clear install message, no crash)
- Subject email not in the CodeTonight workspace (API 403 surfaced clearly)
- Action recommendations are generic and useless — then the GRIP-First enrichment step has
  failed and KONO context was not actually queried
