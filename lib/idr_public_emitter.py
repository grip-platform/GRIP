"""Public decision-chain emitter — the IDR-public dogfood data source.

Appends ALLOWLISTED commit facts to ``data/idr-public.jsonl`` so a public
surface (the /grip-chain route, tasked separately) can replay GRIP's own
shipping decisions: the WHAT, WHY, HOW and WHEN behind each AI decision,
visibly.

Allowlist by construction — each row carries ONLY the eight allowlisted keys
{sha, subject, ts, prev_sha, what, why, how, when}: no bodies, no diffs, no
paths, no client data. ``prev_sha`` chains the rows so the file is
order-evident (a removed row breaks the chain visibly).

The four reasoning fields are ADDITIVE and backwards-compatible: rows emitted
before they existed carry only the original four keys and still verify cleanly,
so a mixed-shape chain (old 4-key rows + new 8-key rows) stays valid. Each new
row derives the reasoning HONESTLY from the commit itself — never fabricated:
  - what : the decision in one plain sentence (the subject, de-jargoned)
  - why  : the reasons/evidence (the commit body if present, else a plain
           restatement of the subject's intent — never invented detail)
  - how  : the method/loop that produced it (parsed from a "Method:" / "Loop:"
           trailer when present, else the honest default "direct commit")
  - when : ISO timestamp of the DECISION moment (the commit's author date),
           distinct from when the row was emitted to this ledger

Usage:
    venv/bin/python -m lib.idr_public_emitter backfill --since 2026-06-09T19:30Z
    venv/bin/python -m lib.idr_public_emitter emit <sha>
Designed to be called from a post-push hook later (tasked); idempotent —
a sha already present is never re-appended.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "idr-public.jsonl"
WITHHELD = ROOT / "config" / "idr-public-withheld.json"
# The whole allowlist. The first four are the original order-evident chain;
# the last four are the additive reasoning fields (what/why/how/when). A row
# may carry only the first four (old shape) and still be valid — see
# ``append_rows`` (writes only the keys present) and ``verify_chain``.
_CHAIN_FIELDS = ("sha", "subject", "ts", "prev_sha")
_REASON_FIELDS = ("what", "why", "how", "when")
_FIELDS = _CHAIN_FIELDS + _REASON_FIELDS + ("leaf_sha256",)
# ``leaf_sha256`` exists ONLY on rows the shield rewrote after they were
# anchored: it is the RFC 6962 leaf hash (SHA-256 over 0x00 || the exact
# pre-redaction line) so a verifier can rebuild the anchored Merkle root
# without the redacted text. Rows never redacted carry no such field — their
# leaf hash IS the hash of the served line. A verifier treats the field as a
# substitute leaf only when it is present; it can never hide a row, because
# the row's sha / prev_sha chain and its position are unchanged.

# An absolute path or a private-key block must never reach the public surface.
# The allowlist already drops every field but the commit subject and the four
# reasoning fields; this catches a path/key accidentally pasted INTO any of
# those free-text fields. Case-insensitive so a lower/mixed-case PEM header is
# still caught (BEGIN PRIVATE KEY, BEGIN ENCRYPTED PRIVATE KEY, …); a bare word
# like "beginning" still can't match because the literal "PRIVATE KEY" must
# follow.
_LEAK = re.compile(r"/Users/|/home/[a-z]|BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE)
# Every free-text field the leak scan must police (not the sha/ts/prev_sha,
# which are machine-generated and structurally cannot carry a pasted secret).
_FREE_TEXT_FIELDS = ("subject", "what", "why", "how", "when")

# --- Canonical redaction shield (2026-07-02) -------------------------------
# The _LEAK regex above covers paths + key blocks only; client/external
# identities, personal emails, IPs and JIDs were structurally invisible to it
# (proven live: 8 rows on the published chain). lib/redaction_shield.py is the
# single shim for that class — loaded lazily and cached. Three states matter:
#   ok         -> shield active (emit-time redaction + publish-gate findings)
#   missing    -> legacy behaviour (repo without the feature; tests, forks)
#   ciphertext/invalid -> shield UNAVAILABLE on a locked checkout; emit now
#                 REFUSES (2026-09-17). It used to stay fail-open on the
#                 reasoning that the ledger is local and "not yet public",
#                 leaving the fail-closed duty to decision_chain_publish
#                 .live_publish and bin/grip-chain-sync — both of which do
#                 still gate. That was defensible and is no longer the design:
#                 an unshielded ledger on disk is one `cp` from the clone that
#                 gets pushed, and it made every future consumer responsible
#                 for remembering a gate it did not write. Refusing at the
#                 WRITE point means unshielded text is never produced at all.
#                 A fork with genuinely no denylist opts out explicitly via
#                 GRIP_ALLOW_UNSHIELDED_PUBLISH=1.
UNSHIELDED_ENV = "GRIP_ALLOW_UNSHIELDED_PUBLISH"
"""Explicit opt-out for a repo that genuinely has NO denylist (a fork).

