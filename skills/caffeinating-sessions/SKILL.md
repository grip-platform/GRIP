---
name: caffeinating-sessions
description: Prevents macOS sleep during long RSI loops and autonomous sessions. Use when starting RSI sprints, pair-mode marathons, or when user invokes /caffeinate.
status: Active
effort: low
version: 1.1.2
created: 2026-03-09
generation: 469
triggers:
  - /caffeinate
  - "enable caffeinate"
  - "keep awake"
  - "prevent sleep"
  - RSI session start
platform: macOS-only
integrates:
  - rsi-aliases
  - pair-mode
---

# Caffeinating Sessions

**Category**: System | Session Continuity
**Token Budget**: <200 (single bash call)
**Origin**: YSH — pattern proven 3x+ (user requested caffeinate in Gen 465, 467, 468, 469)

---

## Purpose

Prevents macOS from sleeping during autonomous RSI loops, pair-mode marathons, and long sprint sessions. Wraps `lib/intelligent-caffeinate.sh` which provides idempotent process management and safety limits, and keeps the Mac awake while letting the screen sleep.

---

## Usage

```bash
/caffeinate              # Start (default: 8h max)
/caffeinate start        # Explicit start
/caffeinate stop         # Stop the keep-awake GRIP recorded
/caffeinate status       # Check if active
```

---

## Implementation

All logic lives in `lib/intelligent-caffeinate.sh`. This skill is a thin invocation wrapper — no duplication.

```bash
source "$HOME/.claude/lib/intelligent-caffeinate.sh"

case "${1:-start}" in
    start)   caffeinate_start_for_rsi ;;
    stop)    caffeinate_stop ;;
    status)  caffeinate_status ;;
    *)       echo "Usage: /caffeinate [start|stop|status]" ;;
esac
```

### What `caffeinate_start_for_rsi` Does

1. Checks macOS (`uname == Darwin`), no-ops on other platforms
2. Retires the keep-awake named in its PID file if it still has the old flags (`caffeinate -dimsu`), which hold the screen on. It never stops a `caffeinate` it did not record.
3. Idempotent — skips if already running (PID file check)
4. Starts `caffeinate -ims -t $max_seconds` in background
5. Writes PID to `$TMPDIR/grip-caffeinate.pid`
6. Max duration: `$GRIP_CAFFEINATE_MAX_HOURS` (default 8h)

### Flags

The flags keep the Mac awake and let the screen sleep on its own timer.

| Flag | Prevents |
|------|----------|
| `-i` | Idle sleep |
| `-m` | Disk sleep |
| `-s` | System sleep (AC power) |

`-d` (display sleep) and `-u` (declares user activity) are left out on
purpose. With `-t`, each holds the screen on for the whole run, and until
2026-11-07 they kept it lit for 8 hours at a time.

---

## Auto-Start Triggers

Caffeinate activates automatically when:

1. `/rsi` is invoked (via `caffeinate_start_for_rsi()`)
2. `GRIP_AUTONOMOUS=1` is set in environment
3. `.autonomous-state.json` has `level > 0`
4. `state/rsi/rsi-active.lock` names a live process. A lock with no pid or a dead one is stale, the rule `lib/rsi/lock.py` applies (GRIP#8989).

---

## Safety

- **Max duration**: 8 hours (configurable via `GRIP_CAFFEINATE_MAX_HOURS`)
- **Ownership by PID file only**: GRIP never adopts or stops a `caffeinate` it did not record, because flags are not proof of ownership. `caffeinate_stop` stops the recorded process and nothing else. A GRIP keep-awake whose PID file was lost ends at its `-t` limit.
- **Signal only what `ps` confirms**: GRIP signals the recorded PID only when `ps` shows a `caffeinate` that began before the PID file was written. A process that began more than a minute later has reused the PID, so its record is dropped and it is never signalled. A keep-awake that `ps` cannot place keeps its record, is left running and ends at its `-t` limit.
- **RSI lock guard**: `caffeinate_stop_if_safe()` keeps caffeinate alive while RSI lock is held
- **Cross-platform**: No-ops silently on Linux and Windows

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~150 |
| Execution | ~50 |
| **Total** | **~200** |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-09 | Initial creation (Gen 469, YSH 3x+) |
| 1.1.0 | 2026-11-07 | Flags `-ims`, so the screen sleeps; ownership by PID file only, so no adoption and no orphan sweep (GRIP#8990) |
| 1.1.1 | 2026-11-08 | The RSI lock counts only while it names a live process, the rule `lib/rsi/lock.py` applies (GRIP#9017) |
| 1.1.2 | 2026-11-08 | A recorded keep-awake whose command `ps` cannot report is kept and never signalled; a PID reused by a process that began after the record is never signalled; the keep-alive tick judges a replacement after a one-second wait and a dry run reports it (GRIP#8991) |

---

## References

- Implementation: `lib/intelligent-caffeinate.sh`
- Issue: [#59](https://github.com/grip-platform/GRIP/issues/59)
- Memory protocol: `MEMORY.md` Caffeinate Protocol section
