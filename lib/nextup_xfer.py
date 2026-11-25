#!/usr/bin/env python3
"""nextup_xfer — Candidate A cross-machine next_up transport shim.

Syscall-doctrine: one thin boundary for the low-latency cross-machine hop
of the #2917 screened `next_up` snapshot over the Tailscale LAN. It is the
best-effort-fast rail; the durable-slow floor is #2917's git-synced
`state/nextup/{enc(scope)}/` snapshot, which every failure here degrades
to silently.

Reuses (DRY — never reimplements):
  - ``next_step_block._enc_scope``      — sha256[:32], injective partition.
  - ``nextup_mesh._screen_snapshot``    — allowed-keys + size-cap +
    secret-shape reject (returns None ⇒ DROP).

Invariants:
  - **Fail-open**: every error ⇒ a benign value (False / []); never raises,
    never blocks the caller (the R3 "notification never delays the caller"
    contract `holistic_synthesiser` already honours).
  - **Defence-in-depth**: the wire is untrusted — a received snapshot is
    re-screened before it is ever partitioned/written.
  - **Firewall by construction**: a snapshot only ever maps to
    ``_enc_scope(scope)`` (sha256, injective) — a forged scope reaches
    only its OWN partition, never another scope's.

Stdlib only (matches `relay_dispatcher`'s stdlib-only constraint).
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from typing import Callable, Optional, Tuple

import next_step_block as nsb
import nextup_mesh as nxm

DEFAULT_TIMEOUT = 2.0


def screen_and_partition(body: object) -> Tuple[Optional[str], Optional[dict]]:
    """Re-screen an inbound snapshot and compute its partition token.

    NEVER trusts the wire. Returns ``(enc, screened)`` on success, or
    ``(None, None)`` if the body is not a dict, has no string ``scope``,
    is screened out (oversize / secret-shaped), or lost ``scope`` to the
    screen (fail-safe against an upstream allowed-keys change).
    """
    if not isinstance(body, dict):
        return None, None
    scope = body.get("scope")
    if not isinstance(scope, str) or not scope:
        return None, None
    screened = nxm._screen_snapshot(body)
    if screened is None or "scope" not in screened:
        return None, None
    return nsb._enc_scope(screened["scope"]), screened


def _http_post(url: str, payload: bytes, timeout: float) -> int:
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def post_snapshot(
    endpoint: str,
    screened: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    http_post: Callable[[str, bytes, float], int] = _http_post,
) -> bool:
    """Best-effort POST a screened snapshot to ``{endpoint}/nextup``.

    Fail-open: ANY error ⇒ ``False`` (never raises). The #2917 git-sync
    floor covers every miss. ``http_post`` is DI (no network in unit).
    """
    try:
        url = endpoint.rstrip("/") + "/nextup"
        payload = json.dumps(
            screened, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        status = http_post(url, payload, timeout)
        return 200 <= int(status) < 300
    except BaseException:
        return False


def get_scope(
    endpoint: str,
    enc: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    http_get: Callable[[str, float], bytes] = _http_get,
) -> list:
    """Best-effort GET ``{endpoint}/nextup/{enc}`` → list of peer snapshots.

    ``enc`` is the caller's OWN ``_enc_scope`` token (the firewall: a
    session only ever GETs its own partition). Token is path-safety
    checked so a poisoned token can never build a traversal URL.
    Fail-open: ANY error (network / parse / shape) ⇒ ``[]``.
    """
    try:
        if not enc or "/" in enc or "\\" in enc or ".." in enc:
            return []
        url = endpoint.rstrip("/") + "/nextup/" + enc
        data = json.loads(http_get(url, timeout))
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]
    except BaseException:
        return []


def sync(
    project_path,
    endpoint: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    http_post: Callable[[str, bytes, float], int] = _http_post,
    http_get: Callable[[str, float], bytes] = _http_get,
) -> None:
    """Best-effort cross-machine sync for the laptop/peer side (W4).

    1. POST THIS session's just-published local snapshot (the file
       ``publish_unified_next_up`` wrote) to the VPS endpoint.
    2. GET the in-scope peer snapshots and write them into the LOCAL
       ``_enc_scope`` partition so the UNCHANGED
       ``synthesise_unified_next_up`` merges them — zero kernel touch (R2).

    Firewall: only ever touches ``_enc_scope(scope)`` (own partition); a
    pulled peer is re-screened AND its own scope must re-hash to the same
    ``enc`` before it is written (defence-in-depth — never write a peer
    blob into a scope dir its scope does not compute to). Fail-open
    ABSOLUTE: ANY error ⇒ silent return (the #2917 git-sync floor covers
    every miss). Never raises, never blocks the caller (R3).
    """
    try:
        scope = nsb.project_scope_key(project_path)
        enc = nsb._enc_scope(scope)
        own = (
            nxm._SNAP_ROOT / enc
            / f"{nxm._machine_id()}--{nxm._session_id()}.json"
        )
        if own.exists():
            try:
                body = json.loads(own.read_text(encoding="utf-8"))
                post_snapshot(endpoint, body, timeout=timeout,
                              http_post=http_post)
            except BaseException:
                pass  # POST best-effort; the pull + #2917 floor still run
        for peer in get_scope(endpoint, enc, timeout=timeout,
                              http_get=http_get):
            e2, screened = screen_and_partition(peer)
            if e2 == enc and screened is not None:
                digest = hashlib.sha256(
                    json.dumps(screened, ensure_ascii=False, sort_keys=True)
                    .encode("utf-8")
                ).hexdigest()[:16]
                nxm._atomic_write(
                    nxm._SNAP_ROOT / enc / f"xfer-{digest}.json", screened
                )
    except BaseException:
        pass  # fail-open ABSOLUTE — never break the caller
