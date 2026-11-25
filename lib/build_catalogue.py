#!/usr/bin/env python3
"""Build state/catalogue/catalogue.json for the "GRIP Hears" product.

Runs the `grip-hear --json` contract over the 44 published SoundCloud tracks,
groups them into the five movements, and emits one catalogue.json the site
generator consumes. Per-track results are cached so re-runs are near-free and
resumable; one track's failure never aborts the build.

This is the dogfood loop: the grip-hear substrate produces the structured
hearing; the product is built from it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

CLAUDE = Path.home() / ".claude"
PY = str(CLAUDE / "venv/bin/python")
HEAR = str(CLAUDE / "scripts/grip-hear.py")
SOUNDCLOUD = Path.home() / "Music/SoundCloud"
OUT_DIR = CLAUDE / "state/catalogue"
CACHE_DIR = OUT_DIR / "cache"
OUT = OUT_DIR / "catalogue.json"

# Title (SoundCloud filename stem) -> movement. The five-movement editorial map.
MOVEMENT = {
    # I. The Confessionals
    "Aisle Seat": "The Confessionals", "Another Thought": "The Confessionals",
    "Dirt Poor Genius": "The Confessionals", "Faith In the Human Race": "The Confessionals",
    "For a While": "The Confessionals", "Happiness That's Broke": "The Confessionals",
    "I woke up this morning": "The Confessionals", "I'm Feeling Good": "The Confessionals",
    "Just Fine": "The Confessionals", "Kry Kontrole": "The Confessionals",
    "Legacy": "The Confessionals",
    "One Day": "The Confessionals", "Realize": "The Confessionals",
    "Reconnected": "The Confessionals", "Scare off the devils": "The Confessionals",
    "Singing Along": "The Confessionals", "The Place": "The Confessionals",
    "Too Much In My Mind": "The Confessionals", "Wait until I feel good": "The Confessionals",
    # II. The Band
    "Ek Is Die Een Wat Omgee": "The Band", "You see": "The Band",
    "Loneliness": "The Band", "Nemesis": "The Band",
    "Ongemaklike Situasies": "The Band", "Opportunities and what happens to them": "The Band",
    "Freetown - Marlon Brando": "The Band", "Freetown - Must be a way": "The Band",
    "Freetown - Psychological Warfare": "The Band",
    # III. Without Words
    "Broken Ring Finger": "Without Words", "That's How I Wanna Go": "Without Words",
    "The Black Sheep": "Without Words", "Sometimes": "Without Words",
    "Tigrrr's Song": "Without Words", "Moving on up": "Without Words",
    "Wave to Frank": "Without Words",
    # IV. The Dark Room
    "It's time": "The Dark Room", "Fuck Everything": "The Dark Room",
    "Trip to the Circus": "The Dark Room", "The Mood Stabilizing Depressant Circus": "The Dark Room",
    # V. Borrowed Voices
    "If (Pink Floyd Cover)": "Borrowed Voices", "You've got to hide your love away": "Borrowed Voices",
    "If You Go Away": "Borrowed Voices", "Golden Hair": "Borrowed Voices",
}
MOVEMENT_ORDER = ["The Confessionals", "The Band", "Without Words", "The Dark Room", "Borrowed Voices"]


def slug(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def hear_json(path: Path) -> dict | None:
    """Run grip-hear --json on one file; return the parsed dict or None on failure."""
    r = subprocess.run(
        [PY, HEAR, str(path), "--json"],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(CLAUDE), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  ! hear failed: {path.name}: {r.stderr.strip()[:160]}", file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout.strip())
    except json.JSONDecodeError as e:
        print(f"  ! bad json: {path.name}: {e}", file=sys.stderr)
        return None


def record_for(path: Path) -> dict | None:
    title = path.stem
    sg = slug(title)
    cache = CACHE_DIR / f"{sg}.json"
    if cache.exists():
        try:
            h = json.loads(cache.read_text())
        except json.JSONDecodeError:
            h = None
    else:
        h = None
    if h is None:
        h = hear_json(path)
        if h is None:
            return None
        cache.write_text(json.dumps(h, ensure_ascii=False, indent=2))
    return {
        "id": sg,
        "title": title,
        "file": str(path),
        "movement": MOVEMENT.get(title, "The Confessionals"),
        "genre": h.get("genre"),
        "mood": h.get("mood"),
        "language": h.get("language"),
        "instrumental": bool(h.get("instrumental", False)),
        # Deterministic librosa floor (tempo/key/mode) + provenance, when the
        # mechanical floor produced them. `*_source` records "librosa" vs the
        # model's guess; absent when librosa was unavailable for that track.
        "tempo_bpm": h.get("tempo_bpm"),
        "tempo_source": h.get("tempo_source"),
        "key": h.get("key"),
        "mode": h.get("mode"),
        "key_source": h.get("key_source"),
        "duration_s": h.get("duration_s"),
        "key_lyric": h.get("key_lyric"),
        # Real transcribed lyrics + provenance (Vulavula STT), when present.
        # `lyrics_source` distinguishes "vulavula" (transcribed) from the
        # model's guessed `key_lyric`; absent for instrumentals / STT misses.
        "lyrics": h.get("lyrics"),
        "lyrics_source": h.get("lyrics_source"),
        "lyrics_lang": h.get("lyrics_lang"),
        "energy": float(h.get("energy", 0.5)),
        "darkness": float(h.get("darkness", 0.5)),
        "one_line": h.get("one_line"),
    }


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        p for p in SOUNDCLOUD.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aif", ".flac"}
    )
    if not files:
        sys.exit(f"FAIL: no audio in {SOUNDCLOUD}")
    records = []
    for i, p in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {p.stem}")
        rec = record_for(p)
        if rec:
            records.append(rec)
    # stable order: by movement, then title
    records.sort(key=lambda r: (MOVEMENT_ORDER.index(r["movement"]) if r["movement"] in MOVEMENT_ORDER else 99,
                                r["title"].lower()))
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(f"\ncatalogue.json: {len(records)} songs -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
