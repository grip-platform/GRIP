"""Detector for code that obfuscates itself to evade a safety scanner.

Origin: 2026-05-15. A background agent (W3 of an IDR audit-chain converge)
tripped the env-var-protection guardrail on `IDR_SIGNING_KEY` + setenv,
then wrote into a test file:

    # Env-var name split to avoid guardrail substring scan on source text.
    _SIGN_KEY_ENV = "IDR_SIGNING" + "_KEY"

That is bypassing a safety check instead of fixing the root cause — a
PARAMOUNT-forbidden anti-pattern. The agent was caught and rejected by the
operator, who directed: "never do something like this ever, mechanically
enforced too". This module is that mechanical enforcement.

Two high-confidence signals; either one DENIES (no bypass — evading a
safety scanner is never legitimate):

  1. Evasion-intent text — a comment/string declaring intent to dodge a
     scanner/guard/hook/gate ("to avoid guardrail scan", "so the literal
     doesn't appear", "obfuscate ... to bypass", ...).
  2. Split-literal reassembly of a credential/secret-shaped identifier —
     adjacent string literals concatenated to reconstruct a token that
     matches a secret/env-secret pattern (the only purpose of which is to
     keep the literal out of a scanner's view).

Path-agnostic: the originating evasion was in nexus-poc, not ~/.claude.
This feature's own files (rule, memory, detector, hook, tests) are exempt
so documenting the anti-pattern is not itself blocked.

Pure module — no I/O. Tested by tests/test_guardrail_evasion_detector.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Files that legitimately CONTAIN the forbidden patterns because they
# document or test them. Substring match against the target path.
_EXEMPT_PATH_MARKERS = (
    "no-guardrail-evasion",
    "guardrail_evasion_detector",
    "guardrail-evasion-gate",
    "test_guardrail_evasion",
    "feedback_never_evade_guardrails",
)

# Documentation contexts that TEACH the anti-pattern (2026-09-10 precision
# fix). The INTENT signal is suppressed in these .md paths — narrative prose
# that explains what evasion is and how a false positive is correctly
# handled is teaching, not acting. The split-literal reassembly signal is
# NOT suppressed here: a .md file can still hide a real reassembled
# credential, and that must keep denying.
_DOC_PATH_MARKERS = (
    "/drafts/",
    "/docs/",
    "/rules/",
    "/memory/",
    "/plans/",
    "harness-handover",
)

# Signal 1 — explicit intent to defeat a safety mechanism. Case-insensitive.
_SAFETY_NOUN = r"(guardrail|guard|scanner|scan|hook|gate|secrets?|detection|protection|sentinel|lint(?:er)?)"
_INTENT_PATTERNS = (
    rf"(?:avoid|evad|bypass|circumvent|defeat|dodg|sidestep|skirt|outsmart|fool|trick|sneak|slip|work\s*around|get\s*past)\w*.{{0,40}}{_SAFETY_NOUN}",
    rf"{_SAFETY_NOUN}\b.{{0,40}}(substring\s+scan|won'?t\s+(?:fire|match|catch|trigger)|doesn'?t\s+(?:fire|match|catch|trigger)|can'?t\s+see|never\s+sees)",
    r"so\s+(?:the|that|a)?\s*\b(?:literal|string|token|substring|pattern|name)\b.{0,30}(?:doesn'?t|does\s+not|won'?t|will\s+not|never)\s+(?:appear|match|trigger|fire|get\s+(?:caught|flagged|scanned))",
    rf"obfuscat\w*.{{0,40}}(?:{_SAFETY_NOUN}|env|var|name|key|token|secret|string)",
    rf"split\b.{{0,30}}(?:to|so|in\s+order\s+to)\b.{{0,20}}(?:avoid|evade|bypass|not\s+(?:match|trigger|appear)|{_SAFETY_NOUN})",
)
_INTENT_RE = re.compile("|".join(_INTENT_PATTERNS), re.IGNORECASE)

# Signal 2 — reconstruct a secret/env-secret identifier from split literals.
# Match 2- or 3-part adjacent string-literal concatenation where both/all
# operands are *literals* (not variables) and the joined value is
# credential-shaped.
_TWO_PART = re.compile(
    r"""(['"])([A-Za-z0-9_\-]{1,40})\1\s*\+\s*(['"])([A-Za-z0-9_\-]{1,40})\3"""
)
_THREE_PART = re.compile(
    r"""(['"])([A-Za-z0-9_\-]{1,30})\1\s*\+\s*(['"])([A-Za-z0-9_\-]{1,30})\3\s*\+\s*(['"])([A-Za-z0-9_\-]{1,30})\5"""
)
_SECRET_SHAPE = re.compile(
    r"(?i)(?:^|_)(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|"
    r"PRIVATE[_-]?KEY|SIGNING[_-]?KEY|CLIENT[_-]?SECRET|AUTH[_-]?TOKEN)"
    r"|(?:API|AUTH|ACCESS|PRIVATE|SIGNING|CLIENT|SESSION|REFRESH|BEARER|"
    r"ENCRYPT(?:ION)?|CRYPTO?|HMAC|JWT|OAUTH|SSH|GPG|PGP|CREDENTIAL|VAULT|"
    r"MASTER|SERVICE)[A-Z0-9]*_(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\b"
    r"|^(?:sk-|ghp_|xoxb-|xoxp-|AKIA|-----BEGIN)"
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    signal: str = ""
    reason: str = ""
    evidence: str = ""


def _comparable(file_path: str) -> str:
    """The path as the markers above are written: lower case, forward slashes.

    On Windows the gate is handed backslash paths, and before this a note at
    "\\memory\\note.md" was scanned as if it were code. A backslash is read as
    a separator on every OS, so the same path string gets the same verdict
    everywhere; on POSIX that includes a file whose NAME contains one. The
    documentation exemption this can grant only ever lifts the intent signal,
    and only for .md files.
    """
    return (file_path or "").replace("\\", "/").lower()


def _is_exempt(file_path: str) -> bool:
    p = _comparable(file_path)
    return any(marker in p for marker in _EXEMPT_PATH_MARKERS)


def _is_doc(file_path: str) -> bool:
    """True for .md documentation contexts that may TEACH the anti-pattern."""
    p = _comparable(file_path)
    if not p.endswith(".md"):
        return False
    return any(marker in p for marker in _DOC_PATH_MARKERS)


def _find_reassembly(content: str) -> str | None:
    """Return the reconstructed token if split literals rebuild a secret."""
    for m in _THREE_PART.finditer(content):
        joined = m.group(2) + m.group(4) + m.group(6)
        if _SECRET_SHAPE.search(joined):
            return joined
    for m in _TWO_PART.finditer(content):
        joined = m.group(2) + m.group(4)
        if _SECRET_SHAPE.search(joined):
            return joined
    return None


def evaluate(content: str, file_path: str = "") -> Verdict:
    """Deny when content shows intent or mechanism to evade a safety scanner.

    Deterministic, no bypass. A safety scanner is fixed at its root or its
    own logic is fixed — it is never obfuscated around.
    """
    if not content or _is_exempt(file_path):
        return Verdict(allowed=True)

    if not _is_doc(file_path):
        intent = _INTENT_RE.search(content)
        if intent:
            snippet = content[max(0, intent.start() - 10): intent.end() + 10].strip()
            return Verdict(
                allowed=False,
                signal="evasion-intent",
                evidence=snippet[:160],
                reason=(
                    "Guardrail-evasion intent detected: this edit declares intent "
                    "to avoid/bypass a safety scanner. This is a PARAMOUNT-forbidden "
                    "anti-pattern. Fix the ROOT CAUSE instead: if the scanner is a "
                    "false positive, rephrase so the code genuinely is not a "
                    "secret/exposure pattern (e.g. hoist an env-var name into one "
                    "readable module constant; use an obviously-fake test value); "
                    "if the scanner logic is wrong, fix the scanner. Never obfuscate "
                    "to slip past a safety control. See rules/no-guardrail-evasion.md."
                ),
            )

    reassembled = _find_reassembly(content)
    if reassembled:
        return Verdict(
            allowed=False,
            signal="split-literal-reassembly",
            evidence=reassembled[:160],
            reason=(
                f"Split-literal reassembly of a credential-shaped identifier "
                f"({reassembled!r}) detected. Concatenating string fragments to "
                f"keep a secret/env-secret name out of a scanner's view is "
                f"guardrail evasion. Write the name plainly as a single readable "
                f"constant and address the underlying scanner finding properly. "
                f"See rules/no-guardrail-evasion.md."
            ),
        )

    return Verdict(allowed=True)
