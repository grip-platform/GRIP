"""Where a capped Claude account's work would go next: a read-only preview.

V>> approved a "ladder preview" in the /hitl round of 2026-10-11 (GRIP#8189): one
command that shows, before a cap happens, where the work would go. The ladder he
decided is the request's own account, then the other pooled subscription accounts,
then HAL's cascade to other model families, then the paid key (only with
``GRIP_CLAUDE_ALLOW_PAID=1``), then an honest cap message.

Nothing here decides anything, changes any file or reaches the network. Each rung
is read with the code that acts on it:

- the order, from ``subscription_room.next_with_room``, which the relay's own
  ``stand_in`` calls;
- HAL's brake on moving work between accounts, from
  ``subscription_room.switching_on``, which the relay obeys;
- whether a stored token can be used, from the token store's own read, called the
  way the relay calls it;
- the paid key, from the failover's own functions (``bin/grip-auth-failover``).

HAL's wire applies the same order to marks it keeps in its own process, and it
passes over an account in the same organisation as the capped one. Neither can be
read from a file, so the preview says so. The room shown is what HAL's ledger last
recorded for each account, with its age, and every preview names the bytes it was
read from (``sources``): the ledger is read once and everything is answered from
that one reading.

The preview also estimates how long each account lasts (the "range estimate" of
GRIP#8189): the share of a window used so far, over the time the window had been
open at that reading, carried forward to 100%. It is an average from one reading,
and the preview prints that reading in front of every estimate. A window with no
pace to go on (spent, renewed, unused or unread) says which instead.

Last, it shows when each pooled token runs out (the "expiry radar" of GRIP#8189),
from the date ``grip-account mint`` recorded for it (``lib/claude_token_expiry.py``).
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import claude_token_expiry as token_expiry
import claude_token_store
import subscription_room as room

#: HAL's ledger name for a sign-in that is not one of the pooled tokens. Only HAL's
#: wire ever moves its work: the relay bills the token store, never this sign-in.
UNPOOLED = "unpooled"
_WINDOW_WORDS = {"five_hour": "5-hour", "5h": "5-hour", "seven_day": "week", "7d": "week"}
_NOBODY = "no pooled account has room"
#: Shown when exactly one account is pooled: GRIP's cap-handling moves a
#: capped account's work to the next pooled one, and with only one there is
#: nowhere for it to go.
_SINGLE_ACCOUNT_NOTICE = (
    "Only one account is configured: when its five-hour or weekly limit is "
    "reached, Claude work pauses until the reset, because there is no "
    "second account to move to. Add one with: grip-account add <label>."
)
#: How long each account-wide window runs. A window is taken to have opened this
#: long before its reset time.
_WINDOW_SECONDS = {"five_hour": 5 * 3600, "5h": 5 * 3600,
                   "seven_day": 7 * 86400, "7d": 7 * 86400}
#: The service reports usage in whole points, so a reading can be half a point off
#: either way. An estimate is given only when both ends of that spread agree.
_HALF_POINT = 0.5
_FAILOVER = Path(__file__).resolve().parent.parent / "bin" / "grip-auth-failover"
_LOADED: dict = {}
#: What the preview cannot see: both are in HAL's wire and in no file.
_LIMITS = (
    "Room is what HAL's ledger last recorded for each account. HAL's wire also decides",
    "from marks in its own process, and it passes over an account in the capped one's",
    "organisation. Neither can be read from a file, so neither is shown here.",
)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _source(path: Path, data: bytes | None) -> dict:
    """Which bytes one part of the preview was read from, so the claim can be checked
    later against the file: its path, its SHA-256 and when it was last written."""
    if data is None:
        return {"path": str(path), "present": False}
    try:
        modified = int(path.stat().st_mtime)
    except OSError:
        modified = None
    return {"path": str(path), "present": True, "modified": modified,
            "sha256": hashlib.sha256(data).hexdigest()}


def _usable(token: Path) -> bool:
    """Whether the relay could bill this stored token. This is the relay's own read
    (``relay_dispatcher._stand_in_token``): the token must be there, not empty, and
    owner-only when the default store is in use."""
    found, _where = claude_token_store.read_active_token(
        token, strict=claude_token_store.uses_default_store())
    return bool(found)


def _full_at(used: float, read_at: float, opened: float) -> float:
    """When a window reaches 100% if it carries on at the pace of ``used`` percent
    in the time from ``opened`` to ``read_at``."""
    return read_at + (100.0 - used) * (read_at - opened) / used


def _settled(used: float, resets, now: float) -> str:
    """The answers that need no pace, or ``""`` when a pace has to decide."""
    if room.window_spent(used, resets, now):
        return "spent"
    if resets is None:
        return "unknown"
    if room.reset_passed(resets, now):
        return "renewed"
    return "idle" if used < 1 else ""


def window_range(used: float, resets, read_at, length: int, now: float) -> dict:
    """How long one window lasts at the pace it had averaged when it was last read.

    ``used`` is the percentage used at ``read_at``. The window is taken to have opened
    ``length`` seconds before ``resets``, so the pace is ``used`` over the time from
    that opening to the reading, and ``full_at`` is when it reaches 100% at that pace.
    It is an average from one reading: usage comes in bursts, and nothing used since
    the reading is counted. A reading can be half a point off either way, so the fast
    and the slow end of that spread are carried forward too.

    ``state`` is one of ``spent``; ``renewed`` (its reset time has passed); ``idle``
    (under one point used); ``lasts`` (not full before it renews, even at the fast
    end); ``fills`` (full before it renews, even at the slow end); ``close`` (the two
    ends disagree about that); ``overdue`` (at the fast end it is full by now, so it
    may be full already); ``unknown`` (no reset time, no reading time, or a reading
    dated before the window opened or after ``now``)."""
    state = _settled(used, resets, now)
    opened = resets - length if resets is not None else None
    if not state and (read_at is None or not opened < read_at <= now):
        state = "unknown"
    if state:
        return {"state": state, "opened": opened, "full_at": None}
    soonest = _full_at(used + _HALF_POINT, read_at, opened)
    latest = _full_at(used - _HALF_POINT, read_at, opened)
    state = ("lasts" if soonest >= resets else "close" if latest >= resets
             else "overdue" if soonest <= now else "fills")
    return {"state": state, "opened": opened, "full_at": (
        int(_full_at(used, read_at, opened)) if state in ("fills", "overdue") else None)}


def _whole_percent(used: float) -> int:
    """The whole points used: cut, never rounded up, so a figure shown under the
    spent line is under it. ``used`` is a fraction times 100, which can land a hair
    under a whole number (0.29 gives 28.999999999999996); that hair is removed first."""
    return int(round(used, 6))


def _windows(entry, now: float) -> list:
    read_at = _seen_at(entry)
    return [{"name": _WINDOW_WORDS[name], "percent": _whole_percent(used), "resets": resets,
             "spent": room.window_spent(used, resets, now),
             "reset_passed": room.reset_passed(resets, now),
             "range": window_range(used, resets, read_at, _WINDOW_SECONDS[name], now)}
            for name, used, resets in room.account_windows(entry)]


def _back_at(windows: list) -> int | None:
    """When a spent account has room again: the latest reset among its spent
    windows, or None when it has none or one of them names no reset."""
    resets = [window["resets"] for window in windows if window["spent"]]
    return max(resets) if resets and None not in resets else None


def _seen_at(entry) -> int | None:
    seen = entry.get("observed_at") if isinstance(entry, dict) else None
    known = isinstance(seen, (int, float)) and not isinstance(seen, bool)
    return int(seen) if known else None


def _row(label: str, entry, verdict, now: float, *, place: int, token: Path | None) -> dict:
    windows = _windows(entry, now)
    return {
        "label": label,
        "place": place,
        "stored": _usable(token) if token else None,
        "state": {True: "room", False: "spent"}.get(verdict, "unread"),
        "back_at": _back_at(windows),
        "windows": windows,
        "seen_at": _seen_at(entry),
        "token": room.token_health(entry, token),
    }


def accounts(active: Path, labels: list, entries: dict, verdicts: dict, now: float) -> list:
    """One row per account: the pool in billing order (``place`` 1, 2, ...), then
    every other account HAL's ledger holds (``place`` 0). ``stored`` says whether the
    relay could use the pooled account's token."""
    rows = [_row(label, entries.get(label), verdicts.get(label), now,
                 place=place, token=room.labelled(active, label))
            for place, label in enumerate(labels, start=1)]
    # The ledger also holds HAL's cascade targets, under names no account label can
    # take (``w4:<provider>:<digest>``). They are a later rung, not an account. HAL's
    # ``unpooled`` sign-in is an account, though no stored token may take that name.
    rows += [_row(label, entries[label], verdicts.get(label), now, place=0, token=None)
             for label in sorted(entries)
             if label not in labels and (label == UNPOOLED
                                         or claude_token_store.label_problem(label) is None)]
    return rows


