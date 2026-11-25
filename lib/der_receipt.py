"""Decision Evidence Receipt (DER) — assemble + zero-trust verify.

The "smallest proof" of GRIP's cryptographic-causation moat: bind one real AI
decision into a receipt a third party (a General Counsel, opposing counsel, a
regulator) can verify against Bitcoin with ZERO vendor trust, using only
``cryptography`` + the ``ots`` CLI + Python stdlib — no GRIP runtime.

Four independent legs, each falsifiable on its own:

  1. FIXITY          recompute sha256 of the canonical decision payload; must
                     equal ``receipt.payload_sha256``. One flipped byte breaks it.
  2. AUTHENTICITY    an **Ed25519** (asymmetric) signature over that hash, verified
                     with the receipt's embedded PUBLIC key. Zero-trust: no shared
                     secret, unlike the council's own HMAC seal.
  3. TIMESTAMP       an OpenTimestamps proof committing the hash to Bitcoin
                     (``pending`` until a block confirms it, then a
                     ``BitcoinBlockHeaderAttestation`` at a specific height).
  4. JOINT-CAUSATION the belief-state (which models decided, the memory head) is
                     *inside* the signed payload. Swap what the AI believed and
                     leg 1 (thus 2) breaks — EVEN with a valid timestamp. A
                     commodity OTS anchor cannot reproduce this; it is the moat,
                     not the anchor.

HONEST LIMIT (the council's #1 gap — never dropped): the DER attests a decision
record's AUTHENTICITY, FIXITY, TIME and BELIEF-BINDING. It does NOT attest the
upstream decision's *correctness*, nor the integrity of inputs fed to the model
before the record was signed. Evidence-of-record, not a compliance guarantee and
not a guarantee of admissibility.

Origin: task #546, V>> 2026-07-18. Import-light on purpose (stdlib + cryptography
+ subprocess ots) so ``verify_receipt`` is copy-paste portable into a standalone
verifier a skeptic runs without installing GRIP.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:  # asymmetric signing — the zero-trust leg. cryptography is a project dep.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _HAVE_ED25519 = True
except Exception:  # pragma: no cover - environment without cryptography
    _HAVE_ED25519 = False

DER_VERSION = "der/1"


# ----------------------------------------------------------------- primitives
def canonical(obj: Any) -> bytes:
    """Deterministic JSON — the ONLY thing hashed/signed. Sorted keys, no
    insignificant whitespace, UTF-8. Two parties compute identical bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _memory_head(speakers: list, judge: Any, sealed_sha256: Any) -> str:
    """Hash of the belief context — the joint-causation binding. Swapping any of
    it changes the payload hash and breaks the signature."""
    return sha256_hex(canonical({"speakers": speakers, "judge": judge,
                                 "sealed_sha256": sealed_sha256}))


# ----------------------------------------------------------------- the payload
def build_payload(record: dict) -> dict:
    """Extract the load-bearing decision + belief-state from a sealed
    sovereign-council record. This dict IS hashed, signed and anchored."""
    speakers = [
        {"provider": r.get("provider"), "model": r.get("model")}
        for r in (record.get("results") or record.get("speakers") or [])
        if isinstance(r, dict)
    ]
    judge = record.get("judge") or record.get("judge_model")
    sealed = record.get("sha256")
    return {
        "der_version": DER_VERSION,
        "decision_id": record.get("idr") or record.get("idr_leaf") or record.get("id"),
        "sealed_sha256": sealed,
        "verdict": record.get("verdict") or record.get("consensus"),
        "belief_state": {"speakers": speakers, "judge": judge,
                         "memory_head": _memory_head(speakers, judge, sealed)},
    }


# ----------------------------------------------------------------- OTS anchor
def ots_stamp(target_hex: str, ots_bin: str = "ots", timeout: int = 40) -> Optional[str]:
    """OpenTimestamps-stamp the payload hash. Returns base64 .ots proof, or None
    if the ots CLI / calendar is unreachable (fail-soft — never faked)."""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "der.hash"
        f.write_text(target_hex)
        try:
            subprocess.run([ots_bin, "stamp", str(f)], capture_output=True,
                           text=True, timeout=timeout, check=True)
        except Exception:
            return None
        proof = f.with_suffix(".hash.ots")
        return _b64e(proof.read_bytes()) if proof.is_file() else None


