#!/usr/bin/env python3
"""inception_spiral.py — /inception: the machine's nested self, depth 11.

A deterministic, no-LLM, no-network delight surface (sibling of grip_haiku /
evolution_march): renders GRIP as eleven nested layers — git, genome, cube,
delight, memory, hypothesis, gates, agents, loop, intent, dream — each with a
real totem from live state and a dream line. --kick collapses the spiral to the
verdict: is the top still spinning, or is the dream wobbling?

Usage:
    python3 lib/inception_spiral.py                 # terminal render
    python3 lib/inception_spiral.py --kick          # collapse to verdict
    python3 lib/inception_spiral.py --html out.html # self-contained HTML

Pure stdlib. Every read is guarded: a missing file renders "unmeasured", never
crashes, never fabricates. The delight verdict IS the criterion instrument.
"""

import argparse
import html
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

G = chr(27) + "[32m"
A = chr(27) + "[33m"
R = chr(27) + "[31m"
D = chr(27) + "[2m"
X = chr(27) + "[0m"
B = chr(27) + "[1m"


# ---------------------------------------------------------------------------
# Level collectors — each returns dict(name, detail, totem, dream, health)
# ---------------------------------------------------------------------------

def _git() -> dict:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
            text=True, timeout=5, cwd=str(_ROOT)).stdout.strip() or "?"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True,
            timeout=5, cwd=str(_ROOT)).stdout.strip().splitlines()
        today = subprocess.run(
            ["git", "log", "--since=midnight", "--oneline"], capture_output=True,
            text=True, timeout=5, cwd=str(_ROOT)).stdout.strip().splitlines()
    except Exception:
        return {"health": "red", "detail": "git unavailable", "totem": "?"}
    nd = len(dirty)
    health = "green" if nd == 0 else ("amber" if nd <= 5 else "red")
    return {
        "health": health, "totem": branch,
        "detail": branch + " · " + str(nd) + " dirty · " + str(len(today)) + " commits today",
        "dream": "the branch is your memory of intent",
    }


def _genome() -> dict:
    try:
        g = json.loads((_ROOT / "data" / "genome-shared.json").read_text())
        genes = g.get("genes", {})
        fits = [(v.get("fitness", 0.0), k) for k, v in genes.items()]
        fits.sort(reverse=True)
        mean = round(sum(f for f, _ in fits) / len(fits), 3) if fits else 0.0
        strongest = fits[0][1] if fits else "?"
        health = "green" if mean >= 0.7 else ("amber" if mean >= 0.5 else "red")
        return {
            "health": health, "totem": strongest,
            "detail": "gen " + str(g.get("exported_from_gen", "?")) + " · " + str(len(genes)) + " genes · fitness " + str(mean),
            "dream": "the genes measure what you actually did",
        }
    except Exception:
        return {"health": "red", "detail": "genome unmeasured", "totem": "?"}


def _cube() -> dict:
    try:
        c = json.loads((_ROOT / "cache" / "cube-state.json").read_text())
        levels = {k: v for k, v in c.get("levels", {}).items() if v}
        top = max(levels, key=levels.get) if levels else "none"
        mesh = round(c.get("mesh_fraction", 0.0) * 100, 1)
        health = "green" if mesh >= 30 else ("amber" if mesh >= 10 else "red")
        return {
            "health": health, "totem": top,
            "detail": "top level: " + top + "@" + str(levels.get(top, 0)) + " · mesh " + str(mesh) + "%",
            "dream": "the cube scores what survives measurement",
        }
    except Exception:
        return {"health": "red", "detail": "cube unmeasured", "totem": "?"}


