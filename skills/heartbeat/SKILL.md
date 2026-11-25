---
name: heartbeat
description: GRIP's autonomous learning pulse. Manage the periodic /evo + /learn pipeline that runs without human invocation.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /heartbeat
  - "heartbeat status"
  - "auto-learning"
see_also:
  - evo
  - learn
  - insights-pipeline
---

# /heartbeat -- Autonomous Learning Pulse

```
converge(facets, calibrated_learning, evo_learn_pipeline, max_depth=auto)
```

The heartbeat makes GRIP alive. It runs the `/evo` + `/learn` pipeline on a
schedule without human invocation. Gates ensure it only runs when useful.

## Usage

```bash
/heartbeat                # Show status (same as --status)
/heartbeat pause          # Disable auto-learning
/heartbeat resume         # Re-enable auto-learning
/heartbeat run            # Force a run now (bypass gates)
/heartbeat install        # Install platform scheduler
```

## Execution

```bash
python3 ~/.claude/scripts/grip-heartbeat.py --status    # Gate check
python3 ~/.claude/scripts/grip-heartbeat.py --force     # Run now
python3 ~/.claude/scripts/grip-heartbeat.py --pause     # Disable
python3 ~/.claude/scripts/grip-heartbeat.py --resume    # Enable
python3 ~/.claude/scripts/grip-heartbeat.py --install   # Install scheduler
```

## Gates (all must pass)

| Gate | What | Override |
|------|------|----------|
| Config | `heartbeat.enabled` in `config/grip.json` | `--resume` |
| Cooldown | N seconds since last run (default 2h) | `--force` |
| Staleness | New facets since last heartbeat | `--force` |
| Session | No active Claude CLI process | `--force` |

## Pipeline stages (`run_pipeline`)

Each tick runs, in order: `evo` → `learn` → `insights` → `cc-harness` →
`grip-harness` → **`learning-proposer`**. The last stage (added 2026-06-24)
routes recurring captured bash-execution mistakes (`learning/insights.jsonl`)
into reviewable fix **proposals** via `lib.learning_proposer route` — staged,
signed-IDR, with a GitHub-issue body. It **never self-applies** (the human/AGORA
review is the exogenous anchor); it fires only when new insights have landed
(`_proposer_stale`), and the per-pattern recurrence threshold is applied inside
the proposer.

## Configuration

All settings in `config/grip.json` under the `heartbeat` key:

```json
{
  "heartbeat": {
    "enabled": true,
    "cooldown_seconds": 7200,
    "pipeline": ["evo", "consolidate", "generate-rules", "converge-check"]
  }
}
```

| Key | Default | What |
|-----|---------|------|
| `enabled` | `true` | Master toggle. `--pause`/`--resume` sets this. |
| `cooldown_seconds` | `7200` | Minimum seconds between runs. |
| `pipeline` | 4 stages | Which learning steps to execute. |

## Pipeline Stages

```text
1. evo.py json          → Fractal facet calibration (false-flag detection)
2. learning_detector.py consolidate → Merge 3x insights (YSH)
3. learning_detector.py generate-rules → Refresh learned-insights.md
4. insights_learner.py converge-check → Check convergence trajectory
```

## Platform Schedulers

| Platform | Mechanism | Config |
|----------|-----------|--------|
| macOS | launchd agent | `config/heartbeat/com.grip.heartbeat.plist` |
| Linux | systemd user timer | `config/heartbeat/grip-heartbeat.{service,timer}` |
| Windows | Task Scheduler | `config/heartbeat/Install-GripHeartbeat.ps1` |

Install: `python3 scripts/grip-heartbeat.py --install`

## Log

Results append to `learning/heartbeat.jsonl`:

```json
{"timestamp": "2026-03-11T...", "steps": [...], "steps_ok": 4, "steps_total": 4}
```

## Structural Mapping (Gentner SMT)

| converge() | /heartbeat |
|------------|------------|
| state | Facet corpus + learning state |
| criterion | All pipeline stages pass |
| improve | evo calibration + rule generation |
| trigger | Time-based (cooldown) + data-based (staleness) |
| pause | `config/grip.json` declarative toggle |

## Opt-Out UX

Three equivalent paths to the same state:

1. **CLI**: `python3 scripts/grip-heartbeat.py --pause`
2. **Config**: Edit `config/grip.json` → `"heartbeat": {"enabled": false}`
3. **Skill**: `/heartbeat pause` (within a Claude session)

No hidden dotfiles. No sentinel files. One source of truth.
