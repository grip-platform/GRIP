# Caffeinate Keepalive Loop

The persistent /loop that re-enables macOS `caffeinate` before its `-t`
window expires — V>>'s 2026-05-26 ultrathink ask: *"create a /loop to make
sure caffeinate re-enables before it expires."*

## The problem it solves

GRIP's caffeinate wrapper (`lib/intelligent-caffeinate.sh::caffeinate_start_for_rsi`)
spawns:

```bash
caffeinate -ims -t 28800   # 8h TTL (GRIP_CAFFEINATE_MAX_HOURS=8 default)
```

The flags keep the Mac awake and let the screen sleep. Until 2026-11-07 they
were `-dimsu`, and `-d` and `-u` kept the screen lit for the whole 8 hours.

After 8 hours the process exits cleanly, the PID file goes stale, and the
machine can sleep mid-RSI / mid-autonomous-loop. Long sprints, overnight
loops, and any work that crosses the 8h boundary are exposed.

The keepalive tick fires every hour, checks remaining-before-expiry, and
rotates (stop + start fresh) when there's <1h left. So while the tick runs
on time and `ps` answers, the keep-awake never reaches its limit. Two rows of
the decision table below cover the exceptions. "Recorded keep-awake is gone"
covers a keep-awake that ended before the tick could rotate it, and "Caffeinate
running, age unknown" covers a `ps` that cannot give the age.

## Decision matrix

| Condition                                              | Action  | What runs                                  |
|--------------------------------------------------------|---------|--------------------------------------------|
| No RSI lock, no autonomous mode, no marker, not running | NOOP    | Nothing (battery-friendly silent tick)    |
| Activation signal set, caffeinate not running           | START   | `caffeinate_start_for_rsi`                |
| Caffeinate running, `remaining > margin` (default 1h)   | HEALTHY | Log only, unless the recorded keep-awake still has the old screen-holding flags (`caffeinate -dimsu`): then replace it. The replacement is judged one second after its start: `replaced` when its PID is alive and `ps` does not show another program, otherwise `replace_failed`. A dry run logs `would_replace` and changes nothing |
| Caffeinate running, `remaining ≤ margin`                | ROTATE  | `caffeinate_stop` then `_start_for_rsi`. The stop signals the old keep-awake only when `ps` confirms it is GRIP's. When `ps` cannot answer at the stop, the outcome is `rotate_failed`, and the old one runs to its own limit. The new keep-awake is judged one second after its start, as a replacement is: `rotated` or `rotate_failed` |
| Recorded keep-awake is gone: no live process at the recorded PID, or `ps` shows another program there, or a `caffeinate` that began over a minute after the record | START or NOOP | The status check drops the record with no signal, so the tick routes as if nothing runs: START when an activation signal is set, NOOP otherwise. This case never reaches ROTATE. GRIP had no keep-awake of its own from the old one's end until this tick. A `caffeinate` that another program runs at that PID is left alone. When the ticks run on time, that gap is at most one tick |
| Caffeinate running, age unknown (ps failed)             | ROTATE  | A rotation is attempted, but without `ps` the stop cannot confirm the old keep-awake, so the outcome is `rotate_failed` and nothing new starts. The old one runs to its own limit, and the first tick after that starts a new one while a loop, autonomous mode or the marker still asks for the Mac awake: a gap of up to one tick |

A keep-awake run by pm2 (`config/grip-caffeinate.ecosystem.cjs`) is never in
the PID file, so no tick touches it. A machine that runs it with the old flags
must re-create it once:
`pm2 delete grip-caffeinate && pm2 start config/grip-caffeinate.ecosystem.cjs`.

