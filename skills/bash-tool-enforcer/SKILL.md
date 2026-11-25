---
name: bash-tool-enforcer
description: Enforces use of modern CLI tools (rg over grep, fd over find) for performance and consistency
status: Active
effort: medium
auto_generated: true
generation_date: 2025-12-03T10:35:51Z
source_pattern: grep-usage-in-scripts
version: 2.0.0
triggers:
  - Using grep instead of rg
  - Using find instead of fd
  - Bash commands without proper exclusions
---

# Bash Tool Enforcer

**Purpose:** Enforce use of modern, efficient CLI tools.

**Severity:** minor
**Token Impact:** 10-100x slower performance, missing exclusions

---

## Problem

Using legacy CLI tools (`grep`, `find`) instead of modern alternatives (`rg`, `fd`) causes:

1. **Performance degradation** - grep is 10-100x slower than ripgrep
2. **Missing exclusions** - grep doesn't auto-exclude .gitignore patterns
3. **Verbose syntax** - find requires complex expressions vs fd's simple patterns
4. **Inconsistency** - Different tools in different places makes maintenance harder

---

## Solution

**Always use modern CLI tools with proper exclusions.**

### Tool Mapping

| Legacy | Modern | Reason |
|--------|--------|--------|
| `grep` | `rg` (ripgrep) | 10-100x faster, respects .gitignore |
| `find` | `fd` | Simpler syntax, faster, smart defaults |
| `cat` for search | `rg` | Don't pipe cat to grep |
| `awk/sed` on JSON | `jq` | Proper JSON parsing |

### Always Include Exclusions

```bash
# Ripgrep with exclusions (ALWAYS)
rg "pattern" --glob '!node_modules/*' --glob '!venv/*'

# fd with exclusions (ALWAYS)
fd "filename" --exclude node_modules --exclude venv
```

---

## Replacements

### grep → rg

```bash
# WRONG
grep -r "pattern" .
grep -n "pattern" file.txt
grep -i "PATTERN" *.js

# RIGHT
rg "pattern" --glob '!node_modules/*'
rg -n "pattern" file.txt
rg -i "PATTERN" --type js
```

### find → fd

```bash
# WRONG
find . -name "*.js" -type f
find . -type d -name "test"

# RIGHT
fd -e js
fd -t d "test"
```

### cat | grep → rg

```bash
# WRONG
cat file.txt | grep "pattern"

# RIGHT
rg "pattern" file.txt
```

---

## Implementation

### Context-Gate Hook (Gate 5)

The `context-gate-hook.py` PreToolUse hook detects and blocks grep/find usage:

```python
# Gate 5: Bash Tool Substitution
if tool == "Bash":
    command = tool_input.get("command", "")
    if re.search(r'\bgrep\b', command):
        return {"decision": "deny", "message": "Use rg (ripgrep) instead of grep"}
    if re.search(r'\bfind\s', command):
        return {"decision": "deny", "message": "Use fd instead of find"}
```

### Detection Regex

```regex
\bgrep\s+-[a-zA-Z]+
```

---

## Examples

### Before (Anti-Pattern)

```bash
# Slow, no exclusions
grep -rn "TODO" .
find . -name "*.md" -exec cat {} \;
```

### After (Correct)

```bash
# Fast, auto-excludes .gitignore
rg "TODO" --glob '!node_modules/*'
fd -e md -x cat {}
```

---

## Integration

This skill is enforced via:

1. **Context-Gate Hook** - PreToolUse blocking (ENFORCED)
2. **Pattern Detection** - `./optim.sh detect` (MONITORING)
3. **bash-safety.md** - Single source of truth for bash rules

### bash-safety.md Reference

```markdown
| Task | Use | Never Use |
|------|-----|-----------|
| Pattern search | `rg` | grep |
| File finding | `fd` | find, ls |
| JSON processing | `jq` | sed/awk on JSON |
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-26 | Regenerated with real content from RSI pipeline |
| 1.0.0 | 2025-12-03 | Auto-generated stub from pattern detection |

---


