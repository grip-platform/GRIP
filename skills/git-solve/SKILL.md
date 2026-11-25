---
name: git-solve
description: Apply GRIP's convergence/RSI substrate to its own operational git workflows. Three domains — branch reconciliation (a stale/divergent feature branch vs main), tree reconciliation (a divergent main/HEAD tree that is both behind origin/main and ahead with superseded local commits), and worktree reconciliation (a set of linked worktrees + live daemons + other sessions' uncommitted work) — each resolved safely, verifiably, replayably. The generic shape — "GRIP solves a GRIP operational problem with its own kernel" — extends to other workflow classes later.
status: Active
effort: medium
version: 0.4.0
triggers:
  - "/git-solve"
  - "reconcile branches"
  - "reconcile tree"
  - "reconcile worktrees"
  - "stale PR conflicts with main"
  - "parallel session diverged"
  - "merge conflict on the same files"
  - "main tree behind origin/main"
  - "superseded local commits"
  - "divergent shared tree"
integrates:
  - git_reconcile.py
  - converge
  - grip-worktree.sh
  - grip-tree-sync
  - hypothesis_engine.py
see_also:
  - converge
  - hypothesis-validation
  - verify-canonical-not-claimed
  - concurrent-session-safety
  - worktree-must-track-main
---

# /git-solve — RSI applied to GRIP's own git workflows

GRIP already has a convergence kernel (`converge`), a falsifiable-hypothesis
engine, isolated git-crypt worktrees, and verify-canonical discipline. `/git-solve`
**composes them** to solve a recurring GRIP operational problem instead of a
product one. It is dogfooding: the same machinery that ships features now repairs
the workflow that ships features.

**Three domains (v0.4):**

1. **Branch reconciliation** (v0.1 — the original): two sessions diverge and
   rewrite the **same files**; the stale branch's PR goes `CONFLICTING`. The naive
   fixes — blind rebase, or admin-merge past the conflict — either lose work
   (toxic-rebase) or are forbidden (never merge past a real conflict).
2. **Tree reconciliation** (v0.4): the tree you stand in is **both** behind
   `origin/main` **and** ahead with local commits, some squash-superseded. Reconcile
   by dropping exactly the superseded prefix and keeping the real tail.
3. **Worktree reconciliation** (v0.4): a repo's set of linked worktrees + live
   daemons + other sessions' uncommitted work, levelled per-worktree without
   disturbing anything that is not yours.

All three are reconciled **safely + verifiably** via the same kernel — a
mechanical floor (classifier) + an agent falsify-self layer (did I lose anything?).

