---
name: check-reachable
description: Browse the full GRIP catalog of skills, modes and agents — every entry tagged by kind, with descriptions and triggers, including the ones never mentioned in CLAUDE.md or rules/*.md. Use to discover skills, modes or agents you didn't know existed, audit dead inventory, or search by keyword.
effort: low
status: Active
version: 1.0.0
triggers:
  - which skills am I not using
  - show me dark skills
  - find unreachable skills
  - skill audit
  - which modes exist
  - list the agents
  - check-reachable
  - what skills are available
  - browse skills
  - skill catalog
  - skill inventory
  - what can you do
  - list all skills
  - search skills
integrates:
  - skill-graph-validator
  - recall
---

# /check-reachable — GRIP Skill Catalog

Surfaces every skill in `skills/*/SKILL.md` as a browsable directory.
Skills marked `[surfaced]` exist and are fully invokable but are not mentioned
anywhere in CLAUDE.md or `rules/*.md` — they are discovered here for the first time.

## Usage

```bash
/check-reachable                      # full catalog — skills, then modes, then agents
/check-reachable --kind mode          # one kind only: skill | mode | agent
/check-reachable --dark-only          # only entries not in docs (surfaced)
/check-reachable --search "voice"     # filter by keyword
/check-reachable --json               # machine-readable: every entry carries "kind"; "counts" equals lib/asset_counter.py
```

## Example output

```
GRIP Skill Catalog — 322 skills (132 referenced in docs, 190 surfaced here for the first time)
  + 31 modes (4 referenced in docs, 27 surfaced here for the first time)
  + 42 agents (20 referenced in docs, 22 surfaced here for the first time)

── skills ──
  /voice-chatting
    Activates GRIP's real-time voice chat mode.
    Invoke when: /chat

  /authentic-writing                         [surfaced]
    Prevents AI-generated fluff in external content.
    Invoke when: LinkedIn post

── modes ──
  /mode decision                             [surfaced]
    Structured decision frameworks, options analysis, criteria weighting
    Invoke when: /mode decision

── agents ──
  agent:design-reviewer
    Audit code against SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O, and CC design principles
    Invoke when: /feature-complete Phase 4
```

The counts are live, not the numbers above: the per-kind totals are the same
inventory `lib/asset_counter.py` reports (tracked files inside the install
root), pinned by `tests/test_skill_reachability_kinds.py`. A mode counts as
referenced only through its slug, `/mode <name>` or a slash-shaped trigger —
its word triggers (`review`, `write`) are activation vocabulary, not names.

## Execution

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/skill_reachability.py [--kind skill|mode|agent] [--dark-only] [--search TERM] [--json]
```

Or from Python:

```python
from lib.skill_reachability import build_catalog
catalog = build_catalog()
for skill in catalog.all_skills:
    print(f"/{skill.slug} — {skill.description[:60]}")
for entry in catalog.all_entries:          # skills, modes and agents
    print(f"{entry.kind}: {entry.slug} — referenced={entry.referenced}")
print(catalog.counts)                      # {"skills": N, "modes": N, "agents": N}
```

## What "surfaced" means

A skill is surfaced (not referenced) if none of the following appear in
CLAUDE.md or any `rules/*.md`:

- The skill directory slug
- The `name` field in SKILL.md frontmatter
- Any trigger phrase from the `triggers` list

Surfaced ≠ broken. The skill is valid, tested, and invokable by name.
It just has no auto-discovery path via the loaded docs. Running `/check-reachable`
is that discovery path.

## When to run

- "What skills do I have for X?" — use `--search X`
- After importing a skills bundle — see what's new
- Before deleting skills — confirm truly unused first
- Periodic inventory — monthly, keeps 246-skill collection honest

## Limitations

Hooks that dynamically invoke skills (e.g. `Skill(tool="asking-users")`, or its siblings `creating-wizards` and `hitl`, inside
`hooks/*.py`) are not scanned. A skill invoked only from hooks may show as
surfaced even though it fires regularly. Widen the corpus to `hooks/*.py` if
needed — pass `--claude-dir` to `build_catalog()` and extend `_build_corpus`.
