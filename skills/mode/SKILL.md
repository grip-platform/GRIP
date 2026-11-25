---
name: mode
description: Switch, list, and manage GRIP operating modes. Supports multi-mode selection (budget-bounded, up to 30). Auto mode uses LLM inference for optimal selection. Use when user invokes /mode, wants to change operating context, or asks about available modes.
status: Active
effort: medium
version: 2.1.0
triggers:
  - /mode
  - switch mode
  - change mode
  - list modes
  - what modes are available
  - activate multiple modes
see_also:
  - mode-switching
  - code-mode
  - security-mode
  - architect-mode
  - analysis-mode
---

# Mode Management

Switch between GRIP operating modes to load domain-specific skills. Supports multi-mode selection (budget-bounded, up to 30). Auto mode uses LLM inference.

## Usage

```bash
/mode                      # List available modes with token budgets
/mode list                 # List available modes
/mode <name>               # Activate single mode
/mode <n1> <n2> [n3...]    # Activate multiple modes (budget-bounded)
/mode off                  # Deactivate all modes
/mode status               # Show current mode status with combined budget
```

## Multi-Mode Selection (Gen 365)

Activate multiple modes simultaneously (budget-bounded at ~50k):

```bash
# Activate code + security for secure development
/mode code security

# Activate analysis + architect for system design
/mode analysis architect

# Maximum 30 modes (lib/mode-loader.sh MAX_MODES; combined token budget is the real bound)
/mode code security research architect analysis
```

### Token Budget Display

When activating modes, the combined token budget is shown:

```text
[MODES ACTIVATED: code security]

[code]
  Skills: feature-complete, design-principles, code-agentic

[security]
  Skills: vulnerability-detection, threat-modeling

Combined token budget: ~10.5k (includes ~1k foundation)
Foundation (PARAMOUNT) skills remain active.
```

## Wizard Flow (Natural Language)

For natural language mode selection, describe your work:

```bash
# Get semantic suggestions
bash ~/.claude/lib/mode-loader.sh suggest "building secure APIs"
# Returns: code, security, architect

# Then activate suggested modes
/mode code security
```

## Execution

When user invokes `/mode`, execute via the mode-loader library:

```bash
# List modes with token budgets
bash ~/.claude/lib/mode-loader.sh list

# Activate single mode
bash ~/.claude/lib/mode-loader.sh code

# Activate multiple modes
bash ~/.claude/lib/mode-loader.sh code security

# Deactivate all
bash ~/.claude/lib/mode-loader.sh off

# Check status (shows all active modes)
bash ~/.claude/lib/mode-loader.sh status

# Get token budget for modes
bash ~/.claude/lib/mode-loader.sh budget code security

# Semantic suggestions
bash ~/.claude/lib/mode-loader.sh suggest "your description"
```

## Available Modes

| Mode | Token Budget | Description |
|------|--------------|-------------|
| `code` | ~6k | Software development (feature-complete, design-principles) |
| `strategy` | ~3.5k | Strategic assessment (strat-ops, leading-organisation) |
| `legal` | ~2.5k | Contract drafting (legal-ops) |
| `marketing` | ~2.5k | Content creation (authentic-writing, medium-article-writer) |
| `business` | ~2.5k | Founder operations (leading-organisation) |
| `create` | ~2k | Meta-mode for skill creation |
| `security` | ~4k | Security analysis (vulnerability-detection, threat-modeling) |
| `research` | ~3k | Research synthesis (research-synthesis, evidence-evaluation) |
| `analysis` | ~3k | Data analysis (riper-workflow) |
| `architect` | ~4k | System architecture (adr-generation, system-diagramming) |
| `teaching` | ~2.5k | Educational content (pedagogical-patterns) |
| `automation` | ~3k | Autonomous workflows (safety-guardrails) |
| `incognito` | ~1k | Minimal output (concise-communication) |
| `upskilling` | ~1.5k | Learning mode (forging-skills) |
| `demo` | ~2k | Live presentation mode |
| `writing` | ~3k | Long-form writing, editing (authentic-writing) |
| `synthesis` | ~3.5k | Knowledge synthesis, bridging (research-synthesis) |
| `decision` | ~3k | Decision frameworks (decision-locking) |
| `brainstorm` | ~2.5k | Ideation, divergent thinking |
| `learning` | ~3.5k | Learning pathway design (pedagogical-patterns) |
| `review` | ~3.5k | Unified review (reviewing-contributions) |

## How Modes Work

1. **Foundation Always Active**: PARAMOUNT skills (ut++, asking-users, context-gate) load with ALL modes (~1k tokens)
2. **Mode Skills Stack**: Mode-specific skills load on top of foundation
3. **Session Persistence**: Active modes survive session restart (stored in `~/.claude/.active-modes`)
4. **Multi-Mode Budget**: Modes bounded by ~50k combined budget (configurable via GRIP_MAX_MODE_BUDGET)
5. **Semantic Matching**: Fuzzy mode names resolved via embeddings (`/mode sec` → security)

## Storage Format

Modes are stored newline-separated in `~/.claude/.active-modes`:

```text
code
security
```

Legacy single-mode format (`.active-mode`) is auto-migrated.

## Mode Definition Location

Mode definitions: `~/.claude/modes/definitions/{name}-mode.yaml`

Each YAML defines:
- `name`: Mode identifier
- `description`: Purpose
- `skills.always`: Skills loaded when mode activates
- `skills.optional`: Skills available on demand
- `token_budget`: Estimated tokens for mode context
- `hidden`: If `true`, mode is excluded from `/mode list` (security modes)

### Hidden Modes

Some modes are hidden from discovery for security reasons but remain functional:

| Mode | Reason | Enable Command |
|------|--------|----------------|
| `flow` | Bypasses permission prompts | `grip flow --enable --i-understand-the-risks` |

Hidden modes can still be activated with `/mode <name>` if the user knows the name.

## Claude AskUserQuestion Integration

When user asks to activate modes interactively:

```text
Question: "Which modes for your work today?"
Header: "Modes"
multiSelect: true  # Allow selecting multiple
Options:
- "code" - Software development (~6k tokens)
- "security" - Security analysis (~4k tokens)
- "architect" - System design (~4k tokens)
- "research" - Research synthesis (~3k tokens)
```

## Related

- `modes/MODE.grip` - Mode index
- `modes/foundation.md` - Always-loaded PARAMOUNT skills
- `skills/creating-wizards/SKILL.md` and `skills/hitl/SKILL.md` - siblings of the `asking-users` foundation skill: multi-step wizards, and the dispatch contract that chooses between them
- `lib/mode-loader.sh` - Mode loading library
- `lib/skill_indexer.py` - Semantic mode matching

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 2.1.0 | 400+ | Hidden mode support for security-sensitive modes (flow) |
| 2.0.0 | 365 | Multi-mode support (max 3), token budget display, semantic suggestions |
| 1.0.0 | 240 | Initial single-mode implementation |

---

**