def hand_overs(active: Path, rows: list, labels: list, entries: dict, verdicts: dict) -> dict:
    """Each account that can start a request, mapped to the account its work goes to
    at a cap, or ``""``. Only two kinds of account can start one: a pooled account
    whose token the relay can read, and the sign-in outside the pool, which only
    HAL's wire moves. Any other label in the ledger gets no entry, because nothing
    hands over from it.

    The destinations are ``subscription_room.candidates``, the list the relay itself
    searches: an account whose token is refused or cannot be read is passed over for
    the next one with room (GRIP#8253)."""
    readable = {row["label"] for row in rows if row["place"] and row["stored"]}
    takers = room.candidates(active, labels, entries, lambda label: label in readable)
    following = {label: room.next_with_room(takers, label, verdicts)
                 for label in labels if label in readable}
    if any(row["label"] == UNPOOLED for row in rows):
        following[UNPOOLED] = room.next_with_room(takers, UNPOOLED, verdicts)
    return following


def stored_outside_pool(active: Path, labels: list) -> list:
    """Labels with a token stored beside the active one that are not in the pool.
    No hand-over ever tries them."""
    prefix = active.name + "."
    found = (path.name[len(prefix):] for path in active.parent.glob(prefix + "*"))
    return sorted(label for label in found
                  if label not in labels and claude_token_store.label_problem(label) is None)


