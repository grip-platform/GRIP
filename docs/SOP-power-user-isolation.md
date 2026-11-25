# GRIP Power-User Isolation SOP

**Status:** Active (shipped 2026-05-01)
**Owners:** V>> (operator) for the framework; each user owns their own branch
**Falsification:** see end of document

## Why this exists

GRIP power-users need to evolve the system in private without their experiments
landing on `main` until they're proven. Without isolation:

- Personal scratchpads bleed into shared state
- Auto-generated artefacts (`learning/insights.jsonl`, `data/genome-shared.json`,
  KONO embeddings) get committed during `/save` and pollute every clone
- No clear boundary between "a power user's customisations" and "GRIP itself"
- Power-users end up running shadow GRIPs that diverge from main

The SOP creates a protected, per-user workspace (`isolation/<user>-personal`)
that is mechanically isolated from main, with a single controlled spillway
(via `bin/grip-promote`) for upstreaming generic enhancements.

## The two protections

| Protection | Mechanism |
|---|---|
| **Personal branch is private** | Ruleset 15803953 + CODEOWNERS — only the owner pushes; nobody can corrupt the workspace |
| **Main is the only authority** | Generic enhancements flow only through PR + leak-check; user-specific paths denylisted |

## Lifecycle

### 1. Onboarding — `bin/grip-isolate-me`

```bash
bin/grip-isolate-me --user <name> [--github <gh-username>] [--sig "X>>"] [--name "Full Name"] [--role "..."]
```

Creates `isolation/<user>-personal` from current main, pushes to origin,
appends an entry to `state/users/isolated-users.yaml`.

Requires the caller to be an org admin (the `creation` rule on ruleset
15803953 blocks non-admin branch creation under the protected pattern).

### 2. Daily work — direct push to your isolation branch

Once onboarded, the user pushes directly to `isolation/<user>-personal`.
The branch is protected against:

- Deletion (`deletion` rule)
- Force push / non-fast-forward (`non_fast_forward` rule)
- Unsigned commits (`required_signatures` rule)
- Merge commits (`required_linear_history` rule — rebase only)

### 3. Staying current — `bin/grip-sync-from-main`

```bash
git checkout isolation/<user>-personal
bin/grip-sync-from-main
```

Rebases the personal branch onto current `origin/main` with autostash.
Detects git-crypt phantom diffs and refreshes the smudge filter.

Run this at least weekly. Branches more than ~100 commits behind main
will be flagged by the drift audit (P2 governance — coming).

### 4. Upstreaming — `bin/grip-promote`

When you've built something on your personal branch that should benefit
all GRIP users:

```bash
# from your isolation branch:
bin/grip-promote <commit-sha>           # promote one commit
bin/grip-promote abc123..HEAD           # promote a range
bin/grip-promote --paths skills/foo/    # promote current state of paths
```

The script:
1. Pre-flight checks the diff against `.github/personal-artefact-paths.txt`
2. Creates `feat/promote-<user>-<sha>` from current main
3. Cherry-picks (or copies) the change
4. Pushes the feature branch
5. Opens a PR to main with the `from-isolation.md` template body
6. Returns the PR URL

The PR triggers the `personal-artefact-leak-check` workflow, which
fails if any denylisted path is in the diff. CODEOWNERS approval is
required before merge per `main_protect`.

### 5. Offboarding (when a power-user leaves)

```bash
# admin only
git checkout isolation/<user>-personal
git tag archive/isolation/<user>-final-snapshot-$(date +%Y%m%d)
git push origin archive/isolation/<user>-final-snapshot-$(date +%Y%m%d)
# remove user's CODEOWNERS lines, set active: false in roster
# branch is retained for historical reference
```

## What MUST NOT cross the boundary

The denylist at `.github/personal-artefact-paths.txt` is the single source
of truth. Categories:

- **Genome / KONO / learning substrate** — `data/genome-shared.json`,
  `learning/insights.jsonl`, `data/absorbed-urls.jsonl`, etc.
- **Embeddings** — `embeddings.db` and friends (per-user KONO substrate)
- **Session-bound transient state** — `state/session/`, `state/ultrado/`,
  `state/sprint-handoff/`, `state/ultraread/`
- **Personal scratchpads** — `backlog/code_tasks.md`, `backlog/rule_candidates.md`
- **Per-machine memory directories** — `projects/`
- **Local config / onboarding markers** — `.active-modes`, `.onboarded.json`,
  `settings.local.json`
- **Session context** — `rules/session-context.md` (already gitignored)
- **Bypass audit logs** — `logs/isolation-bypass.log`

Adding to the denylist: open a PR editing `.github/personal-artefact-paths.txt`
with a brief justification. The denylist is itself path-protected; CODEOWNERS
require V>> review.

## What goes through CODEOWNERS

Beyond the global `* @operator` line, named owners apply to:

- Convergence kernel: `lib/converge*.py` → V>>
- Hooks (safety gates): `hooks/` → V>>
- License + security: `LICENSE`, `SECURITY.md` → V>>
- Isolation infrastructure: this file, the syscalls, the workflow → V>>
- Legal / compliance content: `drafts/contract-*`, `drafts/legal-*`,
  `rules/legal-mode.md`, `skills/legal-ops/` → V>>

## Bypass paths

| Scenario | Mechanism |
|---|---|
| Emergency hotfix needs to go directly to main | Org admin (V>>) bypasses the ruleset (always) |
| Need to test a denylisted path locally on isolation branch | No bypass needed — denylist only applies to PRs to main |
| Force-push required to fix a leaked secret | Org admin only; audit-logged |
| Single-session rule bypass during dev | `GRIP_ISOLATION_BYPASS=1` (logs to `logs/isolation-bypass.log`) |

## Falsification of this SOP

The SOP is wrong if any of these hold after one quarter:

- A power-user runs more code on `main` than on their isolation branch
  (signal: their isolation branch is empty or near-empty in commit count)
- Generic enhancements bypass `bin/grip-promote` more often than they use it
  (signal: PRs from `feat/*` branches not originating from isolation/*)
- The leak-check workflow generates more false positives than true catches
  (signal: operators routinely override or rebase to bypass it)
- The denylist becomes stale (signal: real leaks ship to main despite green
  leak-check — meaning the denylist missed a category of artefact)
- Onboarding takes more than 60 seconds end-to-end via `bin/grip-isolate-me`

If falsified: revisit the affordance design — the tools must be easier than
the workaround, or the SOP rots into ceremony.

## Origin

- 2026-05-01 — V>> created the first `isolation/<user>-personal` branch and ruleset 15803953
- 2026-05-01 — `/ultrado` session shipped W1-W6 of the implementation plan
  at `plans/grip-power-user-isolation-sop.md`
- Hypotheses H-ISO-1 through H-ISO-6 registered with 14-day verification
  deadline at `learning/hypotheses.jsonl`
