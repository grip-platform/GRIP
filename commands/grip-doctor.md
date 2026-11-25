# /grip-doctor

Run GRIP health diagnostics. Checks infrastructure integrity, hook configuration, and system readiness.

## Usage

```bash
/grip-doctor              # Run all health checks
/grip-doctor --verbose    # Show detailed output for each check
```

## What It Checks

- CLAUDE.md configuration exists
- settings.json valid with hooks configured
- Hook files exist and are executable
- MCP servers configured
- Mode definitions present
- KONO substrate responsive (if installed)
- Skill and agent counts
- Instance serializer importable

## Output Format

Each check shows `[PASS]`, `[WARN]`, or `[FAIL]` with a summary line at the end.

## Implementation

Run the health check engine:

```bash
python3 ~/.claude/lib/grip_doctor.py
```

Display results to the user. If any checks fail, suggest remediation steps.
