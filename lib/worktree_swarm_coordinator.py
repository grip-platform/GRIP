"""Worktree-isolated parallel agent swarm coordinator (runtime).

Completes the missing half of ADR 0008: the schema layer lives in
``worktree_swarm.py`` (manifest, validation, shape selection); this module
performs the actual fan-out — creates worktrees, dispatches agents,
collects results, rebases, and merges.

Security + integrity contract (closes AGORA #3764 blockers)
-----------------------------------------------------------

* **Atom goals are agent prompts, never shell strings.** Each goal is handed
  to the agent dispatcher (``claude -p <goal>``) as a single argv element with
  ``shell=False`` — it is DATA the agent reads, never code a shell interprets.
  A goal of ``"; rm -rf /; echo"`` is therefore an (ineffective) prompt, not a
  command (ADR 0008 C5: compose agent dispatch, do not re-shell-out).
* **Atom ids are validated before they touch the filesystem.** ``atom.id`` is
  pattern-checked AND the resolved worktree path is asserted to stay inside the
  worktree base, so a manifest with ``id="../../etc"`` cannot path-traverse
  (fail-safe: an unrecognised id degrades to a hard error, never a wider path).
* **Worktrees are cleaned up and never silently reused.** ``_dispatch_shape_a``
  wraps each atom in ``try/finally`` so the worktree is removed even on failure;
  a pre-existing worktree is removed before re-creation rather than reused; a
  removal failure is surfaced on stderr, never swallowed.
* **Branches are actually integrated.** ``_dispatch_shape_a`` rebases each ``ok``
  atom's branch onto the target (retrying up to ``_MAX_MERGE_RETRIES``) and
  fast-forward-merges it — the "merge" the module name promises is performed,
  not declared. Merge/rebase run with ``cwd`` pinned to the repo root.

The merge step is fast-forward-only (non-destructive: it fails closed rather
than rewriting history) and assumes the coordinator is the single writer of the
target branch for the duration of the swarm.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from lib import throttle_flag
from lib.worktree_swarm import (
    Shape,
    WorkstreamManifest,
    WorkstreamAtom,
    validate_disjoint_ownership,
    select_shape,
)

__all__ = (
    "CoordinatorResult",
    "AtomResult",
    "WorktreeSwarmCoordinator",
    "run_coordinator_from_manifest",
    "main",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKTREE_BASE = _REPO_ROOT / "worktrees"
_WORKTREE_SCRIPT = _REPO_ROOT / "lib" / "grip-worktree.sh"
_MAX_MERGE_RETRIES = 3
_ATOM_TIMEOUT_SEC = 600
# Branch names allow path separators (feat/<sprint>/<atom>); atom ids used as a
# filesystem directory component do NOT — they must be a single safe segment.
_BRANCH_PATTERN = re.compile(r"^[a-zA-Z0-9_./-]+$")
_ATOM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
# Default agent-dispatch binary. Injectable via the constructor for tests so no
# real agent is ever spawned under unit test.
_AGENT_CMD = "claude"

RunCmd = Callable[..., "subprocess.CompletedProcess[str]"]


def _throttled(agent_cmd: str) -> str:
    """The throttle flag's line when the agent is Claude and the flag is set, else "". A headless GRIP-started
    Claude run refuses to start while the flag is set (V>>'s decision of 2026-10-29); another agent spends no
    Claude limit and is not refused, as the relay leaves its opencode harness alone."""
    if Path(agent_cmd).name.startswith("claude") and throttle_flag.is_set():
        return throttle_flag.describe()
    return ""


@dataclass
class AtomResult:
    """Outcome of one swarm atom."""

    atom_id: str
    status: Literal["ok", "fail", "skipped"]
    branch: str | None = None
    worktree_path: str | None = None
    exit_code: int | None = None
    error: str | None = None
    duration_sec: float | None = None
    merged: bool = False


@dataclass
class CoordinatorResult:
    """Aggregate outcome of a swarm dispatch."""

    sprint_id: str
    shape: Shape
    atoms: list[AtomResult]
    overall: Literal["ok", "partial", "fail"]
    duration_sec: float
    error: str = ""  # why the dispatch never started, such as the throttle flag's line; empty when it ran


def _branch_name(sprint_id: str, atom_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", atom_id)
    return f"feat/{sprint_id}/{safe}"


def _run_cmd(
    cmd: list[str],
    timeout: int = 120,
    shell: bool = False,
    cwd: str | None = None,
) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell, cwd=cwd)


def _overall(ok: int, fail: int) -> Literal["ok", "partial", "fail"]:
    if fail == 0:
        return "ok"
    return "partial" if ok > 0 else "fail"


class WorktreeSwarmCoordinator:
    def __init__(
        self,
        manifest: WorkstreamManifest,
        target_branch: str = "main",
        worktree_base: Path = _WORKTREE_BASE,
        repo_root: Path = _REPO_ROOT,
        grip_worktree_script: Path = _WORKTREE_SCRIPT,
        run_cmd: RunCmd = _run_cmd,
        agent_cmd: str = _AGENT_CMD,
    ) -> None:
        validate_disjoint_ownership(manifest)
        self.manifest = manifest
        self.target_branch = target_branch
        self.worktree_base = worktree_base
        self.repo_root = repo_root
        self.grip_worktree_script = grip_worktree_script
        self._run_cmd = run_cmd
        self._agent_cmd = agent_cmd
        self.shape: Shape = select_shape(manifest)

    # -- safety helpers --

    def _validated_worktree_path(self, atom_id: str) -> Path:
        """Resolve the worktree dir for ``atom_id``, refusing path traversal.

        Two independent guards (fail-safe): the id must be a single safe
        segment, AND the resolved path must stay inside ``worktree_base``. An
        unrecognised id raises — it never widens to a path outside the base.
        """
        if not _ATOM_ID_PATTERN.match(atom_id):
            raise ValueError(
                f"unsafe atom id {atom_id!r}: must match {_ATOM_ID_PATTERN.pattern}"
            )
        base = self.worktree_base.resolve()
        wt = (self.worktree_base / atom_id).resolve()
        if base != wt and base not in wt.parents:
            raise ValueError(f"atom id {atom_id!r} escapes worktree base {base}")
        return wt

    # -- worktree lifecycle --

    def _ensure_worktree_base(self) -> None:
        self.worktree_base.mkdir(parents=True, exist_ok=True)

    def _create_worktree(self, atom: WorkstreamAtom) -> Path:
        branch = _branch_name(self.manifest.sprint_id, atom.id)
        if not _BRANCH_PATTERN.match(branch):
            raise ValueError(f"Invalid branch {branch!r}")
        wt_path = self._validated_worktree_path(atom.id)
        if wt_path.exists():
            # Never silently reuse a possibly-corrupt worktree from a crashed
            # prior run — remove it first so the agent starts from a clean tree.
            self._remove_worktree(atom)
        result = self._run_cmd([
            "bash", str(self.grip_worktree_script),
            "add", str(wt_path), branch,
            "--lock-mutex", "--session-id", f"swarm-{atom.id}",
        ])
        if result.returncode != 0:
            raise RuntimeError(f"worktree create failed: {result.stderr.strip()}")
        return wt_path

    def _remove_worktree(self, atom: WorkstreamAtom) -> bool:
        """Remove the atom's worktree. Returns False (and warns) on failure.

        The removal result is checked, not discarded — a failed removal is
        surfaced on stderr so a stale worktree cannot be silently handed to a
        later run.
        """
        wt_path = self._validated_worktree_path(atom.id)
        if not wt_path.exists():
            return True
        result = self._run_cmd(
            ["bash", str(self.grip_worktree_script), "remove", str(wt_path)],
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"[swarm] WARNING: worktree remove failed for {atom.id!r}: "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return True

    # -- agent dispatch (capability-safe: goal is argv data, never shell) --

    def _run_atom(self, goal: str, cwd: str | None) -> "subprocess.CompletedProcess[str]":
        prompt = goal.replace("$REPO_ROOT", str(self.repo_root))
        argv = [self._agent_cmd, "-p", prompt, "--permission-mode", "acceptEdits"]
        refusal = _throttled(self._agent_cmd)
        if refusal:  # every atom is a new headless start, so a flag raised mid-swarm stops the rest (Codex on GRIP#8861)
            return subprocess.CompletedProcess(argv, throttle_flag.THROTTLED_EXIT, stdout="", stderr=refusal)
        return self._run_cmd(argv, timeout=_ATOM_TIMEOUT_SEC, cwd=cwd)

    # -- dispatch shapes --

    def _dispatch_shape_c(self) -> CoordinatorResult:
        start = time.monotonic()
        return CoordinatorResult(
            sprint_id=self.manifest.sprint_id, shape="C", atoms=[],
            overall="ok", duration_sec=time.monotonic() - start,
        )

    def _dispatch_shape_b(self) -> CoordinatorResult:
        start = time.monotonic()
        atoms: list[AtomResult] = []
        for a in self.manifest.atoms:
            a_start = time.monotonic()
            proc = self._run_atom(a.goal, cwd=None)
            atoms.append(AtomResult(
                atom_id=a.id,
                status="ok" if proc.returncode == 0 else "fail",
                exit_code=proc.returncode,
                error=proc.stderr.strip() or None,
                duration_sec=time.monotonic() - a_start,
            ))
        ok = sum(1 for r in atoms if r.status == "ok")
        fail = sum(1 for r in atoms if r.status == "fail")
        return CoordinatorResult(
            sprint_id=self.manifest.sprint_id, shape="B", atoms=atoms,
            overall=_overall(ok, fail), duration_sec=time.monotonic() - start,
        )

    def _dispatch_one_atom_a(self, atom: WorkstreamAtom) -> AtomResult:
        wt_path: Path | None = None
        try:
            wt_path = self._create_worktree(atom)
            a_start = time.monotonic()
            proc = self._run_atom(atom.goal, cwd=str(wt_path))
            return AtomResult(
                atom_id=atom.id,
                status="ok" if proc.returncode == 0 else "fail",
                exit_code=proc.returncode,
                branch=_branch_name(self.manifest.sprint_id, atom.id),
                worktree_path=str(wt_path),
                error=proc.stderr.strip() or None,
                duration_sec=time.monotonic() - a_start,
            )
        except Exception as exc:
            return AtomResult(atom_id=atom.id, status="fail", error=str(exc))
        finally:
            if wt_path is not None:
                self._remove_worktree(atom)

    def _dispatch_shape_a(self) -> CoordinatorResult:
        start = time.monotonic()
        self._ensure_worktree_base()
        atom_results = [self._dispatch_one_atom_a(atom) for atom in self.manifest.atoms]
        self._integrate_branches(atom_results)
        ok = sum(1 for r in atom_results if r.status == "ok")
        fail = sum(1 for r in atom_results if r.status == "fail")
        return CoordinatorResult(
            sprint_id=self.manifest.sprint_id, shape="A", atoms=atom_results,
            overall=_overall(ok, fail), duration_sec=time.monotonic() - start,
        )

    # -- rebase + merge --

    def _rebase_branch(self, branch: str) -> str | None:
        """Rebase ``branch`` onto the target, retrying up to the merge cap.

        Returns the last error string on persistent failure, else None.
        """
        self._run_cmd(
            ["git", "fetch", "origin", self.target_branch],
            timeout=30, cwd=str(self.repo_root),
        )
        last_err: str | None = None
        for _ in range(_MAX_MERGE_RETRIES):
            rebase = self._run_cmd(
                ["git", "rebase", f"origin/{self.target_branch}", branch],
                timeout=60, cwd=str(self.repo_root),
            )
            if rebase.returncode == 0:
                return None
            last_err = rebase.stderr.strip()
            self._run_cmd(
                ["git", "rebase", "--abort"], timeout=15, cwd=str(self.repo_root),
            )
        return last_err

    def _merge_branch(self, branch: str) -> bool:
        self._run_cmd(
            ["git", "checkout", self.target_branch],
            timeout=15, cwd=str(self.repo_root),
        )
        merge = self._run_cmd(
            ["git", "merge", "--ff-only", branch],
            timeout=30, cwd=str(self.repo_root),
        )
        return merge.returncode == 0

    def _integrate_branches(self, results: list[AtomResult]) -> None:
        """Rebase + fast-forward-merge every successful atom's branch."""
        for r in results:
            if r.status != "ok" or not r.branch:
                continue
            rebase_err = self._rebase_branch(r.branch)
            if rebase_err is not None:
                r.error = f"rebase: {rebase_err}"
                continue
            r.merged = self._merge_branch(r.branch)

    # -- public entry point --

    def run(self) -> CoordinatorResult:
        if self.shape == "C":
            return self._dispatch_shape_c()
        refusal = _throttled(self._agent_cmd)
        if refusal:  # before any worktree is made or any agent spawned
            return CoordinatorResult(sprint_id=self.manifest.sprint_id, shape=self.shape, atoms=[],
                                     overall="fail", duration_sec=0.0, error=refusal)
        if self.shape == "B":
            return self._dispatch_shape_b()
        return self._dispatch_shape_a()


def run_coordinator_from_manifest(
    manifest: WorkstreamManifest,
    **kwargs: Any,
) -> CoordinatorResult:
    return WorktreeSwarmCoordinator(manifest, **kwargs).run()


def _parse_manifest_from_json(path: str | Path) -> WorkstreamManifest:
    data = json.loads(Path(path).read_text())
    atoms = tuple(
        WorkstreamAtom(
            id=a["id"], goal=a["goal"],
            owns_paths=tuple(a.get("owns_paths", [])),
            hypothesis_id=a.get("hypothesis_id"),
            useful_work_tokens=a.get("useful_work_tokens", 0),
        )
        for a in data["atoms"]
    )
    return WorkstreamManifest(sprint_id=data["sprint_id"], atoms=atoms)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 -m lib.worktree_swarm_coordinator <manifest.json>", file=sys.stderr)
        sys.exit(1)
    manifest = _parse_manifest_from_json(sys.argv[1])
    result = run_coordinator_from_manifest(manifest)
    print(json.dumps({
        "sprint_id": result.sprint_id,
        "shape": result.shape,
        "overall": result.overall,
        "error": result.error,
        "duration_sec": round(result.duration_sec, 2),
        "atoms": [{
            "atom_id": a.atom_id, "status": a.status,
            "branch": a.branch, "merged": a.merged, "error": a.error,
            "duration_sec": round(a.duration_sec, 2) if a.duration_sec else None,
        } for a in result.atoms],
    }, indent=2))


if __name__ == "__main__":
    main()
