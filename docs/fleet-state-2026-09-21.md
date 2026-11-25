# Fleet state — 21 September 2026

Written so a restarted fleet resumes instead of rediscovering.

**The same record, three surfaces** (all three requested by the operator):

| Surface | Where |
|---|---|
| This document | `docs/fleet-state-2026-09-21.md`, GRIP#6361 |
| Tracking issue | GRIP#6362 — the checklist, with the open decisions |
| Readable away from the desk | <https://claude.ai/code/artifact/a3c9c6f1-892e-40d2-8d80-aca73004af72> |

Every number here was measured on 2026-09-21 against `origin/main` at
`b052f840c`, with the command that produced it named. Where an earlier estimate
of mine was wrong, the correction is stated rather than quietly replaced —
a record that hides its own revisions is worth less than one that shows them.

---

## The headline

**1,938 commits across 693 local branches existed on no remote branch.** That is
roughly eight times the ~250 an earlier pass estimated, and **none of it was
duplicate history**: `git cherry origin/main <branch>` returned `+` for every
commit examined and `-` for none.

All of it is now preserved. Nothing was deleted, rebased, or force-pushed.

```bash
# how the figure was taken
git for-each-ref --format='%(refname:short)' refs/heads/ | while read -r br; do
  [ "$br" = main ] && continue
  ah=$(git rev-list --count "origin/main..$br") || continue
  [ "$ah" = 0 ] && continue
  [ -n "$(git branch -r --contains "$br" | head -1)" ] && continue
  echo "$ah $br"
done
```

`git branch -r --contains` is the load-bearing test. A name-existence check
passes for a branch whose remote has since diverged, so it reports safe work
that is not actually on any remote.

---

## Preserved — what was done about it

| What | Where it now lives | Verified by |
|---|---|---|
| Every unpushed commit and ref (1,717 refs) | `PARAMOUNT_BACKUPS/2026-09-21/unpushed-git-bundle`, 76,221,770 bytes | `git bundle verify` → `is okay`; byte count matches source exactly |
| Shared tree's 38 new commits | `origin/backup/shared-tree-2026-09-21` at `c9805a7d` | `git ls-remote --heads origin` |
| The 8 largest feature branches | pushed to origin under their own names | `git ls-remote`, all 8 accepted without divergence |

A single bundle was chosen over 693 remote branches deliberately. It preserves
everything with no remote footprint, and it leaves the push-or-prune decision
where it belongs — with the operator, not with an unattended loop.

**The bundle is thin**: it excludes objects already in `origin/main` and so
requires a clone of origin to restore from. Origin is on GitHub, so that
prerequisite is durable; it is stated because a bundle that cannot be restored
in isolation should say so.

### Largest branches, all genuinely new

| Branch | New commits | Duplicates |
|---|---|---|
| `fix/cost-guard-and-billing-visibility` | 29 | 0 |
| `triage/pr-4977` | 22 | 0 |
| `doctrine/adversarial-instruction-is-not-exogeneity` | 13 | 0 |
| `feat/kimi-w0-truth` | 10 | 0 |
| `feat/cross-harness-staleness-signal` | 55 (ahead) | — |
| `fix/happi-version-literal-duplication` | 41 (ahead) | — |

---

## Finished

**40 pull requests merged between 18 and 20 September.** The run is dominated by
one theme, and it is worth naming because it says what the fleet has been doing:
**tests that could not fail were found and repaired.** `test(gate): prove the
empty-block guard fires, not merely that it is wired`, `fix(tests): the
measure_plan anchor demanded the absence of its own output`, `test(backlog):
assert the render changed nothing, not that the directory exists`.

