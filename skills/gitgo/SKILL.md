---
name: gitgo
description: Comprehensive git health diagnostic in one command. Use when checking repository status, finding uncommitted work, unpushed commits, or diagnosing git issues.
status: Active
effort: medium
version: 1.1.0
triggers:
  - /gitgo
  - "check git"
  - "git health"
  - "what needs pushing"
  - "git status check"
see_also:
  - shipit
  - branch-cleanup
  - gitops
---

# GitGo — The ONE Git Command

**Purpose:** Single-command git health diagnostic. Finds everything that needs
attention: uncommitted work, unpushed commits, broken state, and recoverable work.

**Activation:** `/gitgo` or "check git health"

## Usage

```bash
python3 ~/.claude/lib/gitgo.py
```

When invoked as `/gitgo`, the agent runs the script and presents the report.

## What It Checks

| Check | Severity | Detects |
|-------|----------|---------|
| State | CRITICAL | Detached HEAD, in-progress merge/rebase/cherry-pick, stale index.lock |
| Staged Secrets | CRITICAL | Private keys, AWS/GitHub/Slack tokens in staged diff |
| Uncommitted | CRITICAL/WARNING | Staged not committed, modified not staged, untracked files |
| Unpushed | WARNING/INFO | Local commits not pushed, PR recommended for large changesets |
| Divergence | WARNING | Branch behind remote (needs pull --rebase) |
| Stashes | INFO | Forgotten stash entries |
| Gitignore | WARNING | .env, node_modules, venv, keys not in .gitignore |
| Config | INFO | fetch.prune not enabled |
| Recoverable | INFO | Recent resets in reflog (lost work) |

## Protected Branch Awareness

When on `main`, `master`, `develop`, `production`, or `staging`:
- Staged changes escalate to CRITICAL with "create a feature branch" suggestion
- 5+ modified files trigger "consider a feature branch" warning

## PR Recommendation

When unpushed changes exceed thresholds:
- 5+ commits OR 300+ lines changed: recommends `gh pr create`
- Below thresholds: suggests `git push`

## Output Format

```
==================================================
  GITGO  my-project (main)
==================================================

  [OK]       No broken state
  [WARNING]  3 file(s) modified but not staged
              -> git add <files>
  [INFO]     2 unpushed commit(s)
              -> git push origin main
  [OK]       Up to date with remote
  [OK]       No stashes
  [OK]       Config optimal
  [OK]       No lost work detected

--------------------------------------------------
  1 warning(s) | 1 info
--------------------------------------------------
```

## Exit Codes

- `0`: No critical issues
- `1`: Critical issues found (or not a git repo)

## Design Decisions (KISS)

- **No flags**: Zero configuration. Run it, read the report.
- **No auto-fix**: Diagnoses only. Use `/shipit` to act on findings.
- **Nine checks**: Each returns `(severity, message, fix_command)` tuples.
- **Colour when TTY**: Plain text when piped (CI-friendly).
- **Under 200 lines**: The whole engine.
- **< 2 seconds**: All checks use fast git plumbing commands.

## Integration

| Command | Relationship |
|---------|-------------|
| `/shipit` | Act on gitgo findings (commit + push + PR) |
| `/prune-branches` | Clean up stale branches gitgo might flag |
| `/create-pr` | Create PR when gitgo recommends one |

## Token Budget

- Skill load: ~200 tokens
- Script execution: 0 tokens (runs in bash, output displayed)
- Total: ~200 tokens
