"""Shared git-crypt filter-restore recipe (single DRY source, issue #4163).

The four `git config` entries `git-crypt unlock` writes for a filter are
needed by BOTH #4161 guards: the SessionStart canary
(hooks/gitcrypt-filter-canary.py) warns with them, and the staged-plaintext
commit gate (hooks/gitcrypt-staged-guard.py) names them in its deny message
so the operator can repair the SPECIFIC broken filter(s). Keeping the
command shapes here means a named key (filter `git-crypt-<key>`) always
carries its `--key-name=<key>` in every emitted command, and the two hooks
can never drift apart.

Zero third-party dependencies; stdlib-only; no I/O.
Tests: tests/test_gitcrypt_guards.py
"""

from __future__ import annotations


def key_name_for(filter_name: str) -> str:
    """git-crypt's key-dir name for a filter: 'git-crypt' -> 'default',
    'git-crypt-<key>' -> '<key>'."""
    if filter_name == "git-crypt":
        return "default"
    return filter_name[len("git-crypt-"):]


def restore_commands(filter_name: str) -> list[str]:
    """The four config entries `git-crypt unlock` writes, as restore
    commands for ONE filter. A named key carries `--key-name=<key>` exactly
    as `git-crypt unlock` writes it (cf. lib/git-crypt-smudge.sh)."""
    key = key_name_for(filter_name)
    suffix = "" if filter_name == "git-crypt" else f" --key-name={key}"
    return [
        f"git config filter.{filter_name}.smudge '\"git-crypt\" smudge{suffix}'",
        f"git config filter.{filter_name}.clean '\"git-crypt\" clean{suffix}'",
        f"git config filter.{filter_name}.required true",
        f"git config diff.{filter_name}.textconv '\"git-crypt\" diff{suffix}'",
    ]