def cascade_config_path() -> Path:
    return Path(os.environ.get("HAL_W4_CONFIG")
                or room.hal_home() / "config" / "w4_cascade.json")


def _named(target) -> bool:
    return isinstance(target, dict) and all(
        isinstance(target.get(key), str) and target.get(key) for key in ("provider", "model"))


def _cascade_targets(data: bytes | None) -> list:
    """The targets of HAL's cascade to other model families, from the bytes of HAL's
    own config file. A missing or unreadable file gives ``[]``: HAL reads that as the
    cascade staying dormant. An entry that names no provider or no model is left out,
    as HAL leaves it out; HAL checks more than that, so a target shown here is
    configured, which is not the same as accepted."""
    try:
        doc = json.loads(data) if data is not None else None
    except ValueError:
        return []
    targets = doc.get("anthropic_targets") if isinstance(doc, dict) else None
    return [target for target in targets if _named(target)] if isinstance(targets, list) else []


def _refusing(entry, now: float) -> tuple:
    """``(refusing work, until when or None)`` from one ledger entry, read as HAL
    reads it: an end that has passed is no refusal, and an end that is not a whole
    number is unknown."""
    raw = entry.get("cooling") if isinstance(entry, dict) else None
    if not isinstance(raw, dict):
        return (False, None)
    until = raw.get("until") if type(raw.get("until")) is int else None
    if until is not None and until <= now:
        return (False, None)
    return (True, until)


