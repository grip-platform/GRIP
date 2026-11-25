"""Detect a diff that REMOVES enforcement code from a guardrail.

Origin: 2026-08-10, GRIP #4513. A dispatch brief for PR #4473 said, in plain
words, "do not merge even on an AGORA pass — a change to a secrets scanner gets
human eyes". The agent that received the brief obeyed. A *different* session,
running concurrently and following the standing PARAMOUNT rule "AGORA-green =
merge, don't ask", merged it at 02:12:47Z. Neither worker misbehaved. The
instruction simply had no way of reaching the second one, because a brief is a
message to one worker and canon is what every worker loads.

This module is the mechanical half of that fix. It does not invent policy:
CLAUDE.md already lists **guardrail removal** among the "real, present, clear
danger" conditions that HALT a merge. It makes that existing rule checkable, so
it binds a session that never saw the brief.

WHY THE SIGNAL IS NARROWER THAN "TOUCHES A GUARDRAIL FILE"
----------------------------------------------------------
The obvious detector — fire whenever a diff touches an enforcement path — is
wrong, and #4473 is the counter-example that proves it. That PR *strengthened*
the secrets scanner (it widened the key alphabet to admit hyphens and switched
file classification from an extension allowlist to a content check), yet it did
so by deleting the six old regex lines and adding replacements. A touch-based
or a lines-removed-based detector fires on it. V>> was explicit that a change
which strengthens a control should merge freely, so firing there would be a
false positive on the single most common shape of guardrail change.

So the signal is the *count of enforcement primitives*, per file:

    a guardrail is being removed when the file ends up with strictly FEWER
    teeth than it started with — not when its text merely changed.

Under that rule #4473 passes (it removes no deny-primitive and adds a great
deal), while deleting `_emit_deny(...)` from a gate, or dropping a hook's line
out of `settings.json`, fires. Neither case needs a judgement call, which is
what makes it safe to enforce mechanically.

WHAT THIS DELIBERATELY DOES NOT COVER
--------------------------------------
Security tests whose teeth are `assert` statements are out of scope. Counting
asserts would fire on an ordinary `pytest.mark.parametrize` refactor that
replaces ten assertions with one — a judgement call, and this detector exists
precisely because the judgement-free part can be trusted without a human. That
limitation is stated rather than papered over: the secrets scanner at
`tests/test_no_live_keys_in_web.py` is NOT protected against weakening here.

Memory records (``projects/*/memory/*.md``) are not covered either, and not by
oversight: ``projects/**`` is gitignored, so a memory deletion never appears in
a pull-request diff, and a glob for it here would be a declared protection with
no actor (rules/declared-input-must-reach-the-actor.md). Rules ARE covered — as
canon, not as enforcement code; see CANON_GLOBS below.

Pure module — no I/O, no network. Tested by
tests/test_guardrail_removal_detector.py.
Rule: rules/agent-dispatch-protocol.md ("A brief binds only its recipient").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Paths whose PURPOSE is enforcement. Matched right-anchored per path segment
# (PurePosixPath.match), so a nested copy under a worktree still matches.
#
# Kept deliberately short: every entry widens what a DENY can fire on, and an
# over-broad list would recreate the false-positive problem this detector was
# written to avoid.
ENFORCEMENT_GLOBS: tuple[str, ...] = (
    "hooks/*gate*.py",
    "hooks/*guard*.py",
    "lib/gates/*.py",
    "lib/*_detector.py",
    "settings.json",
)

# Canon: the rule files every session loads as policy. Kept APART from
# ENFORCEMENT_GLOBS on purpose. A rule is prose, so running the teeth counter
# over it would score a benign rewording that drops one mention of a gate hook
# path as "net -1" — the #4473 false-positive class this detector was written
# to avoid. A rule's only tooth is its own existence, so the one signal taken
# from canon is DELETION. Measured origin (plans/add-c6-10-to-the-woolly-token.md,
# 2026-09-07): deleting ``rules/no-guardrail-evasion.md`` outright passed this
# detector silently, because no glob named a rule path.
CANON_GLOBS: tuple[str, ...] = ("rules/*.md",)

# A line "has teeth" when it carries one of these. Each is a primitive that
# actually stops something: a deny verdict, a blocking exit, or the settings.json
# line that wires a gate into the session at all. An unwired gate is an absent
# gate, which is why the wiring line counts as enforcement.
TEETH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"permissionDecision"),
    re.compile(r"""pretool_decision\(\s*["']deny"""),
    re.compile(r"\b_?emit_deny\b"),
    re.compile(r"\bsys\.exit\(\s*2\s*\)"),
    re.compile(r"hooks/[\w.-]*(?:gate|guard)[\w.-]*\.py"),
)

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


