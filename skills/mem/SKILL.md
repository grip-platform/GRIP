---
name: mem
description: OS memory projector — inspect, analyse, and converge system memory toward a stable fixed point. Integrates with HAL startup and HAL Council as always-on preflight. Uses the Banach fixed-point property — applying the operator to an already-healthy system is a noop.
status: Active
version: 1.0.0
effort: low
triggers:
  - "/mem"
  - "memory pressure"
  - "memory usage"
  - "htop"
  - "ollama memory"
  - "system slow"
  - "memory leak"
  - "purge memory"
  - "halo"
see_also:
  - hal-council
  - hal-server
  - optimizing-system-resources
---

# /mem — Memory Projector

One command. See what is eating your RAM. Converge toward a stable system.

## Commands

| Command | Description |
|---------|-------------|
| `/mem` | Show memory table — who is using what, is the system healthy |
| `/mem project` | Show advisory actions to reduce pressure (dry-run) |
| `/mem act` | Apply actions (stops ollama if running, advises on browsers) |
| `/mem watch` | Start always-on Monitor watcher (fires on pressure change) |
| `/mem status` | One-line summary for embedding in status lines |
| `/mem why` | Explain the Banach fixed-point architecture in plain terms |

## Protocol

### Immediate view (`/mem`)

```python
from lib.hal_mem import probe, fmt_table
state = probe()
print(fmt_table(state))
```

Output is an htop-style table:

```
Memory  [##################------------] 60% used of 16 GB
Pressure: GREEN (ok)   Free: 40%   Inactive(reclaimable): 4.7 GB
Wired: 2.5 GB   Active: 4.7 GB   Compressed: 3.4 GB

    PID  Process                  %MEM    RSS MB
------------------------------------------------------
   4208  Slack                     3.6     593.8
  12065  Brave Browser             2.8     465.5
  68002  WhatsApp                  2.7     436.3
   2647  claude                    2.4     393.3

Fixed point: YES
```

**Fixed point: YES** means the system is green — applying the projector would be a noop.
**Fixed point: NO** means actions are available to recover memory.

### Dry-run advisory (`/mem project`)

```python
from lib.hal_mem import probe, project, is_fixed_point
state = probe()
if not is_fixed_point(state):
    _, actions = project(state, dry_run=True)
    for a in actions:
        print(f"[{a.kind}] {a.target} — saves ~{a.estimated_mb:.0f} MB")
        if a.command:
            print(f"  command: {' '.join(a.command)}")
```

### Apply actions (`/mem act`)

Executes the safe subset of actions (no `sudo purge` — that requires password):

```python
from lib.hal_mem import probe, project
state = probe()
new_state, taken = project(state, dry_run=False, auto_stop_ollama=True)
print(f"Actions taken: {len(taken)}")
from lib.hal_mem import fmt_summary
print(fmt_summary(new_state))
```

### HAL Preflight (auto-runs on HAL startup and HAL Council)

When HAL Server or HAL Council is invoked, the memory projector runs a read-only
probe (~80ms). If memory is YELLOW or RED, a compact warning is shown before the
HAL call proceeds:

```
[!] Memory YELLOW: 18% free of 16GB | active=8.1GB wired=3.2GB compressed=4.9GB
    → ollama is using 6.1 GB — consider /mem act before the HAL council run
```

This ensures council deliberations don't start with a memory-pressured system
that could cause thrashing mid-run.

## The Projector Architecture

```
probe()           →  MemoryState (read-only, ~80ms, no LLM)
is_fixed_point()  →  True iff pressure == GREEN
project(dry_run)  →  (new_state, actions)

Fixed-point property:
  project(green_state) == (green_state, [])   # noop — idempotent
  project(red_state)   → actions → new_state closer to green
```

Inspired by the OCF fold-the-kernel Banach property:
- OCF compactor: ℱ(file) = file when already compact
- Memory projector: ℱ_mem(state) = state when already green

The mathematical claim (falsifiable via test `test_project_on_fixed_point_returns_empty_actions`):
> Applying the projector to a system in the fixed-point state produces zero actions.
> Applying it to a pressured state produces at least one action.

Run `python3 -m pytest tests/test_hal_mem.py -v` to verify.

## Always-On Monitor

The `/mem watch` command starts a Monitor tool watcher that fires whenever
memory pressure changes state (GREEN → YELLOW, YELLOW → RED, etc.):

```bash
# Start the watcher
Monitor monitors/mem-pressure.sh

# The monitor writes to ~/.grip-mem/pressure.jsonl
# CC wakes up only when a new pressure event lands
```

## Integration: HAL Council Auto-Preflight

When `/hal-council` or `/hal` is invoked, insert this before the council prompt:

```python
from lib.hal_mem import probe, fmt_summary, is_fixed_point
state = probe()
if not is_fixed_point(state):
    print(f"[MEM PREFLIGHT] {fmt_summary(state)}")
    print("    Run /mem act to recover memory before the council. Or proceed anyway.")
```

The check costs ~80ms and zero LLM tokens.

## Security

- `probe()` is read-only — safe at any time, no permissions needed
- `project(dry_run=True)` is read-only — safe, only advisory output
- `project(dry_run=False)` may run `killall ollama` — requires explicit `/mem act`
- `sudo purge` is NEVER run automatically — always requires operator password

## Troubleshooting

```bash
# Manual probe
python3 -c "from lib.hal_mem import probe, fmt_table; print(fmt_table(probe()))"

# Run tests (verifies fixed-point property)
python3 -m pytest tests/test_hal_mem.py -v

# Check vm_stat directly
vm_stat

# Check memory pressure level
memory_pressure

# Top processes by memory
ps aux --sort=-%mem | head -10
```
