# /update-team

Canonical scheduled team work-update. Keeps V>>'s teammates informed of his
work in plain-language/eli5/tldr style, posted to #grip-updates ~4x/day with
**no spam** (skips ticks where nothing new landed). A thin dedup + scheduler
wrapper over `/aurora` — reimplements none of the gather/render/post pipeline.

**DRY-RUN by default.** A bare call renders to stdout and posts nothing. A real
post needs **both** `--post` AND `GRIP_UPDATE_TEAM_POST=1`.

## Usage

```
/update-team [duration] [--eli5] [--post] [--force]
```

- `duration` — window, e.g. `24h`, `168h` (default 24h)
- `--eli5` — add the plain-English HAL narrative (fail-soft)
- `--post` — request a real post (still gated by `GRIP_UPDATE_TEAM_POST=1`)
- `--force` — bypass the no-new-work dedup

## Run

Render only (safe — prints the update, posts nothing):

```bash
python3 -m lib.update_team --hours-back 24
```

Force a post now (only fires if the env gate is also set):

```bash
GRIP_UPDATE_TEAM_POST=1 python3 -m lib.update_team --post --force
```

Invoke the skill for the full pipeline, schedule details, and safety contract:
`Skill(skill="update-team")`. Engine: `/aurora`. Schedule template:
`config/com.grip.update-team.plist.template` (08:07/12:07/16:07/19:07, not
auto-installed). Telemetry: `logs/update-team.jsonl`.

**The scheduled auto-post is DISABLED on blackbook since 2026-08-10** (V>>
directive — `com.grip.update-team` booted out, plist removed). Manual
`/update-team` is unaffected. Do not re-arm the timer without asking V>>; see
`skills/update-team/SKILL.md` for the reason and the `gh`-under-launchd defect
that must be fixed first.