Canonical definition lives HERE, in the lower-level module, because
``decision_chain_publish`` already imports this one and the reverse would be
circular. Its ``_unshielded_publish_allowed`` delegates to
``unshielded_allowed`` below so the two gates cannot drift apart.

Why an env var rather than permitting ``missing`` implicitly: a fork with no
denylist and a PRODUCTION host that has LOST its denylist are indistinguishable
from the state alone, and the second case is what leaked on 2026-07-03 when the
file was absent from the VPS. The fork's need is real; it is met explicitly.
"""


def unshielded_allowed() -> bool:
    """True iff the operator has explicitly opted out of shielding."""
    return os.environ.get(UNSHIELDED_ENV, "").strip().lower() in (
        "1", "true", "yes",
    )


_SHIELD_SENTINEL = object()
_shield_cache = _SHIELD_SENTINEL
_LIB_DIR = Path(__file__).resolve().parent


def redaction_state() -> str:
    """'ok' | 'missing' | 'ciphertext' | 'invalid' — the denylist loadability
    the publish gate branches on. Import-safe: 'missing' if the shield module
    itself is absent (older checkouts).

    'overlay-unconfigured' too, on a machine with no private overlay at all.
    The operator's personal entries live only in that overlay, so publishing
    without it would publish without them (rules/personal-matters-stay-private.md).
    GRIP_REDACTION_OVERLAY=none publishes without it on purpose."""
    if str(_LIB_DIR) not in sys.path:
        sys.path.insert(0, str(_LIB_DIR))
    try:
        from redaction_shield import denylist_state, overlay_state
    except ImportError:
        return "missing"
    state = denylist_state()
    if state == "ok" and overlay_state() == "unconfigured":
        return "overlay-unconfigured"
    return state


def _get_shield():
    """Shield (public profile) or None when unusable — never raises.

    Caches only SUCCESS. The failure is deliberately re-probed on every call,
    because it is recoverable: an operator runs `git-crypt unlock` and the
    denylist becomes readable. Memoising the ``None`` made that recovery
    invisible until the interpreter restarted — so a daemon that happened to
    start on a locked checkout would refuse for its whole lifetime with the
    remedy already applied. Raised as a P2 by the Codex reviewer on #6050.

    Re-probing costs one ``redaction_state()`` call, which is a small file
    read, and ONLY on the path that is already refusing — the healthy path
    still hits the cache on its second call. The asymmetry is the point:
    success is stable, failure is a condition somebody is about to fix.
    """
    global _shield_cache
    if _shield_cache is _SHIELD_SENTINEL or _shield_cache is None:
        if redaction_state() == "ok":
            from redaction_shield import Shield, load_denylist
            _shield_cache = Shield(load_denylist())
        else:
            _shield_cache = None
    return _shield_cache


def _shield_text(text: str) -> str:
    """Leak-marker redaction + denylist/PII redaction in one pass — the single
    transform every public free-text field goes through.

    FAILS CLOSED, and that is the whole point of the tri-state. ``shield is
    None`` means ``redaction_state()`` was not ``"ok"`` — a git-crypt-locked
    checkout, a missing config, or an invalid one. Returning the text in that
    case published it UNSHIELDED, which is precisely the "silently publishing
    unshielded text" that ``denylist_state`` was built to distinguish and
    prevent. A CI runner, a fresh clone and the VPS are all locked by default,
    so the unshielded path was the reachable one, not the exotic one.

    This mirrors ``remediate_ledger`` at the other end of this same file, which
    already refuses on the identical condition because a silently-empty list
    would "REPORT clean while leaving every leak in place". Emission carries
    the larger blast radius of the two — it writes a world-readable surface —
    so it cannot be the laxer of the pair.

    Deliberately NOT the shape used by ``lib/aurora_report.py::_shield_text``,
    whose fail-open is intentional and pinned by
    ``test_shield_text_helper_fail_open_on_redact_error``: an operator digest
    that vanishes because a denylist is locked is a worse outcome than a
    slightly under-redacted internal report. That reasoning does not transfer
    to a public surface, which is why the two helpers now differ on purpose.

    Escape-hatch audit (``rules/gate-resolution-escape-hatch.md`` Invariant 1):
    the blocked condition is "denylist unreadable" and its resolution is
    unlocking git-crypt or repairing the config — neither goes through this
    function, so this refusal cannot block its own remedy.
    """
    text = _redact_leaks(text)
    shield = _get_shield()
    if shield is None and not unshielded_allowed():
        raise RuntimeError(
            "cannot shield for public emission: redaction denylist "
            f"state={redaction_state()!r} — refusing to emit unshielded. "
            "'ciphertext' means run `git-crypt unlock`; 'missing' means the "
            "denylist file or the shield module is absent. Set "
            f"{UNSHIELDED_ENV}=1 on a fork that genuinely has no denylist."
        )
    if shield is None:
        return text  # explicit operator opt-out, audited by the env var
    return shield.redact(text).text


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()[:200]}")
    return out.stdout


def existing_shas(ledger: Path = LEDGER) -> set:
    """Shas already in the ledger; tolerates torn final lines."""
    if not ledger.exists():
        return set()
    shas = set()
    for line in ledger.read_text().splitlines():
        try:
            shas.add(json.loads(line)["sha"])
        except (json.JSONDecodeError, KeyError):
            continue  # torn line — never fatal
    return shas


# Conventional-commit type → plain-English verb phrase, so "what" reads as a
# decision sentence rather than a commit log. Anything unmatched falls through
# to the subject verbatim — honest, never invented.
_TYPE_PHRASE = {
    "feat": "Added", "fix": "Fixed", "refactor": "Refactored",
    "docs": "Documented", "test": "Added tests for", "perf": "Sped up",
    "chore": "Maintained", "style": "Restyled", "build": "Changed the build for",
    "ci": "Changed CI for", "revert": "Reverted",
}
# Present-tense verbs whose corresponding type-phrase ("Added", "Fixed", …)
# would otherwise double up when the subject's description ALSO opens with the
# verb (e.g. "feat: add X" → "Added add X"). A leading match is dropped so the
# sentence reads cleanly ("Added X.").
_REDUNDANT_LEAD = re.compile(
    r"^(add|adds|added|fix|fixes|fixed|refactor|refactors|refactored|"
    r"document|documents|documented|revert|reverts|reverted)\b\s*",
    re.IGNORECASE,
)
# A "Method:" or "Loop:" trailer in the commit body names the loop/gate that
# produced the decision (converge / council / gate / N iterations). Parsed when
# present; otherwise the honest default below is used — never fabricated.
_METHOD_TRAILER = re.compile(r"^(?:Method|Loop|How):\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_DEFAULT_HOW = "Direct commit (no multi-step loop recorded)"
_CONV_RE = re.compile(r"^(\w+)(?:\([^)]*\))?(!)?:\s*(.+)$")


def _plain_what(subject: str) -> str:
    """The decision in one plain sentence: de-jargon a conventional-commit
    subject (``feat(scope): X`` → ``Added X.``). Falls back to the subject
    verbatim when it isn't conventional — honest, never invented."""
    m = _CONV_RE.match(subject)
    if not m:
        return subject if subject.endswith((".", "!", "?")) else subject + "."
    verb = _TYPE_PHRASE.get(m.group(1).lower())
    desc = m.group(3).strip()
    if not verb:
        return desc if desc.endswith((".", "!", "?")) else desc + "."
    desc = _REDUNDANT_LEAD.sub("", desc)            # drop "Added add X" → "Added X"
    desc = desc[:1].lower() + desc[1:] if desc else desc
    return f"{verb} {desc}".rstrip(".") + "."


