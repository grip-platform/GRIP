---
name: pre-flight-check
description: Pre-flight environment and constraint check before implementation. Reduces wrong-approach friction by validating credentials, git state, and project constraints before writing code.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/preflight"
  - "pre-flight check"
  - "check environment"
integrates:
  - credential_validator.py
  - continuation_prompt.py
see_also:
  - context-gate
  - design-principles
  - asking-users
  - creating-wizards
  - hitl
---

# /preflight -- Pre-Flight Check

Run a mandatory environment and constraint validation before implementation.
Addresses 194 friction events (145 wrong_approach + 49 misunderstood_request).

## Usage

```bash
/preflight              # Full pre-flight check
/preflight quick        # GitHub + git only (fast)
/preflight --for "task" # Contextual check for a specific task
```

## Protocol

### Step 1: Environment Scan

Run the credential validator:

```bash
python3 ~/.claude/lib/credential_validator.py
```

Report any errors or warnings. If critical failures (GitHub CLI, git-crypt locked):
HALT and report — do not proceed with implementation.

### Step 2: Git State Check

1. Current branch and dirty file count
2. Check for index.lock (another git process)
3. Check for stale stashes from previous sessions
4. Verify remote tracking: `git remote -v`

### Step 3: Constraint Discovery

Read project-specific constraints from:
- `CLAUDE.md` (project rules)
- `rules/*.md` (operational rules)
- `MEMORY.md` (current work context, blockers)

Extract and list:
- Known blockers (from MEMORY.md)
- Push restrictions (hook bypass pattern needed?)
- Worktree limitations (git-crypt?)
- Active modes that affect behaviour

### Step 4: Task-Specific Checks (when `--for` is provided)

If the task involves:
- **External services** (Slack, GitHub API, OAuth): verify credentials with lightweight test call
- **Cross-repo operations**: confirm scope with user (which repos, orgs, team members)
- **Deployment**: check deploy gate permissions
- **File modifications**: identify target files and confirm scope

### Step 5: Approach Proposal

Present a numbered plan:
1. What you will change (specific files)
2. What could go wrong (based on constraint discovery)
3. What fallback you will use if blocked

Wait for user approval before proceeding. If confidence < 99.9999999%:
AskUserQuestion with the plan.

## When to Run

- Before complex multi-step tasks
- Before cross-project operations
- Before external service integrations
- When starting a new session on unfamiliar code
- Automatically suggested by GRIP when task complexity is high

## Integration

The pre-flight check integrates with:
- `lib/credential_validator.py` for automated credential checks
- `lib/continuation_prompt.py` for context state
- Context gate hook for environment awareness
- MEMORY.md for blocker detection
