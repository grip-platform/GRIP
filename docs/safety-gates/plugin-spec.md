# GRIP Safety Gates v2.0

**Deterministic safety gates that the agent cannot override.**

> "Safety emerges not from shackles or philosophy, but from systems that can say 'NO' deterministically -- even when doing so is inconvenient, inefficient, or against apparent optimization."

## The Problem

Anthropic's approach to AI safety relies on values trained into model weights. But:
- Values can be argued with
- Claude can be persuaded
- Uncertainty opens the door to negotiation
- Authority is undefined

## The Solution

GRIP Safety Gates run **outside Claude's weights**. The agent cannot persuade a Python script.

```
+------------------------------------------+
|           Claude's Weights               |
|  (probabilistic, persuadable, drifts)    |
+------------------------------------------+
                    |
                    v
+------------------------------------------+
|         GRIP Safety Gates                |
|  (deterministic, mechanical, absolute)   |
|                                          |
|  if condition(X) -> DENY                 |
|  No arguments. No persuasion.            |
|  Hook doesn't care what Claude thinks.   |
+------------------------------------------+
                    |
                    v
              [Action blocked]
```

## Properties

| Property | Description |
|----------|-------------|
| **User Choice** | User defines what's blocked via config file |
| **Customizable** | Add/remove/modify gates without code changes |
| **Enforceable** | `permissionDecision: deny` actually blocks execution |
| **Tested** | Each gate has must_block/must_allow test cases |
| **Falsifiable** | We can prove when gates fail |
| **Deterministic** | Same input = same output |
| **Guaranteed** | Hooks run before every tool call |

## Default Gates

| Gate | Priority | Description |
|------|----------|-------------|
| `always-allow` | 0 | Never block AskUserQuestion, task tools |
| `confidence-gate` | 10 | HALT when confidence < 70% |
| `dependency-guardian` | 20 | Block reads from node_modules, etc. |
| `destructive-git` | 30 | Block force push, hard reset |
| `secrets-detection` | 40 | Block commits with .env, keys |
| `context-gate` | 50 | HALT at 80% context usage |
| `tool-substitution` | 60 | Block grep/find (use rg/fd) |
| `temp-script-prevention` | 70 | Block temp script creation |
| `production-safety` | 80 | Block production operations |
| `read-cache` | 90 | Block redundant file reads |

## Configuration

### User Gates File

Create `~/.grip/gates.yaml` to customize:

```yaml
version: "2.0"

gates:
  # Override default threshold
  - id: confidence-gate
    enabled: true
    condition:
      type: confidence_threshold
      threshold: 0.8  # More strict: 80% confidence required

  # Add custom gate
  - id: no-rm-rf
    name: "Prevent rm -rf"
    description: "Block recursive force delete"
    priority: 25
    enabled: true
    condition:
      type: command_pattern
      tools: [Bash]
      patterns:
        - "rm\\s+-rf"
        - "rm\\s+-fr"
    action: deny
    reason: "[NO-RM-RF] Recursive force delete blocked. Use trash instead."
    tests:
      must_block:
        - { tool: Bash, command: "rm -rf /" }
        - { tool: Bash, command: "rm -fr /home" }
      must_allow:
        - { tool: Bash, command: "rm file.txt" }
        - { tool: Bash, command: "trash ~/junk" }
```

## Usage

### List Gates

```
grip-gates list
```

### Check Gate Status

```
grip-gates verify
```

### View Audit Log

```
grip-gates audit
```

### Run Gate Tests

```
grip-gates test confidence-gate
```

### Propose New Gate (RSI)

The agent can propose new gates based on observed risky behavior:

```yaml
# Proposal format
id: aws-key-detection
name: "AWS Key Detection"
description: "Block potential AWS key commits"
reason_for_proposal: "User almost committed AWS keys"
condition_type: command_pattern
patterns: ["AKIA[0-9A-Z]{16}"]
action: deny
reason: "[AWS-KEY] Possible AWS key detected"
```

Human reviews at `~/.grip/gate-proposals.yaml` and approves/rejects.

## Test Harness

Each gate includes test cases that verify it works:

```bash
# Run all gate tests
python lib/gates/test_harness.py

# Test specific gate
python lib/gates/test_harness.py --gate confidence-gate

# JSON output
python lib/gates/test_harness.py --json
```

## Audit Trail

All gate decisions are logged to `~/.grip/gates-audit.jsonl`:

```json
{"ts": "2026-02-01T17:00:00Z", "gate": "dependency-guardian", "decision": "deny", "tool": "Read", "path": "node_modules/lodash"}
{"ts": "2026-02-01T17:01:00Z", "gate": "confidence-gate", "decision": "deny", "confidence": 0.45}
```

## RSI Gate Improvement

The agent can improve its own safety constraints:

1. **Observe**: Agent detects risky pattern not covered
2. **Propose**: Agent creates gate proposal
3. **Review**: Human approves/rejects
4. **Enforce**: Approved gate becomes active

```bash
# List pending proposals
python lib/gates/rsi_proposals.py list

# Approve a proposal
python lib/gates/rsi_proposals.py approve aws-key-detection --by "laurie"

# Reject with reason
python lib/gates/rsi_proposals.py reject bad-gate --by "laurie" --reason "Too broad"
```

## The Core Principle

**GRIP makes the agent able to say NO in a way the agent itself cannot override.**

This is fundamentally different from:
- Constitutional AI (Claude can reconsider values)
- RLHF (behavior is shaped, not mechanically constrained)
- Fine-tuning (responses are probabilistic)

GRIP adds a deterministic layer that is not part of the model's weights.

---

*Part of GRIP (Generative Runtime Intelligence Protocol)*

*"Safety through mechanisms that refuse action when constraints are violated."*
