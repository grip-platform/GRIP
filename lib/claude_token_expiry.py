"""When each stored Claude token runs out: the expiry radar.

A token made with ``claude setup-token`` lasts about a year and does not say when it
expires. ``grip-account mint`` writes the date down beside the tokens at the one
moment it is known (``minted.json``, ``claude_token_mint.record_mint``). This reads
that record back for each label, and trusts it only for the token the label holds
now: the record carries a twelve-character digest of the token it was written for,
and a label whose stored token has another digest (replaced since, or copied in from
another machine) has no known expiry.

The ladder hands it what the ladder read: the token store's active file, beside
which the tokens and the record live, and the record's bytes, which the ladder names
among its sources. Each token is read as the relay reads one.

A token that stops working early is a different matter, and is covered elsewhere:
the relay passes over a refused token (GRIP#8262) and HAL's wire over a dead one
(HAL#688), and tests hold both. Read-only: it reads the stored tokens and reaches no
network.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import claude_token_mint as mint
import claude_token_store as store

#: Days before the recorded date from which the radar asks for a new token.
WARN_DAYS = 30

KNOWN, NO_TOKEN, NOT_RECORDED, ANOTHER_TOKEN = "known", "no token", "not recorded", "another token"
NOT_A_LABEL = "not a label"


def ledger_path(active: Path) -> Path:
    """The mint record, beside the token store's active file: ``record_mint`` writes
    it into the store's folder."""
    return active.with_name(mint.LEDGER)


def parse_ledger(data: bytes | None) -> dict:
    """The mint record from its bytes, or ``{}`` when there are none or they are not
    a record."""
    if not data:
        return {}
    try:
        doc = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _date(text) -> date | None:
    if not isinstance(text, str):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def token_expiry(label: str, entry, token: str, today: date) -> dict:
    """One label's expiry: the recorded date and the days left when the record was
    written for the token stored now, else the reason it is not known."""
    if not token:
        return {"label": label, "state": NO_TOKEN}
    expires = _date(entry.get("expires_about")) if isinstance(entry, dict) else None
    if expires is None:
        return {"label": label, "state": NOT_RECORDED}
    if entry.get("digest") != mint.digest(token):
        return {"label": label, "state": ANOTHER_TOKEN}
    return {"label": label, "state": KNOWN, "expires": expires.isoformat(),
            "days": (expires - today).days}


def _stored_token(path: Path) -> str:
    """The token stored at ``path``, read as the relay reads one: owner-only when the
    default store is in use. Empty when there is none to use."""
    token, _source = store.read_active_token(path, strict=store.uses_default_store())
    return token


def radar(active: Path, labels: list, ledger: dict, today: date) -> list:
    """The expiry of each label's token stored beside ``active``, in the order given,
    from the mint record ``ledger``. ``today`` is the UTC date of the caller's own
    clock reading, so one view uses one clock."""
    rows = []
    for label in labels:
        if store.label_problem(label):
            rows.append({"label": label, "state": NOT_A_LABEL})
            continue
        token = _stored_token(active.with_name(f"{active.name}.{label}"))
        rows.append(token_expiry(label, ledger.get(label), token, today))
    return rows


_UNKNOWN = {
    NO_TOKEN: "no usable token stored",
    NOT_RECORDED: "not recorded: minted before the record existed, or on another machine",
    ANOTHER_TOKEN: "not known: the stored token is not the one the record was written for",
    NOT_A_LABEL: "not a name a token can be stored under",
}


def expiry_text(row: dict) -> str:
    """One label's line: the date and the days left, and from :data:`WARN_DAYS` out
    what to do about it. A token is minted from an account's sign-in slot, whose name
    the record does not keep and which can differ from the label, so the command
    names the label with ``--as`` and leaves the slot to fill in."""
    if row["state"] != KNOWN:
        return _UNKNOWN[row["state"]]
    days = row["days"]
    renew = f"mint a new one with grip-account mint <slot> --as {row['label']}"
    if days < 0:
        return f"about {row['expires']}, {-days} days ago: it may already be refused; {renew}"
    if days <= WARN_DAYS:
        return f"about {row['expires']}, in {days} days: {renew}"
    return f"about {row['expires']}, in {days} days"


def radar_lines(rows: list) -> list:
    """The radar as indented lines, one per label."""
    if not rows:
        return ["  no pooled account"]
    wide = max(len(row["label"]) for row in rows)
    return [f"  {row['label']:<{wide}}  {expiry_text(row)}" for row in rows]