def _provider(provider: str, models: list, entries: dict, now: float) -> dict:
    """One cascade provider. HAL keeps one ledger entry per credential, named
    ``w4:<provider>:<digest>``. The provider refuses work only while every credential
    the ledger holds for it does, and it is back when the first of them is."""
    states = [_refusing(entry, now) for key, entry in entries.items()
              if key.startswith(f"w4:{provider}:")]
    ends = [until for refusing, until in states if refusing]
    refusing = bool(states) and len(ends) == len(states)
    return {"provider": provider, "models": models, "credentials": len(states),
            "credentials_refusing": len(ends), "refusing": refusing,
            "refusing_until": min(ends) if refusing and all(ends) else None}


def cascade(targets: list, entries: dict, now: float) -> list:
    """One entry per provider of HAL's cascade, in the order HAL lists them."""
    models: dict = {}
    for target in targets:
        models.setdefault(target["provider"], []).append(target["model"])
    return [_provider(provider, names, entries, now) for provider, names in models.items()]


def _load_failover():
    try:
        loader = importlib.machinery.SourceFileLoader("grip_auth_failover_for_ladder",
                                                      str(_FAILOVER))
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
    except (OSError, ImportError, SyntaxError):
        return None
    return module


def failover():
    """``bin/grip-auth-failover`` loaded once as a module, or None when it cannot be
    loaded. The paid rung is read with its functions, never with a copy of its rules."""
    if "failover" not in _LOADED:
        _LOADED["failover"] = _load_failover()
    return _LOADED["failover"]


def paid_rung() -> dict:
    """The paid key, as the failover itself reads it. ``at_a_cap`` is true only when a
    usage cap would engage the key: the cap policy is ``engage`` (which the failover
    grants only with the opt-in set to exactly "1") and the key is in the keychain.
    ``engaged_now`` is true when new sessions already use it. With no failover script
    nothing is known, and nothing is promised."""
    tool = failover()
    if tool is None:
        return {"known": False, "opt_in": False, "policy": "", "policy_source": "",
                "key_present": False, "engaged_now": False, "at_a_cap": False}
    policy, source = tool._cap_policy()
    key = tool._keychain_present(tool.API_KEY_ENTRY)
    return {"known": True, "opt_in": tool._paid_allowed(), "policy": policy,
            "policy_source": str(source), "key_present": key,
            "engaged_now": tool._active_bucket() == "api-key",
            "at_a_cap": policy == "engage" and key}


def ladder(active: Path | None = None, now: float | None = None,
           ledger: Path | None = None) -> dict:
    """The whole preview as plain data: the accounts, where each one's work goes at
    a cap, the rungs after the pool, and the files it was read from."""
    active = active or claude_token_store.active_path()
    ledger = ledger or room.ledger_path()
    pool_file = active.with_name(active.name + ".pool")
    mint_record = token_expiry.ledger_path(active)
    ledger_bytes, config_bytes = _read(ledger), _read(cascade_config_path())
    mint_bytes = _read(mint_record)
    # The clock is read after the ledger, so no honest reading is ahead of it.
    now = time.time() if now is None else now
    entries = room.accounts_in(ledger_bytes.decode("utf-8", "replace")) if ledger_bytes else {}
    verdicts = room.rooms(entries, now)
    labels = room.pool(active)
    rows = accounts(active, labels, entries, verdicts, now)
    following = hand_overs(active, rows, labels, entries, verdicts)
    return {
        "now": int(now),
        "pool": labels,
        "active": room.active_label(active, labels),
        "accounts": rows,
        "next": following,
        "stored_outside_pool": stored_outside_pool(active, labels),
        "cascade": cascade(_cascade_targets(config_bytes), entries, now),
        # The relay's own reading of HAL's brake, beside the ledger read above.
        "switching": room.switching_on(ledger),
        "paid": paid_rung(),
        "expiry": token_expiry.radar(active, labels, token_expiry.parse_ledger(mint_bytes),
                                     datetime.fromtimestamp(now, timezone.utc).date()),
        "sources": {"pool": _source(pool_file, _read(pool_file)),
                    "ledger": _source(ledger, ledger_bytes),
                    "cascade_config": _source(cascade_config_path(), config_bytes),
                    "mint_record": _source(mint_record, mint_bytes)},
    }


def _when(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} d ago"


