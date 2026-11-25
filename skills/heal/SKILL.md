---
name: heal
description: Self-healing diagnostic and repair for GRIP infrastructure. Detects anomalies across hooks, skills, agents, git state, and configuration, then converges on fixes. Use when GRIP feels broken, hooks are erroring, or after upgrades.
disable-model-invocation: true
effort: high
---

# /heal — GRIP Self-Healing Engine

Agentic self-healing for GRIP infrastructure. Detects, diagnoses, remediates, and verifies — using converge() as the recovery kernel.

## Architecture

```
converge(
    state = GRIP health snapshot,
    criterion = all checks green,
    improve = fix most critical issue,
    max_depth = 5,
    time_budget_sec = 300
)
```

Four-agent model (inspired by Agentic SRE):
1. **Detection Agent** — scans for anomalies
2. **Diagnosis Agent** — traces root cause
3. **Remediation Agent** — executes fix
4. **Verification Agent** — confirms recovery

## Live Health Snapshot (DCI)

**Git state:** !`cd ~/.claude && git status --short 2>/dev/null | head -5`
**Hook health:** !`cd ~/.claude && python3 -c "import os,json; hooks=json.load(open('settings.json'))['hooks']; total=sum(len(g.get('hooks',[])) for groups in hooks.values() for g in groups); print(f'{total} hooks configured')" 2>/dev/null || echo "Cannot read hooks"`
**Python version:** !`python3 --version 2>/dev/null`
**RSI lock:** !`test -f ~/.claude/state/rsi/rsi-active.lock && echo "LOCKED" || echo "clear"`
**Stale branches:** !`cd ~/.claude && git branch --list 'feat/*' 'fix/*' 2>/dev/null | wc -l | tr -d ' '`

## Protocol

### Step 1: Detection Scan

Run all health checks:

| Check | Method | Severity |
|-------|--------|----------|
| Hook imports | `python3 -c "import hooks.X"` for each hook | CRITICAL |
| Settings.json validity | `python3 -m json.tool settings.json` | CRITICAL |
| Git state | `git status`, stale locks, divergent branches | WARNING |
| Stale worktrees | `git worktree list` — orphaned entries | WARNING |
| Missing files | Check all paths referenced in CLAUDE.md | WARNING |
| Test suite | `python3 -m pytest tests/ -x --tb=short -q` | INFO |
| Skill graph | Check for circular dependencies | INFO |
| Memory staleness | MEMORY.md entries older than 30 days | INFO |
| Infra test marker | `.infra-tests-passed` freshness | INFO |

### Step 2: Diagnosis

For each detected issue:
1. Classify severity (CRITICAL > WARNING > INFO)
2. Trace root cause (import error? missing file? config drift?)
3. Check if issue is already known (search TODO tracker, GitHub issues)
4. Estimate fix complexity (1-line, multi-file, architectural)

### Step 3: Remediation (converge loop)

```python
for issue in sorted(issues, key=lambda i: i.severity, reverse=True):
    converge(
        state=issue,
        criterion=lambda i: i.check() == "GREEN",
        improve=lambda i: apply_fix(i),
        max_depth=3,
        time_budget_sec=60
    )
```

Fix strategies by type:
- **Import error** -> fix import path, install missing package
- **Invalid JSON** -> validate and repair settings.json
- **Stale lock** -> check owner PID, remove if dead
- **Divergent branch** -> git pull --rebase
- **Missing file** -> regenerate from template or git checkout
- **Test failure** -> report (do not auto-fix tests)

### Step 4: Verification

Re-run all detection checks. If all green:
- Output health report
- Touch `.infra-tests-passed` marker
- Log healing event to `learning/metrics.jsonl`

If still red after max_depth:
- Output remaining issues with manual fix instructions
- Create GitHub issue for unresolved items

## Output Format

```
GRIP Health Report
==================
[GREEN] Hook imports: 28/28 OK
[GREEN] Settings.json: valid
[YELLOW] Git: 2 stale feature branches (cleaned)
[GREEN] Tests: 23/23 passed
[GREEN] RSI lock: clear

Healed: 1 issue (stale branches)
Remaining: 0 issues
```

## Integration Points

- Runs after CC upgrades (auto-detect via version change)
- Available during RSI sprints for mid-sprint health checks
- Can be scheduled via /loop for periodic healing
- Reports to Slack on significant healing events
