#!/usr/bin/env python3
"""GRIP Sigil -- earned, collectible proof-of-work delight.

A *sigil* is a tiny deterministic glyph-stamp minted ONLY for a verified
milestone (a real merged PR, a CONFIRMED hypothesis, a CI-green ship, a
converged sprint). The art is generated purely from the milestone's IDENTITY
(``kind|ref`` -- the passport dedup key) via SHA-256, so the same milestone
ALWAYS yields the same sigil and it cannot be forged without doing the work --
the sigil IS the proof-of-work, made beautiful. ``gen`` and ``ts`` are recorded
as "when earned" metadata only; they never affect the art. Sigils accumulate in
an append-only passport you can browse with ``/sigil``.

Coheres with ``rules/exogenous-anchor-law.md``: the delight rides a verified
exogenous signal, never a self-claim. Pure-stdlib, deterministic, and
Goodhart-testable -- mirrors ``lib/rave_card.py`` and ``lib/handoff_chime.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# CLAUDE_DIR per rules/path-portability.md
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:  # pragma: no cover - import shim
    from claude_dir import get_claude_dir

    CLAUDE_DIR = get_claude_dir()
except Exception:  # pragma: no cover - fallback
    CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

PASSPORT = CLAUDE_DIR / "state" / "sigils" / "passport.jsonl"

# Verified milestone kinds -- the earned-only anchors (exogenous-anchor-law.md).
KINDS = {
    "pr": "PR merged",
    "hypothesis": "hypothesis CONFIRMED",
    "ship": "CI-green ship",
    "sprint": "sprint converged",
}
_TAG = {"pr": "PR", "hypothesis": "HYP", "ship": "SHIP", "sprint": "SPR"}

# 16 single-width Block-Elements glyphs, one per hash nibble (alignment-safe).
_GLYPHS = "▖▗▘▝▙▟▛▜▚▞▀▄▌▐░▒"
# (rarity, weight in basis points /10000, mark) -- INTEGER weights so there is
# no float-accumulation drift at the boundary; marks are single-width stars.
_RARITY = [
    ("common", 6000, "·"),     # middot
    ("uncommon", 2500, "✶"),   # six-pointed star
    ("rare", 1100, "✦"),       # black four-pointed star
    ("legendary", 400, "✸"),   # heavy eight-pointed star
]
_CONS = "kvrxztnmslpdgbf"
_VOWS = "aeiou"
_INNER = 15  # inner card width


@dataclass(frozen=True)
class Sigil:
    name: str
    kind: str
    ref: str
    gen: int
    ts: int
    rarity: str
    mark: str
    rows: Tuple[str, ...]
    note: str = ""

    def render(self) -> str:
        """A framed, fixed-width collectible card. Every line is the same width."""
        tag = _TAG.get(self.kind, self.kind)
        refdisp = self.ref if len(self.ref) <= 13 else self.ref[:12] + "…"
        top = "╔" + "═" * _INNER + "╗"
        sep = "╟" + "─" * _INNER + "╢"
        bot = "╚" + "═" * _INNER + "╝"
        head = _wall(f"{self.mark} {self.name} {self.mark}")
        body = [_wall(r) for r in self.rows]
        rar = _wall(f"{self.mark} {self.rarity.upper()} {self.mark}")
        reff_kind = _wall(f"{tag} · g{self.gen}")
        reff_ref = _wall(refdisp)
        return "\n".join([top, head, *body, rar, sep, reff_kind, reff_ref, bot])


def _wall(s: str) -> str:
    return "║" + _ctr(s) + "║"


def _ctr(s: str, w: int = _INNER) -> str:
    if len(s) > w:
        s = s[:w]
    pad = w - len(s)
    left = pad // 2
    return " " * left + s + " " * (pad - left)


def _digest(kind: str, ref: str) -> bytes:
    # Seed = the milestone's IDENTITY (== the passport dedup key), NOT mint-time.
    # So the same milestone always yields the same art; gen/ts are metadata only.
    return hashlib.sha256(f"{kind}|{ref}".encode("utf-8")).digest()


def _name(d: bytes) -> str:
    # 6 chars, consonant/vowel alternating -> pronounceable + uppercased.
    out = []
    for j in range(6):
        pool = _CONS if j % 2 == 0 else _VOWS
        out.append(pool[d[j] % len(pool)])
    return "".join(out).upper()


def _rarity(d: bytes) -> Tuple[str, str]:
    # Integer bucketing in [0, 9999]; weights sum to exactly 10000, so there is
    # no float accumulation and no precision drift at the boundary (AGORA r3).
    r = int.from_bytes(d[6:10], "big") % 10000
    cum = 0
    for name, weight, mark in _RARITY:
        cum += weight
        if r < cum:
            return name, mark
    return _RARITY[-1][0], _RARITY[-1][2]  # unreachable: weights sum to 10000


def _rows(d: bytes) -> Tuple[str, ...]:
    nibs = []
    for b in d[10:19]:  # 9 bytes -> 18 nibbles
        nibs.append(b >> 4)
        nibs.append(b & 0x0F)
    g = [_GLYPHS[n] for n in nibs[:18]]
    rows = []
    for r in range(3):
        a, b, c, dd, e, f = g[r * 6 : r * 6 + 6]
        rows.append(f"{a}{b} {c}{dd} {e}{f}")
    return tuple(rows)


def mint(
    kind: str,
    ref: str,
    gen: int,
    ts: Optional[int] = None,
    note: str = "",
) -> Sigil:
    """Mint a sigil from a verified milestone. EARNED: refuses without a referent."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError(
            "sigil is EARNED: a real milestone referent (PR#, SHA, hypothesis id) "
            "is required -- refusing to mint without one"
        )
    if kind not in KINDS:
        raise ValueError(f"unknown milestone kind {kind!r}; one of {sorted(KINDS)}")
    if ts is None:
        ts = int(time.time())  # recorded "when earned" only -- NOT part of the seed
    d = _digest(kind, ref)
    name = _name(d)
    rarity, mark = _rarity(d)
    return Sigil(
        name=name, kind=kind, ref=ref, gen=int(gen), ts=int(ts),
        rarity=rarity, mark=mark, rows=_rows(d), note=note,
    )


