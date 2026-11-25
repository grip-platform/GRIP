# Modes Directory

31 core operating modes for context-specific skill loading.
Additional modes may be available via organization extensions (`organizations/{org}/modes/`).

## Contents

| Item | Purpose |
|------|---------|
| `foundation.md` | PARAMOUNT skills for all modes |
| `definitions/` | YAML mode definitions |
| `MODE.grip` | Mode system documentation |

## Available Modes

| Mode | Purpose | Token Budget |
|------|---------|--------------|
| `code` | Software development | ~6000 |
| `security` | Security auditing | ~3500 |
| `research` | Deep research | ~3000 |
| `strategy` | Strategic planning | ~3500 |
| `legal` | Contract and compliance | ~2500 |
| `marketing` | Content creation | ~2500 |
| `architect` | System design | ~4000 |
| `teaching` | Education and tutorials | ~3000 |
| `pair` | Session monitoring and steering | ~3000 |
| `writing` | Long-form writing and editing | ~3000 |
| `synthesis` | Knowledge synthesis and bridging | ~3500 |
| `decision` | Structured decision frameworks | ~3000 |
| `brainstorm` | Ideation and divergent thinking | ~2500 |
| `learning` | Personal learning pathway design | ~3500 |
| `review` | Unified code/content/doc review | ~3500 |
| `demo` | Live demonstration and showcase | ~2500 |
| `flow` | Trust-based bypass permissions | ~1500 |
| `incognito` | Privacy-focused operation | ~1000 |
| `planning` | Project planning and roadmapping | ~3000 |
| `testing` | QA and test automation | ~3500 |
| `upskilling` | Learning pathway design | ~4000 |
| `business` | Business analysis and strategy | ~2500 |
| `create` | Creative content generation | ~2000 |
| `automation` | Workflow automation | ~4500 |
| `analysis` | Data and systems analysis | ~4000 |
| `rsi` | Recursive Self-Improvement loops | ~5500 |

## Activating Modes

```bash
# Via CLI
modes activate code

# Via slash command
/mode code
```

## Mode Stacking

```
[Mode Skills]        ← Domain-specific
       ↓
[Foundation Skills]  ← Always active (PARAMOUNT)
```

Foundation skills cannot be disabled by any mode.
