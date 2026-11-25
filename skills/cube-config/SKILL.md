---
name: cube-config
description: Configure CUBE dimensions, operational metrics, promotion thresholds, proof report schedule, and external scoring mode via multi-step wizard
command: /cube-config
tags: [cube, config, wizard, hitl]
---

# CUBE Configuration Wizard

Configure which CUBE dimensions to track and how strictly to enforce them.

## Wizard Flow

Use AskUserQuestion for each step (progressive disclosure from @asking-users and its multi-step sibling @creating-wizards).

### Step 1: Current State Overview

Show the current CUBE state table (domains, levels, W-depth) and offer:

```
Quick actions:
  1. Enable all (all 8 principles + 6 operational) — recommended
  2. Keep current configuration
  3. Customise dimensions
```

If "Enable all" or "Keep current" → skip to Step 3.

### Step 2: Operational Dimensions Toggle

Only shown if "Customise" chosen in Step 1.

Show the 6 operational metrics with descriptions:

| Metric | Description | Default |
|--------|-------------|---------|
| convergence_speed | How fast converge() runs resolve | ON |
| friction_density | Calibrated friction per session | ON |
| task_efficiency | Ratio of fully_achieved outcomes | ON |
| achievement_rate | Ratio of fully + mostly achieved | ON |
| compound_rate | Evidence of compound improvement | ON |
| genome_health | Average gene fitness | ON |

Ask: "Which operational metrics should CUBE track? (comma-separated numbers, or 'all')"

### Step 3: Promotion Thresholds

Ask: "How strict should CUBE level promotions be?"

| Option | Description | Thresholds |
|--------|-------------|------------|
| strict | Higher confidence needed for promotion | THREAD: 0.70, FORK: 0.80, COUNCIL: 0.90 |
| default | Standard thresholds | THREAD: 0.60, FORK: 0.70, COUNCIL: 0.80 |
| relaxed | Lower bar for promotion | THREAD: 0.50, FORK: 0.60, COUNCIL: 0.70 |

### Step 4: Proof Report Schedule

Ask: "Run proof report automatically?"

| Option | Description |
|--------|-------------|
| evo | Run after every /evo invocation |
| weekly | Run once per week (via heartbeat) |
| manual | Only when explicitly invoked via /proof |

### Step 5: External Scoring Mode

Ask: "How should converge outputs be scored?"

| Option | Description | Cost |
|--------|-------------|------|
| self-only | Current behaviour, fastest | Free |
| external-on-proof | External scoring only during /proof | Low (Gemini 3.1 Pro lead, Groq free tier) |
| external-on-converge | Every /converge gets external scoring | Medium |
| council | Self + 2 external, weighted median | Medium (recommended) |

Note: Gemini 3.1 Pro is the lead scorer. Claude is EXCLUDED from the external council (same model family = self-scoring bias).

### Step 5 (Confirmation)

Show summary of all selections and write to `cache/cube-config.json`.

## Config File Format

```json
{
  "enabled_operational": ["convergence_speed", "friction_density", ...],
  "promotion_mode": "default",
  "proof_report_schedule": "evo",
  "external_scoring_mode": "council",
  "configured_at": "2026-03-17T12:00:00Z",
  "configured_by": "V>>"
}
```

## Integration Points

- `wire_principles()` and `wire_operational()` read this config to decide what to record
- `/evo` checks `proof_report_schedule` to decide whether to auto-run proof report
- `external_scorer.py` reads `external_scoring_mode` to decide scoring strategy

## Implementation

```python
import json
from pathlib import Path

CUBE_CONFIG_PATH = Path.home() / ".claude" / "cache" / "cube-config.json"

def load_cube_config() -> dict:
    """Load CUBE configuration or return defaults."""
    if CUBE_CONFIG_PATH.exists():
        try:
            return json.loads(CUBE_CONFIG_PATH.read_text())
        except Exception:
            pass
    return {
        "enabled_operational": [
            "convergence_speed", "friction_density", "task_efficiency",
            "achievement_rate", "compound_rate", "genome_health",
        ],
        "promotion_mode": "default",
        "proof_report_schedule": "manual",
        "external_scoring_mode": "self-only",
    }

def save_cube_config(config: dict) -> None:
    """Save CUBE configuration."""
    from datetime import datetime, timezone
    config["configured_at"] = datetime.now(timezone.utc).isoformat()
    CUBE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUBE_CONFIG_PATH.write_text(json.dumps(config, indent=2))
```
