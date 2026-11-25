---
name: bash-command-safety
description: Prevents bash syntax errors from semicolons after command substitution in eval contexts
status: Active
effort: low
auto_generated: true
generation_date: 2025-12-03T10:35:52Z
source_pattern: semicolon-after-subshell
version: 2.0.0
triggers:
  - Semicolon after $() command substitution
  - Semicolon in sed patterns
  - Commands that fail in zsh eval contexts
---

# Bash Command Safety

**Purpose:** Prevent bash syntax errors that cause hook/eval failures.

**Severity:** major
**Token Impact:** Script fails completely with 'parse error near )'

---

## Problem

Using semicolons after command substitution causes parse errors in eval contexts:

```bash
# FAILS in zsh eval (used by Claude Code hooks)
VAR=$(cmd); echo "$VAR"
# Error: (eval):1: parse error near ')'
```

### Why This Happens

Claude Code hooks run through zsh's `eval` mechanism. Semicolons inside patterns or after subshells cause the parser to misinterpret the command structure.

### Affected Contexts

1. **PreToolUse hooks** - Commands evaluated before tool execution
2. **PostToolUse hooks** - Commands evaluated after tool execution
3. **Session hooks** - session-start.sh, etc.
4. **Any sourced script** - Scripts loaded with `source` or `.`

---

## Solution

**Use `&&` instead of `;` after command substitution.**

### Rule 1: Command Substitution

```bash
# WRONG - semicolon after $()
VAR=$(pwd); echo "$VAR"
RESULT=$(grep pattern file); process "$RESULT"

# RIGHT - use && instead
VAR=$(pwd) && echo "$VAR"
RESULT=$(grep pattern file) && process "$RESULT"
```

### Rule 2: Multiple Sed Patterns

```bash
# WRONG - semicolon inside sed
sed 's|a||; s|b||' file

# RIGHT - use pipes
sed 's|a||' file | sed 's|b||'

# Or multiple -e flags
sed -e 's|a||' -e 's|b||' file
```

### Rule 3: Path Encoding

The specific pattern that often fails:

```bash
# WRONG - triggers parse error
PROJECT=$(pwd | sed 's|/|-|g'); echo "$PROJECT"

# RIGHT
PROJECT=$(pwd | sed 's|/|-|g') && echo "$PROJECT"
```

---

## Examples

### Before (Anti-Pattern)

```bash
# Session hook that fails
BRANCH=$(git branch --show-current); export BRANCH
COUNT=$(wc -l < file); echo "Lines: $COUNT"
```

**Result:** `(eval):1: parse error near ')'`

### After (Correct)

```bash
# Session hook that works
BRANCH=$(git branch --show-current) && export BRANCH
COUNT=$(wc -l < file) && echo "Lines: $COUNT"
```

---

## Detection

### Regex Pattern

```regex
\$\([^)]+\)\s*;
```

This matches: `$(anything);`

### Context Indicator

Pattern is most dangerous in:
- Files in `hooks/` directory
- Files in `lib/` directory
- Any script using `set -e` (errexit)

---

## Implementation

### Context-Gate Hook (Gate 6)

The `context-gate-hook.py` PreToolUse hook blocks this pattern:

```python
# Gate 6: Bash Linting
if tool == "Bash":
    command = tool_input.get("command", "")
    if re.search(r'\$\([^)]+\)\s*;', command):
        return {
            "decision": "deny",
            "message": "Semicolon after $() causes parse error. Use && instead."
        }
```

### Static Analysis

The `bash-linter.sh` script scans for this pattern:

```bash
# From lib/bash-linter.sh
rg '\$\([^)]+\)\s*;' "$file" && echo "WARN: Semicolon after subshell"
```

---

## Related Patterns

### Pattern: Readonly Redeclaration

```bash
# WRONG - fails on re-source
readonly VAR="value"

# RIGHT - guard pattern
[[ -z "${VAR:-}" ]] && readonly VAR="value"
```

### Pattern: Path Encoding Double-Dash

```bash
# WRONG - dash-prefixed path interpreted as flag
fd -t d "$PROJECT_DIR"

# RIGHT - end flag parsing
fd -t d -- "$PROJECT_DIR"
```

---

## Integration

This skill is enforced via:

1. **Context-Gate Hook** - PreToolUse blocking (ENFORCED)
2. **bash-linter.sh** - Static analysis (MONITORING)
3. **Pattern Detection** - `./optim.sh detect` (MONITORING)
4. **bash-safety.md** - Single source of truth

### bash-safety.md Reference

```markdown
### No Semicolons After Command Substitution

# BAD - causes parse error in eval contexts
VAR=$(cmd); echo "$VAR"

# GOOD - use && instead
VAR=$(cmd) && echo "$VAR"
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-26 | Regenerated with real content from RSI pipeline |
| 1.0.0 | 2025-12-03 | Auto-generated stub from pattern detection |

---


