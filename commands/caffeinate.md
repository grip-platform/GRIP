---
description: Prevent macOS sleep during RSI loops and long sessions
disable-model-invocation: false
---

# /caffeinate Command

Prevents macOS from sleeping during autonomous sessions. Wraps `lib/intelligent-caffeinate.sh`.

## Usage

```bash
/caffeinate              # Start (8h max, idempotent)
/caffeinate start        # Same as above
/caffeinate stop         # Stop the keep-awake GRIP recorded, and nothing else
/caffeinate status       # Show if active
```

## Implementation

```bash
source "$HOME/.claude/lib/intelligent-caffeinate.sh"

case "${1:-start}" in
    start)   caffeinate_start_for_rsi && echo "caffeinate: $(caffeinate_status)" ;;
    stop)    caffeinate_stop && echo "caffeinate: $(caffeinate_status)" ;;
    status)  echo "caffeinate: $(caffeinate_status)" ;;
esac
```

## Notes

- macOS only (no-ops on other platforms)
- Idempotent: safe to call multiple times
- Auto-starts with `/rsi` sessions
- Max 8 hours (override: `GRIP_CAFFEINATE_MAX_HOURS=N`)
- Keeps the Mac awake and lets the screen sleep (`caffeinate -ims`)
- Knows its own process only by the PID file, so it never adopts or stops a `caffeinate` it did not start. One whose PID file was lost ends at its 8-hour limit.
- Stops the recorded process only when `ps` shows a `caffeinate` that began before the PID file was written, or less than a minute after it. A process that began more than a minute later has reused the PID and is never signalled. When `ps` cannot say, `stop` leaves the keep-awake running, `status` still shows it, and it ends at its 8-hour limit.
- The minute is a deliberate margin, much wider than the check needs. The check compares two readings taken a moment apart, each in whole seconds: the process's age from `ps`, and the PID file's age from its date. Rounding alone needs about two seconds, and the moment between the readings grows on a busy Mac. A margin that is too small is the costly mistake. GRIP's own keep-awake would then look like a stranger, lose its record, and run beside a new one until its 8-hour limit. A wide margin costs little. A reused PID passes only if another `caffeinate` took it within that minute, and then GRIP's keep-awake ended in its first minute. A test sets a floor under the margin: a keep-awake that looks 30 seconds younger than its record must still be stopped, so the margin cannot drop below 30 seconds unnoticed.
- A keep-awake run by pm2 (`config/grip-caffeinate.ecosystem.cjs`) belongs to pm2, not to this command. If it still has the old flags, re-create it once: `pm2 delete grip-caffeinate && pm2 start config/grip-caffeinate.ecosystem.cjs`.
- The Python starter (`lib/intelligent_caffeinate.py`) recorded its keep-awake in `/tmp/grip-caffeinate.pid` until 2026-11-07, and now uses the shell's `$TMPDIR` file. A keep-awake it started before then is no longer seen, and it ends at its 8-hour limit.
