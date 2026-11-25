---
name: mob-doctor
description: Read-only readiness check for the AI Craftspeople Guild's mob-programming "before you start" prerequisites (Git, Node/nvm, project-local TypeScript, test runner, Stryker, mob.sh). Use before joining a Guild mob session, or when diagnosing why a handover or a test run fails on one machine but not another.
status: Active
version: 1.0.0
triggers:
  - /mob-doctor
  - "am I ready for the mob session"
  - "check my mob-programming setup"
---

# /mob-doctor -- Guild mob-programming readiness check

Runs the 18 read-only checks derived from the Guild's onboarding page
(`https://aicraftspeopleguild.github.io/mob-programming-onboarding.html#before`)
and reports each as **pass**, **fail** (with the exact fix), or **needs-input**
(a repository is required and none was given -- not a failure).

## Usage

```bash
bin/grip-mob-doctor                                    # no repo known yet
bin/grip-mob-doctor --repo-path ~/code/guild-sample     # repo already cloned
bin/grip-mob-doctor --repo-url git@github.com:guild/sample.git  # repo not cloned
bin/grip-mob-doctor --json                              # machine-readable
```

Exit code `0` when nothing failed (`needs-input` does not count as a failure);
`1` when at least one check failed.

## What it checks

| Group | Checks |
|---|---|
| macOS prerequisites | Homebrew, Xcode Command Line Tools |
| Git | version >= 2.30, identity, `core.autocrlf`, `core.ignorecase`, SSH/credential auth, push round trip (needs `--repo-url`) |
| Node | managed via nvm (not a system package), no conflicting Homebrew `node`, on an LTS line or matching `.nvmrc` |
| Project (needs `--repo-path`) | `npm ci` deps installed, project-local TypeScript installed and type-checking, editor pinned to the workspace TypeScript, a test runner configured, Stryker configured |
| Handover tool | `mob` installed and on `PATH` |

Every check is **read-only**: it inspects versions, git config and files, and never
installs, writes, pushes or otherwise changes anything. The only state-changing
step on this page (the push access round trip, and `npm ci` itself) stays a
reported **fix command** for a human to run, never something this skill executes
for you.

## Why read-only

A "doctor" that silently fixes things stops being trustworthy the first time its
fix is wrong for your machine -- `nvm`, a global git config change, or an
`npm install` can affect other projects on the same machine, not just the one
mob session. Every fix this skill reports is a single copy-pasteable command, so
the person running it stays in control of what actually changes.

## Example

```text
$ bin/grip-mob-doctor
[FAIL] nvm_installed: no nvm.sh at ~/.nvm
    fix: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash
[NEEDS INPUT] deps_installed: needs the repository path (not yet known)
    fix: re-run with --repo-path <dir> once the repo is cloned
[PASS] mob_on_path: mob found at /opt/homebrew/bin/mob
-- 15 pass, 1 fail, 2 needs input --
```

## Related files

| File | Purpose |
|---|---|
| `lib/mob_doctor.py` | the 18 checks, as a dispatch table (`CHECKS`) |
| `bin/grip-mob-doctor` | thin CLI wrapper |
| `tests/test_mob_doctor.py` | one pass + one fail test per check (plus `needs-input` where that branch exists) |

## Related skills

| Skill | Relationship |
|---|---|
| `mob-start` | guarded wrapper over `mob start`, run once this skill is clean |
| `mob-next` | guarded wrapper over `mob next` |
| `mob-done` | guarded wrapper over `mob done` |