@dataclass(frozen=True)
class FileDiff:
    """One file's contribution to a unified diff."""

    path: str
    deleted: bool = False
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemovalVerdict:
    """Whether a diff removes guardrail enforcement, and the evidence for it."""

    removes_guardrail: bool
    signal: str = ""
    evidence: tuple[str, ...] = field(default=())

    @property
    def reason(self) -> str:
        if not self.removes_guardrail:
            return ""
        detail = "\n".join(f"  - {item}" for item in self.evidence)
        return (
            "This pull request REMOVES enforcement code from a guardrail, "
            "or deletes canon outright:\n"
            f"{detail}\n\n"
            "CLAUDE.md lists guardrail removal under 'real, present, clear "
            "danger' — the one class that HALTs a merge even on an AGORA pass. "
            "A human merges this one.\n"
            "If the change actually STRENGTHENS the control, say so on the PR "
            "and let the operator merge it; do not work around this check "
            "(rules/no-guardrail-evasion.md)."
        )


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Split a unified diff into per-file added/removed line sets.

    Tolerant by design: anything it cannot parse yields no files, and the caller
    treats that as "nothing detected" rather than as a block.
    """
    files: list[dict] = []
    current: dict | None = None
    for line in diff_text.splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            current = {
                "path": header.group("b"),
                "deleted": False,
                "added": [],
                "removed": [],
            }
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("deleted file mode"):
            current["deleted"] = True
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            current["added"].append(line[1:])
        elif line.startswith("-"):
            current["removed"].append(line[1:])
    return [
        FileDiff(
            path=f["path"],
            deleted=f["deleted"],
            added=tuple(f["added"]),
            removed=tuple(f["removed"]),
        )
        for f in files
    ]


def is_enforcement_path(path: str) -> bool:
    """True when a path's purpose is enforcement."""
    candidate = PurePosixPath(path)
    return any(candidate.match(glob) for glob in ENFORCEMENT_GLOBS)


def is_canon_path(path: str) -> bool:
    """True when a path is a rule file — canon whose only tooth is existing."""
    candidate = PurePosixPath(path)
    return any(candidate.match(glob) for glob in CANON_GLOBS)


def count_teeth(lines: tuple[str, ...] | list[str]) -> int:
    """Number of lines carrying an enforcement primitive."""
    return sum(
        1 for line in lines if any(pattern.search(line) for pattern in TEETH_PATTERNS)
    )


def evaluate_diff(diff_text: str) -> RemovalVerdict:
    """Verdict on whether a unified diff removes guardrail enforcement."""
    evidence: list[str] = []
    signals: list[str] = []
    for file_diff in parse_diff(diff_text):
        if is_canon_path(file_diff.path):
            # Deletion-only by design: a rule's prose is never tooth-counted.
            if file_diff.deleted:
                signals.append("canon-deleted")
                evidence.append(
                    f"{file_diff.path}: rule deleted outright — a rule's only "
                    "tooth is its own existence"
                )
            continue
        if not is_enforcement_path(file_diff.path):
            continue
        if file_diff.deleted:
            signals.append("file-deleted")
            evidence.append(f"{file_diff.path}: enforcement file deleted outright")
            continue
        removed = count_teeth(file_diff.removed)
        added = count_teeth(file_diff.added)
        if removed > added:
            signals.append("teeth-removed")
            evidence.append(
                f"{file_diff.path}: {removed} enforcement line(s) removed, "
                f"{added} added — net -{removed - added}"
            )
    if not evidence:
        return RemovalVerdict(removes_guardrail=False)
    return RemovalVerdict(
        removes_guardrail=True,
        signal="+".join(sorted(set(signals))),
        evidence=tuple(evidence),
    )