def _parse_ots_status(text: str) -> dict:
    """Read the ots client's own words. ``confirmed`` needs its success line.

    The client prints ``Success! Bitcoin block N attests ...`` (otsclient/cmds.py
    :431) only after ``verify_against_blockheader`` has actually passed. Merely
    seeing the words ``BitcoinBlockHeaderAttestation`` proves nothing — ``ots
    info`` prints that for any proof, verified or not — so it is not accepted."""
    detail = text.strip()[:400]
    hit = _OTS_SUCCESS_RE.search(text)
    if hit:
        return {"status": "confirmed", "block": int(hit.group(1)), "detail": detail}
    low = text.lower().replace(" ", "")
    if "pending" in low or "notyet" in low:
        return {"status": "pending", "block": None, "detail": detail}
    return {"status": "error", "block": None, "detail": detail}


# ------------------------------------------------- bitcoin block header lookup
#
# `ots verify` wants a Bitcoin node. What one costs, measured 2026-08-01: a
# PRUNED node still performs a full initial block download, because `prune=5000`
# is the retained steady-state size and not the download — the chain is 758 GB
# today. What that buys is a trustless answer for OUR OWN check. It proves
# nothing to a third party, who verifies the anchor against their own node
# either way; the anchor's whole value is that anyone can check it independently.
#
# So verification is tiered, and the tier that produced a verdict is always in
# the result rather than hidden behind a bare "confirmed":
#
#   bitcoin-node          a reachable node did it. No trust assumption.
#   multi-source-header   >= 2 independent header sources returned the identical
#                         header. Trust: those operators would have to collude.
#   neither               NOT confirmed. A confirmation is never manufactured.
#
# The lighter tier is sound because `ots --no-bitcoin verify` still binds the
# digest (cmds.py:460 with -d, :485 by file hash) and then COMPUTES the merkle
# root up from it through the proof's operations, printing the result. Only the
# "is that root really in block N" lookup is outsourced. Restoring the trustless
# tier later needs no code change: point GRIP_BITCOIN_NODE at a node. And any
# node answers it, pruned or not — `getblockheader` reads the block index via
# LookupBlockIndex and never touches block data, so the pruned-data guard
# (bitcoin/src/rpc/blockchain.cpp CheckBlockDataAvailability) cannot fire on it.

DEFAULT_HEADER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("blockstream.info",
     "https://blockstream.info/api/block-height/{height}",
     "https://blockstream.info/api/block/{block_hash}"),
    ("mempool.space",
     "https://mempool.space/api/block-height/{height}",
     "https://mempool.space/api/block/{block_hash}"),
)
MIN_AGREEING_SOURCES = 2
HTTP_TIMEOUT_CAP = 15  # seconds any single HTTP request may take
_TRUST_NODE = "none — a Bitcoin node validated the chain locally"
_MANUAL_ATTESTATION_RE = re.compile(
    r"Bitcoin block (\d+) has merkleroot ([0-9a-fA-F]{64})")
_BLOCK_HASH_RE = re.compile(r"[0-9a-f]{64}")
_OTS_SUCCESS_RE = re.compile(r"Success!\s*Bitcoin block (\d+)", re.IGNORECASE)
#: The client says it is skipping a Bitcoin attestation even when told not to
#: check one. Seeing that while parsing zero attestations means the PARSER
#: failed — not that the proof is still waiting on a block.
#:
#: Deliberately BROAD, and the direction is the whole point. Matching loosely
#: can misread an unrelated mention as a parse failure, which reports "could
#: not establish anything". Matching tightly (say, the exact current sentence)
#: would miss a reworded client and fall back to reporting "still pending" —
#: a confident claim about the world, drawn from a parser that just broke.
#: Between erring toward "I cannot tell" and erring toward a false "not yet",
#: an honesty mechanism must take the first. So a false positive here is an
#: accepted cost, not an oversight.
_ATTESTATION_MENTION_RE = re.compile(r"Bitcoin attestation", re.IGNORECASE)
#: Header sources are fetched over the network, so only https is accepted. No
#: untrusted input reaches ``header_sources`` today (the sole production caller
#: passes the default, and the interpolated height and block hash are regex-
#: constrained to digits and hex before use), but a future caller could plumb
#: it from config — and then file://, http:// to a metadata endpoint, or a
#: private host would all be reachable. Cheap to refuse now, awkward to add
#: after someone has depended on it.
_ALLOWED_SOURCE_SCHEME = "https://"


