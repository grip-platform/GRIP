---
name: mode-switching
description: Operating in mode mismatched to task type
status: Active
effort: medium
auto_generated: true
generation_date: 2026-02-14T21:30:34Z
source_pattern: mode-mismatch
version: 1.0.0
---

# Mode Switching

**Purpose:** Operating in mode mismatched to task type

**Severity:** 
**Token Impact:** 

---

## Problem

Operating in mode mismatched to task type

This pattern indicates inefficient token usage that can be automatically detected and prevented.

---

## Solution



### Implementation Protocol

1. **Detection**: Monitor for pattern `mode-mismatch` in tool calls and responses
2. **Prevention**: Pre-execution validation gate blocks the anti-pattern
3. **Remediation**: Suggest the correct approach before proceeding

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

See `patterns.json` regex: Matches the mode-mismatch pattern indicating this violation.

### After (Correct)



---

## Integration

Registered in pattern database (`~/.claude/patterns.json`):
- Pattern: `mode-mismatch`
- Threshold: See patterns.json
- Severity: 

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-14 | Auto-generated with real content from RSI pipeline |
