"""Cross-session git divergence classifier — the mechanical floor for /git-solve.

GRIP applies its own convergence / RSI substrate to its operational git
workflows. This module is the deterministic *classification* step of that loop:
given a feature branch and a base ref (default ``origin/main``), it reports the
divergence shape + a recommended reconcile strategy, so the agent (the
``improve()`` function in the converge kernel) does NOT guess whether a branch
is a superset, subset, or genuinely divergent.

It is the mechanical floor only. The *judgment* step — deciding whether a
failing "contract" test from the other side is a REAL lost behaviour (port it)
or an INTENTIONAL supersession (keep ours, document why) — stays with the agent
(falsify-self). This module never merges, never pushes, never mutates anything;
it reads git and returns a verdict. Replayable: same repo state → same verdict.

First instance: 2026-06-01 — feat/inbound-relay-hotfix vs main #3353-3356
parallel divergence (PR #3357). Both branches rewrote the same relay files
independently; the classifier said MERGE-KEEP-OURS (branch was a symbol-superset
of every conflict file), and falsify-self confirmed the 4 contract failures were
all intentional supersessions, not losses.

Pure stdlib; git is injected (DIP) so tests run with zero real git / network.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

# A git runner takes argv (without the leading "git") and returns stdout.
GitRunner = Callable[[list[str]], str]

# Strategy verdicts (stable string contract — callers/tests match on these).
STRATEGY_CLEAN = "CLEAN"
STRATEGY_KEEP_OURS = "MERGE-KEEP-OURS"
STRATEGY_DIVERGENT = "DIVERGENT"
STRATEGY_SUPERSEDED = "SUPERSEDED"

# Symbol patterns by extension — "definitions" whose presence we compare to
# decide if one file version is a superset of another. Deliberately coarse: a
# missing top-level def/class/function is the signal that one side has logic the
# other lacks. Not a full parser (YAGNI) — a fast, language-agnostic-enough floor.
_SYMBOL_RES = {
    ".py": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE),
    ".ts": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|interface|type)\s+(\w+)", re.MULTILINE),
    ".js": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+(\w+)", re.MULTILINE),
}
_DEFAULT_SYMBOL_RE = re.compile(r"^\s*(?:def|class|function|const)\s+(\w+)", re.MULTILINE)


def _default_git(args: list[str]) -> str:
    """Run a real git command, returning stdout (empty string on failure)."""
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=60,
    )
    return proc.stdout


@dataclass(frozen=True)
class Divergence:
    """Verdict of classifying ``branch`` against ``other``.

    ``unique_commits``  — branch commits with NO cherry-equivalent on other.
    ``both_changed``    — files changed on BOTH sides since the merge-base
                          (the conflict candidates).
    ``only_other``      — files only the other side changed (land clean on merge).
    ``superset_files``  — of ``both_changed``, those where branch's version
                          contains every symbol the other's version has.
    ``non_superset``    — ``both_changed`` minus ``superset_files`` (need manual
                          inspection — branch may lack logic the other has).
    ``strategy``        — one of STRATEGY_CLEAN / KEEP_OURS / DIVERGENT /
                          SUPERSEDED.
    ``superseded``      — True iff merging branch into other is a NO-OP (branch
                          fully merged, incl. any squash-merge) → safe to delete.
    ``merge_conflicted``— residual files whose merged blob has conflict markers
                          (a STALE overlap → falsify-self other's side).
    ``merge_clean_new`` — residual files merged WITHOUT conflict markers
                          (candidate genuinely-unshipped work → verify).
    """
    base: str
    unique_commits: tuple[str, ...]
    both_changed: tuple[str, ...]
    only_other: tuple[str, ...]
    superset_files: tuple[str, ...]
    non_superset: tuple[str, ...]
    strategy: str
    rationale: str
    superseded: bool = False
    merge_conflicted: tuple[str, ...] = ()
    merge_clean_new: tuple[str, ...] = ()


def merge_base(branch: str, other: str, git: GitRunner = _default_git) -> str:
    """The common ancestor SHA of branch and other (empty if none)."""
    return git(["merge-base", branch, other]).strip()


def unique_commits(branch: str, other: str, git: GitRunner = _default_git) -> list[str]:
    """Branch commits with no equivalent on other (git cherry '+' lines).

    ``git cherry <other> <branch>`` prints '- <sha>' for commits whose patch is
    already on other, '+ <sha>' for those unique to branch. We return the '+'.
    """
    out = git(["cherry", other, branch])
    # Each line is "+ <sha> [message]" or "- <sha> [message]". Take the SHA
    # token only (index 1) so the parse is robust to plain `git cherry` AND
    # `git cherry -v` (which appends the commit subject).
    shas = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "+":
            shas.append(parts[1])
    return shas


def changed_files(a: str, b: str, git: GitRunner = _default_git) -> set[str]:
    """Files changed between ref a and ref b (name-only)."""
    out = git(["diff", "--name-only", a, b])
    return {line.strip() for line in out.splitlines() if line.strip()}


def _symbols(path: str, ref: str, git: GitRunner) -> set[str]:
    """Top-level symbol names in path at ref (empty if file absent there)."""
    content = git(["show", f"{ref}:{path}"])
    if not content:
        return set()
    ext = path[path.rfind("."):] if "." in path else ""
    pat = _SYMBOL_RES.get(ext, _DEFAULT_SYMBOL_RE)
    return set(pat.findall(content))


def is_symbol_superset(
    path: str, branch: str, other: str, git: GitRunner = _default_git,
) -> bool:
    """True iff branch's version of path has every symbol other's version has.

    The floor for "branch ⊇ other" on a conflict file: if other defines a
    function/class branch lacks, branch is NOT a superset and a blind --ours
    would drop that logic. (A superset at the symbol level still needs
    falsify-self to confirm no in-function behavioural loss — that is the
    agent's job, not this module's.)
    """
    other_syms = _symbols(path, other, git)
    branch_syms = _symbols(path, branch, git)
    return other_syms.issubset(branch_syms)


# Tree-OID shape: 40 hex (sha1) or 64 hex (sha256). Tells a real merge-tree
# result from an old-git error string (graceful degradation, no regression).
_TREE_OID_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def merged_tree(branch: str, other: str, git: GitRunner = _default_git) -> str:
    """OID of the tree from merging ``branch`` into ``other`` (squash-aware floor).

    ``git merge-tree --write-tree`` writes the merged tree's OID on the FIRST
    line of stdout in BOTH the clean and the conflicted case (conflict detail
    follows), so the first token is the tree either way. Returns "" when git is
    too old for ``--write-tree`` (the first line is then not a bare OID) → the
    caller degrades to the pre-supersession ladder with no regression.
    """
    out = git(["merge-tree", "--write-tree", other, branch]).strip()
    first = out.splitlines()[0].strip() if out else ""
    return first if _TREE_OID_RE.match(first) else ""


def tree_of(ref: str, git: GitRunner = _default_git) -> str:
    """The tree OID of ``ref`` (``ref^{tree}``)."""
    return git(["rev-parse", f"{ref}^{{tree}}"]).strip()


def _has_conflict_markers(tree: str, path: str, git: GitRunner) -> bool:
    """True if the blob at ``tree:path`` carries a merge conflict marker — the
    signature of a STALE overlap (branch behind the base on a region the base
    moved) rather than clean new logic."""
    return "<<<<<<<" in git(["show", f"{tree}:{path}"])


@dataclass(frozen=True)
class Supersession:
    """Result of the merge-tree supersession probe.

    ``kind`` is "clean" when merging branch into other is a NO-OP (merged tree
    == other's tree) — branch fully merged, INCLUDING any squash-merged PR,
    definitively safe to delete. Otherwise "residual", with the residual split
    into ``conflicted`` (stale overlap) and ``clean_new`` (candidate real work).
    """
    kind: str
    merged_tree: str
    conflicted: tuple[str, ...]
    clean_new: tuple[str, ...]


def supersession(
    branch: str, other: str = "origin/main", git: GitRunner = _default_git,
) -> Supersession:
    """Probe whether ``other`` already contains ALL of ``branch`` — squash-aware.

    ``git cherry`` / ``git branch --merged`` are patch-id based and therefore
    BLIND to squash merges (a squash rewrites the combined patch-id). A
    merge-tree NO-OP is the robust signal instead: if merging the branch into
    ``other`` reproduces ``other``'s exact tree, the branch adds nothing.

    When the merge is NOT a no-op the residual is split so falsify-self knows
    what to inspect:
      * ``conflicted`` — residual files whose merged blob carries conflict
        markers → a STALE overlap (the base moved under the branch); almost
        always supersession noise, not new behaviour.
      * ``clean_new``  — residual files changed WITHOUT conflict markers →
        candidate genuinely-unshipped work; the agent must verify it is not a
        loss before dropping the branch.
    """
    mt = merged_tree(branch, other, git)
    if not mt:
        return Supersession("residual", "", (), ())   # old git → no signal
    other_tree = tree_of(other, git)
    if mt == other_tree:
        return Supersession("clean", mt, (), ())
    residual = sorted(changed_files(other_tree, mt, git))
    conflicted = tuple(f for f in residual if _has_conflict_markers(mt, f, git))
    clean_new = tuple(f for f in residual if f not in set(conflicted))
    return Supersession("residual", mt, conflicted, clean_new)


@dataclass(frozen=True)
class FixedPointProof:
    """A Banach fixed-point / idempotency witness for branch↔other reconciliation.

    Reconciliation is the GRIP convergence kernel with a PROVEN criterion — the
    same Banach fold as the OCF kernel (``ℱ(folded) = folded``). The reconcile
    map ``R`` (merge ``other`` into ``branch``, resolve) contracts the divergence
    metric ``d(branch) = residual_files`` toward the UNIQUE fixed point ``x*``
    where ``d(x*) = 0``. At ``x*`` the merge is a NO-OP — ``merge-tree(other,
    branch)`` reproduces ``other``'s tree — which is simultaneously:

      (a) the CONVERGENCE criterion (the loop has reached the fixed point),
      (b) the BANACH uniqueness witness (a contraction has exactly one fixed
          point; reconciliation cannot converge anywhere else), and
      (c) the IDEMPOTENCY proof (``R(x*) = x*``; re-running changes nothing).

    ``proven`` is False when git gave no merge-tree signal (too old for
    ``--write-tree``); then no fixed-point claim is admissible and the caller
    degrades to the ``classify`` ladder. ``residual_files`` is -1 in that case
    (unknown), 0 at the fixed point, and the residual count off it.
    """
    at_fixed_point: bool
    proven: bool
    merged_tree: str
    other_tree: str
    residual_files: int
    idempotent: bool


def is_idempotent(branch: str, other: str = "origin/main",
                  git: GitRunner = _default_git) -> bool:
    """Mechanical idempotency proof: the merged-tree verdict is a PURE function of
    repo state, so running it twice yields the same tree OID (``ℱ∘ℱ = ℱ``). A
    nondeterministic git layer or an impure classifier breaks this — the proof
    then fails loudly rather than asserting an idempotency it cannot show. An
    empty OID (old git / no signal) is NOT idempotent-provable → False."""
    a = merged_tree(branch, other, git)
    b = merged_tree(branch, other, git)
    return bool(a) and a == b


def reconcile_fixed_point(branch: str, other: str = "origin/main",
                          git: GitRunner = _default_git) -> FixedPointProof:
    """Prove (or refute) that ``branch`` is at the reconciliation fixed point.

    ``at_fixed_point`` is the merge-tree NO-OP (``merged == other^{tree}``) — the
    Banach criterion — and ``residual_files`` is the divergence metric ``d``. By
    construction ``proven and at_fixed_point ⟺ residual_files == 0`` (criterion
    and metric agree; a regression test pins this). Coordinated use: run AFTER
    ``git fetch origin`` and re-confirm at action time, because N parallel
    sessions move ``other`` — the no-op holds only against the LIVE tree
    (verify-canonical). Idempotency makes the re-check free: it never
    double-applies.
    """
    sup = supersession(branch, other, git)
    other_tree = tree_of(other, git)
    proven = bool(sup.merged_tree)
    at_fp = sup.kind == "clean"
    if not proven:
        residual = -1                     # unknown — old git, no merge-tree signal
    elif at_fp:
        residual = 0
    else:
        residual = len(sup.conflicted) + len(sup.clean_new)
    return FixedPointProof(
        at_fixed_point=at_fp, proven=proven,
        merged_tree=sup.merged_tree, other_tree=other_tree,
        residual_files=residual, idempotent=is_idempotent(branch, other, git),
    )


def classify(
    branch: str, other: str = "origin/main", git: GitRunner = _default_git,
) -> Divergence:
    """Classify branch vs other → divergence shape + recommended strategy.

    Strategy ladder:
      * SUPERSEDED     — merging branch into other is a no-op (branch fully
                         merged, incl. any squash-merge) → safe to delete.
                         Checked FIRST; squash-aware where git cherry is not.
      * CLEAN          — no file changed on both sides → a merge/ff applies with
                         no conflict; just do it.
      * MERGE-KEEP-OURS— every both-changed file is a symbol-superset on branch →
                         merge other in, resolve --ours, then FALSIFY-SELF (run
                         other's test contract against the merged result) to
                         confirm no behavioural loss before shipping.
      * DIVERGENT      — ≥1 both-changed file where branch lacks symbols other has
                         → no blind --ours; inspect each conflict manually.
    """
    base = merge_base(branch, other, git)
    uniq = tuple(unique_commits(branch, other, git))
    branch_changed = changed_files(base, branch, git) if base else set()
    other_changed = changed_files(base, other, git) if base else set()
    both = tuple(sorted(branch_changed & other_changed))
    only_other = tuple(sorted(other_changed - branch_changed))
    superset = tuple(f for f in both if is_symbol_superset(f, branch, other, git))
    non_superset = tuple(f for f in both if f not in set(superset))

    # Supersession probe FIRST (squash-merge aware). The git-cherry count above
    # treats a branch's commits as "unique" even after a squash-merge already
    # landed their combined patch on `other` — so a stale, fully-merged branch
    # would mis-classify as DIVERGENT. A merge-tree NO-OP is the robust check.
    sup = supersession(branch, other, git)
    if sup.kind == "clean":
        return Divergence(
            base=base, unique_commits=uniq, both_changed=both,
            only_other=only_other, superset_files=superset,
            non_superset=non_superset, strategy=STRATEGY_SUPERSEDED,
            rationale=(
                f"branch is fully superseded by {other}: merging it in is a "
                f"no-op — every commit, INCLUDING any squash-merged PR, is "
                f"already present. Safe to delete: `git branch -D {branch}`. "
                f"(`git cherry` / `--merged` miss squash merges; the merge-tree "
                f"no-op does not.)"
            ),
            superseded=True, merge_conflicted=(), merge_clean_new=(),
        )

    # Not a clean no-op → existing symbol ladder, enriched with the merge
    # residual so falsify-self knows exactly what (if anything) the branch adds.
    residual_note = ""
    if sup.merged_tree:
        residual_note = (
            f" Merge residual — conflicted (stale overlap, falsify-self {other}'s "
            f"side): {list(sup.conflicted) or 'none'}; clean-new (verify not lost "
            f"work): {list(sup.clean_new) or 'none'}."
        )

    if not both:
        strategy = STRATEGY_CLEAN
        rationale = (
            f"{len(only_other)} file(s) changed only on {other}; none on both "
            f"sides — a merge/fast-forward applies with no conflict."
        )
    elif not non_superset:
        strategy = STRATEGY_KEEP_OURS
        rationale = (
            f"branch is a symbol-superset of all {len(both)} conflict file(s); "
            f"merge {other} in, resolve --ours, then falsify-self (run {other}'s "
            f"test contract against the merged tree) to confirm no behavioural loss."
        )
    else:
        strategy = STRATEGY_DIVERGENT
        rationale = (
            f"{len(non_superset)} of {len(both)} conflict file(s) lack a clear "
            f"superset ({', '.join(non_superset)}); inspect each manually — a "
            f"blind --ours could drop logic the other side has."
        ) + residual_note

    return Divergence(
        base=base, unique_commits=uniq, both_changed=both,
        only_other=only_other, superset_files=superset,
        non_superset=non_superset, strategy=strategy, rationale=rationale,
        superseded=False,
        merge_conflicted=sup.conflicted, merge_clean_new=sup.clean_new,
    )


# --- Tree reconciliation (divergent working TREE vs other) — 2026-10-17 #3 ---
#
# The branch case (classify) answers "is this FEATURE branch safe to delete /
# merge." The TREE case answers a different question: "how do I level a tree
# that is BOTH behind `other` AND ahead with a MIX of superseded + real local
# commits" — the shape of a session (or daemon) committing to a shared main tree
# that another session has since moved, then squash-superseding. Reconcile must
# drop EXACTLY the superseded commits and keep the real ones, without disturbing
# uncommitted work or a live daemon reading the tree.

# Tree strategy verdicts — a DISTINCT namespace from the branch ladder because
# the action space differs (rebase --onto / reset --hard / ff vs branch -D /
# merge --ours). Callers/tests match on these strings.
STRATEGY_TREE_CLEAN = "TREE-CLEAN"
STRATEGY_TREE_FAST_FORWARD = "TREE-FAST-FORWARD"
STRATEGY_TREE_DROP_SUPERSEDED = "TREE-DROP-SUPERSEDED"
STRATEGY_TREE_REBASE_KEEP = "TREE-REBASE-KEEP"
STRATEGY_TREE_DIVERGENT = "TREE-DIVERGENT"


@dataclass(frozen=True)
class TreeDivergence:
    """Verdict of classifying a divergent working TREE (``head``, default HEAD)
    against ``other`` (default origin/main).

    ``superseded`` — the longest PREFIX of ahead commits whose cumulative content
    is already on ``other`` (each is a merge-tree no-op, or its residual is
    conflict-marker-only with no clean-new files). Safe to drop.
    ``keep``       — the ahead commits AFTER that prefix: real local work that
    ``other`` lacks and must survive the reconcile.
    ``dirty``      — ``git status --porcelain --untracked-files=all`` lines of
    UNCOMMITTED working-tree work (tracked modifications + untracked files; the
    flag forces untracked detection even under ``status.showUntrackedFiles=no``).
    A non-empty ``dirty`` blocks the destructive paths: ``git reset --hard``
    discards the *tracked* modifications (untracked files warn of in-flight work,
    not a reset target), and ``git rebase`` refuses outright. The floor downgrades
    the drop path rather than recommend a reset over uncommitted work.
    """
    head: str
    other: str
    ahead: int
    behind: int
    superseded: tuple[str, ...]
    keep: tuple[str, ...]
    strategy: str
    rationale: str
    dirty: tuple[str, ...] = ()


def classify_tree(head: str = "HEAD", other: str = "origin/main",
                  git: GitRunner = _default_git) -> TreeDivergence:
    """Classify a divergent working TREE vs ``other`` → strategy.

    Strategy ladder (tree case):
      * TREE-CLEAN            — 0 ahead, 0 behind → nothing to do.
      * TREE-FAST-FORWARD     — 0 ahead, >0 behind → ``git merge --ff-only other``
                                (a plain level; nothing local to lose).
      * TREE-DROP-SUPERSEDED  — >0 ahead, ALL superseded → drop every local commit
                                (``git reset --hard other`` after falsify-self).
                                Only when the working tree is CLEAN; a dirty tree
                                downgrades to TREE-DIVERGENT (reset --hard would
                                silently discard uncommitted work).
      * TREE-REBASE-KEEP      — >0 ahead, superseded PREFIX + real keep TAIL →
                                ``git rebase --onto other <last-superseded>``
                                (drop the prefix, keep the tail).
      * TREE-DIVERGENT        — the keep tail touches a file ``other`` also
                                changed (the replay may conflict), OR git gave no
                                merge-tree signal (too old for `--write-tree`), OR
                                the tree is dirty and the drop path would silently
                                discard uncommitted work → inspect manually; never
                                drop on silence.

    The superseded/keep split reuses the squash-aware ``supersession()`` probe,
    walking ahead commits oldest→newest: for each, a merge-tree NO-OP *or* a
    conflict-only residual (no clean-new files) marks it superseded; the FIRST
    commit carrying clean-new work is the keep boundary. A superseded commit
    deeper in the line (on TOP of real work) is not detected this way — a known
    limit; the TREE-DIVERGENT residual split still surfaces it for falsify-self.
    """
    counts = git(["rev-list", "--left-right", "--count", f"{head}...{other}"]).split()
    ahead = int(counts[0]) if len(counts) >= 1 else 0
    behind = int(counts[1]) if len(counts) >= 2 else 0

    ahead_shas = git(["rev-list", "--reverse", f"{other}..{head}"]).split()

    superseded: list[str] = []
    degraded = False
    for sha in ahead_shas:
        sup = supersession(sha, other, git)
        if not sup.merged_tree:          # old git / no merge-tree signal
            degraded = True              # fail loud — never classify on silence
            break
        if sup.kind == "clean" or not sup.clean_new:
            superseded.append(sha)
        else:
            break
    keep = tuple(ahead_shas[len(superseded):])

    dirty = tuple(line for line in git(["status", "--porcelain", "--untracked-files=all"]).splitlines()
                  if line.strip())

    if degraded:
        strategy = STRATEGY_TREE_DIVERGENT
        rationale = (
            f"no merge-tree signal for {head} (git too old for `--write-tree`, or "
            f"merge-tree failed) — cannot split superseded vs keep; inspect manually. "
            f"Never drop on silence."
        )
    elif ahead == 0 and behind == 0:
        strategy = STRATEGY_TREE_CLEAN
        rationale = f"{head} is already level with {other} (0 ahead, 0 behind)."
    elif ahead == 0:
        strategy = STRATEGY_TREE_FAST_FORWARD
        rationale = (
            f"{head} is {behind} behind {other} with no local commits — a plain "
            f"fast-forward applies: `git merge --ff-only {other}`."
        )
    elif not keep:
        if dirty:
            strategy = STRATEGY_TREE_DIVERGENT
            rationale = (
                f"all {ahead} local commit(s) are superseded by {other}, BUT the "
                f"working tree is dirty ({len(dirty)} line(s), e.g. {dirty[0]}): "
                f"`git reset --hard` would discard the tracked modifications. "
                f"Stash or own them first — never reset over uncommitted work."
            )
        else:
            strategy = STRATEGY_TREE_DROP_SUPERSEDED
            rationale = (
                f"all {ahead} local commit(s) are superseded by {other} (squash-aware: "
                f"merge-tree no-op or conflict-only residual) — safe to drop: "
                f"`git reset --hard {other}` (after falsify-self)."
            )
    else:
        drop_through = superseded[-1] if superseded else merge_base(head, other, git)
        base = merge_base(head, other, git)
        other_changed = changed_files(base, other, git) if base else set()
        keep_changed: set[str] = set()
        for sha in keep:
            parent = git(["rev-parse", f"{sha}^"]).strip()
            keep_changed |= changed_files(parent, sha, git)
        overlap = keep_changed & other_changed
        if overlap:
            strategy = STRATEGY_TREE_DIVERGENT
            rationale = (
                f"keep commits touch {sorted(overlap)} which {other} also changed "
                f"— the replay may conflict; port manually rather than blind-rebase."
            )
        else:
            strategy = STRATEGY_TREE_REBASE_KEEP
            rationale = (
                f"drop superseded prefix {superseded or 'none'} and keep "
                f"{list(keep)}: `git rebase --onto {other} {drop_through}`."
            )
            if dirty:
                rationale += (
                    f" NOTE: {len(dirty)} uncommitted change(s) present — stash "
                    f"them first (`git rebase` refuses on a dirty tree)."
                )
    return TreeDivergence(
        head=head, other=other, ahead=ahead, behind=behind,
        superseded=tuple(superseded), keep=keep,
        strategy=strategy, rationale=rationale, dirty=dirty,
    )