# The caller's ``timeout`` is a budget for the WHOLE check, not per request.
# Without that distinction the cost multiplies out: attestations (3 on a real
# proof) x sources x 2 requests each, every one waiting the full timeout. A
# single slow source could then stall a 40 s check for minutes. So one deadline
# is set at entry and every step spends against what is left of it.
def _deadline_in(seconds: float) -> float:
    return time.monotonic() + seconds


def _remaining(deadline: Optional[float]) -> float:
    return float("inf") if deadline is None else max(0.0, deadline - time.monotonic())


def _budget(cap: float, deadline: Optional[float]) -> float:
    return min(cap, _remaining(deadline))


def _describe_fetch_error(exc: Exception) -> str:
    """Say WHY a source failed, so a broken API is not read as an outage."""
    if isinstance(exc, _RefusedRedirect):
        # Ours, not the server's. Reporting it as the redirect's status code
        # ("HTTP 302") would hide a security refusal behind an ordinary-looking
        # response, so the reason is kept. Must stay ABOVE the HTTPError branch,
        # which this subclasses.
        return str(exc.reason)
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable ({exc.reason})"
    if isinstance(exc, json.JSONDecodeError):
        return "malformed JSON"
    if isinstance(exc, KeyError):
        return f"response is missing the field {exc}"
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, ValueError):
        # Our own refusals (a non-https source) land here. The message is
        # already credential-safe via _safe_url_label, and "ValueError" alone
        # would tell an operator nothing about what was rejected or why.
        return str(exc)
    return type(exc).__name__


def _safe_url_label(url: str) -> str:
    """Scheme and host only — never the raw URL, in any message.

    A URL can carry credentials in its userinfo (``https://user:token@host/``),
    so echoing even a truncated prefix can put a secret into an error string,
    a log line, or a returned ``error`` field. ``urlsplit().hostname`` drops
    userinfo by construction, which is the property we want rather than a
    hand-rolled regex over something this easy to get wrong."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    return f"{parts.scheme or '?'}://{parts.hostname or '?'}"


class _RefusedRedirect(urllib.error.HTTPError):
    """Our own refusal, distinguishable from a status the server really sent.

    It has to BE an HTTPError so urllib's machinery unwinds correctly, but
    reporting it like one would surface a security block as a bland "HTTP 302".
    The separate type is what lets the reason survive to the caller."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Hold the https guarantee across redirects, not just at the first hop.

    Checking the URL we were handed is not enough: urlopen follows redirects
    on its own, so a trusted host answering with a 302 to ``http://`` — or to
    an internal address — would walk the request straight past the check. The
    scheme is re-tested on every hop, and a failing one is refused rather than
    followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(_ALLOWED_SOURCE_SCHEME):
            raise _RefusedRedirect(
                newurl, code, "refusing a redirect to "
                f"{_safe_url_label(newurl)} — header sources must stay "
                f"{_ALLOWED_SOURCE_SCHEME}…", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects)


def _http_get(url: str, timeout: float) -> str:
    if not url.startswith(_ALLOWED_SOURCE_SCHEME):
        raise ValueError(f"header sources must be {_ALLOWED_SOURCE_SCHEME}… — "
                         f"refusing {_safe_url_label(url)}")
    req = urllib.request.Request(url, headers={"User-Agent": "grip-der/1"})
    with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace").strip()


def fetch_block_header(source: tuple[str, str, str], height: int,
                       timeout: float = HTTP_TIMEOUT_CAP, *,
                       deadline: Optional[float] = None) -> dict:
    """One source's view of a block header. Always returns a dict.

    ``ok`` False carries an ``error`` saying why, so an operator can tell a
    changed API ("response is missing the field 'merkle_root'") from an
    unreachable one ("HTTP 503") — a bare None conflated the two."""
    name, height_url, block_url = source
    if _budget(timeout, deadline) <= 0:
        return {"source": name, "ok": False, "error": "no time left in the budget"}
    try:
        block_hash = _http_get(height_url.format(height=height),
                               _budget(timeout, deadline))
        if not _BLOCK_HASH_RE.fullmatch(block_hash):
            return {"source": name, "ok": False,
                    "error": f"expected a block hash, got {block_hash[:32]!r}"}
        data = json.loads(_http_get(block_url.format(block_hash=block_hash),
                                    _budget(timeout, deadline)))
        return {"source": name, "ok": True, "block_hash": block_hash,
                "merkleroot": str(data["merkle_root"]).lower(),
                "time": int(data["timestamp"])}
    except Exception as exc:  # noqa: BLE001 - classified, never silently swallowed
        return {"source": name, "ok": False, "error": _describe_fetch_error(exc)}


