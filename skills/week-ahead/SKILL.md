---
name: week-ahead
description: Show the combined team calendar for the next 5 days — who's in what meetings, free windows, and good times for a team call.
version: 1.0.0
status: Active
triggers:
  - "/week-ahead"
  - "week ahead"
  - "team calendar"
  - "who is free"
  - "find a time"
  - "when can the team meet"
  - "team schedule this week"
---

# /week-ahead — Team Calendar

Shows the combined CodeTonight team calendar for the next 5 working days.
Useful for finding open time for a call, spotting busy days, and planning
when to schedule external meetings.

## Usage

```
/week-ahead                     # All team members, next 5 days
/week-ahead --days 3            # Shorter window
/week-ahead fabio andre         # Specific people only
/week-ahead --find-slot 60      # Find first 60-min slot when everyone is free
```

## Output Example

```
WEEK AHEAD — 21–25 Apr 2026

MONDAY 21 APR
  V>>:     09:30 Standup
  A>>:     09:30 Standup
  Member:  09:30 Standup · 10:00 Client call (TBC)
  Free windows: 11:00–14:00 (V>>, A>> clear)

TUESDAY 22 APR
  V>>:     (clear)
  A>>:     (clear)
  Everyone free 09:00–17:00 — good day for a deep-work session or team call

...

SCHEDULING TIP
  Best slot for a full-team call this week: Tuesday 10:00–11:00
  (all 5 team members free, no back-to-back risk)
```

## Execution Protocol

### Step 1 — Resolve team members

Read `~/.claude/facts/team.md` for the active CodeTonight team list.
Each person has a `@codetonight.co.za` email that works with `/gcal`.

Default team (if team.md is absent): laurie, andre, arnold.

### Step 2 — Fetch calendars (parallel)

For each team member, run:
```bash
python3 ~/.claude/skills/gcal/gcal.py <email> --days 5
```

Run these **in parallel** using Agent tasks or background processes where possible.
Sequential fetches for 5 people take ~10s; parallel takes ~3s.

Fallback: if a person's calendar fetch fails (403, timeout), skip them and
note "[name]: calendar unavailable" in the output.

### Step 3 — Build daily grid

For each day (Mon–Fri, skipping weekends):
- List each person's events in chronological order
- Show event title + start time only (no end time, no attendees)
- Mark all-day events as `(all day)`
- Mark cross-team events (attended by 3+ team members) as shared

### Step 4 — Identify free windows

For each day:
- Find contiguous 60+ minute gaps when all requested people are free
- "Free" = no events starting within 30 min of the window
- Present: "Free window: HH:MM–HH:MM (names clear)"

If no gaps exist where everyone is free: "No shared free time — check
individual schedules above for partial overlap."

### Step 5 — Scheduling tip (--find-slot)

When `--find-slot N` is passed:
- Find the first N-minute window this week where ALL requested people are free
- State: "Best slot: [day] [time]"
- If none found in 5 days: "No common slot found this week. Consider async instead."

### Step 6 — Format

Present as the template above:
- Day headers as "WEEKDAY DD MMM"
- One row per person per day
- Free windows below each day
- Scheduling tip at the end if `--find-slot` was used
- Keep each row under 80 chars
- British English date format (21 Apr, not Apr 21)

## Performance

Fetching 5 calendars takes ~2–3s if done in parallel. The Agent tool can
dispatch all 5 `gcal.py` calls as concurrent background tasks. If the Agent
tool is unavailable, run them sequentially with a brief note: "Fetching
calendars…" to set expectations.

## Privacy

Calendar data fetched via DWD is internal. Do NOT include personal events
(events where the only attendee is the person themselves) unless the summary
clearly relates to work. A "Doctor appointment" or "Personal" event should
appear as "(blocked)" not with the title.

## Falsification

This skill fails if:
- It shows a "Best slot" that conflicts with an event on one person's calendar
  (scheduling tip must be verified against the fetched data, not guessed)
- Personal events are shown with their real titles
- Weekend days are included (Mon–Fri only by default)