def _redact_leaks(text: str) -> str:
    """Replace any leak marker (absolute path / private-key block) with a safe
    placeholder so a commit body can never carry one onto the public reasoning
    surface. Remediation, not evasion: the marker is removed from what gets
    published, and ``subjects_with_leaks`` still gates the final result."""
    return _LEAK.sub("[redacted]", text)


def _lead_rationale(body: str) -> str:
    """The decision's own rationale: the first PROSE paragraph of the body.
    Skips a leading ``* subject`` line that a squash-merge prepends (and any
    further ``* …`` graph lines), so the public ``why`` is a short statement of
    intent — never a multi-commit body dump."""
    for para in (p.strip() for p in body.split("\n\n")):
        if para and not para.lstrip().startswith("* "):
            return para
    return ""  # body was only graph-subject lines (or empty) — no prose to show


def _derive_reasoning(subject: str, body: str, ts: str) -> dict:
    """Build the four reasoning fields HONESTLY from a commit's own data.
    No fabrication: ``why`` is the lead rationale paragraph when present (else a
    plain restatement of intent); ``how`` is a parsed Method/Loop trailer (else
    the honest direct-commit default); ``what`` is the de-jargoned subject;
    ``when`` is the commit's author timestamp. Every free-text field is leak
    -redacted so an absolute path or key block in a (possibly squash-merged)
    body never reaches the public surface."""
    body = (body or "").strip()
    method = _METHOD_TRAILER.search(body)
    if body:
        # The why is the lead rationale paragraph, not the whole (possibly
        # squash-merged, multi-commit) body. Drop a Method/Loop line first — it
        # is the how, surfaced separately.
        why = _lead_rationale(_METHOD_TRAILER.sub("", body).strip()) or f"Shipped: {subject}"
    else:
        why = f"Shipped without a recorded rationale beyond the change itself: {subject}"
    fields = {
        "what": _plain_what(subject),
        "why": why,
        "how": method.group(1).strip() if method else _DEFAULT_HOW,
        "when": ts,
    }
    return {k: (_shield_text(v) if isinstance(v, str) else v) for k, v in fields.items()}