def agree_on_block_header(height: int, *,
                          sources: tuple = DEFAULT_HEADER_SOURCES,
                          timeout: float = HTTP_TIMEOUT_CAP,
                          min_sources: int = MIN_AGREEING_SOURCES,
                          deadline: Optional[float] = None) -> dict:
    """Independent sources must agree EXACTLY. Disagreement is a red flag.

    Deliberately not a majority vote: if two operators report different headers
    for the same height, something is wrong and the honest answer is "unverified",
    not "the more popular one wins"."""
    answers = [fetch_block_header(s, height, timeout, deadline=deadline)
               for s in sources]
    seen = [a for a in answers if a["ok"]]
    if len(seen) < min_sources:
        why = "; ".join(f"{a['source']}: {a['error']}" for a in answers if not a["ok"])
        return {"agreed": False, "headers": seen,
                "reason": (f"only {len(seen)} of {len(sources)} header sources "
                           f"answered; {min_sources} agreeing sources are required"
                           + (f" ({why})" if why else ""))}
    key = ("block_hash", "merkleroot", "time")
    first = seen[0]
    if any(tuple(h[k] for k in key) != tuple(first[k] for k in key) for h in seen[1:]):
        return {"agreed": False, "headers": seen,
                "reason": (f"header sources DISAGREE about block {height} — "
                           "refusing to treat this as verified")}
    return {"agreed": True, "headers": seen, "reason": "",
            "block_hash": first["block_hash"], "merkleroot": first["merkleroot"],
            "time": first["time"]}


def _attested_heights(ots_bin: str, proof: Path, target: Path,
                      timeout: float) -> Optional[list]:
    """(height, merkleroot) pairs the client COMPUTES from our digest.

    None means the client could not be run, OR it ran and clearly HAD Bitcoin
    attestations that this parser failed to read — both are failures to
    establish anything. An empty list is the honestly different case: the
    client ran and the proof genuinely carries no Bitcoin attestation yet.

    That distinction matters because the two look identical from the outside.
    If the ots client reworded its output, every proof would silently read as
    "still pending" — a broken parser wearing the costume of a proof that
    simply has not landed yet. ``_ATTESTATION_MENTION_RE`` is the discriminator:
    the client names its Bitcoin attestations even when told not to check them,
    so lines mentioning them with zero parsed pairs means the PARSER failed."""
    argv = [ots_bin, "--no-bitcoin", "verify", "-f", str(target), str(proof)]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001 - ots missing or unrunnable
        return None
    text = (out.stdout or "") + (out.stderr or "")
    pairs = [(int(h), r.lower()) for h, r in _MANUAL_ATTESTATION_RE.findall(text)]
    if not pairs and _ATTESTATION_MENTION_RE.search(text):
        return None
    return pairs


def _confirmed_by_headers(height: int, merkleroot: str, agreement: dict) -> dict:
    names = ", ".join(h["source"] for h in agreement["headers"])
    return {
        "status": "confirmed", "block": height,
        "block_hash": agreement["block_hash"], "block_time": agreement["time"],
        "verified_by": "multi-source-header",
        "trust": (f"{len(agreement['headers'])} independent block-header sources "
                  f"({names}) returned the identical header, and they would have "
                  "to collude to forge it. This is NOT a local full node."),
        "detail": (f"confirmed against independent block headers: block {height} "
                   f"({agreement['block_hash']}) carries merkleroot {merkleroot}, "
                   f"which this proof commits to. Sources agreeing: {names}."),
    }


