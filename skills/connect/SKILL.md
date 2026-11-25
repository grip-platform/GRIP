---
name: connect
description: Pairs models, not people. One frontier model plans a small code change, a coding agent in another harness (Codex on the ChatGPT subscription) makes it in a sandboxed worktree, a check with no model proves it, and a reviewer from a third vendor reads the diff. Every step is sealed into a linked IDR leaf and said in a mesh room, under a per-run cost cap, time limits, a stop marker and a veto. Use when the operator invokes /connect, or asks for a change to be planned by one model, coded by another and reviewed by a third.
---

# /connect

`/connect` relays three one-shot seats from three vendors through a deterministic
conductor. It is the model-to-model counterpart of `/pair`, where a human holds one
seat. The conductor is plain code: no model sits between the seats, and no model
text ever reaches a shell string.

```bash
bin/grip-connect run "add mul(a, b) to lib/calc.py with a test" --file lib/calc.py --accept-open-reads
bin/grip-connect plan "<task>"          # admit and plan only; nothing is changed
bin/grip-connect roster                  # the seats the role rule picks today, and why others lost
bin/grip-connect status                  # recent runs and their outcomes
bin/grip-connect stop [RUN_ID]           # a running /connect stops within five seconds
bin/grip-connect verify RUN_ID           # every artefact against its signed leaf
bin/mesh room --room connect-<RUN_ID> tail 50   # --room goes before the subcommand
```

## The six steps

1. **Admit.** Check the stop marker and take the lock for the repository. The role
   rule picks the seats. A Codex home is chosen only if both of its windows have room
   and it holds no credits that Codex could fall back to. The worktree starts at a
   freshly fetched `origin/main`, under `~/.grip-worktrees/`, with every git-crypt
   path left out.
2. **Plan.** The planner gets the task, the tracked file list and the files named
   with `--file`, in one text-only call. It returns PLAN JSON. A plan that names CI,
   hooks, gates, rules, settings, agent instructions or an encrypted file is refused.
   A path outside `--roots` is dropped and noted.
3. **Run.** `codex exec` gets the plan on stdin in the worktree. The sandbox allows
   writes in the worktree only, the network is off, the environment is an allowlist,
   both API-key variables are blank, and `lib/openai_billing.check` passes the argv
   before it starts. A coder that cannot go on ends with `BLOCKED: <question>`, which
   the conductor posts as an `ask` before it ends the run.
4. **Check, with no model.** Git says what changed. Every change must be inside the
   plan, no binary file may change (its bytes never reach the diff), no changed
   symlink may point outside the worktree, and the diff must hold no credential-shaped
   string (it is scanned before it is stored, and a diff with a hit is stored masked).
   The change's own tests must pass on a clean
   checkout of exactly the staged tree, and fail on the base commit with the
   change's test files and test-support files (a `conftest.py`, a `tests/` helper)
   copied in. The tests run under `codex sandbox -P :workspace`.
5. **Review.** A reviewer from a third vendor reads the task, the plan, the check
   and the diff, and returns VERDICT JSON with a `PASS` or `CHANGES` verdict. A reply
   without one never approves: the next reviewer seat is asked. A concern blocks only
   when its quote is copied exactly from a changed line. Other concerns are recorded
   and do not block.
6. **Close.** A failed check or a blocking concern gets one repair round. A pass is
   committed on the local branch `connect/<RUN_ID>`, with trailers that name the
   three served models, and only if the committed tree is the tree the tests passed
   on. The conductor never pushes, opens a pull request or merges. Take the branch
   through `/finish` and AGORA like any other change.

Every git command the conductor runs has hooks switched off (`core.hooksPath=/dev/null`),
because it runs outside the sandbox and a coder could have rewritten a hook or a
script a hook calls. So the repository's own commit hooks do not run on a
`connect/` commit; its leak check and review happen when the branch goes through
`/finish`. Switching hooks off does not stop git's filter, diff and fsmonitor
commands, so admission refuses a repository whose git config runs any such command
other than `git-crypt` or `git-lfs`.

## The role rule

`config/connect-roles.json` lists the seats. `lib/connect_roles.py` checks every
planner, coder and reviewer triple and drops one that breaks a rule:

- **R1:** two seats share a vendor, or the planner and the coder share a harness.
- **R2:** GPT runs off the ChatGPT subscription, Claude runs on a paid key, or
  Claude takes the coder seat.
