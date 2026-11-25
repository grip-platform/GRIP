---
name: dependency-guardian
description: Prevents reading from node_modules, venv, and other dependency folders that waste 50k+ tokens
status: Active
effort: low
auto_generated: true
generation_date: 2025-12-03T10:35:52Z
source_pattern: node-modules-read
version: 2.0.0
triggers:
  - Read tool targeting dependency paths
  - Glob/Grep targeting dependency paths
  - Any file access to node_modules, venv, dist, build
---

# Dependency Guardian

**Purpose:** Prevent catastrophic token waste from reading dependency folders.

**Severity:** CRITICAL
**Token Impact:** 50,000+ tokens wasted per occurrence

---

## Problem

Reading from `node_modules/`, `venv/`, `dist/`, `build/`, or `__pycache__/` directories causes massive token waste. A single read into node_modules can consume 50,000+ tokens - 25% of the entire context budget.

### Why This Happens

- Claude doesn't know the project structure initially
- User says "find where X is defined" and Claude searches everywhere
- Dependency folders contain thousands of files
- Each file read consumes tokens even if unhelpful

---

## Solution

**NEVER read dependency or build folders. Use exclusion flags in ALL searches.**

### Correct Tool Usage

```bash
# Ripgrep with exclusions
rg "pattern" --glob '!node_modules/*' --glob '!venv/*' --glob '!dist/*'

# fd with exclusions
fd "filename" --exclude node_modules --exclude venv --exclude dist

# Or use the Grep tool with exclusion glob
```

### Blocked Paths

| Path | Type | Reason |
|------|------|--------|
| `node_modules/` | npm dependencies | 50k+ files |
| `venv/` / `.venv/` | Python virtualenv | System packages |
| `dist/` / `build/` | Build output | Generated code |
| `__pycache__/` | Python cache | Bytecode |
| `.next/` | Next.js build | Generated |
| `target/` | Rust/Maven build | Compiled |
| `vendor/` | Go/PHP deps | External code |
| `Pods/` | iOS deps | CocoaPods |
| `.git/objects/` | Git internals | Binary data |

---

## Implementation

### Context-Gate Hook (Gate 1)

The `context-gate-hook.py` PreToolUse hook blocks dependency reads with `permissionDecision: "deny"`:

```python
BLOCKED_PATHS = [
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "target", "vendor", "Pods", ".git/objects"
]

# Returns: {"decision": "deny", "message": "..."}
```

### Detection Regex

```regex
(Read|cat|bat|head|tail|less|more).*/(node_modules|venv|\.venv|dist|build|__pycache__)/
```

---

## Examples

### Before (Anti-Pattern)

```bash
# WRONG - reads entire node_modules
Read /project/node_modules/lodash/index.js

# WRONG - searches include dependencies
rg "function" /project
```

### After (Correct)

```bash
# RIGHT - use exclusions
rg "function" /project --glob '!node_modules/*'

# RIGHT - search only src
rg "function" /project/src
```

---

## Integration

This skill is enforced via:

1. **Context-Gate Hook** - PreToolUse blocking (ENFORCED)
2. **Pattern Detection** - `./optim.sh detect` (MONITORING)
3. **CLAUDE.md PARAMOUNT Rule** - Documentation (AWARENESS)

### CLAUDE.md Reference

```text
; ◈ NEVER READ DEPENDENCY FOLDERS
¬Read(node_modules|.next|dist|build|__pycache__|venv|target|vendor|Pods)
; Waste: 50k+ tokens. permissions.deny broken (#6631, #6699)
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-26 | Regenerated with real content from RSI pipeline |
| 1.0.0 | 2025-12-03 | Auto-generated stub from pattern detection |

---


