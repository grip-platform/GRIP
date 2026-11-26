# Skills Directory

340 skills organised by domain.

## Structure

Each skill lives in its own directory:
```
skills/
├── skill-name/
│   ├── SKILL.md        # Main skill document
│   ├── reference.md    # Optional detailed reference
│   └── agents/         # Optional sub-agents
```

## Key Skills

### PARAMOUNT (Always Active)

| Skill | Purpose |
|-------|---------|
| `asking-users` | AskUserQuestion protocol |
| `context-gate` | 80% context threshold |
| `ultrathink` | 6 verification gates |
| `optimizing-complexity` | BIG-O: O(n log n) ceiling |
| `prompt-routing` | Auto-selects execution pattern per prompt; chains to `capability-loading` |

### Development

| Skill | Purpose |
|-------|---------|
| `feature-complete` | 7-phase feature workflow |
| `design-principles` | SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O |
| `code-agentic` | Verification gates for code changes |
| `pr-automation` | PR creation workflow |

### Security

| Skill | Purpose |
|-------|---------|
| `vulnerability-detection` | Security scanning |
| `threat-modeling` | STRIDE analysis |
| `sanitizing-public-releases` | Pre-release security |

### Research

| Skill | Purpose |
|-------|---------|
| `research-synthesis` | Evidence synthesis |
| `reasoning-with-ai` | Articulate-first pattern |

## Invoking Skills

```bash
# Via slash command
/feature-complete

# Via Skill tool (for Claude)
Skill(skill="feature-complete")
```

## Creating Skills

See `skills/skill-creation-best-practices/SKILL.md` for the creation protocol.