def _delight() -> dict:
    try:
        out = subprocess.run(
            [str(_ROOT / "venv" / "bin" / "python3"), "lib/delight_v2.py", "trend"],
            capture_output=True, text=True, timeout=30, cwd=str(_ROOT)).stdout
        trend = "?"
        quality = 0.0
        for line in out.splitlines():
            if line.startswith("Trend:"):
                trend = line.split(":", 1)[1].strip().split(" ")[0]
            if "Average quality" in line:
                try:
                    quality = float(line.split(":")[1].strip())
                except (IndexError, ValueError):
                    pass
        health = "green" if trend == "improving" else ("amber" if trend == "steady" else "red")
        return {
            "health": health, "totem": trend,
            "detail": "trend " + trend + " · avg quality " + ("%.3f" % quality),
            "dream": "delight is the criterion the loop is measured by",
        }
    except Exception:
        return {"health": "red", "detail": "delight unmeasured", "totem": "?"}


def _memory() -> dict:
    try:
        conn = sqlite3.connect("file:" + str(_ROOT / "embeddings.db") + "?mode=ro", uri=True)
        total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        cb = conn.execute(
            "SELECT COUNT(DISTINCT instance_id) FROM kono_attestations").fetchone()[0]
        conn.close()
        frac = cb / total if total else 0.0
        health = "green" if frac >= 0.8 else ("amber" if frac >= 0.5 else "red")
        return {
            "health": health, "totem": "%.1fk" % (total / 1000.0),
            "detail": str(total) + " memories · " + str(cb) + " content-bearing instances",
            "dream": "what you remember is what you become",
        }
    except Exception:
        return {"health": "red", "detail": "memory unmeasured", "totem": "?"}


def _hypothesis() -> dict:
    p = _ROOT / "data" / "hypothesis-registry.jsonl"
    if not p.exists():
        return {
            "health": "amber", "totem": "unmeasured",
            "detail": "hypothesis-registry.jsonl missing — no claims registered",
            "dream": "an unregistered claim cannot be falsified",
        }
    try:
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        statuses = {}
        for row in rows:
            s = row.get("status", "?")
            statuses[s] = statuses.get(s, 0) + 1
        health = "green" if statuses.get("refuted", 0) + statuses.get("confirmed", 0) else "amber"
        detail = ", ".join(k + ":" + str(v) for k, v in statuses.items())
        return {
            "health": health, "totem": detail,
            "detail": str(len(rows)) + " hypotheses · " + detail,
            "dream": "confirmed or refuted — both are science",
        }
    except Exception:
        return {"health": "red", "detail": "hypotheses unreadable", "totem": "?"}


def _gates() -> dict:
    try:
        n_gates = len(list((_ROOT / "lib" / "gates").glob("*.py")))
        denies = 0
        allowed = 0
        p = _ROOT / "data" / "gate-metrics.jsonl"
        if p.exists():
            for line in p.read_text().splitlines()[-500:]:
                try:
                    row = json.loads(line)
                    v = str(row.get("verdict") or row.get("decision") or "").upper()
                    if "DENY" in v:
                        denies += 1
                    elif "ALLOW" in v:
                        allowed += 1
                except Exception:
                    pass
        health = "green" if n_gates >= 5 else "amber"
        return {
            "health": health, "totem": str(n_gates) + " gates",
            "detail": str(n_gates) + " gate modules · " + str(denies) + " denies / " + str(allowed) + " allows (last 500)",
            "dream": "you cannot charm a gate; you cannot persuade a hook",
        }
    except Exception:
        return {"health": "red", "detail": "gates unmeasured", "totem": "?"}


def _agents() -> dict:
    try:
        roster = [p for p in (_ROOT / "agents").glob("*.md")
                  if p.name not in ("CLAUDE.md", "_base-protocol.md")]
        health = "green" if len(roster) >= 5 else "amber"
        return {
            "health": health, "totem": str(len(roster)) + " agents",
            "detail": str(len(roster)) + " named agents in the roster",
            "dream": "the agents are the machine's hands",
        }
    except Exception:
        return {"health": "red", "detail": "agents unmeasured", "totem": "?"}