def _state_text(row: dict) -> str:
    if row["stored"] is False:
        return "no usable token stored here"
    if row["token"] == "dead":
        return "token refused; sign in again"
    if row["state"] == "spent":
        back = row["back_at"]
        return f"spent until {_when(back)}" if back else "spent, reset time not recorded"
    return "room" if row["state"] == "room" else "not read by HAL yet"


def _usage_text(row: dict, now: int) -> str:
    parts = [f"{window['name']} renewed" if window["reset_passed"]
             else f"{window['name']} {window['percent']}%" for window in row["windows"]]
    if row["seen_at"] is not None:
        parts.append(f"seen {_ago(now - row['seen_at'])}")
    return ", ".join(parts)


def _account_lines(view: dict) -> list:
    rows = view["accounts"]
    if not rows:
        return ["  no pooled account on this machine, and HAL's ledger holds none"]
    states = {row["label"]: _state_text(row) for row in rows}
    wide = max(len(row["label"]) for row in rows)
    state_wide = max(len(text) for text in states.values())
    lines = []
    for row in rows:
        label = row["label"]
        mark = str(row["place"]) if row["place"] else "-"
        tail = "   <- active in the token store" if label == view["active"] else ""
        usage = _usage_text(row, view["now"])
        lines.append(f"  {mark:>2}  {label:<{wide}}  {states[label]:<{state_wide}}  "
                     f"{usage}{tail}".rstrip())
    return lines


def _target_text(view: dict, label: str) -> str:
    target = view["next"][label] or _NOBODY
    return f"{target} (by HAL's wire only)" if label == UNPOOLED else target


def _next_lines(view: dict) -> list:
    if not view["next"]:
        return ["  no account here can start a request and hand it over"]
    wide = max(len(label) for label in view["next"])
    lines = [f"  {label:<{wide}}  ->  {_target_text(view, label)}" for label in view["next"]]
    if not view["switching"]:
        lines.append("  HELD: HAL's account switching is off, so none of these moves is "
                     "made until it is turned back on")
    return lines


def _first_back(rows: list) -> tuple | None:
    """``(when, label)`` for the spent pooled account that has room again first."""
    known = [(row["back_at"], row["label"]) for row in rows
             if row["place"] and row["state"] == "spent" and row["back_at"]]
    return min(known) if known else None


def _cascade_text(item: dict) -> str:
    name = f"{item['provider']} ({', '.join(item['models'])})"
    if item["refusing"]:
        until = item["refusing_until"]
        return (f"{name}: refusing work until {_when(until)}, as HAL last saw it" if until
                else f"{name}: refusing work, end not recorded")
    if item["credentials_refusing"]:
        return (f"{name}: {item['credentials_refusing']} of {item['credentials']} "
                "credentials refusing work")
    return f"{name}: no refusal on record"


def _paid_text(paid: dict) -> str:
    if not paid["known"]:
        return "not known (the failover tool could not be read)"
    if paid["engaged_now"]:
        return "IN USE NOW for new sessions (see grip-auth-failover --status)"
    if paid["at_a_cap"]:
        return "would be used at a cap (opt-in set, cap policy engage, key present)"
    if not paid["opt_in"]:
        return "off (GRIP_CLAUDE_ALLOW_PAID is not 1 in this shell)"
    if paid["policy"] != "engage":
        return f"not used at a cap: the cap policy is {paid['policy']} ({paid['policy_source']})"
    return "not used at a cap: no paid key in the keychain"


def _after_pool_lines(view: dict) -> list:
    lead = "  other model families, through HAL's cascade: "
    families = [lead + _cascade_text(item) for item in view["cascade"]]
    back = _first_back(view["accounts"])
    cap = "then a cap message"
    if back:
        cap += f". First pooled account back: {back[1]}, {_when(back[0])}"
    return (families or [lead + "none configured, so this rung is dormant"]) + [
        f"  the paid Anthropic key: {_paid_text(view['paid'])}", f"  {cap}"]