def _check_via_header_sources(ots_bin: str, proof: Path, target: Path, timeout: float,
                              sources: tuple, min_sources: int,
                              deadline: Optional[float] = None) -> dict:
    """Tier 2 — confirm the computed merkle root against independent headers."""
    pairs = _attested_heights(ots_bin, proof, target, _budget(timeout, deadline))
    if pairs is None:
        return {"status": "error", "block": None, "verified_by": None, "trust": None,
                "detail": f"could not run the ots client ({ots_bin})"}
    if not pairs:
        return {"status": "pending", "block": None, "verified_by": None, "trust": None,
                "detail": "the proof carries no Bitcoin attestation yet"}
    notes = []
    for height, merkleroot in sorted(pairs):
        if _remaining(deadline) <= 0:
            notes.append(f"block {height}: ran out of the verification time budget")
            break
        agreement = agree_on_block_header(height, sources=sources,
                                          timeout=min(HTTP_TIMEOUT_CAP, timeout),
                                          min_sources=min_sources, deadline=deadline)
        if not agreement["agreed"]:
            notes.append(f"block {height}: {agreement['reason']}")
        elif agreement["merkleroot"] != merkleroot:
            notes.append(f"block {height}: MERKLEROOT MISMATCH — the proof commits "
                         f"to {merkleroot} but block {height} really carries "
                         f"{agreement['merkleroot']}")
        else:
            return _confirmed_by_headers(height, merkleroot, agreement)
    mismatched = any("MISMATCH" in n for n in notes)
    return {"status": "error" if mismatched else "pending", "block": None,
            "verified_by": None, "trust": None,
            "detail": "not confirmed against block headers: " + "; ".join(notes)}


def _check_via_node(ots_bin: str, proof: Path, node: str, timeout: int) -> Optional[dict]:
    """Tier 1 — a reachable Bitcoin node. None when it could not confirm."""
    argv = [ots_bin] + (["--bitcoin-node", node] if node else []) + ["verify", str(proof)]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001 - fall through to the lighter tier
        return None
    res = _parse_ots_status((out.stdout or "") + (out.stderr or ""))
    if res["status"] != "confirmed":
        return None
    res.update(verified_by="bitcoin-node", trust=_TRUST_NODE,
               detail="confirmed by a Bitcoin node: " + res["detail"])
    return res


def ots_check(target_hex: str, ots_b64: str, ots_bin: str = "ots", timeout: int = 40,
              *, bitcoin_node: Optional[str] = None,
              header_sources: tuple = DEFAULT_HEADER_SOURCES,
              min_sources: int = MIN_AGREEING_SOURCES) -> dict:
    """Verify an OTS proof → {status, block, verified_by, trust, detail}.

    A Bitcoin node is tried first and needs no trust at all. Failing that, the
    merkle root the proof commits to is checked against independent block-header
    sources; that IS reported as ``confirmed``, but the result always carries the
    ``trust`` assumption which made it possible, so no caller can mistake it for
    a local full-node verification. With neither tier available the status stays
    ``pending`` or ``error`` — a confirmation is never manufactured.

    Set ``GRIP_BITCOIN_NODE`` (or pass ``bitcoin_node``) to an RPC URL to use a
    node. Pass ``header_sources=()`` to refuse the lighter tier entirely.

    ``timeout`` budgets the WHOLE check, not each request within it."""
    deadline = _deadline_in(timeout)
    with tempfile.TemporaryDirectory() as td:
        proof = Path(td) / "der.hash.ots"
        proof.write_bytes(_b64d(ots_b64))
        target = Path(td) / "der.hash"
        target.write_text(target_hex)
        node = bitcoin_node or os.environ.get("GRIP_BITCOIN_NODE") or ""
        confirmed = _check_via_node(ots_bin, proof, node, _budget(timeout, deadline))
        if confirmed is not None:
            return confirmed
        if not header_sources:
            return {"status": "pending", "block": None, "verified_by": None,
                    "trust": None,
                    "detail": ("no Bitcoin node reachable and block-header lookup "
                               "is disabled, so the timestamp is unverified here")}
        return _check_via_header_sources(ots_bin, proof, target, timeout,
                                         header_sources, min_sources, deadline)


# ----------------------------------------------------------------- build
def _sign(payload_sha256: str, private_key_b64: Optional[str]) -> dict:
    priv = (Ed25519PrivateKey.from_private_bytes(_b64d(private_key_b64))
            if private_key_b64 else Ed25519PrivateKey.generate())
    sig = priv.sign(payload_sha256.encode("ascii"))
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"alg": "ed25519", "sig_b64": _b64e(sig), "pubkey_b64": _b64e(pub_raw)}


