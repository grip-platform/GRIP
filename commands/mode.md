# /mode

Switch, list, and manage GRIP operating modes. Supports multi-mode selection (budget-bounded, up to 30 — the loader's MAX_MODES, overridable via GRIP_MAX_MODES).

## Usage

```bash
/mode                      # List available modes with token budgets
/mode list                 # List available modes
/mode <name>               # Activate single mode
/mode <n1> <n2> [...]      # Activate multiple modes (budget-bounded, up to 30)
/mode off                  # Deactivate all modes
/mode status               # Show current mode status with combined budget
```

## Multi-Mode Selection (Gen 365)

Activate multiple modes simultaneously (up to 30, bounded by the combined token budget):

```bash
# Secure development
/mode code security

# System design with analysis
/mode analysis architect

# Max 5 modes allowed
/mode code security research architect analysis
```

## Available Modes

| Mode | Token Budget | Description |
|------|--------------|-------------|
| `code` | ~6000 | Software development with design principles |
| `security` | ~3500 | Security auditing, vulnerability detection |
| `research` | ~3000 | Deep research, evidence synthesis |
| `strategy` | ~3500 | Strategic assessment and decision-making |
| `architect` | ~4000 | System design, ADR generation |
| `legal` | ~2500 | Contract drafting and legal documents |
| `marketing` | ~2500 | Authentic content creation (anti-AI slop) |
| `business` | ~2500 | Founder operations and leadership |
| `create` | ~2000 | Meta-mode for creating new modes |
| `analysis` | ~4000 | Structured RIPER analysis |
| `teaching` | ~3000 | Educational content, curriculum design |
| `automation` | ~4500 | Autonomous workflows with guardrails |

## How Modes Work

1. **Foundation Always Loads**: PARAMOUNT skills (~1k tokens) active in ALL modes
2. **Mode Skills Stack**: Mode-specific skills load on top of foundation
3. **Session Persistence**: Active modes survive session restart
4. **Max 5 Modes**: Up to 5 modes active simultaneously (budget-bounded)
5. **Semantic Matching**: Fuzzy names resolved (`/mode sec` → security)

## Examples

```bash
# Single mode for coding
/mode code

# Multi-mode for secure API development
/mode code security

# Return to foundation only
/mode off

# Check what's active
/mode status
```

## Token Budget Display

When activating multiple modes:

```text
[MODES ACTIVATED: code security]

[code]
  Skills: feature-complete, design-principles, code-agentic

[security]
  Skills: vulnerability-detection, threat-modeling

Combined token budget: ~10.5k (includes ~1k foundation)
```

## Mode Definition Location

Mode definitions stored in: `~/.claude/modes/definitions/{name}-mode.yaml`

## Integration

- Uses `mode-loader.sh` library
- Mode state stored in `~/.claude/.active-modes`
- Restored automatically on session start

## Related

- `modes/MODE.grip` - Mode index
- `modes/foundation.md` - Always-loaded PARAMOUNT skills
- `lib/mode-loader.sh` - Mode loading library

---


