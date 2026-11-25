---
name: surfacing-skills
description: Build a slash-command catalogue of GRIP and Codex skills. Scans skill directories, extracts frontmatter, generates catalogue.json and catalogue.md. Use when auditing skill inventory, syncing proxy commands, or discovering dead skills.
---

# Surfacing Skills

Indexes GRIP and Codex skills into a browseable slash-command catalogue.
Answers "what skills exist?", "which have no description?", "which are duplicates?"

## What it does

1. Scans `~/.claude/skills/`, `~/.codex/skills/`, `~/.agents/skills/` for `SKILL.md` files
2. Extracts frontmatter (`name`, `description`)
3. Writes `cache/skill-catalogue.json` (machine-readable) and `cache/skill-catalogue.md` (human-readable)
4. Syncs proxy commands under `commands/` so every surfaced skill can be invoked via slash command

## Quick start

```bash
# Build the catalogue
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py --build

# List all skills with source and command
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py --list

# Sync proxy commands (writes ~/.claude/commands/)
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py --sync
```

## When to use

- `/check-reachable` or auditing the skill inventory
- After auto-generating new skills via RSI — sync to make them slash-invokable
- Detecting dead skills (no description = never surfaced to the model)
- Pre-upgrade inventory snapshot

## Outputs

| File | Format | Purpose |
|------|--------|---------|
| `~/.claude/cache/skill-catalogue.json` | JSON array | Machine-readable skill registry |
| `~/.claude/cache/skill-catalogue.md` | Markdown table | Human-browseable catalogue |
| `~/.claude/commands/<skill>.md` | Proxy command | Enables `/skill-name` slash invocation |

## Resources

### scripts/surface_skills.py
Main catalogue builder. Supports `--build`, `--list`, `--sync`, `--json`, `--markdown`.
Scans 3 source roots: `~/.claude/skills/`, `~/.agents/skills/`, `~/.codex/skills/`.

### agents/openai.yaml
Agent interface definition for the surfacing-skills capability (OpenAI-compatible harness).
