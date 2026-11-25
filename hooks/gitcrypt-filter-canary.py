#!/usr/bin/env python3
"""SessionStart hook: git-crypt filter-config canary (issue #4161).

Why this exists (2026-07-25 incident, ~25h silent): the shared `.git/config`
of the main GRIP checkout lost its entire `[filter "git-crypt"]` and
`[diff "git-crypt"]` sections while the decryption key remained present at
`.git/git-crypt/keys/default` — the signature of a half-completed
`git-crypt unlock`. Every encrypted file then sat as raw `\\x00GITCRYPT\\x00`
ciphertext in the working tree, session preloads ingested ciphertext garbage,
and any commit rewriting an encrypted file from plaintext would have staged
PLAINTEXT to origin. Nothing asserted the filter wiring, so nothing failed
loudly. This canary is that assertion, at the earliest surface: session start.

Detection (the half-unlock signature, per-filter):
  1. The repo's `.gitattributes` declares `filter=git-crypt` (or a named
     `filter=git-crypt-<key>`) entry, AND
  2. the matching key material IS present under
     `<git-common-dir>/git-crypt/keys/` (so this host has unlocked before —
     a never-unlocked clone without the key is a legitimate state, e.g. CI,
     and must NOT cry wolf every session), AND
  3. `git config --get filter.<name>.smudge` resolves EMPTY.

When all three hold, emit a LOUD plain-English warning into session context
naming the exact recovery recipe. Emit-only: this canary never blocks
anything (gate-resolution-escape-hatch — a canary that blocked session start
could block its own fix).

Fail-soft: any internal error exits 0 silently — a canary must never break
session start. Fast: pure local `git` subprocess calls, no network.

Sibling (the load-bearing half): hooks/gitcrypt-staged-guard.py DENIES the
actual plaintext commit regardless of how the config broke.

Refs: issue #4161; memory
feedback_gitcrypt_filter_config_vanished_half_unlock.md;
lib/gitcrypt_preflight.py (ciphertext-on-disk detection, the same class).
Tests: tests/test_gitcrypt_guards.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_CLAUDE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CLAUDE_DIR))

try:
    # Shared with hooks/gitcrypt-staged-guard.py (DRY, issue #4163).
    from lib.gitcrypt_recipe import key_name_for, restore_commands  # noqa: E402
except Exception:  # noqa: BLE001 — a canary must never break session start
    sys.exit(0)

# Exactly "git-crypt" (default key) or "git-crypt-<key-name>" (named key).
_FILTER_RE = re.compile(r"(?:^|\s)filter=(git-crypt(?:-[A-Za-z0-9_-]+)?)(?=\s|$)")
_GIT_TIMEOUT = 3  # seconds per git call — a canary must stay fast


def _git(cwd: Path, *args: str) -> str:
    """Run one git command; return stripped stdout, or '' on ANY failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def declared_gitcrypt_filters(gitattributes_text: str) -> list[str]:
    """Distinct git-crypt-family filter names declared in .gitattributes."""
    active = "\n".join(
        line
        for line in gitattributes_text.splitlines()
        if not line.lstrip().startswith("#")
    )
    return list(dict.fromkeys(m.group(1) for m in _FILTER_RE.finditer(active)))


def broken_filters(top: Path, common_git_dir: Path) -> list[str]:
    """Filter names declared in .gitattributes whose key is present locally
    but whose smudge config is gone — the half-unlock signature."""
    try:
        text = (top / ".gitattributes").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    broken: list[str] = []
    for name in declared_gitcrypt_filters(text):
        key_dir = common_git_dir / "git-crypt" / "keys" / key_name_for(name)
        if not key_dir.exists():
            continue  # never unlocked on this host: legitimate, stay silent
        if not _git(top, "config", "--get", f"filter.{name}.smudge"):
            broken.append(name)
    return broken


def _recipe_for(name: str) -> str:
    """Indented restore block for one filter (shared source:
    lib/gitcrypt_recipe.restore_commands — DRY with the staged guard)."""
    return "\n".join(f"    {cmd}" for cmd in restore_commands(name))


def warning_message(top: Path, broken: list[str]) -> str:
    """LOUD plain-English warning + the exact recovery recipe."""
    names = ", ".join(broken)
    recipes = "\n".join(_recipe_for(n) for n in broken)
    return (
        f"[GITCRYPT FILTER CANARY — ACT BEFORE COMMITTING] The repo at {top} "
        f"declares git-crypt encryption in .gitattributes and the key material "
        f"is present, but the git config filter wiring for [{names}] is GONE "
        f"(git config --get filter.<name>.smudge is empty). This is the "
        f"half-completed-unlock state from the 2026-07-25 incident (issue "
        f"#4161): encrypted files sit as raw \\x00GITCRYPT\\x00 ciphertext in "
        f"the working tree, session reads ingest garbage, and a commit that "
        f"rewrites an encrypted file from plaintext would stage PLAINTEXT to "
        f"origin.\n"
        f"RECOVERY RECIPE (run inside {top}):\n"
        f"  1. Restore the filter config git-crypt unlock writes:\n"
        f"{recipes}\n"
        f"  2. Header-guarded restore of the ciphertext working files: for each "
        f"tracked file whose on-disk bytes begin with \\x00GITCRYPT\\x00, "
        f"delete it and run `git checkout -- <path>` so the restored smudge "
        f"filter re-materialises the plaintext. Files WITHOUT that header may "
        f"hold local edits — skip and inspect those by hand.\n"
        f"  3. Verify: `git-crypt status` reports no warnings and "
        f"`git config --get filter.{broken[0]}.smudge` is non-empty.\n"
        f"Refs: issue #4161; memory "
        f"feedback_gitcrypt_filter_config_vanished_half_unlock.md. The "
        f"staged-plaintext commit gate (hooks/gitcrypt-staged-guard.py) is the "
        f"backstop either way."
    )


def main() -> int:
    cwd = Path.cwd()
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return 0  # not a git repo: nothing to canary
    top_path = Path(top)
    common = _git(cwd, "rev-parse", "--git-common-dir")
    common_path = Path(common) if common else top_path / ".git"
    if not common_path.is_absolute():
        common_path = (cwd / common_path).resolve()
    broken = broken_filters(top_path, common_path)
    if broken:
        print(warning_message(top_path, broken))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — a canary must never block session start
        sys.exit(0)
