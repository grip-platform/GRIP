---
name: path-portability
description: Prevent hardcoded absolute paths in code for cross-environment compatibility
status: Active
effort: low
auto_generated: true
generation_date: 2026-02-14T17:19:29Z
source_pattern: hardcoded-path-introduced
version: 1.1.0
---

# Path Portability

**Purpose:** Prevent hardcoded absolute paths in code for cross-environment compatibility

**Severity:** Medium
**Token Impact:** Low (prevents future debugging sessions)

---

## Problem

Hardcoded absolute paths like `~/project/file.py` or `~/.claude/config` break portability across:
- Different user accounts
- macOS vs Linux environments
- Development vs production deployments
- CI/CD systems

This pattern indicates code that will fail when moved to another machine or environment.

---

## Solution

Use environment-relative paths with standard base directories:

```python
# BAD: Hardcoded absolute path
config_path = "~/.claude/config.json"

# GOOD: Relative to home directory
from pathlib import Path
config_path = Path.home() / ".claude" / "config.json"

# GOOD: Relative to current file
from pathlib import Path
project_root = Path(__file__).parent.parent
config_path = project_root / "config.json"

# GOOD: Environment variables
import os
config_path = os.environ.get("CLAUDE_CONFIG", str(Path.home() / ".claude" / "config.json"))
```

### Implementation Protocol

1. **Detection**: Monitor for pattern `hardcoded-path-introduced` in tool calls and responses
2. **Prevention**: Pre-execution validation gate blocks the anti-pattern
3. **Remediation**: Suggest Path.home(), Path(__file__), or environment variables

---

## Usage

### Automatic Activation

This skill activates automatically via the context-gate hook when:
- Pattern is detected in real-time tool calls
- Efficiency audit identifies the violation
- RSI (Recursive Self-Improvement) cycle is active

### Manual Check

```bash
./optim.sh detect  # Includes this pattern in detection
```

---

## Examples

### Before (Anti-Pattern)

```python
LOG_FILE = "~/work/GRIP/GRIP/.hooks.log"
SKILLS_DIR = "~/.claude/skills"
```

### After (Correct)

```python
from pathlib import Path

LOG_FILE = Path.home() / "work" / "GRIP" / "GRIP" / ".hooks.log"
SKILLS_DIR = Path.home() / ".claude" / "skills"
```

---

## Integration

Registered in pattern database (`~/.claude/patterns.json`):
- Pattern: `hardcoded-path-introduced`
- Threshold: 1 occurrence
- Severity: Medium

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-14 | Auto-generated with real content from RSI pipeline |
| 1.1.0 | 2026-02-14 | Completed placeholder content with examples and proper severity |
