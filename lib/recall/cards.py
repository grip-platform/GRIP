"""Recall delight surface A — the catch/HITL evidence card.

F2 (locked): when the gate catches a would-be false negative, the card is a
GIFT, not a scolding — the artefact plus its provenance chain, handed back
to the operator. F5 (locked): DENY cards carry the negative-existence hint
so semantic search is never mistaken for authority over negatives.

Pure functions, no I/O. ``render_catch_card`` is the ANSI terminal variant;
``render_plain`` is byte-identical content with zero ANSI escapes (for
hook-context injection); ``to_card_dict`` is the JSON-safe form carrying the
FULL hashes (the rendered cards display 12-char short forms).

Accepted inputs: a PreassertResult-dict (has ``verdict`` + ``claim``) or a
ForensicNode-dict (has ``claim_verbatim``). A ForensicNode records a caught
false assertion, so it renders as the DENY/catch shape.
"""

from __future__ import annotations

import textwrap

from lib.recall import schema

CARD_TITLE = "GRIP already made this"
VERIFY_TOOL = "bin/grip-recall verify"
_SHORT = 12
_MIN_WIDTH = 40

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[38;5;46m",
    "amber": "\033[38;5;214m",
    "cyan": "\033[38;5;51m",
    "grey": "\033[38;5;240m",
}
_ROLE_CODES = {
    "title": ("bold", "green"),
    "badge": ("amber",),
    "claim": ("bold",),
    "art": ("green",),
    "detail": ("cyan",),
    "verify": ("amber",),
    "hint": ("dim",),
    "text": (),
    "blank": (),
}


def _paint(role: str, text: str, ansi: bool) -> str:
    if not ansi or not text:
        return text
    codes = "".join(_ANSI[c] for c in _ROLE_CODES.get(role, ()))
    return f"{codes}{text}{_ANSI['reset']}" if codes else text


def _short(digest: str | None) -> str:
    return (digest or "")[:_SHORT]


def _wrap(text: str, limit: int) -> list[str]:
    return textwrap.wrap(text, limit, break_long_words=True) or [""]


def _norm_artefact(raw: dict, fallback_ts: str) -> dict:
    """Normalise an Artefact-dict OR a ForensicNode evidence row."""
    d = dict(raw)
    return {
        "subject": d.get("subject"),
        "path": d.get("path") or "",
        "blob_hash": d.get("blob_hash") or d.get("hash") or "",
        "receipt_hash": d.get("receipt_hash"),
        "idr_id": d.get("idr_id"),
        "created_at": d.get("created_at") or fallback_ts,
    }


def _extract(result: dict, artefacts) -> dict:
    """Shared normalisation for both accepted result shapes."""
    forensic = "claim_verbatim" in result
    verdict = schema.VERDICT_DENY if forensic else result.get(
        "verdict", schema.VERDICT_UNVERIFIABLE
    )
    claim = result.get("claim_verbatim") if forensic else result.get("claim", "")
    fallback_ts = result.get("ts") or ""
    rows = artefacts if artefacts is not None else list(result.get("evidence") or ())
    return {
        "kind": "forensic" if forensic else "preassert",
        "verdict": verdict,
        "claim": claim or "",
        "artefacts": [_norm_artefact(a, fallback_ts) for a in rows],
    }


def _verify_command(info: dict) -> str:
    # A targeted re-check re-runs the query: verify-on-read (freshness + hash
    # recheck) IS the proof, and the CLI's bare `verify` takes no positional.
    arts = info["artefacts"]
    target = (arts[0].get("subject") or arts[0]["path"]) if arts else ""
    return f'bin/grip-recall query --subject "{target}"' if target else VERIFY_TOOL


def _badge(info: dict) -> str:
    if info["kind"] == "forensic":
        return "caught · forensic record"
    if info["verdict"] == schema.VERDICT_ALLOW:
        return "déjà-vu"
    return f"verdict {info['verdict']}"


