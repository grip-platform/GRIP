"""HAPPIverse "Seal of Approval" — a deterministic, witness-able welcome artifact.

An INTERNAL team-induction delight++ card. When a teammate stands GRIP up on a
new machine (the ``.claude → .grip`` junction), this renders them a
self-contained HTML "sticker": a cute grey harbour seal bobbing on a blue wave
inside a badge, plus their inductee details and a derived seal fingerprint.

HAPPIverse ethos — *don't trust it, witness it*. The fingerprint is NOT an
opaque badge number: it is ``SEAL-`` + the first 6 uppercase hex of
``sha256("HAPPIverse|" + name + "|" + handle + "|" + home + "|" + date)``. The
page prints the exact hashed text AND the recompute one-liner, so anyone can
re-derive the six characters by hand. A faked seal cannot survive the recompute.

Purity contract (mirrors ``lib/happiverse_proof.py``): stdlib only, no network,
NO ``Date.now``/``random``/wall-clock — every input (including the date) is an
explicit argument. Identical inputs ⇒ byte-identical output. All animation is
pure CSS, gated behind ``@media (prefers-reduced-motion: no-preference)`` so a
reduced-motion reader gets a fully static seal. The document embeds no external
URL and no ``<script>`` — it is offline by construction.

LEAK-SAFETY: this is an internal artifact. Team names / GitHub handles / own
machine home paths are fine; it carries no secret, token, or third-party PII,
and is NOT for public/marketing publication.

CLI:
    python -m lib.seal_of_approval gen --name Fabio --sig "F>>" \\
        --handle Fubzzz --home "C:\\users\\user" --date 2026-07-01 \\
        --joined "pending live-join" --out apps/seal-of-approval/fabio.html
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import sys
from pathlib import Path
from typing import Optional

# The exact prefix hashed into every seal fingerprint. Changing it re-keys every
# seal — it is part of the public witness formula printed on each page.
_FP_PREFIX = "HAPPIverse|"

_TAILNET_FALLBACK = "your-tailnet.ts.net"


def _resolve_tailnet() -> str:
    """Default tailnet inductees join, resolved from the operator's environment
    at CALL time (not import) so a GRIP_TAILNET set after this module is imported
    is still honoured — seal_of_approval may be imported as a library, not only
    run as a CLI. Cosmetic — NOT hashed into the fingerprint (see
    fingerprint_message), so it hardcodes no operator identity in this
    shipped/mirrored file (rules/no-sensitive-info-in-grip.md). Callers pass
    --tailnet explicitly; this only fills the seal's display line when they don't."""
    return os.environ.get("GRIP_TAILNET", "").strip() or _TAILNET_FALLBACK


# Backward-compat import-time snapshot. Prefer _resolve_tailnet() for the live
# value — argparse calls the function so a late GRIP_TAILNET still wins.
DEFAULT_TAILNET = _resolve_tailnet()


def fingerprint_message(name: str, handle: str, home: str, date: str) -> str:
    """The exact UTF-8 text hashed for the seal fingerprint.

    Order is fixed (name, handle, home, date) and pipe-delimited so the page's
    printed recompute one-liner is byte-identical to what is hashed here. Note
    ``sig``, ``tailnet`` and ``joined`` are deliberately NOT in the message — the
    fingerprint keys on identity (who + which machine + when), so changing name
    OR home OR handle OR date changes it, but re-rendering the same person with a
    cosmetic tailnet/joined tweak does not."""
    return f"{_FP_PREFIX}{name}|{handle}|{home}|{date}"


def seal_fingerprint(name: str, handle: str, home: str, date: str) -> str:
    """``SEAL-XXXXXX`` — first 6 UPPERCASE hex of the message's SHA-256."""
    digest = hashlib.sha256(
        fingerprint_message(name, handle, home, date).encode("utf-8")
    ).hexdigest().upper()
    return "SEAL-" + digest[:6]


def _witness_command(name: str, handle: str, home: str, date: str) -> str:
    """A copy-paste one-liner that re-derives the fingerprint.

    Uses a Python RAW string literal (``r'…'``) so Windows home paths containing
    backslash escapes (``C:\\users\\user`` → the ``\\u`` sequence) are hashed as
    the literal bytes, exactly as :func:`fingerprint_message` does."""
    msg = fingerprint_message(name, handle, home, date)
    return (
        "python3 -c \"import hashlib;"
        "print('SEAL-'+hashlib.sha256(r'" + msg + "'.encode())"
        ".hexdigest().upper()[:6])\""
    )