The generic claim (why it's not just a git script): *any* GRIP operational
workflow with (a) a detectable divergence/failure shape, (b) a mechanical
classifier for it, and (c) a falsifiable "did I lose anything?" check is a
candidate. Git-reconciliation is instance #1.

## The kernel instantiation

```
converge(
  state     = the diverged feature branch,
  criterion = PR mergeable AND zero behavioural loss (proven, not asserted),
  improve   = reconcile-one-pass (classify -> merge-keep-superset -> falsify-self),
  max_depth = 1-2 (one merge pass usually converges; depth 2 if a real loss is found),
)
```

The mechanical floor is `lib/git_reconcile.py` (deterministic classification).
The judgment layer is the agent (falsify-self: supersession vs loss). Neither
alone is sufficient — the classifier can't tell intent, the agent shouldn't guess
divergence shape.

## Protocol (the 6 phases — replayable)

### Phase 0 — Pre-register (falsify-self)
State the hypothesis BEFORE touching anything (register via `hypothesis_engine.py`):
- **Hypothesis**: e.g. "branch is a superset of main's parallel work; merging main
  in loses nothing either side."
- **Null**: they're genuinely divergent — a merge drops functionality from one side.
- **Falsified if**: any test fails post-merge, OR the other side's unique behaviour
  is absent, OR my validated behaviour is absent.

### Phase 1 — Detect (mechanical)
```bash
git fetch origin main
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.git_reconcile import classify
d = classify('<branch>', 'origin/main')
print(d.strategy); print(d.rationale)
print('superseded (safe to delete):', d.superseded)
print('unique commits:', len(d.unique_commits))
print('both-changed (conflict source):', d.both_changed)
print('only-other (lands clean):', d.only_other)
print('non-superset (inspect):', d.non_superset)
print('merge residual — conflicted (stale):', d.merge_conflicted)
print('merge residual — clean-new (verify):', d.merge_clean_new)
"
```
Verdict ∈ {SUPERSEDED, CLEAN, MERGE-KEEP-OURS, DIVERGENT}.

- **SUPERSEDED** (checked FIRST, squash-merge aware) — merging `<branch>` into
  `origin/main` is a no-op (`git merge-tree --write-tree` reproduces main's
  tree): every commit, *including any squash-merged PR*, is already on main.
  `git cherry` / `git branch --merged` are patch-id-based and BLIND to squash
  merges — `d.superseded` is not. Action: `git branch -D <branch>` — there is
  nothing to reconcile.
- When NOT a clean no-op, `d.merge_conflicted` (stale-overlap files — the base
  moved under a behind branch) and `d.merge_clean_new` (files merged WITHOUT
  conflict markers — candidate unshipped work) split the residual for
  falsify-self. `merge_clean_new == []` alongside a non-empty `merge_conflicted`
  is the **superseded-stale** signature: no real new logic, only stale noise →
  safe to drop after a glance at the conflict hunks.

### Phase 2 — Safety (concurrent-session)
- `pgrep -fl claude` — is a parallel CLI session live? (Only the `claude`
  process under your own pty counts; node MCP servers / the desktop app do not.)
- Confirm HEAD is **pushed** (recoverable): `git ls-remote origin <branch>`.
- If a parallel session is live, broadcast intent (grip-presence
  `session_broadcast`) before mutating shared refs.
- **Work in an isolated git-crypt-safe worktree** so the main tree + any live
  daemon reading its files are untouched:
  `bash lib/grip-worktree.sh add /tmp/reconcile-<ts> feat/<reconcile-branch>`

### Phase 3 — Reconcile (one pass, in the worktree)
- `git merge origin/main` (a single resolution pass beats per-commit rebase for
  divergent rewrites).
- **CLEAN** → no conflicts; done.
- **MERGE-KEEP-OURS** → `git checkout --ours <conflict files>` (your superset);
  the other side's non-conflicting files land automatically.
- **DIVERGENT** → do NOT blind `--ours`; open each `non_superset` file, port the
  other side's unique logic manually.

### Phase 4 — Falsify-self (the no-loss proof — the load-bearing step)
Mechanical superset ≠ behavioural superset. Run the **other side's test contract**
against your merged tree:
```bash
git show origin/main:tests/<their_test>.py > /tmp/contract.py
# run it against the merged worktree
```
For **every failure**, classify (this is the agent's judgment, not the classifier's):
- **REAL loss** → the merge dropped behaviour the other side had → go to depth 2,
  port it, re-verify.
- **INTENTIONAL supersession** → your side deliberately changed this (an
  operator-directed evolution the other side's test predates) → keep yours,
  **document why in the merge commit** (cite the directive).
Then run **your own full suite** — it must stay green (your behaviour intact).

### Phase 5 — Ship
- Fast-forward the branch on origin (`git push origin <reconcile>:<pr-branch>`),
  or open a fresh PR.
- Verify `mergeable: MERGEABLE` + required checks (esp. `leak-check`) green.
- Merge. Clean up the worktree (`git worktree remove`).

---

## Domain 2 — Tree reconciliation (a divergent main/HEAD tree)

The branch case reconciles a *feature branch* against `origin/main`. The **tree**
case reconciles the tree you are standing in when it is **both** behind
`origin/main` **and** ahead with local commits, some squash-superseded. This is the
shape of a session (or daemon) committing to a shared main tree that another
session has since moved, then squash-superseding. The naive fixes — `git reset
--hard` (loses real local commits) or `git merge` (keeps the superseded
duplicates) — are both wrong.

The mechanical floor is `classify_tree()` (same module). It reports `ahead` /
`behind`, splits the ahead commits into a **superseded prefix** (safe to drop) and
a **keep tail** (real work that must survive), and recommends the reconcile:

- **TREE-CLEAN** — 0 ahead, 0 behind → nothing to do.
- **TREE-FAST-FORWARD** — 0 ahead, >0 behind → `git merge --ff-only origin/main`.
- **TREE-DROP-SUPERSEDED** — every ahead commit superseded → `git reset --hard
  origin/main` (after falsify-self).
- **TREE-REBASE-KEEP** — superseded prefix + real keep tail →
  `git rebase --onto origin/main <last-superseded>`.
- **TREE-DIVERGENT** — the keep tail touches a file `origin/main` also changed,
  OR git gave no merge-tree signal (too old for `--write-tree`) → port manually;
  no blind rebase, never drop on silence.

```bash
git fetch origin main
PYTHONPATH=$HOME/.grip $HOME/.claude/venv/bin/python -c "
from lib.git_reconcile import classify_tree
t = classify_tree('HEAD', 'origin/main')
print(t.strategy); print('ahead/behind', t.ahead, t.behind)
print('superseded (drop):', t.superseded); print('keep (preserve):', t.keep)
print(t.rationale)
"
```

Protocol:

1. **Pre-register** — hypothesis: "the ahead commits split into a squash-superseded
   prefix and a real keep tail; dropping the prefix and rebasing the tail loses
   nothing." Falsified if any keep-tail content is absent after the rebase.
2. **Detect** — `git fetch origin main`; run `classify_tree('HEAD', 'origin/main')`.
3. **Safety (live daemon + uncommitted work)** — the tree may be a **live daemon's
   home** (launchd, wire_proxy, server) and hold **another session's uncommitted
   work**. Before rebasing: list owners (`pgrep -fl`, `git worktree list`, presence
   mesh); confirm the daemons hold no target file open (`lsof` on `lib/*.py` /
   `config/*` — code is loaded in memory, so a clean lsof means the rebase is safe);
   and **stash** any uncommitted file you do not own
   (`git stash push -m "…" -- <path>`). The floor backs this up **mechanically**:
   `classify_tree()` reports `dirty` (uncommitted tracked + untracked work from
   `git status --porcelain`) and **downgrades the drop path to TREE-DIVERGENT**
   when the tree is dirty — so a blind `reset --hard` over another session's
   uncommitted work is refused at the classifier, not left to the agent to
   remember. (`lsof` alone never catches a parallel *session*, which edits
   transiently and holds no file open — the dirty guard is what closes that gap.)
4. **Reconcile** — `git rebase --onto origin/main <last-superseded>` (drops the
   prefix, replays the keep tail), or `reset --hard` when nothing to keep.
5. **Falsify-self** — `git stash pop`; verify (a) each dropped commit was truly
   superseded (merge-tree no-op or conflict-only residual), (b) the keep tail
   survives (`git show --stat HEAD` file count unchanged), (c)
   `git rev-list --left-right --count HEAD...origin/main` reads `keep-count / 0`.
6. **Ship** — the tree is now level + the real keep tail ahead. **Push is a
   separate, flagged decision** — a shared main tree is high-stakes; do not push
   without explicit steer.

## Domain 3 — Worktree reconciliation (a set of linked worktrees)

A repo may have a main tree + N linked worktrees (`git worktree list`), each at a
different divergence state, some owned by live sessions/daemons, some holding
uncommitted work. Reconcile the **set** safely, per-worktree:

1. **Enumerate** — `git worktree list --porcelain` → paths + HEADs.
2. **Classify each** — `classify_tree()` per divergent tree, or `classify()` for a
   feature-branch worktree.
3. **Respect ownership** — a worktree with a live daemon or a parallel session's
   uncommitted work is NOT yours to mutate: broadcast intent (presence mesh
   `session_broadcast`), or skip it and flag it. **Never** mutate a tree owned by
   another live daemon — this rule is not optional, no matter how clean the
   classify verdict looks.
4. **Level the fast path** — a worktree that is merely *behind* (0 ahead) is
   handled by `bin/grip-tree-sync` (idempotent ff-leveling, autostash, dirty-tree
   heal) — not a divergence problem.
5. **Reconcile divergence** — a worktree that is *ahead* with superseded local
   commits is a Domain-2 tree problem: drop the superseded prefix, keep the tail.

The worktree dimension is a **composition**, not a new mechanical primitive:
`grip-tree-sync` for the behind-only fast path, `classify_tree()` for divergent
trees, and the concurrent-session / live-daemon safety rules for ownership.
Worktree reconciliation inherits the **same falsify-self protocol** as Domain 2
(pre-register the no-loss hypothesis, verify after) — it introduces no new
falsification criteria of its own.

The five `TREE-*` strategy strings (`TREE-CLEAN`, `TREE-FAST-FORWARD`,
`TREE-DROP-SUPERSEDED`, `TREE-REBASE-KEEP`, `TREE-DIVERGENT`) are a **distinct
namespace** from the four branch strategies (`SUPERSEDED`, `CLEAN`,
`MERGE-KEEP-OURS`, `DIVERGENT`) — a caller must never treat one as the other.

## Worked example (instance #1 — replay anchor)

2026-06-01, PR #3357. `feat/inbound-relay-hotfix` (full Mac-relay arc + per-sender
capability tiers) vs main's parallel #3353-3356 (leaner drainer + DNS + allowlist).
Both rewrote `lib/relay_dispatcher.py`, `lib/relay_queue_drain.py`,
`config/relay-listener-prompt.mac.md`, the launchd template, and the drain tests.

- **Classify** → MERGE-KEEP-OURS (branch was a symbol-superset of every conflict
  file; `git cherry` showed all 8 commits unique).
- **Falsify-self** → ran main's drain-test contract against the merged drainer:
  19/23 passed; the **4 failures were all intentional supersessions** (a teammate added
  as co-steerer; reply-into-chat routing) — both V>>-directed that session, not
  losses. The functional logic (GRIP-addressing detection, exactly-once,
  fail-closed resolver) all passed. My full relay suite: 93/93 on the merged tree.
- **Insight (load-bearing)**: a "keep-ours" merge leaves the **live process's code
  byte-identical** → no daemon reload needed, and the dirty-tree-blocks-pull pain
  is sidestepped entirely when the live code is unchanged.
- H810 CONFIRMED. Merged clean, zero loss.

## Worked example (instance #2 — the squash-merge blind spot)

2026-06-01, hours after #3357 squash-merged. The operator asked GRIP to dogfood
`/git-solve` on its own residual. `classify` on the now-stale
`feat/inbound-relay-hotfix` returned **DIVERGENT** — "8 unique commits, 3 files
need manual inspection, blind --ours could drop logic." That verdict was
*misleading*: every one of the 8 commits was already on main via the squash
merges; the branch was **fully superseded, zero-loss-safe to drop**.

Root cause: `git cherry` is patch-id based, and a squash-merge rewrites the
branch's commits into one new patch — so the 8 originals still read as "unique."
GitHub squash-merge is the dominant agentic-engineering merge mode, so this
false-DIVERGENT is a *daily* occurrence for anyone whose agents open + squash PRs.

The dogfood **falsified two detector designs** before landing the fix:
- `git cherry` (the v0.1 classifier) → false-DIVERGENT (above).
- A naive "`git merge-tree` == main tree → superseded" fix → *false-negative*: a
  stale-but-superseded branch (main moved on after the squash) merges to
  `main + conflict-markers`, **not** a clean no-op — so it would mis-read as
  not-superseded.

The shipped fix (v0.2) is the **supersession probe**: a merge-tree NO-OP is the
definitive SUPERSEDED signal (squash-aware); when it is *not* a no-op, the
residual splits into `merge_conflicted` (stale overlap) vs `merge_clean_new`
(candidate real work). On the real branch the probe returned `clean_new == []`
with all three residual files in `conflicted` — *superseded-stale, no real work,
safe to abandon* — which falsify-self confirmed by reading the hunks (every one
was main's superior version). H811 CONFIRMED. The branch was left for the shared
6-session tree to drain (never force-switched).

**Meta-lesson (load-bearing): robust squash-supersession detection is not a single
git primitive** — cherry/`--merged` miss squash; merge-tree-no-op misses stale-
superset; multi-PR squashes defeat the synthetic-commit-cherry trick. The
mechanical floor gives *candidates* (no-op = certain; conflict-only residual =
strong hint); the **falsify-self content read stays the load-bearing arbiter**, as
this skill claimed from v0.1.

## Worked example (instance #3 — the divergent main tree)

2026-10-17, `~/.hal` main. The shared HAL tree had drifted to **2 ahead / 35
behind** `origin/main`, and its 88 "uncommitted" entries were actually 87
byte-identical untracked mirror files + one uncommitted `providers.yaml` edit from
another session. The two ahead commits were `e10f74b2` (reason-threading, since
squash-superseded by origin/main's `9ce76ad1`) and `dad48742` (a snapshot of the
139 untracked mirror files + a `.gitignore` edit).

- **Classify** → `classify_tree('dad48742', 'origin/main')` → **TREE-REBASE-KEEP**:
  `superseded=['e10f74b2']`, `keep=['dad48742']`, 2 ahead / 35 behind. The
  supersession walk marked `e10f74b2` conflict-only-residual (superseded-stale) and
  `dad48742` clean-new (139 files) — the exact boundary.
- **Safety** → 4 live HAL daemons (wire_proxy ×2, gateway, server) held no
  `lib/*.py` or `providers.yaml` open (code already loaded in memory) → rebasing
  the main tree was safe. The uncommitted `providers.yaml` (another session's
  `xai/grok-4.6` edit) was stashed, not overwritten.
- **Reconcile** → `git stash push -- config/providers.yaml`; `git rebase --onto
  origin/main e10f74b2`; `git stash pop`. Clean (no conflict — the snapshot does
  not touch the reason-threading files).
- **Falsify-self** → `rev-list --left-right --count` = **1 / 0** (was 2/35);
  `e10f74b2` gone from history; `HEAD^` == `origin/main`; snapshot file count
  intact (140 files); `providers.yaml` restored with `xai/grok-4.6` default.
- **Meta-lesson (load-bearing)** — the tree case is NOT "delete the branch": it is
  "drop exactly the squash-superseded prefix and keep the real tail," and it adds a
  live-daemon + uncommitted-work safety dimension the branch case never had. The
  `classify_tree()` floor reproduced the manual reconcile verbatim
  (`rebase --onto origin/main e10f74b2`) — the falsify-self of the floor itself.

## Falsification (when this skill is wrong)
- **[FALSIFIED 2026-10-17]** Speaker C's open concern — "live-daemon `lsof` +
  per-worktree ownership suffice to prevent overwriting another session's
  uncommitted files across worktrees" — is **TRUE for the literal cross-worktree
  case** (measured G1: `reset --hard`/`rebase` are worktree-scoped; G3:
  `git gc --prune=now` protects a sibling worktree's staged blobs) but **FALSE
  for the dominant same-tree case**: `git reset --hard` silently clobbers
  uncommitted tracked changes (measured G2 — no refusal, no warning), and `lsof`
  never sees a parallel *session* editing transiently. Fixed mechanically:
  `classify_tree()` now reports `dirty` and downgrades the drop path to
  TREE-DIVERGENT when the tree is dirty. Falsify *this* by finding a case where
  the drop path still runs over uncommitted work.
- If, over the next ≥3 reconciliations, any classifier verdict (branch or tree)
  is wrong (a real loss slips past falsify-self) → the symbol-superset heuristic is
  too coarse; tighten it (line/AST-level), don't trust it blindly.
- If DIVERGENT or TREE-DIVERGENT fires so often that the mechanical floor never
  helps → the classifier adds ceremony without value; drop to manual.
- If the worktree ceremony costs more than the conflicts it prevents on
  single-session repos → use it only when a parallel session is detected.

## Convergence, idempotency, and the Banach fixed point (the proof layer)

Reconciliation is not an ad-hoc merge — it is the GRIP convergence kernel with a
**proven** termination criterion, the same Banach fold as the OCF kernel
(`ℱ(folded) = folded`, H-FOLD). Formally:

```
converge(state = branch,
         criterion = at_fixed_point(branch, other),   # merge-tree NO-OP
         improve   = reconcile_one_pass,               # merge other in, resolve
         max_depth = N)
```

- **Metric.** `d(branch) = |residual(branch, other)|` — the count of files
  merging `other` in would still change (`merge_conflicted ∪ merge_clean_new`).
- **Contraction.** Each `reconcile_one_pass` `R` strictly shrinks the residual
  toward the unique fixed point: `d(R(x)) < d(x)` while `d(x) > 0`. (A pass that
  does not shrink `d` is a no-progress signal → escalate to manual, never loop.)
- **Fixed point = no-op = idempotency witness.** `d(x*) = 0  ⟺  merge-tree(other,
  x*) == tree(other)  ⟺  merging is a NO-OP`. That single no-op is simultaneously
  (a) the **convergence criterion** (you are AT the fixed point), (b) the
  **Banach uniqueness** witness (a contraction has exactly one fixed point —
  reconciliation cannot land anywhere else), and (c) the **idempotency proof**:
  re-applying reconcile changes nothing, `R(x*) = x*` and `R∘R = R` at x*.
- **Mechanically verified, never asserted.** `reconcile_fixed_point()` returns the
  proof; `is_idempotent()` runs the verdict twice and asserts the merged tree is
  identical (a pure, replayable function — same repo state → same OID). The no-op
  is checked against the real tree OIDs, not a status label (verify-canonical).

```python
from lib.git_reconcile import reconcile_fixed_point
p = reconcile_fixed_point('<branch>', 'origin/main')
# p.at_fixed_point  — True  ⟺ converged ⟺ idempotent ⟺ safe-to-absorb (no-op)
# p.residual_files  — d(branch); 0 exactly at the fixed point
# p.idempotent      — verdict is stable on re-run (pure/replayable, proven)
```

**Coordinated (across concurrent sessions).** The fixed point is a property of the
branch vs the *live* `origin/main`, which N parallel sessions are moving. So the
proof is only valid **at the moment of action**: `git fetch origin` immediately
before, re-run `reconcile_fixed_point`, and (≥2 live `claude`) broadcast intent
(grip-presence `session_broadcast`) + hold the branch lock before mutating a
shared ref. A no-op proven against a stale `origin/main` is not a no-op against
the current one — the concurrent-commit-guard pattern applies (re-verify HEAD at
action time). Idempotency makes this safe: re-running the proof is itself a no-op,
so re-verification is free and never double-applies.

## For agentic engineers (why this generalises beyond GRIP)

If you run AI coding agents (Claude Code, Cursor, Aider, …) you hit these git
shapes constantly, because agents open + squash-merge PRs at machine speed:

- **"Is this branch safe to delete?"** Your agent squash-merged a PR; the local
  branch shows "8 ahead" and `git branch --merged` says *not merged* (patch-id-
  blind to squash). The **SUPERSEDED** verdict answers it correctly via a
  merge-tree no-op — and that no-op is a *proof* (Banach fixed point), not a guess.
- **Parallel agents diverged on the same files.** Two sessions rewrote one module;
  the stale branch's PR goes CONFLICTING. The classifier says superset (resolve
  `--ours` + falsify-self) vs genuinely divergent (port by hand) — instead of a
  blind rebase that silently drops work.
- **A live daemon reads files in your working tree.** Reconcile in an isolated
  worktree so the running process is untouched (`grip-worktree.sh`, git-crypt-safe).

The transferable core: a **mechanical floor** (deterministic git classification +
a Banach no-op proof, zero LLM) + an **agent falsify-self** layer (does merging
lose any behaviour?) — never one without the other. Pure stdlib, git injected
(DIP), trivially portable + testable.

## Status / scope
v0.3 ships the classifier + the **supersession probe** + the **Banach
fixed-point / idempotency proof** (`lib/git_reconcile.py`, 18 Goodhart tests),
proven on #3357 (instance #1, reconcile) and the squash-supersession dogfood
(instance #2). **v0.4 adds the tree + worktree domains**: `classify_tree()`
(ahead/behind + a superseded-prefix vs keep-tail walk reusing the squash-aware
supersession probe + a 5-way strategy ladder), proven on the `~/.hal` divergent
main tree (instance #3) — 26 Goodhart tests total. The dirty-tree-sync sub-problem
(runtime-churn files like a live queue blocking `checkout`/`pull`) stays partially
addressed — promote to a mechanical helper at N=2 per
`rules/knowledge-maturation-functor.md`. Generic extension to non-git workflows:
still deferred until a second operational *domain* appears (YAGNI — all instances
so far are git, so the "RSI-on-own-workflow" abstraction is not yet earned; the
Banach/OCF fold IS the cross-domain bridge when it is).