def _loop() -> dict:
    try:
        waves = sorted((_ROOT / "state" / "ultrado").glob("wave*_checkpoint.json"))
        n_waves = len(waves)
        gen = "?"
        try:
            g = json.loads((_ROOT / "data" / "genome-shared.json").read_text())
            gen = g.get("exported_from_gen", "?")
        except Exception:
            pass
        health = "green" if isinstance(gen, int) and gen >= 1000 else "amber"
        return {
            "health": health, "totem": "gen " + str(gen),
            "detail": "generation " + str(gen) + " · " + str(n_waves) + " wave checkpoints",
            "dream": "each generation slightly less wrong than the last",
        }
    except Exception:
        return {"health": "red", "detail": "loop unmeasured", "totem": "?"}


def _intent() -> dict:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    mine = "afk-" + sid.replace("-", "")[:16] if sid else None
    note = ""
    try:
        markers = sorted((_ROOT / "state" / "session").glob("afk-*.json"))
        for m in markers:
            try:
                d = json.loads(m.read_text())
                n = d.get("note") or d.get("intent") or ""
                if not n:
                    continue
                if mine and mine in m.stem:
                    note = n
                    break
                if not note:
                    note = n
            except Exception:
                continue
    except Exception:
        pass
    if not note:
        note = "the operator's next step"
    return {
        "health": "green", "totem": note[:40],
        "detail": note,
        "dream": "intent is the top of the dream — what you came here to do",
    }


def _dream() -> dict:
    try:
        out = subprocess.run(
            [str(_ROOT / "venv" / "bin" / "python3"), "lib/grip_haiku.py"],
            capture_output=True, text=True, timeout=15, cwd=str(_ROOT)).stdout
        lines = []
        for ln in out.splitlines():
            s = ln.strip()
            if (s and "·" not in s and "—" not in s and "GRIP Genome" not in s
                    and "strongest" not in s and "weakest" not in s
                    and "stressed" not in s and "─" not in s
                    and "fitness" not in s and "Gen " not in s):
                lines.append(s)
        haiku = lines[:3] or ["the dream has not been written yet"]
        return {
            "health": "green", "totem": haiku[0][:40],
            "detail": " / ".join(haiku),
            "dream": "the deepest layer dreams in haiku",
        }
    except Exception:
        return {"health": "green", "totem": "?", "detail": "the dream is quiet",
                "dream": "the deepest layer dreams in haiku"}


LEVELS = [
    ("L01", "GIT", _git), ("L02", "GENOME", _genome), ("L03", "CUBE", _cube),
    ("L04", "DELIGHT", _delight), ("L05", "MEMORY", _memory),
    ("L06", "HYPOTHESIS", _hypothesis), ("L07", "GATES", _gates),
    ("L08", "AGENTS", _agents), ("L09", "LOOP", _loop),
    ("L10", "INTENT", _intent), ("L11", "DREAM", _dream),
]

COLOUR = {"green": G, "amber": A, "red": R}

#: COLOUR's web equivalent. One definition, used for both the level cards and
#: the verdict line, so the verdict can never be painted a colour other than
#: the one computed for it.
WEB_COLOUR = {"green": "#7cfc9a", "amber": "#ffb454", "red": "#ff5c5c"}


def collect() -> list:
    """Run every collector and normalise to the full level shape.

    A collector's except-branch may omit a key; setdefault guarantees the
    renderer never KeyErrors — a fresh install or an empty worktree renders
    "unmeasured", never crashes (the same honesty contract as grip_haiku).
    """
    levels = []
    for code, name, fn in LEVELS:
        lv = fn()
        lv.setdefault("health", "red")
        lv.setdefault("totem", "?")
        lv.setdefault("detail", "unmeasured")
        lv.setdefault("dream", "the layer is silent")
        levels.append((code, name, lv))
    return levels


