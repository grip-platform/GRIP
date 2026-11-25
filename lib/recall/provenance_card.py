"""Screen-shareable provenance card over a recall QueryResult (T3, task #505).

The delight surface for the recall substrate: take one ``QueryResult`` dict
(pinned in lib/recall/schema.py), fold it into a compact card, render it as a
GRASP-receipt-style box (plain box-drawing, no colour, no emoji — pastes
cleanly into Slack), and re-verify it LIVE against the index so the moat
proves itself on demand.

Dependency boundary: ``reverify`` and the module main lazily import
``lib.recall.index`` (query_subject / verify_artefact — Track 1 delivers
them in parallel). Absence raises a plain-language RuntimeError naming the
missing module; callers may always inject ``query_fn`` / ``verify_fn``.

Usage::

    python3 -m lib.recall.provenance_card --subject "AOD Arnold" [--store DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from lib.recall.schema import content_hash

__all__ = ["build_card", "render", "reverify"]

_TITLE = "GRIP RECALL · PROVENANCE CARD"
_SEP = object()  # sentinel row -> horizontal rule inside the box
_SHORT = 12

_MISSING_INDEX_MSG = (
    "The recall index module is not available: lib.recall.index could not be "
    "imported (its {name} function ships on the engine track, T1). Build or "
    "install the recall index first, or pass {param} explicitly."
)


# ------------------------------------------------------------------ helpers
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(value) -> str:
    return str(value or "")[:_SHORT]


def _as_result_dict(result) -> dict:
    """Accept a QueryResult dataclass or its dict form; return the dict form."""
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(result)


def _result_hash_of(result_dict: dict) -> str:
    """The pinned hash: subject + artefacts ONLY (never wall-clock freshness)."""
    existing = result_dict.get("result_hash")
    if existing:
        return existing
    return content_hash(
        {
            "subject": result_dict.get("subject", ""),
            "artefacts": list(result_dict.get("artefacts") or ()),
        }
    )


def _proof_summary(inclusion_proof) -> dict | None:
    if not inclusion_proof:
        return None
    return {
        "leaf": inclusion_proof.get("leaf", ""),
        "position": inclusion_proof.get("position"),
        "proof_length": len(inclusion_proof.get("proof") or ()),
        "root": inclusion_proof.get("root", ""),
    }


# --------------------------------------------------------------- build_card
def build_card(query_result, store_dir=None) -> dict:
    """Fold a QueryResult(-dict) into the shareable provenance card."""
    result = _as_result_dict(query_result)
    freshness = result.get("freshness") or {}
    card = {
        "subject": result.get("subject", ""),
        "artefacts": list(result.get("artefacts") or ()),
        "result_hash": _result_hash_of(result),
        "index_head": freshness.get("index_head", ""),
        "proof_summary": _proof_summary(result.get("inclusion_proof")),
        "built_at": _utc_now_iso(),
    }
    if store_dir is not None:
        card["store"] = str(store_dir)
    return card


# ------------------------------------------------------------------- render
def _wrap(text, inner: int) -> list:
    text = str(text)
    if not text:
        return [""]
    return [text[i : i + inner] for i in range(0, len(text), inner)]


def _flatten(rows, inner: int) -> list:
    """Expand each content row to width-bounded pieces; keep _SEP sentinels."""
    pieces = []
    for row in rows:
        if row is _SEP:
            pieces.append(_SEP)
        else:
            pieces.extend(_wrap(row, inner))
    return pieces


def _box(rows, width: int) -> str:
    inner = max(24, width) - 4
    rule = "├" + "─" * (inner + 2) + "┤"
    body = [
        rule if piece is _SEP else "│ " + str(piece).ljust(inner) + " │"
        for piece in _flatten(rows, inner)
    ]
    top = "┌" + "─" * (inner + 2) + "┐"
    bottom = "└" + "─" * (inner + 2) + "┘"
    return "\n".join([top, *body, bottom])


def _artefact_rows(artefacts) -> list:
    rows = ["artefacts ({})".format(len(artefacts))]
    for art in artefacts:
        rows.append("- " + str(art.get("path", "")))
        rows.append(
            "  blob {}  {}  {}".format(
                _short(art.get("blob_hash")),
                art.get("status", ""),
                art.get("created_at", ""),
            )
        )
    return rows


def _hash_rows(card: dict) -> list:
    """Full hashes on their own indented lines so they stay VERBATIM."""
    rows = [
        "result_hash",
        "  {}".format(card.get("result_hash", "")),
        "mmr root (index head)",
        "  {}".format(card.get("index_head", "")),
    ]
    summary = card.get("proof_summary")
    if summary:
        rows.append(
            "proof    leaf {}  pos {}  len {}  root {}".format(
                _short(summary.get("leaf")),
                summary.get("position"),
                summary.get("proof_length"),
                _short(summary.get("root")),
            )
        )
    return rows


def _reverify_command(card: dict) -> str:
    cmd = 'python3 -m lib.recall.provenance_card --subject "{}"'.format(card.get("subject", ""))
    if card.get("store"):
        cmd += " --store {}".format(card["store"])
    return cmd


def render(card: dict, width: int = 74) -> str:
    """Render the card as a plain box-drawing receipt (no ANSI, no emoji)."""
    rows = [_TITLE, _SEP, "subject  {}".format(card.get("subject", ""))]
    rows.append("built    {}".format(card.get("built_at", "")))
    if card.get("store"):
        rows.append("store    {}".format(card["store"]))
    rows.append(_SEP)
    rows.extend(_artefact_rows(card.get("artefacts") or []))
    rows.append(_SEP)
    rows.extend(_hash_rows(card))
    rows.append(_SEP)
    rows.append("verify   {}".format(_reverify_command(card)))
    return _box(rows, width)


# ----------------------------------------------------------------- reverify
def _lazy_query_fn():
    try:
        from lib.recall.index import query_subject
    except ImportError as exc:
        raise RuntimeError(
            _MISSING_INDEX_MSG.format(name="query_subject", param="query_fn")
        ) from exc
    return query_subject


def _lazy_verify_fn():
    try:
        from lib.recall.index import verify_artefact
    except ImportError as exc:
        raise RuntimeError(
            _MISSING_INDEX_MSG.format(name="verify_artefact", param="verify_fn")
        ) from exc
    return verify_artefact


def _artefacts_verified(artefacts, verify_fn, store) -> bool:
    return all(bool(verify_fn(art, store=store).get("ok")) for art in artefacts)


def reverify(card: dict, query_fn=None, verify_fn=None, store_dir=None) -> dict:
    """Re-run the query + per-artefact verification LIVE and compare to card.

    The moat proving itself: a matching card means the recorded result is
    still what the index returns, every artefact still verifies, and the
    index head is unchanged.
    """
    store = str(store_dir) if store_dir is not None else card.get("store")
    if query_fn is None:
        query_fn = _lazy_query_fn()
    if verify_fn is None:
        verify_fn = _lazy_verify_fn()
    live = _as_result_dict(query_fn(card.get("subject", ""), store=store))
    live_freshness = live.get("freshness") or {}
    checks = {
        "result_hash_match": _result_hash_of(live) == card.get("result_hash"),
        "artefacts_verified": _artefacts_verified(
            list(live.get("artefacts") or ()), verify_fn, store
        ),
        "index_head_match": live_freshness.get("index_head", "") == card.get("index_head"),
    }
    return {"ok": all(checks.values()), "checks": checks}


# --------------------------------------------------------------------- main
def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m lib.recall.provenance_card",
        description="Build and render a screen-shareable recall provenance card.",
    )
    parser.add_argument("--subject", required=True, help="subject to recall")
    parser.add_argument("--store", default=None, help="recall store directory")
    parser.add_argument("--json", action="store_true", help="print the card dict as JSON")
    args = parser.parse_args(argv)
    query_fn = _lazy_query_fn()
    card = build_card(query_fn(args.subject, store=args.store), store_dir=args.store)
    print(json.dumps(card, indent=2) if args.json else render(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
