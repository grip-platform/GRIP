---
name: morning-brief
description: Closed-loop morning brief — GRIP orchestrates HAL orchestrates Google Calendar. Produces a structured start-of-day summary covering today's events, remaining sprint work, and one recommended first action.
version: 1.0.0
status: Active
triggers:
  - "/morning-brief"
  - "morning brief"
  - "what's my day look like"
  - "start of day summary"
---

# /morning-brief — Closed-Loop Morning Brief

**Architecture**: GRIP (skill) → gcal.py (GCal via DWD) → HAL (LLM formatting) → user.
This is the first user-facing demonstration that GRIP and HAL operate as a closed loop.

## Usage

```
/morning-brief              # Your own calendar, today
/morning-brief fabio        # Someone else on the team
/morning-brief --brief      # Output only the recommended first action
```

---

## Execution Protocol

### Step 1 — Fetch today's calendar

```bash
python3 ~/.claude/skills/gcal/gcal.py <operator_email> --days 1
```

Resolve `operator_email` from `~/.claude/facts/identity.md`. If a name is given,
resolve via `~/.claude/facts/team.md`. If gcal fails (auth error, missing deps),
continue with an empty events list and note the failure in the output.

### Step 2 — Read remaining work

From `rules/session-context.md` (already loaded): extract the "Remaining Work"
section. Limit to the top 3 items. If the file is absent or the section is empty,
skip this section in the output.

### Step 3 — HAL formatting pass (closed-loop step)

Attempt to produce the brief via HAL's inference layer:

```bash
hal_bin="${HAL_HOME:-$HOME/.hal}/bin/hal"
if [ -x "$hal_bin" ]; then
    printf '%s\n\n%s' "$EVENTS_RAW" "$REMAINING" \
        | "$hal_bin" --happi-api 2>/dev/null
fi
```

If `hal --happi-api` is unavailable or exits non-zero, fall back to Step 4.

The HAPPI/1.0 envelope format for the request:
```json
{"type":"human","content":"Generate a morning brief. Calendar events:\n{events}\n\nRemaining sprint work:\n{remaining}"}
```

### Step 4 — Inline formatting (fallback)

When HAL is unavailable, format the brief directly using the fetched data.
Apply the output schema from Step 5 regardless of which path was taken.

### Step 5 — Output schema

Emit the brief in this exact shape:

```
─── Morning Brief — {date} ─────────────────────────────
  Calendar ({N} events):
    {time}  {title}  [{location or "no location"}]
    ...

  Sprint work remaining:
    • {item 1}
    • {item 2}
    • {item 3}

  First action: {one concrete recommendation — specific, not generic}
─────────────────────────────────────────────────────────
```

Rules:
- Calendar: show only events for TODAY. Skip all-day events that are
  non-blocking (e.g. "Out of office" markers for others). Flag meetings
  that overlap.
- Remaining work: top 3 from session context only. Do not fabricate items.
- First action: derived from the schedule and remaining work — the one thing
  the operator should do first, given what's on the calendar today.

---

## Closed-Loop Design

This skill demonstrates the GRIP↔HAL closed loop at Layer 1 (user-facing):

```
User invokes /morning-brief
  → GRIP skill (this file) orchestrates the workflow
  → gcal.py fetches Google Calendar data via DWD
  → HAL (LLM) synthesises calendar + sprint context into actionable brief
  → Structured output returned to operator via GRIP
```

The loop is closed because the output feeds back into GRIP's session context —
subsequent GRIP skills (e.g. `/standup`, `/daily-planning`) can read the
morning brief's "First action" recommendation as their starting point.

---

## Falsification

This skill is wrong if, over 30 sessions:
- gcal auth fails > 50% of invocations (auth config issue, not skill design issue)
- The "First action" recommendation is rated unhelpful by the operator > 40% of the time
- The HAL fallback fires > 80% of invocations (means HAL integration isn't configured)

---

## Implementation Notes

- `gcal.py` requires `google-auth` and `google-api-python-client`. The script
  auto-detects the right Python environment — see `skills/gcal/SKILL.md` for details.
- DWD auth requires `laurie@` as the active gcloud ADC account with Token Creator
  role on `grip-meet-absorb-sa@your-gcp-project-id`. See memory file
  `project_gcal_skill.md` for the full auth path.
- `hal --happi-api` requires `HAL_HOME` to be set or `~/.hal/bin/hal` to exist.
  The fallback (Step 4) makes this optional — the skill produces output regardless.