def _artefact_rows(art: dict, inner: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("blank", ""), ("art", f"✓ {art['path']}")]
    hashes = []
    if art["blob_hash"]:
        hashes.append(f"blob {_short(art['blob_hash'])}")
    if art["receipt_hash"]:
        hashes.append(f"receipt {_short(art['receipt_hash'])}")
    if hashes:
        rows.append(("detail", "  " + " · ".join(hashes)))
    prov = []
    if art["idr_id"]:
        prov.append(f"idr {art['idr_id']}")
    if art["created_at"]:
        prov.append(f"created {art['created_at']}")
    for line in _wrap(" · ".join(prov), inner - 2) if prov else []:
        rows.append(("detail", f"  {line}"))
    return rows


def _compose(info: dict, width: int) -> list[tuple[str, object]]:
    inner = width - 4
    rows: list[tuple[str, object]] = [
        ("titlebar", (CARD_TITLE, _badge(info))),
        ("sep", ""),
    ]
    if info["claim"]:
        lead = info["claim"] if info["verdict"] == schema.VERDICT_ALLOW \
            else f'caught: "{info["claim"]}"'
        rows += [("claim", line) for line in _wrap(lead, inner)]
    if not info["artefacts"]:
        rows.append(("text", "(no artefact evidence attached)"))
    for art in info["artefacts"]:
        rows += _artefact_rows(art, inner)
    rows += [("blank", ""), ("verify", f"re-verify: {_verify_command(info)}")]
    if info["verdict"] == schema.VERDICT_DENY:
        rows.append(("sep", ""))
        rows += [("hint", line) for line in
                 _wrap(schema.NEGATIVE_EXISTENCE_HINT, inner)]
    return rows


def _titlebar(left: str, right: str, inner: int, ansi: bool) -> str:
    gap = inner - len(left) - len(right)
    if gap < 2:  # narrow card: keep the title, drop the badge inline
        return _paint("title", left, ansi) + " " * max(0, inner - len(left))
    return _paint("title", left, ansi) + " " * gap + _paint("badge", right, ansi)


def _frame(rows: list, width: int, ansi: bool) -> str:
    inner = width - 4
    edge = lambda l, m, r: _paint("hint", l + m * (width - 2) + r, ansi)  # noqa: E731
    out = [edge("┌", "─", "┐")]
    bar = lambda s: "│ " + s + " │"  # noqa: E731
    for role, payload in rows:
        if role == "sep":
            out.append(edge("├", "─", "┤"))
        elif role == "titlebar":
            out.append(bar(_titlebar(payload[0], payload[1], inner, ansi)))
        else:
            text = str(payload)
            pad = " " * max(0, inner - len(text))
            out.append(bar(_paint(role, text, ansi) + pad))
    out.append(edge("└", "─", "┘"))
    return "\n".join(out)


def render_catch_card(result: dict, artefacts=None, width: int = 74) -> str:
    """ANSI terminal card: the caught claim + its provenance, as a gift."""
    width = max(width, _MIN_WIDTH)
    return _frame(_compose(_extract(result, artefacts), width), width, ansi=True)


def render_plain(result: dict, artefacts=None, width: int = 74) -> str:
    """Same content as render_catch_card, zero ANSI (hook-context safe)."""
    width = max(width, _MIN_WIDTH)
    return _frame(_compose(_extract(result, artefacts), width), width, ansi=False)


def to_card_dict(result: dict, artefacts=None) -> dict:
    """JSON-safe card content. Carries FULL hashes (cards display short)."""
    info = _extract(result, artefacts)
    deny = info["verdict"] == schema.VERDICT_DENY
    return {
        "title": CARD_TITLE,
        "kind": info["kind"],
        "verdict": info["verdict"],
        "claim": info["claim"],
        "artefacts": info["artefacts"],
        "verify_command": _verify_command(info),
        "hint": schema.NEGATIVE_EXISTENCE_HINT if deny else None,
    }
