---
name: ultrareview
description: Context-aware parallel code review — runs specialist review agents in parallel (security, design principles, BIG-O, test coverage) locally without requiring the web. Shows diffstat before starting. Mirrors /ultraplan's context-injection pattern but for review, not planning.
command: /ultrareview
effort: medium
version: 1.0.0
triggers:
  - /ultrareview
  - ultrareview
---

# Ultrareview — Context-Aware Parallel Code Review

Runs a deep, parallelised review of changed code using specialist agents, entirely
locally. No web session required. Mirrors `/ultraplan`'s context-injection approach:
the review gets the session intent so reviewers know *why* the code was written,
not just *what* it does.

## The Problem It Solves

CC's built-in `/ultrareview` requires a web session. GRIP's version runs the same
parallel checks locally via Agent workers — security, design principles, BIG-O,
and test coverage all fire simultaneously and results are aggregated in under a
minute.

## How It Works

1. **Diffstat** — show what is about to be reviewed before starting
2. **Gather intent** — pull session context so reviewers know the goal
3. **Parallel review** — four specialist agents fire simultaneously
4. **Aggregate** — merge findings, deduplicate, prioritise by severity
5. **Report** — structured output: MUST FIX / SHOULD FIX / CONSIDER

## Usage

```bash
/ultrareview                      # Review all staged + unstaged changes vs main
/ultrareview HEAD~3               # Review last 3 commits
/ultrareview src/foo.py           # Review a specific file
/ultrareview --staged             # Review only staged changes
```

## Implementation

When invoked:

### Step 1 — Diffstat

Run and display before any review starts:

```bash
git diff --stat HEAD          # or the user-supplied ref
```

Show the operator what is being reviewed. This is the "diffstat in launch dialog"
pattern from CC 2.1.113.

### Step 2 — Gather Context

Read `~/.claude/rules/session-context.md` — extract:
- What the session was trying to achieve (Recent Achievements / Remaining Work)
- Any hypothesis registered for this PR (from `lib/hypothesis_engine.py status`)

This becomes the `intent` block injected into every reviewer's prompt.

### Step 3 — Parallel Review (4 agents)

Launch all four agents in a **single message** so they run concurrently:

**Agent A — Design Principles** (`design-reviewer` subagent_type)
```
Review the diff below against SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O.
Focus on: cyclomatic complexity, abstraction leaks, responsibility violations.
Intent: {intent}
Diff: {diff}
```

**Agent B — Security** (`general-purpose` subagent_type)
```
Security review the diff below. Check for: injection vectors, secret leaks,
unsafe deserialization, broken auth, OWASP Top 10.
Intent: {intent}
Diff: {diff}
```

**Agent C — Test Coverage** (`general-purpose` subagent_type)
```
Review test coverage for the diff below. Check: are new branches tested?
Are edge cases covered? Are there Goodhart-guard violations (tests that
always pass regardless of logic)?
Intent: {intent}
Diff: {diff}
```

**Agent D — Performance / BIG-O** (`general-purpose` subagent_type)
```
Review the diff for algorithmic complexity. Flag any O(n²)+ operations,
N+1 query patterns, missing indexes, or unbounded loops.
Intent: {intent}
Diff: {diff}
```

### Step 4 — Aggregate

After all four agents return, merge their findings:

1. Collect all findings with their severity (MUST FIX / SHOULD FIX / CONSIDER)
2. Deduplicate overlapping findings (same line flagged by multiple agents)
3. Sort: MUST FIX first, then SHOULD FIX, then CONSIDER

### Step 5 — Report

Output in this format:

```
## Ultrareview — {branch} vs {base}
{diffstat summary: N files, +X −Y}

### MUST FIX
- [Security] {finding} — {file}:{line}
- [DRY] {finding} — {file}:{line}

### SHOULD FIX
- [BIG-O] {finding} — {file}:{line}

### CONSIDER
- [YAGNI] {finding} — {file}:{line}

### Coverage gaps
- {untested branch} in {file}:{line}

Reviewed by: design-principles + security + test-coverage + performance
```

## Local vs Remote

| Condition | Mode |
|-----------|------|
| Default | **Local** — 4 parallel Agent workers, no web needed |
| User passes `--remote` | RemoteTrigger with context-rich prompt + local fallback |
| Context budget < 20% | Warn operator, offer `--remote` to offload |

Local is the default and preferred path. Remote is an opt-in enhancement, not
a requirement.

## Fallback

If any individual agent fails:
- Log the failure
- Continue with the remaining agents
- Note which review dimension was skipped in the final report

## Integration Points

- **Pre-PR gate**: Call `/ultrareview --staged` before `/create-pr` to catch
  issues before they hit GitHub
- **RSI loops**: Run automatically at wave boundaries as a quality gate
- **Hypothesis validation**: If a hypothesis is registered for this PR, include
  its claim in the intent block so reviewers can assess whether the code
  supports the claim