- **R3:** a metered or free model takes a seat with no token bound, or its worst
  case at list price exceeds the per-run cap.
- **R4:** the coder's harness has no verified sandbox.
- **R5:** the seat has no headroom now, or it already failed in this run.

The best surviving triple wins on rank. Billing class breaks a tie: flat-rate first,
then free, then metered. Today that is Fable 5.2 to plan, Codex (GPT-6 Astra) to
code and DeepSeek v4 Pro to review. If both Claude seats fail, DeepSeek plans and
Gemini 3.1 Pro reviews. A seat that has already served is never replaced.

## The brakes

- **Cost.** A metered call reserves its worst case before it is sent: the prompt's
  bytes at the input price plus the output cap at the output price. The run's cap is
  $0.75. The reservation stands, because the transports report no token counts. The
  cap is a worst case, not expected spend: DeepSeek v4 Pro spends its output cap on
  hidden reasoning before it answers, so its seats carry a 64,000-token cap (a
  16,384 cap returned no answer for a 13 KB review prompt on 2026-10-05), which is
  $0.25 of output at its peak price. The two live runs of 2026-11-11 reserved $0.04
  each with the earlier, smaller caps.
- **Time.** Plan 900 seconds, run 1,200, check 300 (half each for the head and the
  base), review 900.
- **Stop.** `grip-connect stop` sets `state/connect/STOP`, or `STOP-<RUN_ID>` for
  one run. The conductor checks it between steps, every five seconds during every
  seat call and test run, and once more before the commit. It kills the process
  group it started for the coder and the tests. A planner or reviewer call is
  abandoned instead, because its answer can no longer change anything.
- **Veto.** `bin/mesh room --room connect-<RUN_ID> veto <seq> "<why>"` stops the
  run. The room can stop a run and never advance one. The conductor reads only
  `veto` rows.

## The record

Each step writes an artefact to `state/connect/runs/<RUN_ID>/` and a signed IDR leaf
on the shared chain that holds the artefact's hash and the previous leaf's id. A
leaf holds hashes, ids and model names only, never prose. `grip-connect verify`
re-hashes every artefact against the hash its leaf signed, and each stored diff
against the hash its check signed. It checks the links, requires every signature to
read `VERIFIED` (a signature it could not check is a problem, never a pass), requires
the record to end at a signed `close`, and compares the run's leaves with the shared
chain, so a step cut from the end of the local record still shows. A veto is sticky:
once read, it holds for the rest of the run.

## Honest limits of this first build

- **One-shot seats only.** There is no stateful foreign peer, no serve or attach
  mode, no turn-taking, and no seat posts to the mesh itself. The `BLOCKED` count
  measures whether turn-taking is needed (the turn-taking follow-up issue).
- **Reads are not confined, so a run must accept that.** The Codex sandbox blocks
  writes outside the worktree and every network call, but a coder can still read
  files outside it, such as login tokens or unlocked encrypted files in the main
  checkout, and a prompt-injected coder could send them to its vendor. So `run`
  refuses unless `--accept-open-reads` is passed for that run, and the admit record
  says it was. `plan` runs no coder and needs no flag. GRIP#9154 tracks real read
  confinement. `/tmp` and `$TMPDIR` stay writable for the test run. These were
  measured on codex-cli 0.156.1 on 2026-11-11.
- **Small changes only.** A change of more than 1,000,000 bytes fails the check
  before its diff is read, and a reviewer's prompt is bounded at 30,000 bytes.
- **Python tests only.** The check runs pytest files named `test_*.py`.
- **A coder that exits non-zero ends the run.** There is no retry on another Codex
  home within one run.
- **The first Codex home is never a coder.** It is the council's own seat. List more
  reserved homes, such as the PR-review seat, in `GRIP_CONNECT_RESERVED_HOMES`, or pin
  one with `GRIP_CONNECT_CODEX_HOME`.
- **No acceptance measurement yet.** The `relay-10` test (five live runs and five
  planted defects) is a tracked follow-up, and so is the nightly model bake-off.

Design: Fable 5.2's position of 2026-11-11, sealed by the `/ultraplan` trio, and the
operator's picks the same day. Code: `lib/connect.py`, `lib/connect_roles.py`,
`lib/connect_check.py`. Tests: `tests/test_connect.py`.