# --- static presentation assets (no interpolation → leak-safe, deterministic) --

_CSS = """
  :root{--bg:#0d1117;--panel:#161b22;--ink:#e6edf3;--muted:#9da7b3;
        --line:#2b333d;--accent:#58a6ff;--sea:#2f6fb0;--seal:#9aa4b0;}
  *{box-sizing:border-box;}
  html{-webkit-text-size-adjust:100%;}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       padding:clamp(1rem,4vw,3rem);}
  main{max-width:640px;margin:0 auto;}
  h1{font-size:clamp(1.5rem,5vw,2.1rem);line-height:1.2;margin:0 0 .4rem;}
  .label{color:var(--accent);font-weight:600;letter-spacing:.08em;
         text-transform:uppercase;font-size:.78rem;}
  .muted{color:var(--muted);} .small{font-size:.85rem;}
  .mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;}
  .tagline{margin:.2rem 0 1.4rem;}
  .sticker{display:flex;justify-content:center;margin:1.4rem 0 .4rem;}
  .sticker svg{width:min(288px,74vw);height:auto;}
  .seal-id{display:flex;flex-direction:column;align-items:center;gap:.15rem;
           margin:.2rem 0 1.7rem;}
  .seal-id-code{font-size:1.7rem;font-weight:700;letter-spacing:.14em;color:var(--accent);}
  .seal-id-note{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;
         padding:1rem 1.2rem;margin:1rem 0;}
  .panel-h{margin:0 0 .6rem;color:var(--muted);font-size:.72rem;text-transform:uppercase;
           letter-spacing:.07em;font-weight:700;}
  .kv{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1.2rem;margin:0;font-size:.92rem;}
  .kv dt{color:var(--muted);} .kv dd{margin:0;word-break:break-word;}
  .topo-line{font-size:1.15rem;display:flex;align-items:center;gap:.55rem;
             margin:.1rem 0 .5rem;flex-wrap:wrap;}
  .tok{background:#21262d;border:1px solid var(--line);border-radius:7px;padding:.15rem .55rem;}
  .tok.accent{color:var(--accent);border-color:var(--accent);}
  .arrow{color:var(--muted);}
  .wield{margin:.2rem 0 0;padding-left:1.1rem;} .wield li{margin:.3rem 0;}
  .wield strong{color:var(--accent);}
  code{background:#21262d;border-radius:5px;padding:.05rem .3rem;
       font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em;}
  .code-block{background:#0b0f14;border:1px solid var(--line);border-radius:8px;
              padding:.7rem .8rem;overflow-x:auto;font-size:.8rem;white-space:pre-wrap;
              word-break:break-all;margin:.3rem 0 .8rem;}
  .tailnet{text-align:center;color:var(--muted);font-size:.82rem;
           margin:1.5rem 0 .4rem;letter-spacing:.02em;}
  .footer{text-align:center;font-weight:800;letter-spacing:.3em;font-size:1.2rem;
          color:var(--accent);margin:.4rem 0 0;}
  .badge-band{fill:#10151c;} .badge-rim{fill:none;stroke:var(--accent);stroke-width:3;}
  .badge-rim-in{fill:none;stroke:var(--accent);stroke-width:2;}
  .medallion{fill:#0f1620;}
  .ringtext{fill:var(--ink);font:700 12px ui-monospace,Menlo,Consolas,monospace;letter-spacing:1px;}
  .shimmer{fill:#ffffff;opacity:0;}
  .wave-back{fill:#24567f;} .wave-front{fill:var(--sea);}
  .wave-foam{fill:none;stroke:#bcd9f2;stroke-width:2;opacity:.7;}
  .seal-body{fill:var(--seal);} .seal-belly{fill:#c2cad4;}
  .seal-dark{fill:#20262e;} .seal-nose{fill:#3a4048;}
  .whisker{stroke:#5b636d;stroke-width:1.4;stroke-linecap:round;}
  @media (prefers-reduced-motion: no-preference){
    .seal{transform-box:fill-box;transform-origin:center;animation:bob 3.4s ease-in-out infinite;}
    .wave-front{animation:sway 5s ease-in-out infinite;}
    .shimmer{animation:shimmer 4.6s ease-in-out infinite;}
  }
  @keyframes bob{0%,100%{transform:translateY(0);}50%{transform:translateY(-4px);}}
  @keyframes sway{0%,100%{transform:translateX(0);}50%{transform:translateX(3px);}}
  @keyframes shimmer{0%,44%{opacity:0;}50%{opacity:.15;}56%,100%{opacity:0;}}
""".strip("\n")

