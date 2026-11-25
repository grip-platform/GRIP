#!/usr/bin/env python3
"""RFC 3161 trusted timestamps — the external time anchor our chain did not have.

WHY THIS EXISTS. Measured 2026-09-17: a commitment leaf's ``ts`` is written by the
same party the record is meant to constrain, and the audit block carried signatures
only. Signatures prove WHO wrote a leaf and never WHEN. Hash-chaining proves order
*within* the chain and says nothing about order relative to the outside world, so
nothing stopped appending a commitment after the action with a ``ts`` that said
otherwise. ``rules/congruent-continuity.md`` had to be narrowed to admit that the
anti-HARKing property holds against a third party without the signing key and is
NOT established against the writer.

An RFC 3161 Time-Stamp Authority closes exactly that: you send it a digest, it
returns a token binding that digest to a time, signed with ITS key. The writer
cannot backdate it because the writer does not hold that key. A surveyed
competitor (Asqav, source-available under Elastic License 2.0 — not MIT, as an
earlier revision of this line said) already ships this; we recorded its absence.

WHAT LEAVES THIS MACHINE. A sha256 digest and nothing else. The TSA never sees the
record — that is the whole design of the protocol, and it is why anchoring is not
publishing. It is still an outbound request to a third party, so it is OPT-IN:
nothing here runs unless ``GRIP_TSA_URL`` is set.

FAIL-OPEN, BUT NEVER SILENT. A blocked or slow TSA must not stop a commitment from
being recorded — refusing to record a decision because a timestamp server is down
would be worse than recording it unanchored. So the failure is fail-open AND
explicit: the status is ``unavailable`` with a reason, and a caller that stores it
puts that in the leaf. An attacker who blocks the network therefore gets an
UNANCHORED record that says so, rather than an anchored-looking one. Absence that
announces itself is the only kind worth having.
"""
from __future__ import annotations

import base64
import hashlib
import os
import urllib.error
import urllib.request
from typing import Any

#: Opt-in. No outbound request is made unless this is set.
TSA_URL_ENV = "GRIP_TSA_URL"
#: Public authorities, for documentation — NOT a default. Naming one as a default
#: would make every commitment phone a third party without anyone choosing it.
KNOWN_TSAS = (
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "http://freetsa.org/tsr",
)
_TIMEOUT_SEC = 10.0
_CONTENT_TYPE = "application/timestamp-query"
_GRANTED = ("granted", "granted_with_mods")


def tsa_url() -> str:
    return os.environ.get(TSA_URL_ENV, "").strip()


def _fail(reason: str, target: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"status": "unavailable", "anchored": False, "reason": reason}
    if target:
        out["tsa"] = target
    return out


def _build_request(digest: bytes) -> bytes:
    """DER-encode a TimeStampReq for a sha256 digest."""
    from asn1crypto import tsp  # noqa: PLC0415 — optional dependency, imported lazily

    return tsp.TimeStampReq({
        "version": "v1",
        "message_imprint": {
            "hash_algorithm": {"algorithm": "sha256"},
            "hashed_message": digest,
        },
        # Ask for the authority's certificate so the token verifies later without
        # having to fetch the chain separately.
        "cert_req": True,
    }).dump()


def _post(target: str, body: bytes) -> tuple[bytes | None, dict[str, Any] | None]:
    req = urllib.request.Request(target, data=body,
                                 headers={"Content-Type": _CONTENT_TYPE})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:  # noqa: S310
            return resp.read(), None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, _fail(f"{type(exc).__name__}: {exc}", target)


def _extract_token(raw: bytes, target: str) -> tuple[bytes | None, dict[str, Any] | None]:
    try:
        from asn1crypto import tsp  # noqa: PLC0415

        parsed = tsp.TimeStampResp.load(raw)
        status = parsed["status"]["status"].native
        if status not in _GRANTED:
            info = parsed["status"]["fail_info"]
            detail = info.native if info is not None else ""
            return None, _fail(f"authority refused: {status} {detail}".strip(), target)
        return parsed["time_stamp_token"].dump(), None
    except Exception as exc:  # noqa: BLE001
        return None, _fail(f"unparseable response: {type(exc).__name__}: {exc}", target)


def _token_time(token_der: bytes) -> str | None:
    """The genTime the authority asserted, or None when it cannot be read.

    ``encap_content_info['content']`` is a ``ParsableOctetString`` and asn1crypto
    already knows the inner type is TSTInfo, so ``.parsed`` hands back the parsed
    structure. An earlier version called ``TSTInfo.load(encap.native)``, which
    re-parses an already-parsed OrderedDict and raises — the token was fine and
    the reader was wrong, which is why the failure is caught and reported per
    token rather than discarding the anchor.
    """
    from asn1crypto import cms  # noqa: PLC0415

    signed = cms.ContentInfo.load(token_der)["content"]
    encap = signed["encap_content_info"]["content"]
    tst = encap.parsed if hasattr(encap, "parsed") else encap
    gen = tst["gen_time"].native
    return gen.isoformat() if gen is not None else None


def anchor_digest(digest: bytes, *, url: str | None = None) -> dict[str, Any]:
    """Anchor ``digest`` to an RFC 3161 authority. Never raises.

    ``status`` is always present and is one of:

    ``disabled``     — no url configured; nothing was sent.
    ``anchored``     — a token came back; ``gen_time`` and ``token_b64`` are set.
    ``unavailable``  — a request was attempted and failed; ``reason`` says how.

    The three are deliberately distinguishable. Collapsing ``unavailable`` into
    ``disabled`` reintroduces the silent-absence problem this module exists to
    remove: "nobody asked for an anchor" and "an anchor was asked for and did not
    arrive" are different facts about a record.
    """
    target = (url or tsa_url()).strip()
    if not target:
        return {"status": "disabled", "anchored": False}

    try:
        body = _build_request(digest)
    except ImportError as exc:
        return _fail(f"asn1crypto not installed: {exc}")
    except Exception as exc:  # noqa: BLE001 — a malformed request is unavailability
        return _fail(f"could not build request: {type(exc).__name__}: {exc}")

    # "Never raises" is a contract, so it is enforced rather than assumed. Both
    # helpers catch their own expected failures; this catches the unexpected ones,
    # because a commitment must never be lost to an anchor helper throwing. Found
    # by this module's own test, which monkeypatched _post with a raising stub and
    # watched the exception propagate straight out.
    try:
        raw, err = _post(target, body)
        if err is not None:
            return err
        token_der, err = _extract_token(raw or b"", target)
        if err is not None:
            return err
    except Exception as exc:  # noqa: BLE001 — any escape is unavailability
        return _fail(f"anchor helper raised: {type(exc).__name__}: {exc}", target)

    out: dict[str, Any] = {
        "status": "anchored",
        "anchored": True,
        "tsa": target,
        "digest_sha256": digest.hex(),
        "token_b64": base64.b64encode(token_der or b"").decode(),
    }
    try:
        out["gen_time"] = _token_time(token_der or b"")
    except Exception as exc:  # noqa: BLE001 — a token we cannot read is still a token
        out["gen_time"] = None
        out["gen_time_error"] = f"{type(exc).__name__}: {exc}"
    return out


def anchor_text(text: str, *, url: str | None = None) -> dict[str, Any]:
    """Convenience: anchor the sha256 of ``text``."""
    return anchor_digest(hashlib.sha256(text.encode()).digest(), url=url)


if __name__ == "__main__":
    import json
    import sys

    payload = sys.argv[1] if len(sys.argv) > 1 else "grip rfc3161 probe"
    print(json.dumps(anchor_text(payload), indent=2)[:900])
