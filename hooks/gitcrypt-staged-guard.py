#!/usr/bin/env python3
"""PreToolUse hook: DENY a git commit that would push git-crypt PLAINTEXT to origin.

The load-bearing half of the issue #4161 pair (canary:
hooks/gitcrypt-filter-canary.py). On 2026-07-25 the shared `.git/config` of
the main GRIP checkout lost its `[filter "git-crypt"]` sections for ~25h.
In that state nothing runs the clean filter, so any commit rewriting an
encrypted-set file from plaintext stages PLAINTEXT to origin — on a repo
with a client-org mirror. This gate puts the control where the damage
happens (the staged bytes), not in the config that failed:

  When a Bash `git commit` is about to run in a repo whose `.gitattributes`
  declares `filter=git-crypt` entries, every staged blob on a
  git-crypt-attributed path MUST begin with the git-crypt header
  `\\x00GITCRYPT\\x00` (lib/gitcrypt_preflight.GITCRYPT_MAGIC — DRY, single
  definition). A staged blob without the header is plaintext about to leave
  the machine: DENY, plain-English reason naming the paths and the fix.

  Supplementary `-a`/`--all` closure: `git commit -a` stages working-tree
  content AFTER this hook runs, so the index check alone cannot see it.
  When the command carries a stage-all flag AND a git-crypt-attributed file
  is modified-unstaged AND that filter's `clean` config is absent, the
  commit itself would stage plaintext — DENY the same way. (With the clean
  config live, -a encrypts on staging and is allowed.)

Fail directions (deliberate, per the task contract):
  - Fail-OPEN on infrastructure errors — git absent, not a repo, check-attr
    or cat-file failing, stdin unparseable. A guard bug must never brick
    commits.
  - Fail-CLOSED on the detected-plaintext case — a readable staged blob on
    an attributed path without the header is denied, always.

Escape hatch (rules/gate-resolution-escape-hatch.md Invariant 1): this gate
denies only commands containing a `git ... commit` shape. Every resolution
step it names — `git config` filter restore, `git restore --staged`,
`git add` through a working clean filter — is NOT a commit and therefore
NOT in the denied set. The gate cannot deadlock its own remedy.

I/O contract mirrors hooks/guardrail-evasion-gate.py: stdin JSON payload,
deny via lib.hook_response.pretool_decision, audit append to
logs/gitcrypt-staged-guard.jsonl.

No git-crypt binary required: only `git` plumbing + the 10-byte header check.
Refs: issue #4161; memory
feedback_gitcrypt_filter_config_vanished_half_unlock.md.
Tests: tests/test_gitcrypt_guards.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CLAUDE_DIR))

from lib.gitcrypt_preflight import GITCRYPT_MAGIC  # noqa: E402
from lib.gitcrypt_recipe import restore_commands  # noqa: E402
from lib.hook_response import emit, pretool_decision  # noqa: E402

_DEFAULT_AUDIT_LOG = _CLAUDE_DIR / "logs" / "gitcrypt-staged-guard.jsonl"
# `commit` must be the git SUBCOMMAND, not merely a word in the command:
# `git`, then any run of global options — each `-`/`--` token optionally
# followed by ONE separate non-dash argument (`-C <path>`, `-c k=v`,
# `--git-dir <p>`; `=`-joined forms are a single token) — then the literal
# token `commit` ending at whitespace/end/shell-separator. So
# `git commit-tree`, `git commit-graph` and `git log --grep=commit` no
# longer match, while `rtk git commit` (unanchored search) and
# `git -C <path> commit` still do. A quoted global-option argument
# containing spaces is a known fail-open miss (defence in depth: the
# SessionStart canary + gitcrypt_preflight cover that state).
_COMMIT_RE = re.compile(
    r"\bgit"
    r"(?:\s+--?[^\s|&;]+(?:\s+[^-\s|&;][^\s|&;]*)?)*"
    r"\s+commit(?=$|[\s|&;])"
)
_REPO_FLAG_RE = re.compile(r"\bgit\s+-C\s+(\S+)")
# Short-cluster containing 'a' (-a, -am, -qa), or --all / --include. Long
# flags such as --amend never match (the leading '-' of '--' breaks the
# letter-cluster alternation and the alternation list is explicit).
_STAGE_ALL_RE = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*a[a-zA-Z]*|--all|--include)(?=\s|$)")
_GIT_TIMEOUT = 5  # seconds per git call


def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _git_bytes(repo: Path, *args: str) -> tuple[int | None, bytes]:
    """Run one git command; (returncode, stdout bytes). (None, b'') on
    infrastructure failure (git absent, timeout, unusable cwd)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, b""
    return proc.returncode, proc.stdout


