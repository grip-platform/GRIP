# Skill Invocation Protocol (cross-harness)

`Skill(skill="<name>")` is a **protocol-level construct**, not a harness builtin.
Every harness expands the same logical call to its own native mechanism. This
resolves the ambiguity of what `Skill(...)` means when GRIP runs outside Claude Code.

| Harness | Expansion of `Skill(skill="<name>")` |
|---------|--------------------------------------|
| Claude Code | native `Skill` tool call (`/skill <name>`) |
| Aider | `run <name>` / `execute <name> skill` — load `skills/<name>/SKILL.md` into context |
| OpenCode | auto-loaded from the skills tree; `/skill <name>` |
| bash / any other | `bash scripts/skill-dispatch.sh <name>` — prints the `SKILL.md` body with frontmatter stripped |

## The universal fallback: `scripts/skill-dispatch.sh`

The bash dispatcher is the lowest common denominator every harness can shell out to.
It is **CWD-independent**: it resolves `$GRIP_DIR/skills/<name>/SKILL.md`, where
`$GRIP_DIR` is taken from the environment if set, otherwise derived from the script's
own location. So a harness may invoke it from any working directory without first
`cd`-ing into the GRIP repository.

```bash
bash scripts/skill-dispatch.sh <skill-name>   # prints the SKILL.md body (frontmatter stripped)
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | success — body printed |
| 1 | missing skill name (or unexpected parse error) |
| 2 | skill file not found |
| 3 | frontmatter opened but never closed |
| 4 | no frontmatter present |

## Path aliasing (`~/.claude` ≡ `~/.grip`)

The canonical GRIP home is `~/.grip`. `~/.claude` is a compatibility symlink to it,
so `~/.claude/skills/` and `~/.grip/skills/` resolve to the **same** tree. Harness
configs may reference either; the dispatcher and all skills work through both. New
references should prefer the canonical `~/.grip/`.