Also merged: the Room viewer's author names (#6334, #6340 — a session's posts
resolve to a name rather than a raw UUID), redaction ordering (#6330 — redact
before asking what is still exposed), and rules-scope refusing to shed a
dangerous-action rule (#6344).

---

## In progress

| Item | State | The single next step |
|---|---|---|
| **GRIP#6359** — mesh inbox messages reach the model | **MERGED** (squash `18aa8ba5`), AGORA **DEFER CHANGES** 3/3 speakers. GRIP#6360 addressed in-branch and closed. | Done. |
| **693 branches / 1,938 commits** | Bundled and backed up; 9 branches pushed | Decide per branch: promote to a PR, or prune. Nothing is at risk while the bundle stands. |
| **Shared tree `~/.grip`** | 0 behind, 38 ahead, 22 dirty tracked files | Its 38 are backed up. Levelling it is blocked — see GRIP#6346 below. |

### GRIP#6359 in one paragraph

GRIP could send a message from one Claude Code session to another, and **none of
those messages ever arrived**. The hook printed them to stderr; Claude Code
fills a hook attachment's `content` from stdout. Measured over a real transcript
of 5,748 attachments: **22 MESH-INBOX attachments, every one with empty
`content`**, the text present only in stderr, while the 6 attachments that did
reach the model all had `content == stdout`. Worse than silent — the hook marked
each message delivered *before* printing it, so every broadcast was drained,
flagged done, and discarded unseen.

This matters beyond the bug: **any plan that relies on broadcasting an
instruction to other sessions was inert.** The hook is bound by path and read
fresh each invocation, so the fix reaches every running session on its next tool
call, with no restart.

**Delivery remains pull-based and that is unchanged.** A session idling at a
prompt makes no tool calls, so the hook never runs and that session cannot be
reached at all. Any future fleet-coordination design has to account for that.

---

## Blocked — decisions only the operator can make

| Issue | What it says | Why it blocks |
|---|---|---|
| **GRIP#6346** | `grip-tree-sync` cannot level the shared tree, because 25 launchd agents dirty the tree it refuses to merge | The refusal is correct — a peer sync has already eaten a resolved merge once. Resolving it means deciding what those agents may write, which is an operator call. |
| **GRIP#6307** | ~~17 tests red on pristine `origin/main`~~ — **re-measured 22 Sept: zero.** See the correction below. | No longer blocking. |
| **GRIP#6360** | AGORA's deferred concern on #6359 | **Closed** — addressed in-branch before merge. |

200+ issues are open overall; these three are the ones a restart trips over.

---

## Corrections to earlier measurements in this session

Stated plainly, because each was used to justify a plan step:

1. **"Many of the 250 commits are probably duplicates."** False. `git cherry`
   found zero duplicates in every branch examined, and the true figure is 1,938.
2. **"Three live sessions are running in deleted working directories."** Not
   supported. Every worktree's directory exists today.
3. **"A reminder bot is still firing every five minutes."** False. Those
   messages ran 13–19 September (**1,779 of them**, not the 1,245 an earlier
   7-day window suggested) and stopped on the 19th; the producing launchd agents
   were disabled on the 20th and are confirmed not loaded. The thread has
   traffic from today, so the silence is real and not a stale mirror.
4. **"Zero open PRs."** True when taken; now one (#6359).

---

## What a restarted fleet should know

- **`origin/main` IS green** — corrected 22 Sept, see below. If your suite is
  red, it is probably yours.
- **Nothing is lost, but little is merged.** 1,938 commits sit on branches with
  no PR behind them. They are bundled; they are not reviewed.
- **Broadcasting to other sessions did not work until #6359**, and even then it
  cannot reach a session idling at a prompt.
- **The shared tree is what launchd agents execute from.** Leaving it on a
  feature branch is an unreviewed deploy, not untidiness.

### Correction, 22 September: the baseline was never red

This document said a restart assuming a green baseline was assuming something
false. **That was wrong, and it is the most consequential thing in here.**

Re-measured at `origin/main` `fa3d8cef0`: **759 passed, 2 skipped, 0 failed**
across all 45 `precog.idr`-importing test files, and **24 passed** for the
`oss/idr-chain` suite run correctly. GRIP#6307's seventeen are zero.

**Fourteen of them were never failures.** `idr_chain` vendors its dependencies
under the name `lib` and refuses to import when the host process already owns
`lib.audit_chain`, `lib.context_chain`, `lib.precog` and eleven more — listing
every collision and saying "import idr_chain from a process that does not have
them". Run its tests from the repo root and that refusal fires on all of them.
It is the anti-shadowing guard working. The suite is not red there; it is
un-runnable there, by design.

**The other three do not reproduce**, and no commit has touched those files or
`lib/context_chain.py` in the 53 commits since GRIP#6307's baseline — so the
same source that failed then passes now. The cause is **not established**. The
crypto backend and a system interpreter were both eliminated by measurement;
this issue's own hypothesis, a process binding `precog.idr` off a shared tree
then 101+ commits stale, remains the strongest and cannot be tested after the
fact.

GRIP#6392 makes the suite skip where it cannot run and adds the CI gate those
24 tests never had.

### Merged is not delivered

GRIP#6359 merged to `origin/main` at 15:43 and the shared tree **still ran the
old hook** — 105 lines different. Hooks execute from `~/.grip`, which was 1
behind, 48 ahead and 23 files dirty, so `grip-tree-sync` could not level it
(GRIP#6346). The fixed file was delivered on its own, byte-identical to
`origin/main`, and proved live end to end:

```
$ CLAUDE_MESH_DB_PATH=... hooks/mesh-inbox-poll-hook.py
{"hookSpecificOutput": {"hookEventName": "PostToolUse",
 "additionalContext": "[MESH-INBOX from=V>> kind=broadcast] ..."}}
```

**Check the shared tree after every merge that changes a hook or a rule.** A
green merge says the code is on `origin/main`; it says nothing about the tree
the code actually runs from.

Primary Author: Laurie Scheepers
Provenance: Claude Opus 5 (1M context)
