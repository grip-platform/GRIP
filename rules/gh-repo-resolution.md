# gh Repo Resolution (PARAMOUNT)

Origin: 2026-05-17. `gh` resolves its default repo from the cwd git remote /
`gh repo set-default`, not from a globally stable value. When an orchestrator
session (cwd `~/.hal`) drives GRIP PRs, bare `gh` silently targets HAL — the
wrong repo. Worst failure: `gh pr list` returns EMPTY with exit 0 (no error),
wasting entire investigation rounds. Root cause documented in
`rules/operational-context.md` "GitHub CLI — Projects-classic GraphQL
deprecation" section.

## The Rule

Cross-repo `gh` calls MUST resolve the target repo explicitly. Three
admissible paths:

1. **Pass `-R OWNER/REPO`** — explicit, always correct.
2. **Set `$GH_REPO`** — gh honours it natively; set in the calling shell.
3. **Run `gh` from inside the target repo's worktree** — cwd git remote
   resolves to the right repo deterministically.

**Never** rely on gh's ambient default across shell sessions or when cwd ≠
target repo. The `gh repo set-default` value is per-machine and per-repo;
it silently differs across orchestrator and target-repo shells.

## Enforcing Shim: `bin/grip-gh`

`bin/grip-gh` is the syscall-doctrine shim (deterministic ladder, loud on
ambiguity):

1. `-R`/`--repo` already in args → passthrough unchanged.
2. `$GH_REPO` set → passthrough unchanged.
3. `git rev-parse --show-toplevel` from `$PWD` → parse `origin` remote →
   inject `--repo OWNER/REPO`; emit one `[grip-gh] repo → …` stderr line.
4. Known-dir map (`$HOME/.claude` → GRIP, `$HOME/.hal` → HAL) as fallback.
5. Ambiguous / no git context → **exit 1 + loud stderr** (never silent guess).

Use `grip-gh` for any `gh` invocation where the cwd may differ from the
target repo.

## `gh pr list` Silent-Empty Failure (Critical)

`gh pr list` on the wrong repo exits 0 and returns empty output — it does
**not** error. This is the costliest failure: the caller sees "0 PRs" and
wastes investigation rounds. Always use the REST fallback or `grip-gh` when
in doubt:

```bash
# REST (safe from any cwd):
gh api 'repos/OWNER/REPO/pulls?state=open&per_page=100' --jq '.[].html_url'

# Shim (cwd-aware, loud on ambiguity):
grip-gh pr list
```

## Cross-References

- `feedback_gh_default_repo_fork_check` — check `gh repo set-default` on
  fork-with-upstream setups.
- `feedback_grip_hal_dual_search` — dual-repo search patterns.
- `rules/operational-context.md` — `gh pr list` silent-empty documented under
  "GitHub CLI — Projects-classic GraphQL deprecation".

## Falsification

Wrong if `gh` ever introduces a stable global default that does not vary by
cwd/session (then `grip-gh` steps 3-5 are pure overhead — remove them,
keep step 1-2 passthrough only). Track: wrong-repo incidents per 30 PRs;
goal zero.