# Circular seal badge. Inline SVG in an HTML document is auto-namespaced by the
# HTML parser, so NO xmlns is needed — and omitting it keeps the whole document
# free of any "http" string (the SVG namespace URI would otherwise contain one),
# preserving the offline-by-construction guarantee. All refs are internal (#id).
_SVG_SEAL = """
    <svg viewBox="0 0 240 240" role="img" aria-hidden="true">
      <defs>
        <path id="ring" d="M120,120 m-100,0 a100,100 0 1,1 200,0 a100,100 0 1,1 -200,0" fill="none"/>
        <clipPath id="med"><circle cx="120" cy="120" r="85"/></clipPath>
      </defs>
      <circle class="badge-band" cx="120" cy="120" r="116"/>
      <circle class="badge-rim" cx="120" cy="120" r="116"/>
      <circle class="badge-rim-in" cx="120" cy="120" r="87"/>
      <text class="ringtext">
        <textPath href="#ring" startOffset="0" textLength="618" lengthAdjust="spacing">SEAL OF APPROVAL &#10022; HAPPIVERSE &#10022;</textPath>
      </text>
      <g clip-path="url(#med)">
        <circle class="medallion" cx="120" cy="120" r="85"/>
        <rect class="shimmer" x="24" y="20" width="54" height="200" transform="rotate(20 120 120)"/>
        <g class="seal">
          <ellipse class="seal-body" cx="96" cy="150" rx="13" ry="8" transform="rotate(-24 96 150)"/>
          <ellipse class="seal-body" cx="144" cy="150" rx="13" ry="8" transform="rotate(24 144 150)"/>
          <ellipse class="seal-body" cx="120" cy="126" rx="46" ry="42"/>
          <ellipse class="seal-belly" cx="120" cy="134" rx="30" ry="30"/>
          <ellipse class="seal-belly" cx="120" cy="120" rx="20" ry="16"/>
          <ellipse class="seal-dark" cx="106" cy="106" rx="7" ry="8"/>
          <ellipse class="seal-dark" cx="134" cy="106" rx="7" ry="8"/>
          <circle fill="#ffffff" cx="108" cy="103" r="2.2"/>
          <circle fill="#ffffff" cx="136" cy="103" r="2.2"/>
          <ellipse class="seal-nose" cx="120" cy="118" rx="6" ry="4.5"/>
          <path d="M120,122 q-5,7 -11,4 M120,122 q5,7 11,4" fill="none" stroke="#3a4048" stroke-width="1.6" stroke-linecap="round"/>
          <line class="whisker" x1="104" y1="118" x2="86" y2="115"/>
          <line class="whisker" x1="104" y1="121" x2="86" y2="122"/>
          <line class="whisker" x1="136" y1="118" x2="154" y2="115"/>
          <line class="whisker" x1="136" y1="121" x2="154" y2="122"/>
        </g>
        <path class="wave-back" d="M18,150 q25,-14 51,0 t51,0 t51,0 t51,0 V222 H18 Z"/>
        <path class="wave-front" d="M18,160 q25,14 51,0 t51,0 t51,0 t51,0 V224 H18 Z"/>
        <path class="wave-foam" d="M18,160 q25,14 51,0 t51,0 t51,0 t51,0"/>
      </g>
    </svg>
""".strip("\n")


def render(
    name: str,
    sig: str,
    handle: str,
    home: str,
    tailnet: str,
    date: str,
    joined: str,
) -> str:
    """Return a complete, self-contained HTML seal document (deterministic).

    Every caller-supplied value is HTML-escaped before interpolation, so a
    signature like ``F>>`` renders as ``F&gt;&gt;`` rather than breaking markup.
    No branch, no clock, no randomness — identical arguments yield byte-identical
    output."""
    e = html.escape
    fp = seal_fingerprint(name, handle, home, date)
    msg = fingerprint_message(name, handle, home, date)
    witness = _witness_command(name, handle, home, date)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seal of Approval — {e(name)} · HAPPIVERSE</title>