def commit_rows(rev_range: str) -> list:
    """Oldest-first allowlisted rows for a git rev range, each enriched with the
    four reasoning fields (what/why/how/when) derived honestly from the commit.
    Uses a NUL record separator + Unit-Separator field split so multi-line
    commit bodies survive intact."""
    # %x00 between records, %x1f between fields → body may contain newlines.
    raw = _git("log", "--reverse", "--format=%H%x1f%s%x1f%cI%x1f%b%x00", rev_range)
    rows = []
    for record in raw.split("\x00"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, ts, body = record.split("\x1f", 3)
        # The subject is itself a public free-text field — shield it exactly
        # like the derived reasoning (a client name in a commit subject was
        # the live 2026-07-02 leak shape).
        subject = _shield_text(subject)
        row = {"sha": sha, "subject": subject, "ts": ts}
        row.update(_derive_reasoning(subject, body, ts))
        rows.append(row)
    return rows


def append_rows(rows: list, ledger: Path = LEDGER) -> int:
    """Chain + append new rows; returns count appended. Idempotent by sha.

    Writes only the allowlisted keys that are PRESENT in each row, in canonical
    order. A row carrying the four reasoning fields is written 8-key; an
    old-shape row carrying only the chain fields is written 4-key. Both are
    valid and verify together — the additive fields never break a mixed chain."""
    seen = existing_shas(ledger)
    prev = _last_sha(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    with ledger.open("a") as fh:
        for row in rows:
            if row["sha"] in seen:
                continue
            record = {**row, "prev_sha": prev}
            fh.write(json.dumps({k: record[k] for k in _FIELDS if k in record}) + "\n")
            prev = row["sha"]
            appended += 1
    return appended


def _last_sha(ledger: Path = LEDGER) -> str | None:
    if not ledger.exists():
        return None
    last = None
    for line in ledger.read_text().splitlines():
        try:
            last = json.loads(line)["sha"]
        except (json.JSONDecodeError, KeyError):
            continue
    return last


def verify_chain(ledger: Path = LEDGER) -> bool:
    """True iff every row's prev_sha equals the prior row's sha."""
    prev = None
    for line in ledger.read_text().splitlines() if ledger.exists() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("prev_sha") != prev:
            return False
        prev = row.get("sha")
    return True


def subjects_with_leaks(ledger: Path = LEDGER) -> list:
    """Free-text values in the ledger that carry a leak marker (absolute path or
    a private-key block). Scans EVERY free-text field — the commit subject AND
    the four reasoning fields (what/why/how/when) — because each is now a public
    surface that could carry a pasted secret. Returns the offending values
    oldest-first; empty list means the ledger is safe to publish. Tolerates torn
    lines (skipped, never fatal) — the single source of truth for the
    public-surface leak check, reused by ``check-leak`` + ``bin/grip-chain-sync``."""
    hits = []
    for line in ledger.read_text().splitlines() if ledger.exists() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn line — never fatal
        hits.extend(_leaking_values(row))
    return hits


def _leaking_values(row: dict) -> list:
    """Free-text values in one row that trip the leak marker OR the denylist
    shield. Scans the fixed five-field allowlist (O(1) per row), so the caller
    stays single-pass over the ledger — no quadratic scan. Shield findings
    come from ONE verify() pass over the newline-joined fields (offsets are
    irrelevant here — only the finding strings gate the publish); when the
    shield is unusable (missing/locked) only the legacy path/key markers fire
    here — but emission itself now refuses in that state, so a row reaching
    this function was written under a readable denylist or an explicit
    GRIP_ALLOW_UNSHIELDED_PUBLISH opt-out."""
    hits = [
        v for f in _FREE_TEXT_FIELDS
        for v in (row.get(f, ""),)
        if isinstance(v, str) and _LEAK.search(v)
    ]
    shield = _get_shield()
    if shield is not None:
        joined = "\n".join(
            v for v in (row.get(f, "") for f in _FREE_TEXT_FIELDS)
            if isinstance(v, str)
        )
        hits.extend(f"{s.category}:{s.original}" for s in shield.verify(joined))
        # Name-shaped findings REFUSE the publish rather than being rewritten.
        # verify() reports residue the shield would have replaced; blockers()
        # reports what it deliberately will not touch — a person, org or case
        # reference that nobody has put on either list. Rewriting with that
        # pattern would turn "Google Drive" into [PERSON]; refusing instead
        # puts the call where it belongs, with a human, once per string.
        hits.extend(f"blocker:{s.category}:{s.original}" for s in shield.blockers(joined))
    return hits


def _remediate_row(row: dict, shield) -> tuple:
    """Shield every free-text field of ONE row in place.
    Returns (changed, replacements). sha/prev_sha/ts are never touched."""
    changed, replacements = False, 0
    for f in _FREE_TEXT_FIELDS:
        v = row.get(f)
        if not isinstance(v, str):
            continue
        result = shield.redact(_redact_leaks(v))
        if result.text != v:
            row[f] = result.text
            replacements += len(result.spans)
            changed = True
    return changed, replacements


# Commits whose free text never reaches the public chain, whatever a scan finds.
# A denylist can only remove what it already knows to look for; withholding by
# commit is the allowlist half (rules/sync-mirror-safety.md). A withheld row
# keeps sha / ts / prev_sha / when, so the chain still links, and remediate()
# records its pre-redaction leaf hash, so every anchor over it still verifies.
_WITHHELD_MARK = "[redacted]"
_WITHHOLD_FULL = ("subject", "what", "why", "how")
_WITHHOLD_TEXT = ("what", "why", "how")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_WITHHELD_KEYS = frozenset({"version", "comment", "full", "text"})


class WithheldListError(RuntimeError, ValueError):
    """The withheld list cannot be trusted, so nothing is remediated or published.
    A RuntimeError like every other refusal here, and a ValueError so callers that
    caught ValueError (the base of the json.JSONDecodeError an unparseable list
    used to raise) still catch it. A caller that named json.JSONDecodeError itself
    does not catch it and must catch ValueError or this class instead."""


def _no_duplicate_keys(pairs: list, path: Path) -> dict:
    """json object_pairs_hook: refuse a key that appears twice in one object.
    json.loads keeps only the last, so a merge that left two "full" blocks would
    silently drop the first block and withhold none of its entries."""
    seen, repeated = set(), set()
    for key, _ in pairs:
        (repeated if key in seen else seen).add(key)
    if repeated:
        raise WithheldListError(f"cannot remediate: withheld list at {path} repeats the keys {sorted(repeated)}")
    return dict(pairs)


def _withheld_entries(data, path: Path) -> tuple:
    """(text, full) from a parsed withheld list. The whole list fails closed
    (GRIP#8994) when it is not an object, carries a key this loader does not
    know (a misspelt "full" or "text" would otherwise withhold nothing), holds
    something other than a list under "full" or "text", or holds an entry that
    is not a full lowercase commit sha: such an entry matches no row, so the
    text it was meant to withhold would be published. The order of entries
    carries no meaning, and a sha listed under both keys is withheld in full,
    the wider scope."""
    if not isinstance(data, dict):
        raise WithheldListError(f"cannot remediate: withheld list at {path} is not a JSON object")
    unknown = sorted(set(data) - _WITHHELD_KEYS)
    if unknown:
        raise WithheldListError(f"cannot remediate: withheld list at {path} has unknown keys {unknown}")
    text, full = data.get("text", []), data.get("full", [])
    if not (isinstance(text, list) and isinstance(full, list)):
        raise WithheldListError(f"cannot remediate: 'full' and 'text' at {path} must be lists")
    bad = [s for s in (*text, *full) if not (isinstance(s, str) and _COMMIT_SHA.fullmatch(s))]
    if bad:
        raise WithheldListError(
            f"cannot remediate: {len(bad)} withheld entries at {path} are not 40-character commit shas"
        )
    return text, full


def _withheld_scopes(path: Path | None = None) -> dict:
    """sha -> the free-text fields withheld from the public chain. A malformed
    list raises, and so does a missing one unless a fork has opted out with
    GRIP_ALLOW_UNSHIELDED_PUBLISH: a fork with no list and a host that has LOST
    its list look identical, and the second would republish every withheld row
    (the reasoning UNSHIELDED_ENV records for a missing denylist)."""
    path = path or WITHHELD
    if not path.exists():
        if unshielded_allowed():
            return {}
        raise WithheldListError(f"cannot remediate: withheld list missing at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=lambda pairs: _no_duplicate_keys(pairs, path))
    except WithheldListError:
        raise  # a repeated key: keep its own reason
    except (OSError, ValueError) as exc:  # unreadable, not UTF-8, not JSON
        raise WithheldListError(f"cannot remediate: withheld list at {path} is unreadable: "
                                f"{type(exc).__name__}") from None
    text, full = _withheld_entries(data, path)
    return {**dict.fromkeys(text, _WITHHOLD_TEXT), **dict.fromkeys(full, _WITHHOLD_FULL)}


def withheld_rows_missing(ledger: Path = LEDGER, withheld: Path | None = None) -> list:
    """Withheld shas that name no row of ``ledger``, sorted, read from the withheld
    list at ``withheld`` (default: the module's list). Each such entry withholds
    nothing: a mistyped sha, a branch commit that never reached main, or a commit
    from another repository. The publisher refuses while any exist
    (``check-withheld``). remediate() does not check this itself, because its
    other callers may work on a ledger that holds only some of the rows. An
    unusable list raises WithheldListError, as everywhere else; a missing or
    unreadable ledger raises OSError or ValueError, so the fault is named as the
    ledger's rather than as every withheld entry's."""
    rows = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            sha = json.loads(line).get("sha")
        except (json.JSONDecodeError, AttributeError):
            continue  # a torn or non-object line names no row
        if isinstance(sha, str):
            rows.add(sha)  # a row whose sha is not a string names no commit
    return sorted(set(_withheld_scopes(withheld)) - rows)


def _cli_check_withheld() -> int:
    """``check-withheld``: exit 2, with the reason on stderr, while any withheld
    sha names no ledger row, or the withheld list or the ledger is unusable."""
    try:
        missing = withheld_rows_missing(LEDGER)
    except WithheldListError as exc:
        print(f"withheld list unusable: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:  # the ledger itself cannot be read
        print(f"ledger unreadable at {LEDGER}: {type(exc).__name__}", file=sys.stderr)
        return 2
    for sha in missing:
        print(f"withheld sha names no row of the ledger: {sha}", file=sys.stderr)
    if missing:
        return 2
    print("every withheld sha names a row")
    return 0


def _withhold(row: dict, fields: tuple) -> bool:
    """Replace the listed free-text fields of ONE row; True if any changed."""
    hits = [f for f in fields if f in row and row[f] != _WITHHELD_MARK]
    row.update(dict.fromkeys(hits, _WITHHELD_MARK))
    return bool(hits)


def remediate(ledger: Path = LEDGER) -> tuple:
    """Rewrite every free-text field of every row through the shield, IN
    PLACE, after withholding the commits listed in config/idr-public-withheld.json.
    Chain-safe by construction: sha / prev_sha / ts are never touched,
    so verify_chain holds before and after. Idempotent — a second run changes
    zero rows (the shield's own fixed point). Unparseable (torn) lines are
    preserved verbatim, never dropped. Returns (rows_changed, replacements).

    Requires a usable denylist: remediation with a silently-empty list would
    REPORT clean while leaving every leak in place, so this fails loudly."""
    if redaction_state() != "ok":
        raise RuntimeError(
            f"cannot remediate: redaction denylist state={redaction_state()!r}"
        )
    shield = _get_shield()
    scopes = _withheld_scopes()
    out_lines, rows_changed, total_replacements = [], 0, 0
    for line in ledger.read_text().splitlines() if ledger.exists() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)  # torn line — preserved, never dropped
            continue
        withheld = _withhold(row, scopes.get(row.get("sha"), ()))
        changed, replacements = _remediate_row(row, shield)
        changed = changed or withheld
        if changed and "leaf_sha256" not in row:
            # First redaction of this row: keep the leaf hash of the exact line
            # that was (or may have been) anchored, so published anchors over
            # the pre-redaction bytes stay verifiable from the redacted ledger.
            row["leaf_sha256"] = hashlib.sha256(b"\x00" + line.encode("utf-8")).hexdigest()
        rows_changed += changed
        total_replacements += replacements
        out_lines.append(json.dumps({k: row[k] for k in _FIELDS if k in row}))
    tmp = ledger.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))
    tmp.replace(ledger)
    return rows_changed, total_replacements


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Public decision-chain emitter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill", help="append commits since a date")
    bf.add_argument("--since", required=True, help="ISO date, e.g. 2026-06-09")
    em = sub.add_parser("emit", help="append one commit by sha")
    em.add_argument("sha")
    sub.add_parser("verify", help="check the prev_sha chain")
    sub.add_parser("check-leak", help="fail (exit 2) if any subject carries a leak marker")
    sub.add_parser("check-withheld", help="fail (exit 2) if a withheld sha names no row of the ledger")
    sub.add_parser("remediate",
                   help="rewrite all free-text fields through the redaction shield (chain-safe)")
    ns = ap.parse_args(argv)

    if ns.cmd == "remediate":
        changed, replacements = remediate(LEDGER)
        print(f"remediated {changed} row(s), {replacements} replacement(s) in {LEDGER}")
        return 0

    if ns.cmd == "backfill":
        rows = [r for r in commit_rows("origin/main") if r["ts"] >= ns.since]
        n = append_rows(rows)
        print(f"appended {n} rows to {LEDGER}")
        return 0
    if ns.cmd == "emit":
        n = append_rows(commit_rows(f"{ns.sha}^!"))
        print(f"appended {n} row(s)")
        return 0
    if ns.cmd == "check-withheld":
        return _cli_check_withheld()
    if ns.cmd == "check-leak":
        hits = subjects_with_leaks(LEDGER)
        for subject in hits:
            print(f"leak marker in subject: {subject}", file=sys.stderr)
        if hits:
            return 2  # the public-surface leak contract (bin/grip-chain-sync exit 2)
        print("no leak markers")
        return 0
    ok = verify_chain()
    print("chain OK" if ok else "chain BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
