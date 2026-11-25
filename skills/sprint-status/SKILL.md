---
name: sprint-status
description: Live sprint dashboard using Dynamic Context Injection. Shows git state, open PRs, issues, RSI health, and task progress in real time. Use during RSI sprints or when checking session progress.
effort: low
---

# /sprint-status — Live Sprint Dashboard (DCI)

All data below is injected live via Dynamic Context Injection at invocation time.

## Git State

**Branch:** !`cd ~/.claude && git branch --show-current 2>/dev/null`
**Last 5 commits:**
!`cd ~/.claude && git log --oneline -5 2>/dev/null`

**Working tree:**
!`cd ~/.claude && git status --short 2>/dev/null | head -10`

## Open PRs

!`cd ~/.claude && gh pr list --limit 5 --json number,title,state --jq '.[] | "- #\(.number) [\(.state)]: \(.title)"' 2>/dev/null || echo "No open PRs or gh not configured"`

## Open Issues (top 5)

!`cd ~/.claude && gh issue list --limit 5 --json number,title,labels --jq '.[] | "- #\(.number): \(.title) [\(.labels | map(.name) | join(","))]"' 2>/dev/null || echo "No issues or gh not configured"`

## RSI Health

**RSI lock:** !`test -f ~/.claude/state/rsi/rsi-active.lock && echo "LOCKED (active sprint)" || echo "Unlocked"`
**Budget:** !`python3 -c "import json; d=json.load(open('$HOME/.claude/state/rsi/rsi-budget-state.json')); print('%.2f / %.2f (%d calls)' % (d['estimated_cost_usd'], d['budget_usd'], d['tool_calls']))" 2>/dev/null || echo "No budget state"`
**Caffeinate:** !`pgrep -x caffeinate >/dev/null 2>&1 && echo "Active" || echo "Inactive"`

## Metrics

**Skills count:** !`ls -d ~/.claude/skills/*/SKILL.md 2>/dev/null | wc -l | tr -d ' '`
**Agents count:** !`ls ~/.claude/agents/*.md 2>/dev/null | wc -l | tr -d ' '`
**Insights:** !`wc -l < ~/.claude/learning/insights.jsonl 2>/dev/null || echo "0"` entries
**Metrics:** !`wc -l < ~/.claude/learning/metrics.jsonl 2>/dev/null || echo "0"` entries

## Action

Based on the live data above, provide a brief assessment:
1. What needs immediate attention?
2. What's healthy?
3. Recommended next action?
