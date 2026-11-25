---
name: testing-modes-skills
description: Validates mode YAML syntax, skill dependencies, and token budgets. Use when testing GRIP modes/skills or after infrastructure changes.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /test-modes
  - /test-skills
  - /test-integration
  - "validate modes"
  - "test grip infrastructure"
---

# Testing Modes and Skills

Infrastructure validation skill for GRIP modes and skills.

## Commands

| Command | Purpose |
|---------|---------|
| `/test-modes` | Validate all mode YAML files |
| `/test-skills` | Run quality validation on all skills |
| `/test-integration` | Check skill dependencies exist |
| `/test-improvement-tracking` | Validate improvement tracking infrastructure |
| `/test-all` | Run all tests |

## Test Protocols

### /test-modes

Validates all mode definition files in `~/.claude/modes/definitions/`:

```bash
# Check YAML syntax
for yaml in ~/.claude/modes/definitions/*.yaml; do
  python3 -c "import yaml; yaml.safe_load(open('$yaml'))" 2>&1 || echo "FAIL: $yaml"
done
```

Validation criteria:

| Field | Required | Validation |
|-------|----------|------------|
| `name` | Yes | Non-empty string |
| `description` | Yes | Non-empty, < 1024 chars |
| `skills.always` | Yes | Array of skill names |
| `token_budget` | Yes | Number or string with ~ prefix |
| `foundation` | No | Boolean (defaults to true) |

Expected output:
```
[TEST-MODES]
Scanning ~/.claude/modes/definitions/
- code-mode.yaml: PASS
- strategy-mode.yaml: PASS
- upskilling-mode.yaml: PASS
...
Total: X modes, X passed, 0 failed
```

### /test-skills

Runs skill-validator.sh on all skills:

```bash
~/.claude/lib/skill-validator.sh --all
```

Quality threshold: 70/100

Validation includes:

| Check | Points |
|-------|--------|
| Frontmatter complete | 30 |
| Name convention (gerund) | 10 |
| Description quality | 20 |
| Body structure | 20 |
| No placeholders | 10 |
| Examples present | 10 |

Expected output:
```
[TEST-SKILLS]
Running skill-validator on all skills...
- asking-users: 85/100 PASS
- creating-wizards: 78/100 PASS
- testing-modes-skills: 82/100 PASS
...
Total: X skills, X passed, 0 failed
```

### /test-integration

Checks that all skill dependencies exist:

```bash
# For each mode, verify skills exist
for mode in ~/.claude/modes/definitions/*.yaml; do
  skills=$(yq -r '.skills.always[]' "$mode" 2>/dev/null)
  for skill in $skills; do
    if [[ ! -d ~/.claude/skills/$skill ]]; then
      echo "MISSING: $skill (required by $(basename $mode))"
    fi
  done
done
```

Expected output:
```
[TEST-INTEGRATION]
Checking skill dependencies...
- code-mode: 3/3 skills found
- strategy-mode: 2/2 skills found
- upskilling-mode: 6/6 skills found
...
Total: X dependencies checked, 0 missing
```

### /test-improvement-tracking

Validates improvement tracking infrastructure:

```bash
# 1. Check log file exists
[[ -f ~/.claude/improvement-log.jsonl ]] && echo "Log file: EXISTS" || echo "Log file: MISSING"

# 2. Run summary
python3 ~/.claude/lib/improvement_analyzer.py summary

# 3. Run heatmap
python3 ~/.claude/lib/improvement_analyzer.py heatmap

# 4. Check refactor candidates
python3 ~/.claude/lib/improvement_analyzer.py check

# 5. Verify log command works
python3 ~/.claude/lib/improvement_analyzer.py log test test_component "Test entry" test
# Clean up test entry if needed
```

Expected output:
```
[TEST-IMPROVEMENT-TRACKING]
Log file: EXISTS
=== GRIP Self-Improvement Summary ===
Total improvements: N
...
=== Component Heat Map ===
...
No components need refactoring (threshold: 3+ improvements)
# OR
[IMPROVEMENT HEATMAP] Components need refactor: component1, component2
```

Integration test: Make 3 improvements to same component, verify `[IMPROVEMENT HEATMAP]` signal emits.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Improvement log missing | First improvement creates it automatically |
| Heatmap not triggering | Need 3+ improvements to same component |

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~800 |
| /test-modes execution | ~200 |
| /test-skills execution | ~500 |
| /test-integration execution | ~200 |

## Related Files

| File | Purpose |
|------|---------|
| `lib/periodic_scanner.py` | Pattern scanner |
| `lib/improvement_analyzer.py` | Improvement tracking and heatmap |
| `improvement-log.jsonl` | Improvement log storage |
| `modes/definitions/*.yaml` | Mode definitions |
| `lib/skill-validator.sh` | Skill quality validation |
| `skills/hitl/SKILL.md` | HITL dispatch contract over `asking-users` and `creating-wizards`, two skills in the sample output above |

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2026-01-27 | Add /test-improvement-tracking (Gen 248) |
| 1.0.0 | 2026-01-27 | Initial creation (Gen 239) |

---

**
