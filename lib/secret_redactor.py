#!/usr/bin/env python3
"""secret_redactor.py — deterministic credential scrubber for captured text.

Origin (2026-08-17, AGORA REQUEST_CHANGES): the PostToolUse capture hook
``hooks/bash_error_learner_hook.py`` persisted the first 200 characters of any
FAILED Bash command verbatim into ``learning/insights.jsonl``, which is a
tracked, non-git-crypt file that is also broadcast cross-machine. A reproduction
confirmed an ``sk-ant-…`` token inside a failing ``curl -H "Authorization:
Bearer …"`` survived whole into the persisted string, and that the *evidence*
line leaks independently whenever the shell echoes the failing command back
(``/bin/sh: /usr/local/bin/deploy --token <SECRET>: No such file or directory``).

Scope — what this module is, and what it is NOT
-----------------------------------------------
This is the **L1 deterministic floor** for CREDENTIAL text, sitting beside
``lib/redaction_shield.py``, which is the shield for **IDENTITY / PII** (people,
orgs, hosts) on publish surfaces. They are deliberately separate modules because
the two data classes have opposite over-redaction economics: masking a hash by
mistake costs nothing, masking a person's name mid-sentence destroys meaning.
``redaction_shield``'s key group is also **prefix-only** (``AKIA``/``ghp_``/
``sk-``/``gsk_``/``AIza``), so it cannot see an opaque no-prefix key, and
``load_denylist`` RAISES on a git-crypt-locked checkout — neither property is
acceptable on a hook hot path that must never break a tool call.

**Redaction is defence-in-depth here, never the primary control.** The primary
control is not capturing the raw command at all. This module exists for the text
that must still be captured to stay useful — the error line itself.

Three tiers, most-confident first (one pass each, all pure):

1. **Vendor-prefixed** — ``sk-ant-…``, ``ghp_…``, ``xoxb-…``, ``AKIA…``,
   ``AIza…``, ``glpat-…``, ``hf_…``, JWTs, PEM blocks.
2. **Structural** — the value after ``Bearer``, after an ``--api-key``/``--token``
   style flag, or after a ``KEY=``/``SECRET=``/``PASSWORD=``/``TOKEN=``
   assignment. This tier is shape-agnostic: it redacts by CONTEXT, so a key with
   no recognisable prefix is still caught.
3. **High-entropy opaque token** — the tier that closes the case a prefix
   denylist structurally cannot: a Mistral key is 32 opaque characters with no
   prefix and no surrounding keyword. A run of >= ``_MIN_TOKEN`` characters that
   mixes lower + upper + digit AND carries Shannon entropy >= ``_MIN_ENTROPY``
   is masked.

Tier 3's three-character-class requirement is what keeps it quiet on real error
text: snake_case identifiers, kebab-case flags, absolute paths and 40-char git
SHAs carry at most two classes, so they survive intact and the captured error
stays useful. That is a deliberate, measured trade — see
``tests/test_secret_redactor.py``, which asserts the entire in-tree classifier
corpus passes through byte-for-byte unchanged.

Falsification: this module is wrong if, over the next ~50 captured errors,
(a) a real credential still lands in ``learning/insights.jsonl`` (matcher defect
— widen a tier, never drop it); (b) it mangles so many legitimate error lines
that the captured evidence stops being actionable (over-block — raise
``_MIN_ENTROPY`` or ``_MIN_TOKEN``, do not remove the tier); or (c) tier 3 never
fires on anything tier 1 and 2 missed (then the entropy floor is ceremony and
can be demoted — the opaque-32-char case refutes that today).

Pure by construction: stdlib only, no I/O, no filesystem, no network, and no
raising path — ``redact_secrets`` returns a string for every input including
``None``, because its callers are hooks that must never break a tool call.
"""

from __future__ import annotations

import math
import re
from collections import Counter

__all__ = ["redact_secrets", "MASK"]

MASK = "[REDACTED]"

# --- tier 3 thresholds (documented so a tuner changes a number, not a regex) --
_MIN_TOKEN = 20        # shortest opaque run considered; real keys are >= 20
_MIN_ENTROPY = 3.0     # bits/char; measured against the in-tree error corpus
_MIN_CLASSES = 3       # lower + upper + digit — see module docstring


# ---------------------------------------------------------------------------
# Tier 1 — vendor-prefixed credentials (highest confidence)
# ---------------------------------------------------------------------------
_VENDOR_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|AIza[A-Za-z0-9_\-]{12,}"
    r"|glpat-[A-Za-z0-9_\-]{16,}"
    r"|gsk_[A-Za-z0-9_\-]{16,}"
    r"|hf_[A-Za-z0-9]{16,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r"|-----BEGIN[A-Z ]*PRIVATE KEY-----"
)