def _span(seconds: float) -> str:
    """A length of time in words: minutes up to 90 minutes, hours up to two days,
    then days and hours."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} d {hours % 24} h" if hours % 24 else f"{hours // 24} d"


#: The answers that state no pace.
_NO_PACE = {"renewed": "its reset time has passed", "idle": "under 1% used so far",
            "unknown": "no estimate from this reading"}


def _pace_text(window: dict, seen_at: int, now: int) -> str:
    """One window's estimate, with the reading it rests on in front of it."""
    found = window["range"]
    pace = f"{window['percent']}% in {_span(seen_at - found['opened'])}"
    if found["state"] == "lasts":
        return f"{pace}, so it lasts to its renewal"
    if found["state"] == "close":
        return f"{pace}, too close to call"
    if found["state"] == "overdue":
        return f"{pace} when read {_ago(now - seen_at)}, so it may be full by now"
    return (f"{pace}, so full about {_when(found['full_at'])}, "
            f"{_span(window['resets'] - found['full_at'])} before it renews")


def _range_text(window: dict, seen_at, now: int) -> str:
    state = window["range"]["state"]
    if state == "spent":
        resets = window["resets"]
        return (f"spent until {_when(resets)}" if resets is not None
                else "spent, reset time not recorded")
    return _NO_PACE.get(state) or _pace_text(window, seen_at, now)


def _range_lines(view: dict) -> list:
    rows = view["accounts"]
    if not rows:
        return ["  no account to estimate for"]
    wide = max(len(row["label"]) for row in rows)
    return [f"  {row['label']:<{wide}}  " + ("; ".join(
        f"{window['name']}: {_range_text(window, row['seen_at'], view['now'])}"
        for window in row["windows"]) or "not read by HAL yet") for row in rows]


def _source_text(name: str, source: dict) -> str:
    if not source["present"]:
        return f"{name}: not there"
    written = f", written {_when(source['modified'])}" if source["modified"] else ""
    return f"{name}: sha256 {source['sha256'][:12]}{written}"


def _closing_lines(view: dict) -> list:
    lines = ["HAL's account switching is on." if view["switching"] else
             "HAL's account switching is OFF: neither HAL nor the relay moves any work."]
    if view["pool"] and not view["active"]:
        lines.append("The active token in the store is not one of the pooled accounts, "
                     "so the relay hands nothing over.")
    outside = view["stored_outside_pool"]
    if outside:
        lines.append(f"Stored here but not pooled, so never tried: {', '.join(outside)}")
    lines.extend(_LIMITS)
    lines.append("To put another account in the pool: grip-account add <label>")
    sources = view["sources"]
    lines.append(f"Read {_when(view['now'])} from "
                 + "; ".join(_source_text(name, sources[key]) for name, key in (
                     ("the pool file", "pool"), ("HAL's ledger", "ledger"),
                     ("HAL's cascade config", "cascade_config"),
                     ("the mint record", "mint_record"))))
    return lines


def render(view: dict) -> list:
    """The preview as lines of plain text for a terminal. Every section is printed
    whatever the others hold, so a machine with no pool still shows its stored
    tokens, the later rungs and the files that were read."""
    lines = ["Claude accounts on this machine, in billing order", ""]
    lines += _account_lines(view)
    if view["accounts"] and not view["pool"]:
        lines += ["", "No pool on this machine, so there is no account to hand over to."]
    elif len(view["pool"]) == 1:
        lines += ["", _SINGLE_ACCOUNT_NOTICE]
    lines += ["", "Where a capped account's work goes next (the first pooled account, "
                  "in order, that has room):"]
    lines += _next_lines(view)
    lines += ["", "After the pool:"]
    lines += _after_pool_lines(view)
    lines += ["", "How long each account lasts at the pace it had averaged when HAL last "
                  "read it (an estimate):"]
    lines += _range_lines(view)
    lines.append("  Each window is counted from one window-length before its reset; "
                 "use since the last reading is not counted.")
    lines += ["", "When each pooled token runs out (about a year from minting, as recorded "
                  "on this machine):"]
    lines += token_expiry.radar_lines(view["expiry"])
    lines.append("")
    lines += _closing_lines(view)
    return lines
