# /evo Dispatch Protocol (PARAMOUNT)

Origin: 3rd-recurrence dispatch envelope failure. DFI-FIB depth-2 fix 2026-05-02.

## NEVER run /evo inline

**WRONG — pollutes main session with 80+ lines:**
```bash
python3 ~/.claude/lib/evo.py run        # inline Bash
python3 ~/.claude/lib/evo.py run &      # background Bash (output still lands)
```

**CORRECT — isolated context, one-line summary back:**
```python
Agent(
    description="Run /evo fractal evolution",
    subagent_type="general-purpose",
    run_in_background=True,
    prompt="Run: python3 ~/.claude/lib/evo.py run 7\nReturn a 2-line summary: facet count + calibrated FD."
)
```

## Why

`/evo` outputs 80+ lines of friction tables, verdicts, yolo tiles, and trajectory data.
Inline execution (even with `run_in_background=True` on Bash) writes this output to the
session JSONL where the main context reads it. An Agent isolates the output to a separate
context; only the agent's return value reaches the main thread.

Three recurrences (2026-04-18 Gen 482, 2x more) confirmed the feedback memory alone
is insufficient — this rule lives in `rules/` to fire every session.

## Scope

- `/evo` → always Agent tool
- `/evo trajectory` → always Agent tool
- `/evo json` → always Agent tool
- `/learn` Step 1.7 (standalone facet evolution) → always Agent tool
- Bash `python3 ~/.claude/lib/evo.py` in ANY form → blocked

## Falsification

This rule is wrong if /evo output becomes structurally small (< 10 lines) in a future
refactor, or if Agent dispatch latency becomes prohibitive. Check `lib/evo.py` output
before removing this rule.
