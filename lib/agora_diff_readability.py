"""Detect files AGORA could not actually read in a PR diff.

Origin: 2026-09-11. GRIP encrypts several paths with git-crypt
(``.gitattributes``: ``docs/restricted/**``, ``drafts/**``, ``docs/preps/**``,
``prep/**``, ``rules/operational-context.md``, ``facts/*.md``, ``facts/*.html``,
``data/genomes/*.json``, ...). Git renders an encrypted blob's diff as a binary
marker rather than text hunks, so the council receives no readable code — and
returned a verdict anyway, as though it had reviewed the change.

The measured instance (PR #5526, grip-platform/GRIP): one line plus a comment
changed in ``drafts/opensource-agpl/reverify.py``. The ENTIRE diff the council
received was 217 bytes::

    diff --git a/drafts/opensource-agpl/reverify.py b/drafts/opensource-agpl/reverify.py
    index 3e9cd6f1d..88513d1cb 100644
    Binary files a/drafts/opensource-agpl/reverify.py and b/drafts/opensource-agpl/reverify.py differ

AGORA returned ``DEFER CHANGES`` with 3/3 speakers. Speaker C (xAI Grok 4.3)
wrote, verbatim: *"The only visible update is a binary swap in one supporting
script, with no readable code changes supplied."* The council said out loud
that it could not see the change, and the pipeline still produced a verdict.

Why that matters more than one PR: ``rules/operational-context.md`` is a
Tier-1, always-loaded PARAMOUNT rules file, and it is encrypted. A PR editing
GRIP's operational doctrine would get an AGORA "review" over ciphertext, CI
would go green, and ``rules/exogenous-anchor-law.md`` would treat that verdict
as the exogenous ratification that authorises merge. It is not one. Per
CLAUDE.md's own AGORA-green rule, "absence-of-signal != PASS": a council that
reviewed ciphertext is nearer to "AGORA never ran" than to a PASS.

WHY THIS ANNOTATES RATHER THAN BLOCKS (``rules/gate-resolution-escape-hatch.md``
Invariant 1). Encryption is by design and permanent: an author cannot make the
diff readable, so a hard-red on an unreadable file would be a gate whose
blocked condition can NEVER be resolved — every PR touching an encrypted path
would be unmergeable forever, including the fix to an encrypted file. So this
module adds a LOUD signal and changes no verdict, no check conclusion, and no
validity gate. The escape hatch is that nothing is closed in the first place.

Pure functions, no I/O — the detector takes the diff string and returns what it
found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# git-crypt stamps every encrypted blob with this NUL-delimited magic at byte 0.
# Source: git-crypt's own format (``\0GITCRYPT\0`` + 12-byte nonce). Verified
# 2026-09-11 against the real blob for ``rules/operational-context.md``:
#   00000000: 0047 4954 4352 5950 5400 7f97 ...  .GITCRYPT.....
_GITCRYPT_ASCII = "GITCRYPT"
_GITCRYPT_NUL = "\x00" + _GITCRYPT_ASCII
# What follows a flush-left ``GITCRYPT`` in SOURCE: an assignment, annotation,
# call, index or list separator. A blob's magic is followed by its version
# byte and nonce instead. See ``_looks_like_ciphertext``.
_SOURCE_AFTER_MAGIC = re.compile(r"\s*[=:(\[.,]")

# ``Binary files a/X and b/X differ`` — what git emits by default, and what the
# GitHub diff API actually served for PR #5526 (confirmed by fetching it).
_BINARY_FILES_RE = re.compile(
    r"^Binary files (?:a/)?(.+?) and (?:b/)?(.+?) differ\s*$",
)
# ``GIT binary patch`` — what ``git diff --binary`` emits (delta/literal blocks).
_BINARY_PATCH_RE = re.compile(r"^GIT binary patch\s*$")
# ``diff --git a/PATH b/PATH`` — the per-file header that scopes everything after it.
_DIFF_GIT_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$")

# Reason codes. Stable strings — they are rendered into the PR comment and fed
# to the judge, so a rename is a visible contract change, not a refactor.
REASON_BINARY_MARKER = "binary-marker"
REASON_BINARY_PATCH = "binary-patch"
REASON_GITCRYPT_MAGIC = "git-crypt-ciphertext"

_REASON_PHRASE = {
    REASON_BINARY_MARKER: (
        "git replaced the hunks with a binary marker — no line of this "
        "change was visible to the council"
    ),
    REASON_BINARY_PATCH: (
        "git emitted an encoded binary patch — the council saw an opaque "
        "blob, not source"
    ),
    REASON_GITCRYPT_MAGIC: (
        "the hunk carries git-crypt ciphertext (the \\0GITCRYPT\\0 magic) — "
        "the council read encrypted bytes, not code"
    ),
}


@dataclass(frozen=True)
class UnreadableFile:
    """One changed file the council could not actually read."""

    path: str
    reason: str

    @property
    def phrase(self) -> str:
        return _REASON_PHRASE.get(self.reason, "unreadable in the diff")


@dataclass(frozen=True)
class DiffReadability:
    """What a single scan of a unified diff found.

    ``total_files`` counts files the diff CLAIMS to change; ``unreadable``
    lists the subset whose content never reached the council.
    """

    total_files: int = 0
    unreadable: list[UnreadableFile] = field(default_factory=list)

    @property
    def any_unreadable(self) -> bool:
        return bool(self.unreadable)

    @property
    def all_unreadable(self) -> bool:
        """True when EVERY changed file was unreadable — the council reviewed
        nothing at all, which is the strongest form of this defect."""
        return bool(self.unreadable) and len(self.unreadable) >= self.total_files > 0

    @property
    def paths(self) -> list[str]:
        return [u.path for u in self.unreadable]


def _hunk_body(line: str) -> str | None:
    """Content of a hunk line (``+``/``-``/`` `` prefix), or None if not one.

    ``+++ b/x`` and ``--- a/x`` are FILE HEADERS, not content, and are excluded
    — otherwise every diff would look like it carried added content.
    """
    if line.startswith("+++ ") or line.startswith("--- "):
        return None
    if line[:1] in ("+", "-", " "):
        return line[1:]
    return None


def _looks_like_ciphertext(body: str) -> bool:
    """True when a hunk line's content carries the git-crypt magic.

    TWO narrow conditions, both chosen so that source code *mentioning*
    git-crypt cannot match — this very module, and the tests for it, contain
    the literal string ``GITCRYPT`` and must not flag themselves:

    1. ``\\0GITCRYPT`` anywhere in the body. A NUL byte cannot occur in source
       text, so this is unambiguous.
    2. Bare ``GITCRYPT`` at position 0 of the body. The magic sits at byte 0 of
       an encrypted blob, so it can only ever be the first thing on the line;
       any mention inside real source is indented, quoted, or mid-sentence.

    Condition 2 has one carve-out, measured 2026-10-12 on GRIP#7513: a
    flush-left IDENTIFIER named after the magic — ``GITCRYPT = b"\\x00GITCRYPT"``
    in tests/test_ste_voice_documents.py — was reported as ciphertext, and the
    council told the operator a plain test file needed a human with the key.
    In a real blob the magic is followed by its version byte and the nonce,
    never by the punctuation of an assignment, annotation, call or index, so
    bare ``GITCRYPT`` followed by optional spaces and one of ``= : ( [ . ,``
    is read as source.

    Known residual, stated rather than left to be discovered: a fixture written
    flush-left whose first characters are the bare magic, followed by anything
    but that punctuation, WOULD match. That is accepted — such a line genuinely
    looks like ciphertext, and tightening further would cost the
    NUL-stripped-transport case in condition 2. The carve-out's own residual
    is a NUL-stripped nonce that happens to start with that punctuation, about
    one line in eighty: a nonce whose first byte is ``=`` arrives as
    ``GITCRYPT=`` followed by eleven more nonce bytes, and that line is read as
    source. Condition 1 still catches it on any transport that keeps the NUL.
    """
    if _GITCRYPT_NUL in body:
        return True
    if not body.startswith(_GITCRYPT_ASCII):
        return False
    return _SOURCE_AFTER_MAGIC.match(body, len(_GITCRYPT_ASCII)) is None


def _matched_path(pattern: re.Pattern, line: str) -> str | None:
    """Post-change path from a two-path diff line, or None if it does not match.

    Both ``diff --git a/X b/Y`` and ``Binary files a/X and b/Y differ`` name the
    file twice. Prefer the ``b/`` (post-change) name; fall back to ``a/`` when
    the post-change side is ``/dev/null`` (a deletion).
    """
    match = pattern.match(line)
    if not match:
        return None
    before, after = match.group(1), match.group(2)
    return before if after == "/dev/null" else after


def _record(seen: list[str], path: str) -> None:
    """Append a changed-file path once, preserving first-seen order."""
    if path and path != "/dev/null" and path not in seen:
        seen.append(path)


def _scan_into(diff: str, seen: list[str], flagged: dict[str, str]) -> None:
    """Walk the diff once, recording changed files and the unreadable ones.

    Recognises all three shapes git produces for an encrypted blob (each
    verified empirically 2026-09-11 against real git output): the default
    ``Binary files ... differ`` marker, the ``GIT binary patch`` block from
    ``git diff --binary``, and a ``git diff --text`` hunk carrying the
    git-crypt magic. First reason per path wins.
    """
    current: str | None = None
    for line in diff.splitlines():
        header = _matched_path(_DIFF_GIT_RE, line)
        if header:
            current = header
            _record(seen, current)
            continue
        # Self-describing: the marker carries its own paths, so an unreadable
        # file is still detected when no `diff --git` header preceded it.
        marker = _matched_path(_BINARY_FILES_RE, line)
        if marker:
            current = current or marker
            _record(seen, current)
            flagged.setdefault(current, REASON_BINARY_MARKER)
            continue
        if current is None:
            continue
        if _BINARY_PATCH_RE.match(line):
            flagged.setdefault(current, REASON_BINARY_PATCH)
            continue
        body = _hunk_body(line)
        if body is not None and _looks_like_ciphertext(body):
            flagged.setdefault(current, REASON_GITCRYPT_MAGIC)


def scan_diff_readability(diff: str) -> DiffReadability:
    """Scan a unified diff for files whose content never reached the reviewer.

    Pure: no filesystem, no network, no git invocation. See ``_scan_into`` for
    the three unreadable shapes this recognises.
    """
    if not diff:
        return DiffReadability()
    seen_files: list[str] = []
    flagged: dict[str, str] = {}
    _scan_into(diff, seen_files, flagged)
    unreadable = sorted(
        (UnreadableFile(path=p, reason=r) for p, r in flagged.items()),
        key=lambda u: u.path,
    )
    return DiffReadability(total_files=len(seen_files), unreadable=unreadable)


def format_unreadable_note(report: DiffReadability) -> str:
    """The UNREADABLE DIFF block injected into the judge prompt.

    Returns a populated placeholder when nothing was unreadable, so the judge
    prompt always has a filled slot (same contract as the quote/temporal gates).
    """
    if not report.any_unreadable:
        return "(none — every changed file was readable as text)"
    rows = "\n".join(
        f"- UNREADABLE — `{u.path}`: {u.phrase}." for u in report.unreadable
    )
    scope = (
        "EVERY changed file in this PR was unreadable. The speakers reviewed "
        "NOTHING of the actual change."
        if report.all_unreadable
        else (
            f"{len(report.unreadable)} of {report.total_files} changed files "
            "were unreadable. The speakers reviewed only the remainder."
        )
    )
    return f"{scope}\n{rows}"


def format_unreadable_render_block(report: DiffReadability) -> str:
    """The notice shown in the posted AGORA PR comment.

    Empty string when every file was readable, so callers concatenate freely.
    """
    if not report.any_unreadable:
        return ""
    rows = "\n".join(f"> - `{u.path}` — {u.phrase}" for u in report.unreadable)
    n = len(report.unreadable)
    noun = "file" if n == 1 else "files"
    if not report.all_unreadable:
        scope = f"**{n} of {report.total_files} changed files**"
    elif report.total_files == 1:
        scope = "**the only changed file**"
    else:
        scope = f"**every one of the {report.total_files} changed files**"
    return (
        f"> ### :lock: The council could NOT read {scope}\n"
        ">\n"
        f"> This review did not see the contents of the following {noun}. Git "
        "showed the council a binary or encrypted blob instead of source "
        "lines, so nothing below is a review of what actually changed in "
        "them:\n"
        ">\n"
        f"{rows}\n"
        ">\n"
        "> **This verdict is therefore NOT an exogenous ratification of those "
        "files** (`rules/exogenous-anchor-law.md`). Per CLAUDE.md's AGORA-green "
        "rule, absence-of-signal is not a PASS — a council that reviewed "
        "ciphertext is nearer to \"AGORA never ran\" than to a green. A human "
        "who can decrypt these paths must read the change before treating this "
        "PR as reviewed.\n"
        ">\n"
        "> *Why this is a notice and not a red check: git-crypt encryption is "
        "permanent and by design, so an author cannot make the diff readable. "
        "Blocking here would make every PR touching an encrypted path "
        "permanently unmergeable — a gate that cannot be resolved "
        "(`rules/gate-resolution-escape-hatch.md` Invariant 1).*\n\n"
    )


def unreadable_verdict_caveat(report: DiffReadability) -> str:
    """One advisory line appended to the verdict blockquote.

    The verdict block is the thing a reader (or an AGORA-green=merge agent)
    scans first, so the caveat has to live THERE — a notice further down the
    comment does not stop a verdict from reading as a clean ratification.
    Empty string when everything was readable.
    """
    if not report.any_unreadable:
        return ""
    n = len(report.unreadable)
    scope = (
        "every changed file"
        if report.all_unreadable
        else f"{n} of {report.total_files} changed files"
    )
    return (
        f"> :lock: **NOT a clean ratification — the council could not read "
        f"{scope}** ({', '.join(f'`{p}`' for p in report.paths)}). "
        "Requires a human who can decrypt them; see the notice below.\n"
    )