"Activation signal" = any of:
- `state/rsi/rsi-active.lock` names a live process (RSI loop active). A lock with no pid or a dead one is stale, as `lib/rsi/lock.py` rules.
- `.autonomous-state.json` `level > 0` (autonomous mode marker)
- `state/caffeinate-keepalive.lock` exists (operator opt-in marker)
- caffeinate is currently running (don't drop a manually-started session)

## Architecture (syscall-doctrine compliant)

```
[launchd schedule, every 1h on :17]
        ↓
[bin/caffeinate-keepalive]            ← bash wrapper, does ALL the I/O
        ↓ (gathers state)
   ps -o etime= -p $PID
   read CAFFEINATE_PID_FILE
   read state/rsi/rsi-active.lock (counts only with a live pid)
   read .autonomous-state.json
   stat state/caffeinate-keepalive.lock
        ↓ (feeds into)
[lib/caffeinate_keepalive.py]         ← PURE functions, no I/O
   should_activate(...)
   should_restart(age, max, margin)
   parse_etime_to_seconds(str)
   route_action(...) → noop | start | rotate | healthy
        ↓ (returns action label)
[wrapper acts]
   source lib/intelligent-caffeinate.sh
   → caffeinate_start_for_rsi  (START / ROTATE)
   → caffeinate_stop           (ROTATE first step; it signals only what ps
                                confirms, otherwise rotate_failed)
   → nothing                   (NOOP; HEALTHY with current flags; a dry run,
                                which logs would_replace for the old flags)
   → _caff_retire_legacy, then caffeinate_start_for_rsi
                               (HEALTHY with the old flags: replaced / replace_failed,
                                judged one second after the start)
        ↓
[telemetry line → data/caffeinate-keepalive.jsonl]
```

## Cadence rationale

- TTL = 8h = 28800s (default; `GRIP_CAFFEINATE_MAX_HOURS` overrides)
- Safety margin = 1h = 3600s
- Tick fires every 1h on `:17`
- 8 ticks per TTL window — generous redundancy; a missed tick still
  rotates on the next pass
- Off-minute `:17` avoids fleet-wide `:00 / :30` pile-up

Worst case: previous tick missed at age 7h, current tick fires at age 7h59m,
rotates 1min before natural expiry — still safe.

## Operate

```bash
# One-shot manual tick (matches what launchd fires)
bin/caffeinate-keepalive

# Decide-but-don't-act
CAFFEINATE_KEEPALIVE_DRY_RUN=1 bin/caffeinate-keepalive

# Override TTL / margin for testing
CAFFEINATE_KEEPALIVE_MAX_S=3600 \
  CAFFEINATE_KEEPALIVE_MARGIN_S=600 \
  bin/caffeinate-keepalive

# Force keepalive on without autonomous / RSI signals
touch state/caffeinate-keepalive.lock
bin/caffeinate-keepalive    # now routes start/rotate

# Inspect last 10 tick decisions
tail -10 data/caffeinate-keepalive.jsonl | jq .

# Install / uninstall the launchd schedule
bin/caffeinate-keepalive-launchd install
bin/caffeinate-keepalive-launchd status
bin/caffeinate-keepalive-launchd run-now    # one-off via launchd (testing)
bin/caffeinate-keepalive-launchd uninstall
```

## Verified end-to-end (2026-05-26)

After install + run-now:

```
[caffeinate-keepalive-launchd] kickstarting com.codetonight.caffeinate-keepalive once
[caffeinate-keepalive] healthy (PID=47256 age=204s, 28596s remaining of 28800s)
```

Telemetry line:

```json
{"ts":"2026-05-26T01:39:15Z","action":"healthy","outcome":"healthy",
 "pid":"47256","etime":"02:18","age_s":138,
 "max_s":28800,"margin_s":3600,
 "rsi_lock":0,"auto_level":1,"manual":0,"dry_run":0}
```

The tick correctly identified the running caffeinate (started by an
earlier kickstart of the same loop), parsed its etime, computed remaining,
and routed to HEALTHY (no rotation needed yet). This row is not in the
telemetry file that the status note under Hypotheses counts, which starts on
2026-07-18.

## Hypotheses

- **H-CAFF-KA-1** (deadline 2026-08-26): over the next 90 days of operation
  with this loop installed, zero "machine slept during RSI" incidents
  reported by V>>. Falsified by any operator report of machine sleeping
  mid-RSI / mid-autonomous-loop.
- **H-CAFF-KA-2** (deadline 2026-08-26): the keepalive tick fires reliably
  via launchd at the scheduled `:17` mark. Falsified by any 1h gap in
  `data/caffeinate-keepalive.jsonl` during an active autonomous session.
- **H-CAFF-KA-3** (deadline 2026-08-26): the 1h margin is sufficient — no
  rotation occurs more than once per 7h window. Falsified by a tick log
  showing two ROTATE events within a 7h window for the same PID lineage.

**Status, 2026-11-08: never tested.** None of the three was registered in the
hypothesis engine. `learning/hypotheses.jsonl` holds no `H-CAFF-KA` id and no
claim about this loop, so nothing checked them at the deadline. The machine
measured that day had no data to test them either. Its telemetry held 11 rows,
from 2026-07-18 to 2026-11-08. `launchctl list` showed no keepalive schedule,
and `~/Library/LaunchAgents` held no plist for it. KA-2 is about the schedule's
reliability once installed, so a schedule that was never installed says nothing
about it. Its falsifier also needs an active autonomous session, and no row
shows one. In `data/caffeinate-keepalive.jsonl`, `auto_level` is 0 in all 11
rows. `rsi_lock` is 1 in 10 of them, from 2026-07-18 to 2026-09-22. Until
GRIP#9017 merged at 15:38 UTC on 2026-11-08, that field counted any lock file,
live or not. The one row written after that, at 16:44 UTC, has `rsi_lock` 0. A
test needs the schedule installed and a new registration with a new deadline.

## Falsification

This loop is wrong if:

- Tick produces `start` / `rotate` events when no work is being protected
  (NOOP path is broken) — drains battery / wears the display unnecessarily.
- Tick fails to ROTATE before expiry under default cadence + margin while
  `ps` answers (the thing it was built to do). The one exception is the row
  "Caffeinate running, age unknown": with no age from `ps`, the old
  keep-awake runs to its own limit. With the ticks on time, the row "Recorded
  keep-awake is gone" is not this failure, because there something else ended
  the keep-awake first. A missed tick that lets it expire is this failure.
- The `is_running` activation signal causes the loop to keep caffeinate
  alive forever after a session ends, even when nothing else wants it —
  i.e. you can never get it to NOOP again without `/caffeinate stop`.
  (Note: this IS the intended behaviour, but if it surprises operators,
  the rule needs revising.)

## Related

- `lib/intelligent-caffeinate.sh` — underlying primitives (`caffeinate_start_for_rsi`,
  `caffeinate_stop`, `_is_caffeinate_running`).
- `skills/caffeinating-sessions/SKILL.md` — the `/caffeinate` skill itself.
- `bin/agora-convergence-tick` + `launchd/com.codetonight.agora-convergence-tick.plist`
  — sibling loop for AGORA review convergence (PR #3250). Same launchd shape.
- `docs/agora-convergence-loop.md` — sibling doc.