<meta name="description" content="An honest, witness-able welcome seal for {e(name)} joining the HAPPIverse. The seal fingerprint is derived by a public formula, not invented — recompute it yourself.">
<style>
{_CSS}
</style>
</head>
<body>
<main>
  <p class="label">HAPPIVERSE · SEAL OF APPROVAL</p>
  <h1>Welcome to the HAPPIverse, {e(name)}.</h1>
  <p class="muted tagline">Don't trust it — <strong>witness it</strong>. The seal below is
     derived from your own details by a public formula. Recompute it yourself; it can't be faked.</p>

  <div class="sticker" role="img" aria-label="Seal of Approval badge: a grey harbour seal bobbing on a blue wave, ringed by the words SEAL OF APPROVAL HAPPIVERSE">
{_SVG_SEAL}
  </div>

  <div class="seal-id">
    <span class="seal-id-code mono">{e(fp)}</span>
    <span class="seal-id-note">your seal fingerprint</span>
  </div>

  <div class="panel">
    <p class="panel-h">Inductee</p>
    <dl class="kv">
      <dt>Name</dt><dd>{e(name)}</dd>
      <dt>Signature</dt><dd class="mono">{e(sig)}</dd>
      <dt>GitHub</dt><dd class="mono">{e(handle)}</dd>
      <dt>Date</dt><dd class="mono">{e(date)}</dd>
      <dt>Machine home</dt><dd class="mono">{e(home)}</dd>
    </dl>
  </div>

  <div class="panel">
    <p class="panel-h">The junction you now run</p>
    <p class="topo-line mono"><span class="tok">.claude</span><span class="arrow">&#8594;</span><span class="tok accent">.grip</span></p>
    <p class="muted small">Your harness still reads <code>~/.claude</code>, but it now points straight
       at <code>~/.grip</code> — one GRIP home, no copies. On Windows that link is a directory
       junction (needs no admin); on macOS a symlink. Same target, same brain.</p>
  </div>

  <div class="panel">
    <p class="panel-h">What you now wield</p>
    <ul class="wield">
      <li><strong>GRIP</strong> — a critical-thinking harness that improves itself as you work.</li>
      <li><strong>HAL</strong> — run any model you like; one substrate speaks to all of them.</li>
      <li><strong>HAPPI</strong> — the polyglot protocol every AI call travels through.</li>
    </ul>
  </div>

  <div class="panel">
    <p class="panel-h">Witness your seal</p>
    <p class="muted small">The fingerprint is <code>SEAL-</code> + the first 6 uppercase hex of the
       <code>sha256</code> of this exact text:</p>
    <pre class="code-block mono">{e(msg)}</pre>
    <p class="muted small">Recompute it (macOS / Linux / Git-Bash):</p>
    <pre class="code-block mono">{e(witness)}</pre>
    <p class="muted small">Same six characters as <span class="mono">{e(fp)}</span>? Then the seal is real.</p>
  </div>

  <p class="tailnet mono">tailnet · {e(tailnet)} · {e(joined)}</p>

  <p class="footer">LFG <span aria-hidden="true">&#129773;</span></p>
</main>
</body>
</html>
"""


def _write(out: Path, doc: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="HAPPIverse Seal of Approval renderer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="render a seal HTML document")
    g.add_argument("--name", required=True)
    g.add_argument("--sig", required=True, help="operator signature, e.g. 'F>>'")
    g.add_argument("--handle", required=True, help="GitHub handle")
    g.add_argument("--home", required=True, help="machine home path")
    g.add_argument("--date", required=True, help="ISO induction date, e.g. 2026-07-01")
    g.add_argument("--joined", required=True, help="tailnet join state / detail")
    g.add_argument("--tailnet", default=_resolve_tailnet())
    g.add_argument("--out", help="output HTML path (default: stdout)")

    ns = ap.parse_args(argv)
    doc = render(ns.name, ns.sig, ns.handle, ns.home, ns.tailnet, ns.date, ns.joined)
    fp = seal_fingerprint(ns.name, ns.handle, ns.home, ns.date)
    if ns.out:
        _write(Path(ns.out), doc)
        print(f"wrote seal → {ns.out} [{fp}]")
    else:
        sys.stdout.write(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
