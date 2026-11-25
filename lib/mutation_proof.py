"""Prove a test can fail, without hand-rolling the ritual every time.

``tests-must-be-able-to-fail.md`` mandate 3: a check that has never been observed
failing is an oracle, not a test. The way to observe it is to break the thing it
guards and watch the right test go red.

That ritual has six steps and I got two of them wrong in a single session while
performing it roughly twenty times by hand:

1. The anchor must be UNIQUE. ``str.replace(old, new, 1)`` silently edits the first
   of several matches, so the mutation lands somewhere other than intended while
   every downstream check still passes.
2. The mutation must be CONFIRMED APPLIED by re-reading the file. A mutation that
   never applied reports green, and the tell -- every mutant row identical to the
   control -- is easy to miss.
3. Confirming it must check the SPECIFIC REPLACEMENT, not a global substring.
   Searching the whole file for ``'"reproducible"'`` reported "did not apply" while
   three tests correctly failed, because that string also appears in a docstring.
   The check was coarser than the property it checked.
4. The EXPECTED test must be the one that fails. "Something went red" is compatible
   with having broken an unrelated thing.
5. The file must be restored, and the restoration verified byte-identically.
6. Restoration must happen even when the test run raises.

This module does all six. It is a testing aid, not a runtime component: nothing in
production imports it, and it never runs as part of a normal suite.

PRIOR ART, which this generalises rather than replaces:
``tests/mutation_proof_ultraclear.py`` (135 lines) and
``tests/mutation_proof_ultraclear_session.py`` (119 lines) are bespoke falsifiers,
one per module, each hand-rolling this same ritual. Two independent copies of a
procedure is where YSH says abstract, and the second of them already knew something
this module did not: bytecode caching can serve a stale ``.pyc`` and manufacture a
false survivor. That guard is now here, credited in ``run``.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence


class MutationError(AssertionError):
    """Raised when the ritual itself is wrong, as distinct from the test being weak.

    A separate type on purpose: "your anchor matched three places" and "this test
    cannot fail" are different problems and must not be confused with each other.
    """


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class MutationRun:
    """What a mutated test run actually produced."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def killed(self, expected: str) -> bool:
        """Did the NAMED test fail? Not merely: did anything fail?"""
        return self.returncode != 0 and expected in self.output


@contextmanager
def mutated(path: Path | str, old: str, new: str) -> Iterator[Path]:
    """Apply a uniquely-anchored edit, yield, then restore byte-identically.

    Raises ``MutationError`` -- never a bare AssertionError -- when the anchor is
    absent or ambiguous, when the edit does not take, or when restoration does not
    reproduce the original bytes.
    """
    p = Path(path)
    original = p.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    src = original.decode("utf-8")

    found = src.count(old)
    if found != 1:
        raise MutationError(
            f"anchor must match EXACTLY ONCE in {p}, found {found}. "
            f"A replace() on an ambiguous anchor edits the first match, which is "
            f"rarely the one you meant. Lengthen the anchor until it is unique."
        )

    mutant = src.replace(old, new, 1)
    if mutant == src:
        raise MutationError(f"the mutation is a no-op in {p}: `new` equals `old`")

    p.write_text(mutant, encoding="utf-8")
    try:
        # Confirm on the SPECIFIC replacement, never a global substring search --
        # the replaced text may legitimately occur elsewhere, in a docstring or a
        # comment, and a whole-file search then reports a false alarm.
        readback = p.read_text(encoding="utf-8")
        if readback != mutant:
            raise MutationError(f"mutation did not take effect on disk: {p}")
        yield p
    finally:
        p.write_bytes(original)
        after = _sha(p)
        if after != before:
            raise MutationError(
                f"RESTORATION FAILED for {p}: sha256 {before[:12]} -> {after[:12]}. "
                f"The working tree is now dirty and must be fixed by hand."
            )


def run(cmd: Sequence[str], *, cwd: Optional[Path] = None, timeout: float = 600.0) -> MutationRun:
    """Run a test command and capture everything, without raising on failure.

    ``PYTHONDONTWRITEBYTECODE=1`` is forced, and it is not hygiene. A ``.pyc`` is
    reused when its recorded mtime and size match the source, so a mutation of the
    same length applied and reverted inside one filesystem-timestamp tick can be
    served from a cache of the OTHER version -- producing a false survivor, which
    reads as "this test cannot fail" when the test was never actually run against
    the mutant. Learned from ``tests/mutation_proof_ultraclear_session.py``, which
    hit it first and documented it.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        list(cmd), capture_output=True, text=True, cwd=str(cwd) if cwd else None,
        timeout=timeout, env=env,
    )
    return MutationRun(r.returncode, r.stdout, r.stderr)


def prove_kills(
    path: Path | str,
    old: str,
    new: str,
    cmd: Sequence[str],
    *,
    expect_failing: str,
    cwd: Optional[Path] = None,
) -> MutationRun:
    """The whole ritual in one call. Returns the mutant run for further assertions.

    Raises ``MutationError`` if the NAMED test did not fail -- which means either the
    test cannot fail, or the mutation broke something else instead. Both are real
    findings and the message says which evidence to look at.
    """
    with mutated(path, old, new):
        result = run(cmd, cwd=cwd)
    if not result.killed(expect_failing):
        raise MutationError(
            f"{expect_failing!r} did NOT fail under the mutation (exit "
            f"{result.returncode}). Either that test cannot fail, or the mutation "
            f"changed something it does not guard. Output:\n{result.output[-1500:]}"
        )
    return result