# --- passport (the collection) --------------------------------------------

def _key(s: Sigil) -> Tuple[str, str]:
    return (s.kind, s.ref)


def _parse(text: str) -> List[Sigil]:
    # Dedup by (kind, ref) at READ time, keeping first occurrence. This makes the
    # one-sigil-per-milestone invariant hold UNCONDITIONALLY: even if a rare
    # concurrent-mint race wrote a duplicate line (``_lock`` is best-effort and
    # swallows its own errors), it collapses here -- so correctness never depends
    # on the file lock.
    out: List[Sigil] = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            o["rows"] = tuple(o.get("rows", ()))
            s = Sigil(**o)
        except Exception:
            continue  # a malformed line never breaks the passport
        k = _key(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def load_passport() -> List[Sigil]:
    if not PASSPORT.exists():
        return []
    return _parse(PASSPORT.read_text(encoding="utf-8"))


def _lock(fh) -> None:
    """Best-effort exclusive lock from _compat: POSIX fcntl, or LockFileEx on Windows.

    Correctness does NOT rely on it: a racing duplicate line is collapsed by the
    dedup-on-read in _parse, so the worst case is a transient extra line in the
    file, never a wrong view (GRIP#8757).
    """
    try:
        from _compat import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass  # no lock: dedup-on-read (see _parse) makes this safe


def record(sigil: Sigil) -> bool:
    """Append unless (kind, ref) already collected. True iff newly recorded.

    The read-check-append runs under an exclusive file lock, so two concurrent
    mints of the same milestone cannot both append (no TOCTOU race). Sigils are
    low-volume (one per verified milestone), so the full re-read per append is
    bounded in practice -- an index would be YAGNI here.
    """
    PASSPORT.parent.mkdir(parents=True, exist_ok=True)
    with PASSPORT.open("a+", encoding="utf-8") as fh:
        _lock(fh)
        fh.seek(0)
        if _key(sigil) in {_key(s) for s in _parse(fh.read())}:
            return False
        fh.write(json.dumps(asdict(sigil), ensure_ascii=False) + "\n")
        return True


def mint_for_milestone(
    kind: str,
    ref: str,
    gen: Optional[int] = None,
    ts: Optional[int] = None,
    note: str = "",
    verify: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[Sigil, bool]:
    """Convenience for wiring: mint + record from a verified exogenous milestone.

    ``verify`` is the earned-only enforcement seam (DIP): a real milestone trigger
    injects a checker -- e.g. ``lambda k, r: gh_pr_is_merged(r)`` -- so a
    fabricated ref (a PR that never merged) is refused HERE, at the call site,
    before anything is recorded. Default ``None`` keeps the generator pure and
    offline for preview/tests; the network/``gh`` dependency lives in the caller,
    never in this module.
    """
    if verify is not None and not verify(kind, ref):
        raise ValueError(
            f"unverified milestone: {kind} {ref!r} does not correspond to a real "
            "merged PR / confirmed hypothesis -- refusing to mint a fabricated sigil"
        )
    if gen is None:
        gen = int(os.environ.get("GRIP_GEN", "0") or 0)
    s = mint(kind, ref, gen, ts, note)
    return s, record(s)


def render_passport() -> str:
    sigils = load_passport()
    if not sigils:
        return (
            "No sigils yet. Ship something real -- merge a PR, confirm a "
            "hypothesis -- and one is minted. They're earned, never given."
        )
    tally = {}
    for s in sigils:
        tally[s.rarity] = tally.get(s.rarity, 0) + 1
    order = [r[0] for r in _RARITY]
    tline = "   ".join(f"{r}:{tally[r]}" for r in order if tally.get(r))
    head = f"GRIP Passport -- {len(sigils)} sigil(s) earned"
    cards = _sidebyside([s.render() for s in sigils[-6:]], per_row=3)
    return f"{head}\n{tline}\n\n{cards}"


def _sidebyside(cards: List[str], per_row: int = 3) -> str:
    groups = [cards[i : i + per_row] for i in range(0, len(cards), per_row)]
    return "\n\n".join(_join_row(g) for g in groups)


def _join_row(group: List[str]) -> str:
    """Render fixed-height cards side by side (zip-transpose, no nested loops)."""
    if not group:
        return ""
    cols = [c.split("\n") for c in group]
    height = max(map(len, cols))
    width = len(cols[0][0])
    padded = [col + [" " * width] * (height - len(col)) for col in cols]
    return "\n".join("   ".join(parts) for parts in zip(*padded))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sigil", description="GRIP Sigil -- earned proof-of-work delight")
    sub = ap.add_subparsers(dest="cmd")

    m = sub.add_parser("mint", help="mint + record a sigil for a verified milestone")
    m.add_argument("--kind", required=True, choices=sorted(KINDS))
    m.add_argument("--ref", required=True, help="real referent: PR#, SHA, hypothesis id")
    m.add_argument("--gen", type=int, default=int(os.environ.get("GRIP_GEN", "0") or 0))
    m.add_argument("--ts", type=int, default=None)
    m.add_argument("--note", default="")

    p = sub.add_parser("preview", help="preview a sigil WITHOUT recording (deterministic)")
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--ref", required=True)
    p.add_argument("--gen", type=int, default=0)
    p.add_argument("--ts", type=int, default=0)

    sub.add_parser("show", help="render your passport (default)")
    args = ap.parse_args(argv)

    if args.cmd == "mint":
        s = mint(args.kind, args.ref, args.gen, args.ts, args.note)
        new = record(s)
        print(s.render())
        print("✓ minted + added to passport" if new else "• already collected (not duplicated)")
        return 0
    if args.cmd == "preview":
        print(mint(args.kind, args.ref, args.gen, args.ts).render())
        return 0
    print(render_passport())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
