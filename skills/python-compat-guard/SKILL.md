---
name: python-compat-guard
description: Prevent Python version compatibility issues in cross-environment codebases.
status: Active
effort: low
version: 1.0.0
triggers: ["python compat", "syntax error ci", "f-string error", "python version"]
integrates: []
see_also: [code-agentic, design-principles, converge]
---

# Python Compatibility Guard

Prevents Python version-dependent syntax errors that compile locally but fail on CI runners, remote machines, or pair-mode targets.

## Rules

### Rule 1: No Backslashes in F-String Expressions (Python < 3.12)

**Never** put backslashes inside f-string `{}` expressions. Python < 3.12 raises `SyntaxError: f-string expression part cannot include a backslash`.

Bad:
```python
risk_factors.append(f"data-critical({pattern.split('(')[0].strip('\\s')})")
```

Good:
```python
label = pattern.split('(')[0].strip('\\s')
risk_factors.append(f"data-critical({label})")
```

Applies to: `\n`, `\t`, `\\`, `\'`, any escape sequence inside `{expr}`.

**Why it's invisible locally**: PEP 701 (Python 3.12+) removed this restriction. Code compiles fine on dev machines with 3.12+ but fails on CI runners with 3.9-3.11.

### Rule 2: Use Future Annotations for Union Syntax (Python < 3.10)

Add `from __future__ import annotations` for PEP 604 (`int | str`) or PEP 585 (`list[str]`).

### Rule 3: No Walrus Operator in Comprehension Conditions (Python < 3.8)

Avoid `:=` in list comprehension conditions on older Python targets.

## Detection

Before committing Python code, mentally check:
1. Any f-string with `\` inside `{}`? Extract to variable.
2. Any `X | Y` type hints without future import? Add it.
3. Any `match/case` statements? Only Python 3.10+.

## Origin

Gen 349: PR #346 CI failure. The f-string compiled locally (Python 3.12+) but GitHub Actions runner (Python 3.9) rejected it. This pattern blocked ALL pending PRs since the file was on main.
