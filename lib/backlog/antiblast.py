"""W0 anti-blast-radius gate TRIAD for the unified backlog system.

Three legs — ALL must pass; any single failure → DENY the entire run:

  (a) repo-allowlist   — writes only to the 3 authorised repos
  (b) mutation-ceiling — caps total cross-repo mutations per run
  (c) protected-path   — no Pages publish / .github/workflows write /
                         force-push; Pages publish DENY unless a valid
                         provenance marker with scope "pages-publish" exists

Design mirrors no-guardrail-evasion + yolo-mode:
  - Non-yolo-relaxable (PARAMOUNT, same class as force-push guard)
  - Fail-closed: any exception → DENY
  - Provenance read fresh each call (state-file, not env var)
  - Audit every DENY to logs/backlog-antiblast.jsonl

Usage:
    from lib.backlog.antiblast import check, DenyError

    try:
        check(repos=["grip-platform/HAL"], paths=[...], mutation_count=3)
    except DenyError as exc:
        # gate fired — abort the run; log exc.leg + exc.reason
        raise

Falsifier (H-BL-1): removing any one leg must cause at least one test in
test_backlog_antiblast_gate.py to fail (mutation-survival requirement).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Sequence
import os
import sys

from lib.backlog.provenance import read_marker

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
_AUDIT_LOG = _CLAUDE_DIR / "logs" / "backlog-antiblast.jsonl"

# ── (a) Authorised repos ────────────────────────────────────────────────────
AUTHORISED_REPOS: frozenset[str] = frozenset({
    "grip-platform/HAL",
    "grip-platform/GRIP",   # canonical GitHub name for ~/.claude
    "happi-lang/happi.md",   # HAPPI repo
})

# ── (b) Mutation ceiling ─────────────────────────────────────────────────────
DEFAULT_MUTATION_CEILING: int = 50  # cross-repo mutations per run

# ── (c) Protected path patterns ──────────────────────────────────────────────
_PROTECTED_PREFIXES: tuple[str, ...] = (
    ".github/workflows",
)
_PAGES_PATHS: tuple[str, ...] = (
    "docs/",          # GitHub Pages default source
    "_site/",
    "public/",
    "gh-pages/",
)


class DenyError(Exception):
    """Raised when any triad leg fires. Callers must NOT catch and continue."""

    def __init__(self, leg: str, reason: str) -> None:
        self.leg = leg
        self.reason = reason
        super().__init__(f"[backlog-antiblast/{leg}] {reason}")


def check(
    repos: Sequence[str],
    paths: Sequence[str],
    mutation_count: int,
    ceiling: int = DEFAULT_MUTATION_CEILING,
    is_force_push: bool = False,
) -> None:
    """Run the full TRIAD. Raises DenyError on first failing leg.

    Args:
        repos:          target repo slugs (e.g. ["grip-platform/HAL"])
        paths:          file paths being written (relative to repo root)
        mutation_count: total mutations this run across all repos
        ceiling:        override the default ceiling (tests use low values)
        is_force_push:  True when the operation includes a force-push
    """
    try:
        _check_repo_allowlist(repos)
        _check_mutation_ceiling(mutation_count, ceiling)
        _check_protected_paths(paths, is_force_push)
    except DenyError:
        raise
    except Exception as exc:
        # Fail-closed: unexpected errors are treated as DENY
        _audit("fail-closed", "unexpected", str(exc))
        raise DenyError("fail-closed", f"unexpected error: {exc}") from exc


# ── Leg implementations ───────────────────────────────────────────────────────

def _check_repo_allowlist(repos: Sequence[str]) -> None:
    """Leg (a): every target repo must be in AUTHORISED_REPOS."""
    for repo in repos:
        if repo not in AUTHORISED_REPOS:
            _audit("repo-allowlist", repo, "not in authorised set")
            raise DenyError(
                "repo-allowlist",
                f"repo '{repo}' not in authorised set {sorted(AUTHORISED_REPOS)}",
            )


def _check_mutation_ceiling(mutation_count: int, ceiling: int) -> None:
    """Leg (b): total mutations must not exceed ceiling."""
    if mutation_count > ceiling:
        _audit("mutation-ceiling", str(mutation_count), f"exceeds ceiling {ceiling}")
        raise DenyError(
            "mutation-ceiling",
            f"mutation_count={mutation_count} exceeds ceiling={ceiling}",
        )


def _check_protected_paths(paths: Sequence[str], is_force_push: bool) -> None:
    """Leg (c): block workflows writes, force-push, and unpermissioned Pages publish."""
    if is_force_push:
        _audit("protected-path", "force-push", "always denied")
        raise DenyError("protected-path", "force-push is always denied")

    for path in paths:
        norm = path.lstrip("/")

        # Block .github/workflows writes unconditionally
        if any(norm.startswith(p) for p in _PROTECTED_PREFIXES):
            _audit("protected-path", norm, "workflows write denied")
            raise DenyError(
                "protected-path",
                f"write to '{norm}' denied — .github/workflows modifications blocked",
            )

        # Pages paths require a valid provenance marker with scope "pages-publish"
        if any(norm.startswith(p) for p in _PAGES_PATHS):
            marker = read_marker()
            if marker is None or marker.get("scope") not in ("pages-publish", "any"):
                _audit("protected-path", norm, "Pages publish without approval marker")
                raise DenyError(
                    "protected-path",
                    f"write to Pages path '{norm}' denied — "
                    "set a provenance marker (scope='pages-publish') first",
                )


def _audit(leg: str, subject: str, detail: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "leg": leg,
            "subject": str(subject),
            "detail": detail,
        }
        with _AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        # NEVER silently swallow: a lost DENY-audit must be loud (a safety
        # gate that cannot log its own DENYs is failing invisibly). Do NOT
        # re-raise — the security DENY itself must still propagate even if
        # its audit write fails.
        print(
            f"[backlog-antiblast] AUDIT WRITE FAILED for leg={leg!r} "
            f"subject={str(subject)!r} ({exc!r}) — DENY still enforced, "
            f"but this deny is UNLOGGED at {_AUDIT_LOG}",
            file=sys.stderr,
        )
