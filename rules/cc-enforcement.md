---
paths:
  - "**/*.py"
---

# Cyclomatic Complexity Enforcement (PARAMOUNT)

Origin: Gen 470. Mentor insight from a mentor (AWS/Mxit background):
"People don't realise, adding one if conditional adds another path, more complexity."

## The Principle

Every `if`, `for`, `while`, `except`, `and`, `or`, `assert`, and comprehension
adds a decision path. Cyclomatic complexity (CC) counts these paths. High CC
means the function is doing too many things — split it.

## Thresholds (Trust-Modulated)

| Trust Level | Warn | Block |
|-------------|------|-------|
| STRICT      | 10   | 20    |
| DEFAULT     | 15   | 30    |
| RELAXED     | 20   | 40    |

CI ratchet threshold: CC > 25 fails the build (changed files only).

## Grade Definitions

| Grade | CC Range | Meaning |
|-------|----------|---------|
| A     | 1-5      | Simple, well-focused function |
| B     | 6-10     | Moderate, acceptable |
| C     | 11-20    | Needs attention, consider decomposition |
| D     | 21-40    | Refactor needed, too many decision paths |
| F     | 41+      | Unmaintainable, must decompose |

## Decomposition Patterns

### 1. Extract-Dispatch
Replace if/elif chains with type-to-handler dispatch:
```python
# Before (CC: 12)
def process(event):
    if event.type == "click": ...
    elif event.type == "hover": ...
    elif event.type == "scroll": ...

# After (CC: 2)
HANDLERS = {"click": handle_click, "hover": handle_hover, "scroll": handle_scroll}
def process(event):
    handler = HANDLERS.get(event.type, handle_default)
    return handler(event)
```

### 2. Extract-Independent-Blocks
Logically independent try/except blocks become helpers:
```python
# Before (CC: 18)
def load_config():
    try: load_yaml()
    except: ...
    try: load_env()
    except: ...
    try: validate()
    except: ...

# After (CC: 3, each helper CC: 2)
def load_config():
    config = _load_yaml_safe()
    env = _load_env_safe()
    return _validate_safe(config, env)
```

### 3. Coordinator Pattern
Parent function becomes pure orchestrator calling focused helpers:
```python
# Before (CC: 35)
def build_report(data):
    # 200 lines of mixed concerns

# After (CC: 5)
def build_report(data):
    validated = _validate_input(data)
    metrics = _compute_metrics(validated)
    formatted = _format_output(metrics)
    return _add_metadata(formatted)
```

## Enforcement Points

1. **Gate CC (PreToolUse)**: Warns/blocks on Write/Edit of Python files exceeding CC threshold
2. **CI Ratchet**: Changed files only — new code must meet threshold, existing code grandfathered
3. **RSI Scanner**: Periodic scan of `lib/` for offenders, feeds decomposition sprints

## Measurement

Uses `complexity_profiler.calculate_complexity()` — canonical McCabe calculator
counting: if, for, while, except, with, assert, and/or, comprehensions.