# ---------------------------------------------------------------------------
# Tier 2 — structural: redact by CONTEXT, so an opaque value is still caught.
# Each pattern keeps its label (group "lead") and masks only the value.
# ---------------------------------------------------------------------------
_SECRET_WORD = (
    r"(?:api[_-]?keys?|secret[_-]?(?:key|access[_-]?key)?|access[_-]?key"
    r"|auth[_-]?token|token|password|passwd|pwd|credential|bearer)"
)

_STRUCTURAL = (
    # Authorization: Bearer <value>   /   Bearer <value>
    re.compile(r"(?i)(?P<lead>\bbearer\s+)(?P<val>[A-Za-z0-9._\-+/=]{8,})"),
    # --api-key <value> | --token=<value> | -p <value> is deliberately excluded
    re.compile(
        r"(?i)(?P<lead>--?" + _SECRET_WORD + r"[=\s]+)"
        r"(?P<val>[\"']?[^\s\"']{8,})"
    ),
    # KEY=<value> | SECRET: <value> | "password": "<value>" | DB_PASSWORD=<value>
    #
    # NOTE: there is deliberately NO \b before the keyword. Env var names are
    # routinely PREFIXED (DB_PASSWORD, MISTRAL_API_KEY, PGPASSWORD), and \b
    # cannot match between "_" and "P", so a leading \b silently skipped the
    # single most common assignment shape. Found by mutation-testing this tier:
    # DB_PASSWORD=lowercaseonlypassphrase was caught by NEITHER tier.
    # Matching mid-identifier is safe here because the keyword must still be
    # followed by an optional quote and a ":" or "=" — "tokenize: x" does not
    # match, because "ize" sits between "token" and the colon.
    re.compile(
        r"(?i)(?P<lead>" + _SECRET_WORD + r"\"?\s*[:=]\s*)"
        r"(?P<val>[\"']?[^\s\"',;]{8,})"
    ),
)


def _mask_structural(match: "re.Match") -> str:
    """Keep the label, mask the value — the label is the learning signal."""
    return f"{match.group('lead')}{MASK}"


# ---------------------------------------------------------------------------
# Tier 3 — high-entropy opaque token (the no-prefix, no-keyword case)
# ---------------------------------------------------------------------------
# NOTE: "/" is deliberately EXCLUDED from this character class. Including it
# made an absolute path match as ONE long token, and paths are the entire
# learning value of the no_such_file / rg_io_missing_path categories — measured
# before exclusion: "~/projects/-Users-you--grip/<uuid>/x.py" collapsed
# to "[REDACTED]". Excluding "/" splits a path into segments that fall below
# _MIN_TOKEN or below _MIN_CLASSES, so paths survive intact. The cost is that a
# base64 secret containing "/" is caught only in segments (or by tiers 1-2);
# that is the accepted trade, recorded here so a future tuner changes it
# knowingly rather than by accident.
_TOKEN_RE = re.compile(r"[A-Za-z0-9+=_\-]{%d,}" % _MIN_TOKEN)


def _shannon_entropy(text: str) -> float:
    """Shannon entropy in bits per character. 0.0 for empty input."""
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _class_count(text: str) -> int:
    """How many of {lowercase, uppercase, digit} appear in ``text``."""
    return sum(
        (
            any(c.islower() for c in text),
            any(c.isupper() for c in text),
            any(c.isdigit() for c in text),
        )
    )


def _looks_opaque(token: str) -> bool:
    """True when ``token`` has the shape of an opaque credential.

    Requires BOTH character-class mixing and high entropy, which is what keeps
    identifiers, flags, paths and git SHAs (<= 2 classes) out of scope.
    """
    return (
        _class_count(token) >= _MIN_CLASSES
        and _shannon_entropy(token) >= _MIN_ENTROPY
    )


def _mask_opaque(match: "re.Match") -> str:
    token = match.group(0)
    return MASK if _looks_opaque(token) else token


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def redact_secrets(text) -> str:
    """Return ``text`` with credential-shaped substrings replaced by ``MASK``.

    Never raises: any input that is not a string (including ``None``) is
    coerced, because every caller is a hook that must not break a tool call.
    Idempotent — ``MASK`` contains no character run that any tier re-matches,
    so ``redact_secrets(redact_secrets(x)) == redact_secrets(x)``.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return text
    out = _VENDOR_RE.sub(MASK, text)
    for pattern in _STRUCTURAL:
        out = pattern.sub(_mask_structural, out)
    return _TOKEN_RE.sub(_mask_opaque, out)