def verdict(levels: list) -> dict:
    counts = {"green": 0, "amber": 0, "red": 0}
    for _, _, lv in levels:
        h = lv.get("health")
        if h not in counts:
            # Unknown/unmeasured health is a warning, never green: counting
            # "?" under its own bucket left counts["red"] == 0, which painted
            # a fully unmeasured machine "still spinning" green.
            h = "red"
        counts[h] += 1
    delight = next((lv for _, n, lv in levels if n == "DELIGHT"), {})
    d = delight.get("detail", "")
    declining = "declining" in d
    improving = ("improving" in d) or ("steady" in d)
    # The CRITERION is delight. Subsystem reds are context, not the verdict.
    if declining:
        line = "The top wobbles — delight is declining. The dream needs a nudge."
        tone = "red"
    elif counts["red"] == 0:
        line = "The top is still spinning — you are dreaming."
        tone = "green"
    else:
        line = "The top spins, but the machine has rough edges — " + d.split(" · ")[0]
        tone = "amber"
    return {
        "counts": counts, "tone": tone,
        "line": line,
        "delight": d or "delight unmeasured",
    }


def render_terminal(levels: list, kick: bool = False) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    gen = next((lv["totem"] for _, n, lv in levels if n == "LOOP"), "?")
    out = []
    out.append(D + "┌────────────────────────────────────────────────────────────┐" + X)
    out.append(B + G + "  ⥀  I N C E P T I O N  ⥀" + X + "   the machine's nested self")
    out.append(D + "  depth 11 · " + str(gen) + " · " + now + " · deterministic · no LLM" + X)
    out.append(D + "└────────────────────────────────────────────────────────────┘" + X)
    out.append("")
    for code, name, lv in levels:
        col = COLOUR.get(lv.get("health"), COLOUR["red"])
        indent = "  " * (int(code[1:]) % 3)
        out.append(indent + D + code + X + " " + B + col + name.ljust(11) + X + " "
                   + D + "◢" + X + " " + col + lv["detail"] + X)
        out.append(indent + "  " + D + "↳ " + lv["dream"] + X)
    out.append("")
    v = verdict(levels)
    counts = v["counts"]
    out.append(D + "  ── the kick ──────────────────────────────────────────────" + X)
    out.append(B + ({"green": G, "amber": A, "red": R}[v["tone"]]) + "  " + v["line"] + X)
    out.append(D + "  " + str(counts["green"]) + " steady · " + str(counts["amber"])
               + " wobbling · " + str(counts["red"]) + " broken   |   " + X + B + A
               + "delight: " + v["delight"] + X)
    if not kick:
        out.append(D + "  (press K or re-run with --kick to see it collapse)" + X)
    return chr(10).join(out)


_STYLE = (
    '  :root{--bg:#070b09;--ink:#d8e8dd;--dim:#7d9188;--mono:ui-monospace,Menlo,monospace}'
    '  *{box-sizing:border-box;margin:0;padding:0}'
    '  body{background:var(--bg);color:var(--ink);font-family:var(--mono);padding:40px 20px}'
    '  .wrap{max-width:640px;margin:0 auto}'
    '  h1{font-size:24px;letter-spacing:.1em;color:#7cfc9a;margin-bottom:4px}'
    '  .sub{color:var(--dim);font-size:12px;margin-bottom:26px}'
    '  .card{border:1px solid #1c2b23;border-left:3px solid var(--c);border-radius:8px;'
    '    padding:10px 14px;margin-bottom:10px;background:#0d1511}'
    '  .row{display:flex;gap:10px;align-items:baseline}'
    '  .code{color:var(--dim);font-size:11px}'
    '  .name{font-weight:700;color:var(--c);letter-spacing:.08em}'
    '  .totem{margin-left:auto;color:var(--c);font-size:12px;max-width:45%;text-align:right;'
    '    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
    '  .detail{font-size:12.5px;margin-top:4px}'
    '  .dream{color:var(--dim);font-size:11px;font-style:italic;margin-top:2px}'
    '  .kick{margin-top:18px;border-top:1px solid #1c2b23;padding-top:12px}'
    # No colour here on purpose: the verdict is coloured inline from its own
    # computed tone, so a red verdict can never be painted green by a rule that
    # does not know what was computed.
    '  .kick .verdict{font-weight:700;font-size:15px}'
    '  .kick .counts{color:var(--dim);font-size:11px;margin-top:4px}'
    '  button{background:#0d1511;color:#7cfc9a;border:1px solid #3f8f5a;border-radius:6px;'
    '    font-family:var(--mono);padding:8px 16px;cursor:pointer;margin-top:10px}'
    '  button:hover{background:#12241a}'
)

