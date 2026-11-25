# Commit Standards

## Critical
- PROVENANCE (per `rules/provenance-name-the-authors.md`): NAME the specific assisting AI models on substantive commits/PRs — a `Co-Authored-By:` / `Provenance:` trailer naming the models actually used (e.g. `Claude Opus 4.8`) is ENCOURAGED. This REVERSES the former "never Co-Authored-By: Claude" ban (V>> 2026-07-23). Attribute truthfully in both directions.
- NEVER use emojis in commits, PRs, or documentation
- NEVER commit secrets (.env, API keys, credentials)
- NEVER force numerology or pseudo-coincidence into documents (e.g. stretching factorisation chains to find Fibonacci/prime connections that are not genuinely remarkable). If a number has no natural mathematical significance, say nothing.

## Format
```
type: brief description

Details. Primary Author: [Name from git config]
```
Types: feat, fix, chore, docs, test, refactor, perf, style

## Eval-Bearing Commits (trailer convention)

Origin: 2026-05-18, R-6 of the rrr-and-ddd agentic-hygiene investigation
(@DeRonin_ tweet point 3: "commit on every passing eval with model
version + pass rate"). The hypothesis engine binds *prediction → deadline*;
this binds *commit SHA → model × eval-pass-rate* in the log itself — the
difference between an incident report ("prompt vX, model Sonnet 4.6.1,
last eval 94%") and a guess ("deployed Tuesday?").

When a commit's primary change is **gated by an eval/test suite whose
pass-rate is meaningful** (RSI-loop output, model-version-sensitive
prompts, eval-bearing logic), append a trailer:

```
Eval: <suite> <pass>/<total> (<pass%>)
Model: <model-id>
```

Example:
```
Eval: tests/test_cost_tracker_budget_cap.py 11/11 (100%)
Model: claude-opus-4-7
```

- **Opt-in, not mandatory.** Docs/chore/style commits do not carry it
  (YAGNI — a trailer on every commit is noise, not signal).
- **Complementary to the hypothesis engine, not a replacement.** The
  engine tracks falsifiable predictions to a deadline; this trailer is
  the reproducibility paper trail at the commit. Use both for RSI work.
- Reproduce a past run from one SHA: the trailer pins which suite passed
  at what rate under which model when that code shipped.

## PR Format
```
## Summary
<1-3 bullets>
## Test plan
- [ ] Items
```

**Plain-language PR body (PARAMOUNT — every PR, V>> 2026-05-29).** Every PR body
MUST be readable by someone who has never seen the code. Lead with a "What this
does (plain English)" framing of the problem and the fix — graspable in one read,
WHY before HOW — before any jargon, paths, flags, or metrics. A PR body is a
user-facing deliverable (reviewers, teammates, future-you read it cold), so
Rule 17 (Plain Language) applies in full: the canonical-output-style concision is
scoped to in-session working prose and is NOT licence to compress PR bodies.
Technical detail (commands, paths, measured wins, test plan, hypothesis) still
appears — after the plain frame, not instead of it. Reference: PR #3278.

## Pre-PR Gate

**Client repos that mandate CodeRabbit** — ALWAYS run CodeRabbit CLI review before creating a PR; address any issues flagged by CodeRabbit before submitting.

**CodeTonight repos** — CodeTonight does **NOT** use CodeRabbit. The HAL Council unified PR reviewer replaces the CodeRabbit + Ralph + Broly + claude-code-review stack on all CodeTonight-SA live repos. Plan: `plans/fluffy-twirling-waffle.md`. Gate documentation: `docs/SAFETY-GATES.md`. Do not add CodeRabbit steps to any CodeTonight workflow.

## Git Safety
- Never force push without explicit request. Never auto-commit.
- Never modify `.github/workflows/` on a **fork** PR branch — GitHub does not run fork pull-request workflows automatically for outside contributors (a maintainer must approve them), and fork PRs get no secrets and a read-only `GITHUB_TOKEN`, so CI there will not behave like CI on main. Enforced by Gate N. **Same-repo branches are fine**: the old blanket "GitHub suppresses `pull_request` triggers on any workflow edit" was false and is refuted three times in GRIP itself — PR #4745 (two workflows) and PR #5531 (one), each `isCrossRepository: false`, each running all 12 checks, plus a third instance 2026-09-13.
- Always `git remote -v` before push (never assume `origin`).
- Branch names: all-lowercase (cross-platform safety).
- No Windows-incompatible chars in filenames: `< > : " / \ | ? *`
- **PARAMOUNT: After merging any PR, ALWAYS `git pull` main before starting new work.** Stale main causes divergent branches, failed fast-forwards, and wasted context resolving merge conflicts.
- **GRIP repo: Ignore Vercel deploy preview failures** when merging PRs. Non-blocking. Frequent pushes were costing hundreds in Vercel builds.

## Git Rebase Defaults (`~/.claude` local config)

The `~/.claude` repo has `rebase.autoStash=true` and `pull.rebase=true` set in local git config. This means:

- `git pull` defaults to rebase (not merge) for linear history
- `git pull --rebase` and `git rebase` automatically park uncommitted changes in a temporary stash, rebase, then restore the stash in one atomic operation
- **Why**: concurrent sessions on main accumulate unstaged changes from other sessions, auto-bots, and hook writes. git-crypt blocks native worktree isolation (Rule 13), so dirty trees are the norm. Without autostash, every rebase fails with "cannot pull with rebase: You have unstaged changes" and recovery requires manual stash/pop with footgun potential

### Pre-flight checks before any autostash-enabled rebase

1. Run `git status` — **abort if `.gitattributes` is modified** (git-crypt round-trip corruption risk)
2. Note any untracked (`??`) files — `--autostash` does NOT stash untracked files, so they may still block the rebase if they collide with incoming additions
3. After the operation: check `git stash list` is empty and `git status` shows no unmerged paths

### Escape hatch

For situations where you explicitly want to see the unstaged state before rebasing (mid-conflict resolution, intentional WIP you want protected from incoming changes):

```bash
git -c rebase.autoStash=false pull --rebase
```

### Complementary tool

For git-crypt phantom-diff handling (encrypted files showing as modified after pull because the smudge filter re-decrypts them), use the existing `git grip-pull` alias — see `scripts/grip-pull.sh`. The two tools address different problems and can be needed in the same session:

| Tool | Problem solved |
|------|----------------|
| `rebase.autoStash=true` (this rule) | Unrelated dirty-tree changes blocking a rebase |
| `git grip-pull` / `scripts/grip-pull.sh` | git-crypt smudge filter phantom diffs on encrypted files |

### Hypothesis tracking

This rule is registered as **H027** via `lib/hypothesis_engine.py`. Claim: the autostash default reduces dirty-tree pull failures to zero over 90 days without introducing silent-failure incidents. Verification deadline: 2026-07-14. Check current status:

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/hypothesis_engine.py status
```

If H022 is refuted (silent pop conflicts, git-crypt corruption, or operator-reported lost work), revert with `git config --local --unset rebase.autoStash` and `git config --local --unset pull.rebase`.