def build_receipt(record: dict, *, private_key_b64: Optional[str] = None,
                  ots_bin: str = "ots", do_stamp: bool = True) -> dict:
    """Assemble a full Decision Evidence Receipt from a sealed decision record."""
    if not _HAVE_ED25519:
        raise RuntimeError("cryptography (Ed25519) required to build a receipt")
    payload = build_payload(record)
    payload_sha256 = sha256_hex(canonical(payload))
    anchor = {"alg": "opentimestamps", "target_sha256": payload_sha256,
              "status": "absent", "ots_b64": None, "block": None}
    if do_stamp:
        proof = ots_stamp(payload_sha256, ots_bin=ots_bin)
        if proof:
            anchor.update(status="pending", ots_b64=proof)
    return {
        "der_version": DER_VERSION, "decision": payload,
        "payload_sha256": payload_sha256,
        "signature": _sign(payload_sha256, private_key_b64), "anchor": anchor,
        "honest_limit": (
            "Attests this record's AUTHENTICITY, FIXITY, TIME and BELIEF-BINDING. "
            "Does NOT attest the upstream decision's correctness nor pre-signing "
            "input integrity. Evidence-of-record, not a compliance/admissibility "
            "guarantee."),
    }


# ----------------------------------------------------------------- verify legs
def _leg_fixity(receipt: dict) -> tuple[bool, dict]:
    recomputed = sha256_hex(canonical(receipt.get("decision", {})))
    claimed = receipt.get("payload_sha256", "")
    return recomputed == claimed, {"recomputed": recomputed, "claimed": claimed}


def _leg_joint_causation(receipt: dict, fixity_ok: bool) -> tuple[bool, dict]:
    payload = receipt.get("decision", {})
    belief = payload.get("belief_state", {})
    expect = _memory_head(belief.get("speakers"), belief.get("judge"),
                          payload.get("sealed_sha256"))
    ok = (belief.get("memory_head") == expect) and fixity_ok
    return ok, {"expected_head": expect, "receipt_head": belief.get("memory_head")}


def _leg_authenticity(receipt: dict) -> tuple[bool, Any]:
    if not _HAVE_ED25519:
        return False, "cryptography (Ed25519) not available to verify"
    sig = receipt.get("signature", {})
    claimed = receipt.get("payload_sha256", "")
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(sig.get("pubkey_b64", "")))
        pub.verify(_b64d(sig.get("sig_b64", "")), claimed.encode("ascii"))
        return True, "ed25519 signature valid for payload_sha256"
    except (InvalidSignature, Exception) as exc:  # noqa: BLE001
        return False, f"signature INVALID: {type(exc).__name__}"


def _leg_timestamp(receipt: dict, ots_bin: str, check_anchor: bool) -> tuple[str, dict]:
    anchor = receipt.get("anchor", {})
    if check_anchor and anchor.get("ots_b64"):
        res = ots_check(anchor.get("target_sha256", receipt.get("payload_sha256", "")),
                        anchor["ots_b64"], ots_bin=ots_bin)
        return res["status"], res
    status = anchor.get("status", "absent")
    return status, {"status": status, "note": "no ots proof in receipt"}


def verify_receipt(receipt: dict, *, ots_bin: str = "ots",
                   check_anchor: bool = True) -> dict:
    """Zero-trust verify — cryptography + stdlib + ots only, NO GRIP runtime.

    ok is True iff fixity + authenticity + joint-causation hold. A *pending*
    timestamp is reported but does not fail the receipt (a real commitment
    awaiting Bitcoin confirmation); only these three legs gate ``ok``."""
    checks: dict = {}
    detail: dict = {}
    checks["fixity"], detail["fixity"] = _leg_fixity(receipt)
    checks["joint_causation"], detail["joint_causation"] = _leg_joint_causation(
        receipt, checks["fixity"])
    checks["authenticity"], detail["authenticity"] = _leg_authenticity(receipt)
    checks["timestamp"], detail["timestamp"] = _leg_timestamp(receipt, ots_bin, check_anchor)
    ok = bool(checks["fixity"] and checks["authenticity"] and checks["joint_causation"])
    return {"ok": ok, "checks": checks, "detail": detail}