#: KICK collapses the spiral to the verdict: it fades the level cards and
#: leaves the verdict standing. It deliberately does NOT touch the verdict's
#: text or colour. It used to overwrite both with a hardcoded green success
#: sentence, which asserted health over a computed red or amber — a false
#: outward claim, made at exactly the moment the operator asked for the verdict.
_KICK_SCRIPT = (
    'function kick(){'
    '  var cards=document.querySelectorAll(\'.card\');'
    '  cards.forEach(function(c,i){setTimeout(function(){c.style.opacity=\'0.25\';'
    '    c.style.transform=\'scale(0.98)\'},i*90)});'
    '}'
)


def _esc(value) -> str:
    """Escape a value before it is spliced into the artifact.

    Every interpolated field here is third-party text as far as this renderer
    is concerned: totems and details come from live git branch names, notes
    under state/session/, and subprocess output. Without this, a branch called
    `<img src=x onerror=...>` executes in the browser of whoever opens the
    health page. Only WEB_COLOUR values and integer counts bypass it, because
    those are produced by this module from a closed set.
    """
    return html.escape(str(value), quote=True)


def _render_card(code: str, name: str, lv: dict) -> str:
    col = WEB_COLOUR.get(lv.get("health"), WEB_COLOUR["red"])
    return (
        '<div class="card" style="--c:' + col + '">'
        + '<div class="row"><span class="code">' + _esc(code) + '</span>'
        + '<span class="name">' + _esc(name) + '</span>'
        + '<span class="totem">' + _esc(lv["totem"]) + '</span></div>'
        + '<div class="detail">' + _esc(lv["detail"]) + '</div>'
        + '<div class="dream">' + _esc(lv["dream"]) + '</div></div>')


def _render_kick(v: dict) -> str:
    """The verdict block, carrying the computed line AND its computed tone."""
    return (
        '  <div class="kick">'
        + '    <div class="verdict" id="verdict" style="color:'
        + WEB_COLOUR[v["tone"]] + '">' + _esc(v["line"]) + '</div>'
        + '    <div class="counts">' + str(int(v["counts"]["green"])) + ' steady · '
        + str(int(v["counts"]["amber"])) + ' wobbling · ' + str(int(v["counts"]["red"]))
        + ' broken &nbsp;|&nbsp; delight: ' + _esc(v["delight"]) + '</div>'
        + '    <button onclick="kick()">⥀ KICK</button>'
        + '  </div>')


def render_html(levels: list) -> str:
    v = verdict(levels)
    cards = ''.join(_render_card(code, name, lv) for code, name, lv in levels)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        + '<meta name="viewport" content="width=device-width, initial-scale=1">'
        + '<title>⥀ INCEPTION — the machine\'s nested self</title>'
        + '<style>' + _STYLE + '</style></head><body><div class="wrap">'
        + '  <h1>⥀ INCEPTION</h1>'
        + '  <div class="sub">the machine\'s nested self · depth 11 · deterministic · no LLM</div>'
        + cards
        + _render_kick(v)
        + '</div>'
        + '<script>' + _KICK_SCRIPT + '</script></body></html>'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="INCEPTION — the machine's nested self, depth 11")
    ap.add_argument("--kick", action="store_true", help="collapse the spiral to the verdict")
    ap.add_argument("--html", metavar="OUT", help="write a self-contained HTML render")
    args = ap.parse_args()
    levels = collect()
    if args.html:
        Path(args.html).write_text(render_html(levels))
        print("inception: wrote " + args.html)
        return 0
    print(render_terminal(levels, kick=args.kick))
    return 0


if __name__ == "__main__":
    sys.exit(main())
