#!/usr/bin/env python3
"""grip_launch_card — the GRIP launch card, and the splash it replaces.

ONE renderer serves both surfaces (operator decision, V>> 2026-07-31): the card
shown when a harness is launched IS the splash shown on a bare `gg++`. On a bare
launch there is no model or effort to report, so those rows say so rather than
leaving a hole.

Four bands, all drawn from GRIP's moat rather than from decoration:

  chain    every signed decision re-walked and structurally verified, NOW
  memory   the belief chain -- what GRIP believed, and when
  recap    what actually shipped since you were last here
  dreamt   last night's divergent run, admitted only if it survived limbo

CBC DISCIPLINE, and it is the whole point. Every figure is read from real state
at render time. A source that could not be read prints the reason instead of a
comforting number, and an absent source is reported as absent. The old splash
did the opposite: lib/grip-splash.sh hardcoded skill_count="~150" as both the
value AND its own fallback, so a cache miss and a live read were
indistinguishable on screen. Nothing here can flatter, because nothing here is
allowed to invent.

Read-only, no network, pure stdlib. Runs in a few milliseconds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

GRIP = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
WIDTH = 78

IDR_PATH = GRIP / "state/precog/idr.jsonl"
CONTEXT_PATH = GRIP / "state/precog/context.jsonl"
ANCHOR_PATH = GRIP / "state/crypto-anchor/decision-chain-anchor-latest.json"
WAKE_PATH = GRIP / "state/inception/wake-latest.json"
CARD_PATH = GRIP / "state/launch-card/latest.txt"


# ---------------------------------------------------------------- state reads

def _jsonl(path: Path) -> list:
    # UnicodeDecodeError is a ValueError, so `except OSError` alone let one
    # malformed byte crash every reader in this module rather than fail open
    # (#4321). A half-written record is the normal state of an append-only log
    # whose writer died, which is the case fail-open exists for. Caught here as
    # well as in read_memory: this is the path every OTHER band reads through,
    # so fixing only the counting path would have left the card crashing on the
    # same file for a different reason.
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return [obj for obj in map(_loads, raw) if obj is not None]


def _loads(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        val = json.loads(line)
    except ValueError:
        return None
    return val if isinstance(val, dict) else None


def _git(*args: str) -> str:
    """Run git in the CURRENT directory, not the GRIP install directory.

    The moat bands describe GRIP; the recap and the branch describe the repo the
    operator is actually sitting in, which on an isolated launch is the session
    worktree. Reading these from the install directory reported whatever branch
    that checkout happened to be on -- true of the install, useless to the
    reader, and actively misleading when the two differ.
    """
    try:
        res = subprocess.run(["git", "-C", os.getcwd(), *args],
                             capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _is_human_root(pred) -> bool:
    return isinstance(pred, str) and pred.startswith("human:")


def _signature_of(rec: dict) -> str:
    aud = rec.get("audit")
    return str(aud.get("signature") or "") if isinstance(aud, dict) else ""


def _scheme_of(rec: dict) -> str:
    aud = rec.get("audit")
    return str(aud.get("scheme") or "") if isinstance(aud, dict) else ""


def _is_broken(rec: dict, by_id: dict) -> bool:
    pred = rec.get("predecessor_idr")
    return bool(pred) and not _is_human_root(pred) and pred not in by_id


def read_chain() -> dict:
    """Re-walk the decision forest and structurally verify it, now."""
    started = time.monotonic()
    recs = _jsonl(IDR_PATH)
    by_id = {r["id"]: r for r in recs if r.get("id")}
    chained = [r for r in recs if r.get("predecessor_idr")]
    return {
        "total": len(recs),
        "chained": len(chained),
        "standalone": len(recs) - len(chained),
        "broken": [r.get("id") for r in chained if _is_broken(r, by_id)],
        "unsigned": [r.get("id") for r in recs if not _signature_of(r)],
        "schemes": sorted({s for s in map(_scheme_of, recs) if s}),
        "ms": max(int((time.monotonic() - started) * 1000), 1),
        "recs": recs,
        "by_id": by_id,
    }


def read_memory(full: bool = True) -> dict:
    """Belief-chain counts. `full=False` counts lines without parsing them.

    The belief chain is the biggest thing on the card and the fastest-growing:
    6,506 records at the time of writing, and the receipt prints only the total.
    Fully parsing every record to produce a number that a line count already
    gives dominated the cost of EVERY launch, and would get worse for ever. The
    line count is not an approximation -- one record per line is the format --
    so nothing is traded away except the signed tally, which only the card shows.
    """
    if not full:
        # Streamed, not slurped (#4243). read_text().splitlines() held the whole
        # chain in memory TWICE at peak — one contiguous string plus the list of
        # every line — to produce a single integer. Iterating the handle keeps
        # one line resident at a time, so cost stops scaling with chain size on
        # the launch path, which is exactly where this must stay cheap.
        #
        # UnicodeDecodeError is caught alongside OSError because it is a
        # ValueError, not an OSError, and one malformed byte anywhere in the
        # chain would otherwise crash the card instead of failing open. A
        # truncated or partially-written record is exactly the state an
        # append-only log is in when something died mid-write, so this is the
        # case the fail-open guarantee exists for.
        try:
            with CONTEXT_PATH.open(encoding="utf-8") as fh:
                return {"total": sum(1 for ln in fh if ln.strip()), "signed": None}
        except (OSError, UnicodeDecodeError):
            return {"total": 0, "signed": None}
    recs = _jsonl(CONTEXT_PATH)
    return {"total": len(recs), "signed": sum(1 for r in recs if _signature_of(r))}


def _block_from(status: str) -> str:
    marker = "BitcoinBlockHeaderAttestation("
    return status.split(marker, 1)[1].split(")", 1)[0] if marker in status else ""


def _normalise_offset(text: str) -> str:
    """Turn a `+0200` timezone offset into the `+02:00` form.

    Python 3.9's fromisoformat rejects the compact form, and the anchor file
    uses it. Left unhandled the card printed `?d ago` -- honest, but wrong, and
    only on machines where python3 resolves to the system interpreter, which is
    exactly where nobody would notice. Both forms parse everywhere after this.
    """
    if len(text) > 5 and text[-5] in "+-" and text[-5:].lstrip("+-").isdigit():
        return text[:-2] + ":" + text[-2:]
    return text


def _days_since(stamp) -> str:
    stamp = _normalise_offset(str(stamp))
    try:
        then = datetime.fromisoformat(str(stamp))
    except (ValueError, TypeError):
        return "?"
    return str((datetime.now(timezone.utc) - then).days)


def read_anchor() -> dict:
    try:
        data = json.loads(ANCHOR_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "why": "anchor file unreadable"}
    root = str(data.get("canonical_merkle_root_rfc6962")
               or data.get("merkle_root") or "")
    return {"ok": bool(root), "root": root[:8], "rows": data.get("rows"),
            "block": _block_from(str(data.get("status") or "")),
            "days": _days_since(data.get("anchored_at"))}


def read_wake() -> dict:
    """The Inception band. Absent is REPORTED, never faked."""
    try:
        data = json.loads(WAKE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "why": "no run recorded yet"}
    except (OSError, ValueError):
        return {"ok": False, "why": "wake report unreadable"}
    return {"ok": True, **data}


def read_shipped(count: int = 4) -> list:
    lines = _git("log", "-n", str(count), "--pretty=%h\x1f%s").splitlines()
    return [tuple(ln.split("\x1f", 1)) for ln in lines if "\x1f" in ln]


# ------------------------------------------------------- chain topology (O(n))

def _parent_of(rid: str, by_id: dict):
    rec = by_id.get(rid)
    pred = rec.get("predecessor_idr") if rec else None
    return None if (not pred or _is_human_root(pred) or pred not in by_id) else pred


def _climb(start: str, by_id: dict, depth: dict):
    """Walk up until a node of known depth (or a root). One pass, no rescan."""
    stack, seen, cur = [], set(), start
    while cur is not None and cur not in depth and cur not in seen:
        seen.add(cur)
        stack.append(cur)
        cur = _parent_of(cur, by_id)
    return stack, cur


def _assign(stack: list, anchor, depth: dict) -> None:
    base = depth.get(anchor, 0) if anchor else 0
    for node in reversed(stack):
        base += 1
        depth[node] = base


def depth_map(chain: dict) -> dict:
    """Depth per node, memoised so each node is costed exactly once."""
    by_id, depth = chain["by_id"], {}
    for rid in by_id:
        if rid not in depth:
            stack, anchor = _climb(rid, by_id, depth)
            _assign(stack, anchor, depth)
    return depth


def _walk_back(start: str, by_id: dict) -> list:
    path, seen, cur = [], set(), start
    while cur is not None and cur in by_id and cur not in seen:
        seen.add(cur)
        path.append(by_id[cur])
        cur = _parent_of(cur, by_id)
    return list(reversed(path))


def longest_spine(chain: dict) -> list:
    depth = depth_map(chain)
    if not depth:
        return []
    return _walk_back(max(depth, key=lambda k: depth[k]), chain["by_id"])


# ------------------------------------------------------------------ rendering

GLYPH_UNICODE = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
                 "node": "◼", "human": "◻", "mid": "╞═", "end": "╘═", "dot": "·", "ell": "…"}
GLYPH_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
               "node": "#", "human": "o", "mid": "|=", "end": "\\=", "dot": "-", "ell": "..."}


def use_ascii() -> bool:
    """Unicode box-drawing needs a UTF-8 capable terminal; degrade otherwise."""
    if os.environ.get("GRIP_CARD_ASCII"):
        return True
    enc = (sys.stdout.encoding or "").lower()
    lang = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return "utf" not in enc and "utf" not in lang


def _pad(text: str, width: int) -> str:
    return text + " " * (width - len(text)) if len(text) < width else text


def _clip(text: str, width: int, ell: str = "…") -> str:
    """Truncate to `width`. `ell` is glyph-dependent: the default ellipsis is
    U+2026, which is NOT ascii, so ascii mode must pass "..." or the card stops
    being ascii exactly when a line happens to be long enough to truncate."""
    return text if len(text) <= width else text[: width - len(ell)] + ell


def _band_moat(g: dict, chain: dict, mem: dict, anc: dict) -> list:
    dot = g["dot"]
    anchor = (f"anchor     {anc['root']} {dot} rows 1-{anc['rows']} {dot} "
              f"bitcoin block {anc['block']} {dot} {anc['days']}d ago"
              if anc["ok"] else
              f"anchor     not checked {dot} {anc.get('why', 'unavailable')}")
    # signed is None when the cheap line-count path was used. Say so rather than
    # printing a plausible number nobody actually counted. Kept as a separate
    # statement, not a nested f-string: nested same-quote f-strings are Python
    # 3.12+ only, and this file must parse under an older system python3.
    signed = mem.get("signed")
    signed_txt = "signed count not read" if signed is None else f"{signed:,} signed"
    return [
        f"decisions  {chain['total']} signed {dot} {chain['chained']} chained {dot} "
        f"{chain['standalone']} standalone {dot} {len(chain['broken'])} broken "
        f"{dot} {chain['ms']} ms",
        f"memory     {mem['total']:,} beliefs {dot} {signed_txt} {dot} "
        f"what GRIP believed, and when",
        anchor,
        "",
    ]


def _spine_row(g: dict, rec: dict, last: bool) -> str:
    conn = g["end"] if last else g["mid"]
    mark = "signed" if _signature_of(rec) else "UNSIGNED"
    return (f"  {conn}{g['node']} {_pad(str(rec.get('kind', ''))[:20], 20)} "
            f"{_pad(str(rec.get('id', ''))[:30], 30)} {mark}")


def _band_spine(g: dict, chain: dict) -> list:
    spine = longest_spine(chain)
    if not spine:
        return []
    out = []
    pred = str(spine[0].get("predecessor_idr") or "")
    if pred.startswith("human:"):
        who = pred.split(":", 1)[1].split("-", 1)[0]
        out.append(f"  {g['human']}   human directive {g['dot']} {who} "
                   f"{g['dot']} {str(spine[0].get('ts', ''))[:10]}")
    out += [_spine_row(g, r, i == len(spine) - 1) for i, r in enumerate(spine)]
    out += ["", f"  {chain['standalone']} stand alone {g['dot']} each signed, "
                f"none claiming a parent", ""]
    return out


def _band_shipped(g: dict, width: int) -> list:
    rows = [f"  {sha}  {_clip(subj, width - 14, g['ell'])}"
            for sha, subj in read_shipped(4)]
    return ["shipped recently", *(rows or ["  no commits readable here"]), ""]


def _band_dream(g: dict, wake: dict, width: int) -> list:
    dot = g["dot"]
    if not wake["ok"]:
        return ["dreamt overnight",
                f"  {wake['why']} {dot} nothing to show, and nothing invented"]
    rate = float(wake.get("survival_rate", 0)) * 100
    verdict = ("healthy — most of it was thrown away" if wake.get("healthy")
               else "TOO MUCH ADMITTED — not really diverging")
    head = (f"  {wake.get('generated', 0)} dreamt {dot} {wake.get('admitted', 0)} "
            f"survived the limbo test {dot} {rate:.0f}% {dot} {verdict}")
    ideas = [f"  {dot} {_clip(str(i.get('text', '')), width - 6, g['ell'])}"
             for i in (wake.get("ideas") or [])[:2]]
    return ["dreamt overnight", head, *ideas]


# Typographic characters that reach the card inside TEXT WE DO NOT CONTROL —
# commit subjects, GitHub issue titles, dreamt idea text — plus the few the card
# writes itself. Every mapping is one character to one character so folding
# cannot change a string's width, which is what makes it safe to apply after the
# box has already been laid out.
_ASCII_FOLD = str.maketrans({
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": ".", "·": "-", "•": "*", " ": " ",
})


def _to_ascii(text: str) -> str:
    """Force a rendered card to pure ASCII without changing its width.

    Switching the glyph set is not enough on its own. The card renders strings
    it did not write — a commit subject, an issue title, a dreamt idea — and any
    one of them may carry an em-dash or a smart quote. That is not hypothetical:
    the ASCII-purity test began failing the moment a commit landed whose own
    subject contained an em-dash, with no wake report on disk at all.

    Known characters fold to a sensible ASCII equivalent; anything else becomes
    '?' rather than being dropped, since deleting a character would silently
    shorten a line the box has already been sized around.
    """
    return text.translate(_ASCII_FOLD).encode("ascii", "replace").decode("ascii")


def render(harness: str = "", model: str = "", effort: str = "",
           ascii_only=None) -> str:
    want_ascii = use_ascii() if ascii_only is None else bool(ascii_only)
    g = GLYPH_ASCII if want_ascii else GLYPH_UNICODE
    chain, mem, anc, wake = read_chain(), read_memory(), read_anchor(), read_wake()
    inner = WIDTH - 2
    body = (_band_moat(g, chain, mem, anc) + _band_spine(g, chain)
            + _band_shipped(g, inner) + _band_dream(g, wake, inner) + [""]
            + [f"structure re-walked now {g['dot']} "
               f"{', '.join(chain['schemes']) or 'no'} signatures present, "
               f"not recomputed"])

    context = " ".join(x for x in (harness, model, effort) if x) or \
        f"{chain['total']} sealed {g['dot']} nothing chosen yet"
    title, right = " chain of custody ", f" {context} "
    top = (g["tl"] + g["h"] + title
           + g["h"] * max(inner - len(title) - len(right) - 1, 1) + right + g["tr"])
    foot = f" {_git('rev-parse', '--abbrev-ref', 'HEAD') or '?'} "
    bottom = g["bl"] + g["h"] + foot + g["h"] * max(inner - len(foot) - 1, 1) + g["br"]
    rows = [g["v"] + " " + _pad(_clip(t, inner - 2, g["ell"]), inner - 2) + " " + g["v"]
            for t in body]
    card = "\n".join([top, *rows, bottom])
    return _to_ascii(card) if want_ascii else card


def _button(card_path: Path) -> str:
    """A clickable OSC 8 link where supported, an actionable hint where not.

    file_link() degrades to bare label text, which would be useless on its own
    -- the reader would see "card" with no way to open it -- so the unsupported
    branch names the command instead.
    """
    try:
        from tui_links import file_link, supports_hyperlinks
    except ImportError:
        return "(gg++ --card)"
    return file_link(str(card_path), "[▸ card]") if supports_hyperlinks() \
        else "(gg++ --card)"


def write_card(text: str) -> Path:
    try:
        CARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        CARD_PATH.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    return CARD_PATH


def receipt(harness: str = "", model: str = "", effort: str = "") -> str:
    chain, mem = read_chain(), read_memory(full=False)
    context = " · ".join(x for x in (harness, model, effort) if x) or "claude"
    broken = len(chain["broken"])
    return (f"{context} · {chain['total']} signed, {broken} broken · "
            f"{mem['total']:,} beliefs · {chain['ms']} ms  {_button(CARD_PATH)}")


def _opt(argv: list, flag: str) -> str:
    if flag not in argv:
        return ""
    idx = argv.index(flag)
    return argv[idx + 1] if idx + 1 < len(argv) else ""


def _hyperlinks_ok() -> bool:
    try:
        from tui_links import supports_hyperlinks
    except ImportError:
        return False
    return supports_hyperlinks()


def main(argv: list) -> int:
    harness = _opt(argv, "--harness")
    model = _opt(argv, "--model")
    effort = _opt(argv, "--effort")

    if "--card" in argv:
        text = render(harness, model, effort)
        write_card(text)
        print(text)
        return 0

    # The receipt runs on EVERY launch, so it does the least work that is still
    # honest. The full card costs two git subprocesses plus the spine walk, and
    # is only worth paying for when the link will actually be clickable --
    # otherwise `gg++ --card` renders it fresh on demand anyway.
    if _hyperlinks_ok():
        write_card(render(harness, model, effort))
    print(receipt(harness, model, effort))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