def _repo_dir(command: str) -> Path:
    """The repo a `git commit` targets: an explicit `git -C <path>`, else cwd."""
    match = _REPO_FLAG_RE.search(command)
    if match:
        return Path(os.path.expanduser(match.group(1).strip("\"'")))
    return Path.cwd()


def _declares_gitcrypt(top: Path) -> bool:
    """Fast path: does this repo's .gitattributes declare git-crypt filters?"""
    try:
        text = (top / ".gitattributes").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "filter=git-crypt" in text


def _commit_target(payload: dict) -> tuple[str, Path] | None:
    """(command, repo toplevel) when this is a Bash `git commit` inside a
    git-crypt-declaring repo; None (allow) for every other tool call."""
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name != "Bash":
        return None
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    command = str(tool_input.get("command", "") or "")
    if not _COMMIT_RE.search(command):
        return None
    rc, out = _git_bytes(_repo_dir(command), "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        return None  # not a git repo / git absent: infrastructure, fail open
    top = Path(out.decode("utf-8", "replace").strip())
    return (command, top) if _declares_gitcrypt(top) else None


def _z_paths(repo: Path, *args: str) -> list[str] | None:
    """NUL-separated path list from a git command; None on infra failure."""
    rc, out = _git_bytes(repo, *args)
    if rc != 0:
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _gitcrypt_attr_map(repo: Path, paths: list[str]) -> dict[str, str]:
    """path -> filter name, for paths whose filter attr is git-crypt-family."""
    if not paths:
        return {}
    rc, out = _git_bytes(repo, "check-attr", "-z", "filter", "--", *paths)
    if rc != 0:
        return {}
    fields = out.decode("utf-8", "replace").split("\0")
    attr_map: dict[str, str] = {}
    for i in range(0, len(fields) - 2, 3):  # triplets: path, attr-name, value
        path, value = fields[i], fields[i + 2]
        if value == "git-crypt" or value.startswith("git-crypt-"):
            attr_map[path] = value
    return attr_map


def _staged_blob_head(repo: Path, path: str) -> bytes | None:
    """First len(GITCRYPT_MAGIC) bytes of the staged blob for `path`.
    None when the blob is unreadable (deletion oddity, submodule, git error)
    — an infrastructure miss, never treated as plaintext."""
    try:
        proc = subprocess.Popen(
            ["git", "cat-file", "blob", f":{path}"],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        head = proc.stdout.read(len(GITCRYPT_MAGIC)) if proc.stdout else b""
    finally:
        proc.kill()
        rc = proc.wait()
    if len(head) == len(GITCRYPT_MAGIC):
        return head  # full header-length read: judge it (rc is the kill, ignore)
    return head if rc == 0 else None  # short blob: only trust a clean exit


def _staged_plaintext(repo: Path, attr_map: dict[str, str]) -> list[str]:
    """Attributed staged paths whose blob does NOT begin with the header."""
    plaintext: list[str] = []
    for path in attr_map:
        head = _staged_blob_head(repo, path)
        if head is None:
            continue  # unreadable: infrastructure, fail open for this path
        if not head.startswith(GITCRYPT_MAGIC):
            plaintext.append(path)
    return plaintext


def _stage_all_risk(repo: Path, command: str) -> dict[str, str]:
    """Attributed modified-unstaged paths (-> their filter name) a -a/--all
    commit would stage WITHOUT a live clean filter (config gone =>
    plaintext would land)."""
    if not _STAGE_ALL_RE.search(command):
        return {}
    modified = _z_paths(repo, "diff", "--name-only", "-z", "--diff-filter=d")
    if not modified:
        return {}
    risky: dict[str, str] = {}
    for path, filter_name in _gitcrypt_attr_map(repo, modified).items():
        rc, out = _git_bytes(repo, "config", "--get", f"filter.{filter_name}.clean")
        if rc == 0 and out.strip():
            continue  # clean filter wired: -a stages through it, ciphertext lands
        risky[path] = filter_name
    return risky


def _resolution_text(filters: list[str]) -> str:
    """Restore steps for the SPECIFIC affected filter(s) — the exact config
    entries `git-crypt unlock` writes (shared source: lib/gitcrypt_recipe,
    DRY with the SessionStart canary). Named keys keep their --key-name."""
    restores = "; ".join(
        f"restore the {name} filter config: " + " && ".join(restore_commands(name))
        for name in (filters or ["git-crypt"])
    )
    return (
        "This is the .git/config filter breakage class from the 2026-07-25 "
        "incident (issue #4161): the clean filter is not running. RESOLUTION "
        "(none of these commands are blocked by this guard): "
        f"1) {restores}; "
        "2) unstage the plaintext: git restore --staged -- <path>; "
        "3) re-add through the now-working clean filter: git add -- <path>, "
        "then re-run the commit. Full recipe: memory "
        "feedback_gitcrypt_filter_config_vanished_half_unlock.md."
    )


def _deny_message(plaintext: list[str], risky: list[str], filters: list[str]) -> str:
    lines = [
        "GIT-CRYPT STAGED-PLAINTEXT GUARD: this commit would push UNENCRYPTED "
        "content to origin for file(s) this repo encrypts with git-crypt."
    ]
    if plaintext:
        lines.append(
            "Staged WITHOUT the git-crypt header (\\x00GITCRYPT\\x00), i.e. "
            "plaintext already in the index: " + ", ".join(sorted(plaintext)) + "."
        )
    if risky:
        lines.append(
            "The -a/--all flag would stage these modified encrypted-set files "
            "with NO clean filter configured (plaintext would land in the "
            "index): " + ", ".join(sorted(risky)) + "."
        )
    lines.append(_resolution_text(filters))
    return "\n".join(lines)


def _audit_log_path() -> Path:
    """Audit destination, resolved at CALL time (not import) so tests and
    deployments can redirect via env. $GITCRYPT_GUARD_LOG must be an
    absolute path — the hook's cwd varies per session, so honouring a
    relative override would scatter logs; such values fall back to the
    default (the audit write is fail-soft telemetry either way)."""
    override = os.environ.get("GITCRYPT_GUARD_LOG", "").strip()
    if override and Path(override).is_absolute():
        return Path(override)
    return _DEFAULT_AUDIT_LOG


def _audit(record: dict) -> None:
    try:
        log_path = _audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _emit_deny(reason: str) -> None:
    emit(pretool_decision("deny", reason))
    sys.exit(0)


def _deny_and_audit(
    top: Path, attr_map: dict[str, str], plaintext: list[str], risky_map: dict[str, str]
) -> None:
    """Audit the deny, then emit it with resolution steps for the SPECIFIC
    affected filter(s) (never the generic default-only recipe)."""
    risky = sorted(risky_map)
    filters = sorted({attr_map[p] for p in plaintext} | set(risky_map.values()))
    _audit({
        "decision": "deny",
        "tool": "Bash",
        "target": str(top),
        "signal": "staged-plaintext" if plaintext else "stage-all-no-clean-filter",
        "evidence": {"staged_plaintext": plaintext, "stage_all_risk": risky},
    })
    _emit_deny(_deny_message(plaintext, risky, filters))


def main() -> int:
    try:
        target = _commit_target(_read_stdin())
        if target is None:
            return 0
        command, top = target
        staged = _z_paths(
            top, "diff", "--cached", "--name-only", "-z", "--diff-filter=d"
        )
        if staged is None:
            return 0  # listing failed: infrastructure, fail open
        attr_map = _gitcrypt_attr_map(top, staged)
        plaintext = _staged_plaintext(top, attr_map)
        risky_map = _stage_all_risk(top, command)
        if plaintext or risky_map:
            _deny_and_audit(top, attr_map, plaintext, risky_map)
    except Exception:
        # Fail-open on infrastructure errors: a guard bug must never brick
        # commits. The fail-CLOSED branch above (detected plaintext) has
        # already emitted its deny and exited before reaching here.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
