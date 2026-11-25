"""RSI-witness publisher — render GRIP's live self-improvement as a verifiable,
Banach-idempotent static site. The cousin of ``lib/decision_chain_publish.py``.

    "Don't trust that the AI is recursively self-improving — WITNESS it."

Each signal card carries the *provenance* to verify it independently; a fabricated
signal has no resolvable provenance. ``render_site(signals)`` is a PURE function of
the signals (byte-identical on re-render — the Banach fixed point), so the site
changes only when the real signals change. Timestamps come from the signal data,
never from ``datetime.now()``, so the render stays deterministic.

Part of the Precognition area (task/issue: public RSI-witness dashboard). W1:
local render + verify + selftest (dry-run only; the live gh-pages publish is the
operator's gated step, per exogenous-anchor-law bootstrap carve-out).
"""
from __future__ import annotations

import hashlib
import html
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

DECISION_CHAIN_URL = "https://codetonight-sa.github.io/grip-decision-chain/"
SIGNALS_FILE = "signals.json"
INDEX_FILE = "index.html"
# A published signal must never carry a real secret/host. Cheap denylist floor;
# the full redaction shield (lib/redaction_shield) is layered in at W4 (publish).
_LEAK_MARKERS = ("@s.whatsapp.net", "sk-", "ghp_", "AKIA", "-----BEGIN")

# The IP-suite brands, embedded as base64 data-URIs (self-contained, CSP-safe) from
# assets/witness/logos.json — generated once from the canonical on-dark marks. Loaded
# at import so render stays a pure function (no file reads / Pillow at render time).
_BRANDS = ("GRIP", "HAL", "HAPPI", "GRASP")
_BRAND_TAGLINE = {
    "GRIP": "critical-thinking RSI engine",
    "HAL": "multi-provider AI substrate",
    "HAPPI": "universal AI protocol",
    "GRASP": "public provability",
}


def _load_logos() -> dict:
    """Load the embedded brand-logo data-URIs. Fail-soft to {} (a fork without the
    asset renders no marks rather than breaking)."""
    try:
        data = json.loads((_LIB.parent / "assets" / "witness" / "logos.json").read_text())
        return {b: data[b]["data_uri"] for b in _BRANDS if b in data}
    except Exception:
        return {}


_LOGOS = _load_logos()

# The moat, distilled to its load-bearing components across five IP-suite layers
# (top product -> bottom engine). The middle layer is THE moat — cryptographic
# causation's three legs — rendered in the accent. Deterministic: the SVG is
# generated purely from this spec (no dates/randomness) so render stays Banach.
_MOAT_LAYERS = [
    ("PRODUCT", [
        ("GRASP", "public provability — a skeptic verifies the chain"),
        ("blackbook-xp", "one-install sovereign witnessable node"),
    ]),
    ("MOAT · CRYPTOGRAPHIC CAUSATION", [
        ("IDR", "what it decided — signed, Merkle-rooted"),
        ("memory-chain", "what it believed when — signed"),
        ("prove-it", "every claim provable — cite.verify"),
    ]),
    ("PROTOCOL", [
        ("HAPPI", "polyglot wire spec — idr + context + cite.verify"),
    ]),
    ("SUBSTRATE", [
        ("HAL", "multi-provider syscall — failover + signing"),
        ("shims", "everything is a shim; AI is a syscall"),
    ]),
    ("ENGINE", [
        ("GRIP", "RSI engine — converge() to an exogenous criterion"),
        ("InnerspeakerGo", "sovereign-ssh syscall — self-witness, no infra"),
    ]),
]
_MOAT_W = 880
_MOAT_ROW_H = 118
_MOAT_BOX_H = 76
_MOAT_BOX_W = 200


def _moat_box_centres(n: int) -> list:
    """X-centres for n boxes, kept fully INSIDE the viewBox (a box centre must stay
    within [halfwidth+margin, W-halfwidth-margin] or the box clips at the edge)."""
    margin = _MOAT_BOX_W / 2 + 14
    left, right = margin, _MOAT_W - margin
    if n == 1:
        return [(left + right) / 2]
    step = (right - left) / (n - 1)
    return [left + i * step for i in range(n)]


def _row_y(i: int) -> int:
    return 34 + i * _MOAT_ROW_H  # 34px top leaves room for the first row's layer label


def _adjacent_row_pairs(centres: list) -> list:
    """(y1, y2, x1, x2) for every box->below-box connector. Bounded per row."""
    from itertools import product
    out = []
    for i in range(len(centres) - 1):
        y1, y2 = _row_y(i) + _MOAT_BOX_H, _row_y(i + 1)
        out += [(y1, y2, a, b) for a, b in product(centres[i], centres[i + 1])]
    return out


def _moat_edges_svg(centres: list) -> str:
    """Connector lines: each box to every box in the row below."""
    return "".join(
        f'<line x1="{a:.0f}" y1="{y1}" x2="{b:.0f}" y2="{y2}" class="moat-edge"/>'
        for y1, y2, a, b in _adjacent_row_pairs(centres)
    )


def _wrap_two(role: str, budget: int = 30) -> tuple:
    """Split a role string into (line1, line2) at the FIRST word that overflows the
    budget — everything after breaks to line2 (preserve reading order; never
    cherry-pick a later short word back onto line1)."""
    words = role.split()
    line1, i = [], 0
    for i, w in enumerate(words):
        if line1 and len(" ".join(line1 + [w])) > budget:
            break
        line1.append(w)
    else:
        return " ".join(line1), ""
    return " ".join(line1), " ".join(words[len(line1):])


def _moat_box_svg(x: float, y: int, title: str, role: str, moat: bool) -> str:
    l1, l2 = _wrap_two(role)
    cls = "moat-box moat-box-hl" if moat else "moat-box"
    out = [f'<g class="{cls}">',
           f'<rect x="{x - _MOAT_BOX_W / 2:.0f}" y="{y}" width="{_MOAT_BOX_W}" height="{_MOAT_BOX_H}"/>',
           f'<text x="{x:.0f}" y="{y + 28}" class="moat-title">{html.escape(title)}</text>',
           f'<text x="{x:.0f}" y="{y + 48}" class="moat-role">{html.escape(l1)}</text>']
    if l2:
        out.append(f'<text x="{x:.0f}" y="{y + 63}" class="moat-role">{html.escape(l2)}</text>')
    out.append("</g>")
    return "".join(out)


def _moat_row_svg(i: int, layer: str, boxes: list, centres: list) -> str:
    y = _row_y(i)
    moat = i == 1
    hl = " moat-layer-hl" if moat else ""
    # Label sits ABOVE the row (in the inter-row gap), left-aligned — no collision
    # with the boxes (which was clipping "PRODUCT" to "PROD" etc.).
    label = (f'<text x="14" y="{y - 8}" '
             f'class="moat-layer{hl}">{html.escape(layer)}</text>')
    boxes_svg = "".join(_moat_box_svg(x, y, t, r, moat) for x, (t, r) in zip(centres, boxes))
    return label + boxes_svg


def _moat_svg() -> str:
    """Generate the layered moat architecture SVG from _MOAT_LAYERS — pure, no deps."""
    height = len(_MOAT_LAYERS) * _MOAT_ROW_H + 20
    centres = [_moat_box_centres(len(boxes)) for _, boxes in _MOAT_LAYERS]
    rows = "".join(_moat_row_svg(i, layer, boxes, centres[i])
                   for i, (layer, boxes) in enumerate(_MOAT_LAYERS))
    return (f'<svg viewBox="0 0 {_MOAT_W} {height}" role="img" '
            f'aria-label="GRIP IP-suite moat architecture" class="moat-svg">'
            f'{_moat_edges_svg(centres)}{rows}</svg>')


def _load_shield():
    """The redaction shield (client-identity denylist) for the PUBLIC witness, or
    None if the shield library is unavailable (a fork without it). A ciphertext/
    invalid denylist is handled fail-CLOSED in verify_publishable, not here — GRIP
    is private, so PR-titles/commit-subjects are the leak surface for the auto-loop."""
    try:
        from redaction_shield import Shield, load_denylist
        return Shield(load_denylist())
    except Exception:
        return None


def _item_is_safe(shield, text) -> bool:
    """True iff the shield finds no denylisted identity in text.

    Fail-open ONLY when there is genuinely no shield (shield is None — a fork
    without the library; live_publish's denylist-state gate is the backstop there).
    A PRESENT shield that ERRORS mid-scan fails CLOSED (returns False → the item is
    omitted) — a shield we cannot run cannot vouch that the text is client-clean."""
    if shield is None:
        return True
    try:
        return not shield.redact(str(text)).changed
    except Exception:
        return False  # present-but-erroring shield => omit (fail closed)


def _item_clean(shield, item) -> bool:
    """Full-item shield check: scans the item's ENTIRE canonical JSON — title,
    tldr, url, links, every present and future field — never one chosen field.

    Defence at the source (AGORA #3970): a client identity in a URL slug (e.g. a
    client-named repo in a PR link) is OMITTED here item-by-item (fail-soft, the
    page still publishes) instead of only tripping the fail-closed whole-publish
    refusal at the push boundary."""
    return _item_is_safe(shield, json.dumps(item, sort_keys=True,
                                            ensure_ascii=False, default=str))


def _denylist_state() -> str:
    """Denylist usability: ok / missing / ciphertext / invalid. 'missing' is
    legacy-permitted (a fork without the shield); ciphertext/invalid fail closed."""
    try:
        from redaction_shield import denylist_state
        return denylist_state()
    except Exception:
        return "missing"


def _signals_shield_clean(signals: dict) -> bool:
    """True iff EVERY published string in EVERY signal passes the shield. The
    canonical JSON blob is the exact bytes that get pushed, so scanning it catches
    a denylisted identity anywhere (title, tldr, caption, link text) — even if the
    signals came from a cached signals.json that bypassed refresh's per-item shield.
    No shield => True (the denylist-state gate above already refused a locked one)."""
    shield = _load_shield()
    if shield is None:
        return True
    return _item_is_safe(shield, _canonical_signals(signals))


def _failsoft(fn):
    """Wrap a signal-loader so ANY failure OMITS the card (returns None) rather
    than faking it or raising — the witness never shows a signal it cannot back."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None
    return wrapper


@_failsoft
def _load_hypotheses(base: Path):
    """Hypothesis verdicts that rest on an actual measurement.

    Publishes the GROUNDED count, not the total. A verdict is grounded when a
    collector exists for its metric; otherwise it was graded from the sentinel
    _resolve_metric returns for an unknown metric -- a 0.0 indistinguishable
    from a genuine measurement of zero.

    This surface previously published every verdict and described the number as
    "exogenously verified". Measured 2026-09-20 at the time of the fix: 282
    verdicts, of which 12 were grounded and 270 -- 95.7% -- were graded from
    that sentinel. Describing 282 as exogenously verified overstated the
    evidence by a factor of more than twenty, on a page whose entire purpose is
    to let a stranger check the claim rather than take it on trust.

    (The audit that found this put the ungrounded share at 252 of 282. Measured
    here directly through the engine's own _metric_is_resolvable, it is 270 of
    282 -- the audit understated it. The number published is the measured one.)

    The total is still shown, labelled as what it is. Both figures are true and
    they answer different questions: how many verdicts exist, and how many of
    them rest on a measurement. Publishing the first under the name of the
    second is the defect; publishing the first BESIDE the second is honest and
    is more informative than either alone.
    """
    sys.path.insert(0, str(base))
    import lib.hypothesis_engine as he  # noqa: E402

    st = he.status()
    by = st.get("by_status", {})
    grounded = st.get("grounded_by_status", {})

    g_conf = grounded.get("confirmed", 0)
    g_ref = grounded.get("refuted", 0)
    g_total = g_conf + g_ref
    all_total = by.get("confirmed", 0) + by.get("refuted", 0)
    ungrounded = all_total - g_total

    caption = (
        f"{g_total} of {all_total} verdicts rest on a resolvable metric; "
        f"the other {ungrounded} were graded from a sentinel that means no "
        f"collector exists, and are NOT evidence. "
        f"{st.get('total', 0)} hypotheses registered."
    )

    return {
        "key": "hypotheses",
        "label": "Hypotheses tested (grounded)",
        "value": f"{g_conf} confirmed / {g_ref} refuted",
        "caption": caption,
        "verify": "python lib/hypothesis_engine.py status",
    }


def _decision_chain_signal():
    """Pointer to the sibling decision-chain — what GRIP decided + believed when."""
    return {
        "key": "decision_chain",
        "label": "Decision chain",
        "value": "tamper-evident",
        "caption": "Every consequential decision — the what / why / how / believed-when — signed and replayable",
        "verify": DECISION_CHAIN_URL,
    }


# grip-bot follows every merged PR with a "chore(changelog): update for PR #N"
# commit. It records a change rather than making one, so counting it doubles
# the pulse it sits beside: measured 2026-10-05, 40 of 106 commits on main in
# the past week, and 4 of the 8 items the shipped stream then showed.
_BOOKKEEPING_PREFIX = "chore(changelog): update for PR"


def _is_bookkeeping(subject: str) -> bool:
    return subject.startswith(_BOOKKEEPING_PREFIX)


@_failsoft
def _load_rsi_velocity(base: Path):
    """Recent shipping velocity — commits merged to main in the last 7 days: the
    loop's pulse."""
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(base), "log", "--format=%s", "--since=7 days ago", "origin/main"],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        return None
    n = len([s for s in out.stdout.splitlines() if s.strip() and not _is_bookkeeping(s)])
    return {
        "key": "velocity",
        "label": "Shipping velocity",
        "value": f"{n} changes / 7 days",
        "caption": "commits merged to main in the last week, not counting the bot's automatic changelog entries",
        "verify": f"git log --oneline --since='7 days ago' origin/main | grep -vc '{_BOOKKEEPING_PREFIX}'",
    }


@_failsoft
def _load_converging(base: Path):
    """Live WIP — the PRs GRIP is converging RIGHT NOW, with REAL links. Read from a
    snapshot refreshed out-of-band (refresh_converging) so the render stays a pure
    function of the snapshot (Banach). Omitted when nothing is in flight."""
    snap = json.loads((base / "state" / "rsi-witness" / "converging.json").read_text())
    prs = snap.get("prs", [])
    if not prs:
        return None
    return {
        "key": "converging",
        "label": "Converging now",
        "value": f"{len(prs)} in flight",
        "caption": "open PRs GRIP is driving through CI + the cross-provider council right now — watch it happen",
        "verify": snap.get("verify", "gh pr list"),
        "links": [{"text": f"#{p.get('number')} {p.get('title', '')}"[:70], "url": p.get("url", "")}
                  for p in prs[:8]],
    }


@_failsoft
def _load_status_stream(base: Path):
    """The rolling live status of the ever-running RSI stream — the current WIP
    plus recent activity in plain language. Read from a snapshot (pure render)."""
    data = json.loads((base / "state" / "rsi-witness" / "status-stream.json").read_text())
    items = data.get("items", [])
    if not items:
        return None
    return {"key": "stream", "items": items[:12]}


@_failsoft
def _load_dojo(base: Path):
    """The dojo panel — a live tip + the newest GRIP commands + a mesh/HAPPIverse
    context line. Read from a snapshot refreshed out-of-band (refresh writes it with
    a daily-rotating tip), so the render stays a pure function. Omitted if absent."""
    data = json.loads((base / "state" / "rsi-witness" / "context.json").read_text())
    if not (data.get("tip") or data.get("newest")):
        return None
    return {"key": "dojo", "tip": data.get("tip", ""),
            "newest": data.get("newest", [])[:6], "mesh": data.get("mesh", "")}


def load_signals(claude_dir=None) -> dict:
    """Collect the live RSI signals. Fail-soft: a source that cannot load is
    OMITTED (never faked) — the witness never shows a signal it cannot back.

    There is no genome card. A pre-registered test on 2026-11-22 (H955) found
    that no production code acts on the genome, so a card for it invited a
    claim this page cannot back, and V>> removed it. Re-adding one needs
    evidence that the genome changes behaviour."""
    base = Path(claude_dir) if claude_dir else get_claude_dir()
    raw = [_load_status_stream(base), _load_hypotheses(base),
           _load_rsi_velocity(base), _load_converging(base), _decision_chain_signal(),
           _load_dojo(base)]
    return {"signals": [s for s in raw if s is not None]}


def _canonical_signals(signals: dict) -> str:
    return json.dumps(signals, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_NONCARD_KEYS = {"stream", "dojo"}


def _ablank(url: str, text: str, cls: str = "") -> str:
    """An external link that opens in a new tab (target=_blank + rel=noopener)."""
    c = f' class="{cls}"' if cls else ""
    return (f'<a{c} href="{html.escape(url)}" target="_blank" rel="noopener">'
            f'{html.escape(text)}</a>')


def _links_html(sig: dict) -> str:
    links = sig.get("links") or []
    if not links:
        return ""
    items = "".join(_ablank(str(lk.get("url", "")), str(lk.get("text", "")), "wip") for lk in links)
    return f'\n      <div class="links">{items}</div>'


def _card_html(sig: dict) -> str:
    label = html.escape(str(sig.get("label", "")))
    value = html.escape(str(sig.get("value", "")))
    caption = html.escape(str(sig.get("caption", "")))
    verify = str(sig.get("verify", ""))
    proof = _ablank(verify, verify) if verify.startswith("http") else f"<code>{html.escape(verify)}</code>"
    cls = "card wip-card" if sig.get("links") else "card"
    return (
        f'    <div class="{cls}">\n'
        f'      <div class="label">{label}</div>\n'
        f'      <div class="value">{value}</div>\n'
        f'      <div class="caption">{caption}</div>'
        f'{_links_html(sig)}\n'
        f'      <div class="verify"><span class="vk">verify</span> {proof}</div>\n'
        f"    </div>"
    )


def _wip_item_html(item: dict) -> str:
    kind = html.escape(str(item.get("kind", "")))
    url = html.escape(str(item.get("url", "")))
    tldr = html.escape(str(item.get("tldr", "")))
    shimmer = '<span class="bar"><span></span></span>' if item.get("kind") == "converging" else ""
    return (
        f'      <a class="wip-item {kind}" data-key="{url}" href="{url}" target="_blank" rel="noopener">'
        f'<span class="k">{kind}</span>'
        f'<span class="txt">{tldr}</span>{shimmer}</a>'
    )


def _stream_html(stream) -> str:
    """The live ACTIVITY panel — a vertical stack of the current in-flight + recently
    shipped items. Scrolls with the page (not sticky); on mobile it is a toggleable
    menu (see the .activity CSS + livetoggle JS). Each converging item fades out LIVE
    when it finishes (client poll of signals.json)."""
    items = (stream or {}).get("items") or []
    if not items:
        return ""
    n_wip = sum(1 for i in items if i.get("kind") == "converging")
    tower = "\n".join(_wip_item_html(i) for i in items[:9])
    return (
        '  <section class="activity" id="activity">\n'
        '    <div class="shead"><span class="dot"></span><span class="lbl">LIVE</span>'
        f'<span class="cnt">{n_wip} in flight</span></div>\n'
        f'    <div class="tower" id="tower">\n{tower}\n    </div>\n'
        "  </section>"
    )


def _brand_strip_html() -> str:
    """The IP-suite brand marks (GRIP · HAL · HAPPI · GRASP), embedded data-URIs.
    Falls back to a text wordmark when the logo asset is absent (a fork), so the
    nav is never an empty bordered bar."""
    if not _LOGOS:
        words = "".join(f'<span class="brand"><span class="bn">{b}</span></span>' for b in _BRANDS)
        return f'<div class="brands">{words}</div>'
    marks = "".join(
        f'<span class="brand" title="{b} — {html.escape(_BRAND_TAGLINE.get(b, ""))}">'
        f'<img src="{_LOGOS[b]}" alt="{b}" width="30" height="30">'
        f'<span class="bn">{b}</span></span>'
        for b in _BRANDS if b in _LOGOS
    )
    return f'<div class="brands">{marks}</div>'


def _moat_section_html() -> str:
    """The novel delight: an SVG map of how the moat's components compose, and each
    one's role in the IP suite."""
    return (
        '  <section class="moat" id="moat">\n'
        '    <div class="sec-label">THE MOAT &middot; HOW IT ALL FITS</div>\n'
        '    <h2>One suite. Every decision provable.</h2>\n'
        '    <p class="lede">GRIP improves itself; HAL runs it on any model; HAPPI is the wire; '
        'GRASP lets a skeptic verify. The middle layer &mdash; what it decided, what it believed, '
        'every claim proven &mdash; is the moat: <b>cryptographic causation</b>.</p>\n'
        f'    <div class="moat-wrap">{_moat_svg()}</div>\n'
        "  </section>"
    )


def _dojo_html(dojo: dict) -> str:
    """The dojo panel — a live tip-of-the-day + the newest GRIP commands + a mesh /
    HAPPIverse context line, refreshed autonomously so the page stays current."""
    if not dojo:
        return ""
    tip = html.escape(str(dojo.get("tip", "")))
    rows = "".join(
        f'<li><code>{html.escape(str(c.get("name", "")))}</code>'
        f'<span class="cd">{html.escape(str(c.get("desc", "")))}</span></li>'
        for c in (dojo.get("newest") or [])
    )
    mesh = html.escape(str(dojo.get("mesh", "")))
    tip_block = (f'<div class="dojo-tip"><div class="sec-label">TIP OF THE DAY</div>'
                 f'<p>{tip}</p></div>') if tip else ""
    cmd_block = (f'<div class="dojo-cmds"><div class="sec-label">NEWEST COMMANDS</div>'
                 f'<ul>{rows}</ul></div>') if rows else ""
    mesh_block = f'<div class="dojo-mesh">{mesh}</div>' if mesh else ""
    return (
        '  <section class="dojo" id="dojo">\n'
        f'    <div class="dojo-grid">{tip_block}{cmd_block}</div>\n'
        f'    {mesh_block}\n'
        "  </section>"
    )


def render_html(signals: dict) -> str:
    items = list(signals.get("signals", []))
    stream = next((s for s in items if s.get("key") == "stream"), None)
    dojo = next((s for s in items if s.get("key") == "dojo"), None)
    cards = "\n".join(_card_html(s) for s in items if s.get("key") not in _NONCARD_KEYS)
    return (_PAGE
            .replace("<!--BRANDS-->", _brand_strip_html())
            .replace("<!--STREAM-->", _stream_html(stream))
            .replace("<!--CARDS-->", cards)
            .replace("<!--MOAT-->", _moat_section_html())
            .replace("<!--DOJO-->", _dojo_html(dojo)))


def render_site(signals: dict, dest: Path) -> Path:
    """Render the witness site into ``dest`` (created if absent). Re-rendering the
    same ``signals`` yields a byte-identical tree — the Banach fixed point."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / SIGNALS_FILE).write_text(_canonical_signals(signals))
    (dest / INDEX_FILE).write_text(render_html(signals))
    return dest


def tree_manifest(root: Path) -> dict:
    """sha256 per file — two renders are the same site iff their manifests match."""
    manifest = {}
    for name in (SIGNALS_FILE, INDEX_FILE):
        path = root / name
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    return manifest


def verify_publishable(signals: dict) -> tuple:
    """Refuse to publish an empty or leak-bearing witness. (The fail-closed
    redaction-denylist gate lives on the push path in live_publish, so this stays
    a pure content check the dry-run + tests can exercise without a keyring.)"""
    items = signals.get("signals", [])
    if not items:
        return False, "no signals — nothing to witness"
    blob = _canonical_signals(signals)
    hits = [m for m in _LEAK_MARKERS if m in blob]
    if hits:
        return False, f"leak marker(s) {hits} in signals — NOT publishing"
    return True, f"publishable — {len(items)} signals, no leaks"


def selftest_idempotent(signals: dict | None = None) -> bool:
    """Prove ``render(render(S)) == render(S)`` byte-for-byte — the fixed point."""
    import tempfile

    signals = signals if signals is not None else load_signals()
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        render_site(signals, Path(a))
        render_site(signals, Path(b))
        return tree_manifest(Path(a)) == tree_manifest(Path(b))


def dry_run_publish(claude_dir=None) -> dict:
    """Render to a temp dir and report the verdict + manifest — never pushes."""
    import tempfile

    signals = load_signals(claude_dir)
    ok, msg = verify_publishable(signals)
    with tempfile.TemporaryDirectory() as d:
        render_site(signals, Path(d))
        manifest = tree_manifest(Path(d))
    return {"publishable": ok, "message": msg, "signals": len(signals["signals"]),
            "manifest": manifest, "idempotent": selftest_idempotent(signals)}


# --- W4 deploy: operator-gated live publish to the gh-pages witness repo ---
DEFAULT_WITNESS_REPO = "https://github.com/CodeTonight-SA/grip-rsi-witness.git"
_CONVERGING_REPOS = ("grip-platform/GRIP", "grip-platform/HAL")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a same-directory temp file + rename, so a kill mid-write can never
    leave a partial/truncated snapshot on disk. Safety never depended on this (the
    push boundary re-scans + fails closed) — this closes the AVAILABILITY residue:
    a half-written snapshot would make the page render without that panel for a
    tick (the _failsoft loaders omit unparseable files)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _open_prs(repo: str, shield) -> list:
    """Open PRs for one repo as {number,title,url} dicts, full-item shield-filtered
    AT THE SOURCE (interface parity with _recent_shipped: the filter lives here,
    not at one call site, so every caller — present or future — inherits it; a
    client identity ANYWHERE in the item, title or URL slug, never leaves this
    function). Fail-soft => []."""
    import subprocess

    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls?state=open&per_page=20",
             "--jq", ".[] | {number, title, url: .html_url}"],
            capture_output=True, text=True, timeout=30,
        )
        rows = [json.loads(ln) for ln in out.stdout.splitlines() if ln.strip()]
        return [p for p in rows if _item_clean(shield, p)]
    except Exception:
        return []


def _recent_shipped(base: Path, shield) -> list:
    """Recent commits merged to main as 'shipped' status items, shield-filtered
    AT THE SOURCE so every caller inherits the filtration (AGORA #3970 — the
    filter lives here, not at one call site). Fail-soft => []."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(base), "log", "-40", "--format=%H\t%s", "origin/main"],
            capture_output=True, text=True, timeout=15,
        )
        rows = (ln.partition("\t") for ln in out.stdout.splitlines())
        real = [(sha, subj) for sha, _sep, subj in rows
                if subj and not _is_bookkeeping(subj)][:8]
        return [item for item in
                ({"kind": "shipped", "tldr": subj[:90],
                  "url": f"https://github.com/grip-platform/GRIP/commit/{sha}"}
                 for sha, subj in real)
                if _item_clean(shield, item)]
    except Exception:
        return []


_TIPS = (
    "verify-canonical: trust origin/main + the live endpoint + an e2e probe — never a status label.",
    "Exogenous-anchor law: a loop is only valid if a process it cannot please evaluates the exit.",
    "No stubs: every symbol is fully implemented — a hollow placeholder is a false-completeness signal.",
    "Banach idempotence: if improve(state) == state, the loop has converged. Provable, not felt.",
    "Everything is a shim; AI is a syscall — one stable boundary, provider-swappable underneath.",
    "Rate-limited never blocks: a transient 429 is a retry/failover signal, not a halt.",
    "Parallel over either-or: independent, non-exclusive work runs together, not as a menu.",
    "R0: every claim needs specific evidence, a scope that matches it, and a stated falsifier"
    " — on any miss, qualify it or drop it.",
    "A green test that cannot fail is not a check: prove it goes red before you trust it passes.",
    "Declared input must reach the actor — an accepted flag nothing reads is a lie about influence.",
    "The shared tree is not canonical: resolve any decision-gating read against origin/main.",
    "A negative claim is only as strong as the search behind it — name the scopes you checked.",
)


def _daily_tip(day_ordinal: int) -> str:
    """The tip of the day, drawn ONLY from the curated set above.

    It deliberately does NOT read ``learning/insights.jsonl``. That file is the
    friction ledger, not a tip corpus: on 2026-08-25, 427 of its 449 qualifying
    rows were machine records shaped like ``/evo: 'user_rejected_action' ff=18%
    (34/41). Ev: grip_workflow:broly, ...``, and the remaining 22 were report
    suggestions quoting the operator verbatim and naming third parties. None of
    it is advice, and publishing it here would put internal material on a public
    surface (rules/visual-artifact-leak-prevention.md).

    Deterministic given ``day_ordinal`` — the same day renders the same tip.
    """
    return _TIPS[day_ordinal % len(_TIPS)]


def _skill_desc(skill_md: Path) -> str:
    """First-sentence description from a SKILL.md frontmatter (short, plain)."""
    try:
        for line in skill_md.read_text(errors="replace").splitlines():
            if line.lower().startswith("description:"):
                return line.split(":", 1)[1].strip().strip('"').split(".")[0][:80]
    except Exception:
        pass
    return ""


def _added_dates(base: Path) -> dict:
    """``{skill name: unix ts of the commit that first added it}``, from ONE git call.

    ``git log`` walks newest-first, so overwriting as we go leaves the EARLIEST
    add — the moment the command actually appeared. Fail-soft => ``{}``.

    The format is ``%ct`` (committer date), NOT ``%at`` (author date). They differ
    whenever history is rewritten, and this repository squash-merges every PR, so
    the author date can predate the commit that actually put the file on main by
    days. "When did this command land" is a question only the committer date
    answers.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(base), "log", "--diff-filter=A", "--format=%ct",
             "--name-only", "--", "skills/"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return {}
    if out.returncode != 0:
        # Partial stdout on a nonzero exit would be indistinguishable from a
        # complete answer, and _newest_commands would then rank every missing
        # skill at 0 instead of falling back to mtime. Fail-soft means empty.
        return {}
    added, ts = {}, None
    for ln in out.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.isdigit():
            ts = int(ln)
        else:
            parts = ln.split("/")
            if ts and len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
                added[parts[1]] = ts
    return added


# Status words that only ever mark a retired command. Words that double as
# ordinary subject matter are deliberately absent — "legacy" ("refactoring
# legacy codebases"), "archived" and "no longer" would each false-positive on a
# live skill. Measured against all 306 real descriptions on 2026-08-25, the three
# kept here hide exactly 4 skills and all 4 are genuinely retired
# (/ability-framework, /auto-ship, /pair-mode, /rsi-optimal); "archived" and
# "no longer" contributed nothing, so dropping them costs no coverage at all.
_RETIRED_MARKERS = ("deprecated", "tombstone", "superseded")


def _newest_commands(base: Path, n: int = 6) -> list:
    """The most recently ADDED GRIP commands, ordered by git history.

    Ordering by ``st_mtime`` is what this used to do, and it answers a different
    question: "last touched on disk", which a checkout or a worktree switch
    rewrites wholesale. It ranked /pair-mode (added 2026-02-07, and self-described
    DEPRECATED) above /owes (added 2026-08-18). Git add dates are also stable
    across clones, so the rendered page stops differing between machines that hold
    identical content — the Banach property this module claims.

    Untracked skill folders sort last rather than by mtime, for that determinism.
    If git cannot answer at all, the whole ordering falls back to mtime.
    """
    if n <= 0:
        return []                      # entries[:n] used to mean this; keep it
    try:
        skills = base / "skills"
        dirs = [d for d in skills.iterdir()
                if d.is_dir() and (d / "SKILL.md").exists()]
        added = _added_dates(base)
        entries = sorted(
            ((added.get(d.name, 0) if added else d.stat().st_mtime, d.name)
             for d in dirs), reverse=True)
        out = []
        for _when, name in entries:
            desc = _skill_desc(skills / name / "SKILL.md")
            if any(w in desc.lower() for w in _RETIRED_MARKERS):
                continue
            out.append({"name": "/" + name, "desc": desc})
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


def _build_dojo_context(base: Path, shield, day_ordinal: int) -> dict:
    """Assemble the dojo panel content (tip + newest commands + mesh line), shielded."""
    tip = _daily_tip(day_ordinal)
    if not _item_is_safe(shield, tip):
        tip = ""
    newest = [c for c in _newest_commands(base)
              if _item_is_safe(shield, c["name"] + " " + c["desc"])]
    # Plain unicode (not HTML entities) — the mesh line is html.escape'd at render.
    mesh = ("grip-session-mesh over Tailscale · HAPPIverse public constellation "
            "— newest commands surface here on every autonomous refresh.")
    return {"tip": tip, "newest": newest, "mesh": mesh}


def refresh_converging(base=None) -> int:
    """Snapshot the open PRs (converging.json) AND the rolling live-status stream
    (status-stream.json) across GRIP + HAL (network, out-of-band — keeps the render
    a pure function of the snapshots). Returns the open-PR count."""
    base = Path(base) if base else get_claude_dir()
    shield = _load_shield()
    # Shield filtration lives INSIDE _open_prs/_recent_shipped (defence at the
    # source, full-item scan — AGORA #3970), so a PR/commit that names a client
    # ANYWHERE can never reach the public auto-published witness. Snapshot writes
    # are atomic (temp+rename) so a kill mid-refresh never leaves a partial file.
    prs = []
    for repo in _CONVERGING_REPOS:
        prs.extend(_open_prs(repo, shield))
    dest = base / "state" / "rsi-witness"
    dest.mkdir(parents=True, exist_ok=True)
    _write_atomic(dest / "converging.json", json.dumps(
        {"prs": prs, "verify": "gh pr list --repo grip-platform/GRIP"},
        indent=2, sort_keys=True) + "\n")
    converging = [{"kind": "converging", "tldr": p.get("title", "")[:90], "url": p.get("url", "")}
                  for p in prs]
    shipped = _recent_shipped(base, shield)  # shield-filtered at the source
    items = converging + shipped
    _write_atomic(dest / "status-stream.json", json.dumps(
        {"items": items}, indent=2, sort_keys=True) + "\n")
    # Dojo context: a daily-rotating tip + the newest GRIP commands + a mesh line, so
    # the page (and GRIP's own loaded context) stays current on every autonomous tick.
    import datetime
    dojo = _build_dojo_context(base, shield, datetime.date.today().toordinal())
    _write_atomic(dest / "context.json", json.dumps(dojo, indent=2, sort_keys=True) + "\n")
    return len(prs)


def _git(clone: Path, *args: str) -> str:
    import subprocess

    out = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()[:200]}")
    return out.stdout


def _mirror_into(dest: Path, clone: Path) -> None:
    import shutil

    for name in (SIGNALS_FILE, INDEX_FILE):
        shutil.copyfile(dest / name, clone / name)


def _deploy_to_clone(signals: dict, repo: str, clone: Path) -> dict:
    """Pristine-reset the gh-pages clone, mirror the rendered tree, push ONLY when
    the tree actually changed (the Banach property at the git layer)."""
    import subprocess
    import tempfile

    if not (clone / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--", repo, str(clone)], check=True)
    _git(clone, "fetch", "-q", "origin", "main")
    _git(clone, "reset", "-q", "--hard", "origin/main")
    with tempfile.TemporaryDirectory() as tmp:
        _mirror_into(render_site(signals, Path(tmp)), clone)
    if not _git(clone, "status", "--porcelain").strip():
        return {"ok": True, "published": False, "reason": "up to date — nothing to publish"}
    _git(clone, "add", "--", SIGNALS_FILE, INDEX_FILE)
    _git(clone, "commit", "-q", "-m", f"witness: sync {len(signals['signals'])} signals")
    _git(clone, "push", "-q", "origin", "main")
    return {"ok": True, "published": True, "reason": f"published {len(signals['signals'])} signals -> {repo}"}


def live_publish(claude_dir=None, *, repo=None, clone=None, allow_push: bool = False) -> dict:
    """Render + verify + selftest, then (allow_push ONLY) mirror into a gh-pages
    clone and push when changed. Default DRY: never clones or pushes — the live
    publish is the operator's gated step (exogenous-anchor bootstrap carve-out).
    A leak-bearing or non-idempotent witness can never be pushed."""
    repo = repo or DEFAULT_WITNESS_REPO
    signals = load_signals(claude_dir)
    ok, msg = verify_publishable(signals)
    if not ok:
        return {"ok": False, "published": False, "reason": msg}
    state = _denylist_state()
    if state in ("ciphertext", "invalid"):
        # Fail CLOSED: a locked keyring cannot KNOW the content is client-clean, so
        # the irreversible public push refuses rather than publishing unshielded
        # (the auto-loop's most dangerous failure mode). Mirrors decision_chain.
        return {"ok": False, "published": False,
                "reason": f"redaction denylist unusable ({state}) — refusing publish (run git-crypt unlock)"}
    # Re-scan the ACTUAL signals about to be pushed with the shield — not just trust
    # that refresh_converging shielded them. signals may come from a cached/stale
    # signals.json that bypassed refresh, so the shield must gate the real bytes at
    # the push boundary (the last line of defence before the irreversible publish).
    if not _signals_shield_clean(signals):
        return {"ok": False, "published": False,
                "reason": "a signal trips the client-identity shield — refusing publish"}
    if not selftest_idempotent(signals):
        return {"ok": False, "published": False, "reason": "not idempotent — refusing to publish"}
    if not allow_push:
        return {"ok": True, "published": False,
                "reason": f"DRY: {len(signals['signals'])} signals ready for {repo} — "
                          "re-run with allow_push=True (operator-gated) to deploy"}
    clone = Path(clone) if clone else (get_claude_dir() / "state" / "grip-rsi-witness")
    return _deploy_to_clone(signals, repo, clone)


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WITNESS &mdash; GRIP is recursively self-improving</title>
<style>
  :root {
    color-scheme: dark;
    --bg:#0d0d0c; --panel:#141311; --panel2:#191714; --line:#2a2824; --line2:#38352f;
    --ink:#ece9e3; --text:#d8d4cc; --muted:#8f8a80; --faint:#8a857b; --accent:#e8490f;
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html { scroll-behavior:smooth; }
  body { background:var(--bg); color:var(--text); font:16px/1.6 var(--sans);
         -webkit-font-smoothing:antialiased; overflow-x:hidden;
         -webkit-tap-highlight-color:rgba(232,73,15,0.25); }
  a { color:var(--accent); text-decoration:none; }
  .wrap { max-width:960px; margin:0 auto; padding:0 5vw; }
  .sec-label { font:600 0.68rem/1 var(--mono); letter-spacing:0.22em; text-transform:uppercase;
               color:var(--faint); }
  /* --- nav (scrolls with page, NOT sticky) --- */
  .nav { display:flex; align-items:center; justify-content:space-between; gap:1rem;
         padding:1.6rem 5vw; border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .brands { display:flex; align-items:center; gap:1.5rem; flex-wrap:wrap; }
  .brand { display:flex; align-items:center; gap:0.5rem; }
  .brand img { display:block; width:30px; height:30px; opacity:0.92; }
  .brand .bn { font:600 0.7rem/1 var(--mono); letter-spacing:0.12em; color:var(--muted); }
  .livetoggle { display:none; font:600 0.72rem/1 var(--mono); letter-spacing:0.14em;
                text-transform:uppercase; color:var(--ink); background:var(--panel);
                border:1px solid var(--line2); padding:0.7rem 1rem; cursor:pointer;
                min-height:44px; }
  .livetoggle:hover, .livetoggle:focus-visible { border-color:var(--accent); outline:none; }
  .livetoggle .tc { color:var(--accent); }
  /* --- activity (scrolls with page; mobile = toggleable) --- */
  .activity { max-width:960px; margin:2rem auto 0; padding:0 5vw; }
  .shead { display:flex; align-items:center; gap:0.7rem; }
  .shead .dot { width:0.5rem; height:0.5rem; background:var(--accent); flex:none;
                animation:pulse 1.9s ease-in-out infinite; }
  .shead .lbl { font:600 0.72rem/1 var(--mono); letter-spacing:0.2em; color:var(--accent); }
  .shead .cnt { font:0.72rem/1 var(--mono); letter-spacing:0.08em; color:var(--muted); }
  .tower { display:flex; flex-direction:column; gap:0.4rem; margin-top:1rem; }
  .wip-item { display:flex; align-items:flex-start; gap:0.9rem; padding:0.75rem 0.95rem;
              border:1px solid var(--line); background:var(--panel); overflow:hidden;
              max-height:5.5rem; animation:rise .5s ease;
              transition:opacity .6s ease,transform .6s ease,max-height .6s ease,
                         padding .6s ease,margin .6s ease,border-width .6s ease,background .2s ease; }
  .wip-item:hover { border-color:var(--line2); background:var(--panel2); }
  .wip-item .k { font:600 0.6rem/1 var(--mono); letter-spacing:0.12em; text-transform:uppercase;
                 color:var(--accent); flex:none; width:5rem; }
  .wip-item.shipped .k { color:var(--muted); }
  .wip-item .txt { font:0.82rem/1.35 var(--mono); color:var(--text); flex:1; overflow:hidden;
                   display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
  .wip-item .bar { width:2.6rem; height:2px; background:var(--line2); overflow:hidden; flex:none; }
  .wip-item .bar span { display:block; width:40%; height:100%; background:var(--accent);
                        animation:slide 1.4s ease-in-out infinite; }
  .wip-item.done { opacity:0; transform:translateX(1.5rem); max-height:0; padding:0 0.95rem;
                   margin:0; border-width:0; }
  /* --- hero --- */
  .hero { padding:9vh 0 5vh; }
  .hero .sec-label { margin-bottom:1.4rem; }
  h1 { font:700 clamp(3.4rem,13vw,7rem)/0.9 var(--sans); letter-spacing:-0.045em; color:var(--ink); }
  .live { display:inline-block; width:0.6rem; height:0.6rem; background:var(--accent);
          margin-left:0.5rem; vertical-align:baseline; animation:pulse 1.9s ease-in-out infinite; }
  .thesis { margin-top:1.6rem; max-width:40rem; font-size:1.18rem; color:var(--text); }
  .thesis b { color:var(--ink); }
  /* --- signal cards --- */
  .grid { display:grid; gap:1px; background:var(--line);
          grid-template-columns:repeat(auto-fit,minmax(min(100%,17rem),1fr));
          border:1px solid var(--line); }
  .card { background:var(--bg); padding:1.7rem 1.5rem; display:flex; flex-direction:column;
          min-width:0; }  /* min-width:0 lets the card shrink below a nowrap child's width */
  .card .label { font:600 0.66rem/1 var(--mono); letter-spacing:0.18em; text-transform:uppercase;
                 color:var(--faint); }
  .card .value { font:700 1.7rem/1.05 var(--sans); letter-spacing:-0.02em; color:var(--ink);
                 margin:0.7rem 0 0.5rem; }
  .card .caption { font-size:0.86rem; color:var(--muted); line-height:1.5; }
  .verify { margin-top:1.1rem; font:0.74rem/1.5 var(--mono); color:var(--faint); }
  .verify .vk { color:var(--faint); word-break:normal; } .verify .vk::after { content:" \\2192 "; }
  .verify a, .verify code { color:var(--accent); word-break:break-all; }
  .verify code { color:var(--text); }
  .links { display:flex; flex-direction:column; gap:0.35rem; margin-top:0.9rem; }
  .wip { font:0.78rem/1.4 var(--mono); color:var(--text); padding:0.45rem 0.6rem;
         border:1px solid var(--line2); background:var(--panel); white-space:nowrap;
         overflow:hidden; text-overflow:ellipsis; }
  .wip:hover { border-color:var(--accent); }
  /* --- moat diagram --- */
  .moat { padding:8vh 0 2vh; }
  .moat h2 { font:700 clamp(1.8rem,5vw,2.8rem)/1 var(--sans); letter-spacing:-0.03em;
             color:var(--ink); margin:1.1rem 0; }
  .moat .lede { max-width:42rem; color:var(--muted); font-size:1.02rem; margin-bottom:2.4rem; }
  .moat .lede b { color:var(--accent); }
  .moat-wrap { border:1px solid var(--line); background:var(--panel); padding:1.5rem 1rem; }
  /* Scale the whole viewBox to fit the container — never clip a box (print + narrow). */
  .moat-svg { width:100%; height:auto; display:block; max-width:880px; margin:0 auto; }
  .moat-edge { stroke:var(--line2); stroke-width:1; }
  .moat-box rect { fill:var(--bg); stroke:var(--line2); stroke-width:1; }
  .moat-box-hl rect { stroke:var(--accent); stroke-width:1.5; }
  .moat-title { fill:var(--ink); font:700 14px var(--mono); text-anchor:middle; letter-spacing:0.02em; }
  .moat-box-hl .moat-title { fill:var(--accent); }
  .moat-role { fill:var(--muted); font:11px var(--sans); text-anchor:middle; }
  .moat-layer { fill:var(--faint); font:600 10px var(--mono); letter-spacing:0.12em; }
  .moat-layer-hl { fill:var(--accent); }
  /* --- dojo --- */
  .dojo { padding:6vh 0 2vh; }
  .dojo-grid { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:var(--line);
               border:1px solid var(--line); }
  .dojo-tip, .dojo-cmds { background:var(--bg); padding:1.7rem 1.5rem; min-width:0; }
  .dojo-tip p { margin-top:0.9rem; color:var(--text); font-size:0.98rem; line-height:1.6; }
  .dojo-cmds ul { list-style:none; margin-top:1rem; display:flex; flex-direction:column; gap:0.6rem; }
  .dojo-cmds li { display:flex; flex-direction:column; gap:0.15rem; }
  .dojo-cmds code { font:600 0.82rem/1 var(--mono); color:var(--accent); }
  .dojo-cmds .cd { font-size:0.8rem; color:var(--muted); }
  .dojo-mesh { margin-top:1px; background:var(--panel); border:1px solid var(--line); border-top:0;
               padding:1rem 1.5rem; font:0.76rem/1.5 var(--mono); color:var(--muted); }
  /* --- footer --- */
  footer { border-top:1px solid var(--line); margin-top:8vh; padding:3rem 5vw; }
  footer .wrap { color:var(--faint); font:0.78rem/1.7 var(--mono); max-width:960px; }
  footer b { color:var(--muted); }
  @keyframes slide { 0%{transform:translateX(-120%);} 100%{transform:translateX(320%);} }
  @keyframes rise { from{opacity:0;transform:translateY(-0.4rem);} to{opacity:1;transform:none;} }
  @keyframes pulse { 0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(232,73,15,.5);}
                     50%{opacity:.45;box-shadow:0 0 0 0.5rem rgba(232,73,15,0);} }
  @media (max-width:640px) {
    h1 { font-size:clamp(2.8rem,17vw,4rem); }
    .nav { gap:0.75rem; }
    .livetoggle { display:inline-flex; align-items:center; gap:0.4rem; }
    .activity { display:none; }
    .activity.open { display:block; animation:rise .3s ease; }
    .grid, .dojo-grid { grid-template-columns:minmax(0,1fr); }  /* 0-min: no overflow */
    .brands { gap:1.1rem; min-width:0; flex-wrap:nowrap; overflow-x:auto; -webkit-overflow-scrolling:touch; }
    .brand .bn { display:none; }               /* marks-only on narrow screens */
    .wip-item { min-height:44px; }              /* touch target */
    .moat { padding-top:6vh; }
  }
  @media (prefers-reduced-motion:reduce) { * { animation:none !important; transition:none !important; } }
  @media print {
    :root { color-scheme:light; --bg:#fff; --panel:#fff; --panel2:#fff; --ink:#000; --text:#111;
            --muted:#333; --faint:#555; --line:#bbb; --line2:#999; }
    body { overflow:visible; }
    .nav, .activity, .livetoggle { display:none; }     /* live/interactive chrome is not printable */
    .grid, .dojo-grid { border-color:#bbb; }
    .card, .moat-wrap, .dojo-tip, .dojo-cmds, .dojo-mesh, footer { background:#fff; }
    .moat-svg { min-width:0; width:100%; }
    .moat-box rect { fill:#fff; stroke:#999; }
    a, .verify a, .dojo-cmds code { color:#000; text-decoration:underline; }
    * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  }
</style>
</head>
<body>
  <header class="nav">
<!--BRANDS-->
    <button class="livetoggle" aria-expanded="false" aria-controls="activity">Live <span class="tc">&#9679;</span></button>
  </header>
<!--STREAM-->
  <main class="wrap">
    <div class="hero">
      <div class="sec-label">Witness &middot; public RSI provenance</div>
      <h1>WITNESS<span class="live" title="live &mdash; recomputed from the real substrate"></span></h1>
      <p class="thesis">Don't trust that the AI is recursively self-improving. <b>Verify it.</b>
         Every signal is backed by deterministic provenance &mdash; open the proof yourself.</p>
    </div>
    <div class="grid" id="witness">
<!--CARDS-->
    </div>
<!--MOAT-->
<!--DOJO-->
  </main>
  <footer><div class="wrap">Rendered deterministically from GRIP's live substrate &mdash; a
    <b>Banach fixed point</b>, so the page changes only when the real signals do. A fabricated
    signal has no resolvable proof. This is the GRASP moat pointed at GRIP itself.</div></footer>
  <script>
  (function () {
    // Mobile: the activity panel is a toggleable menu.
    var btn = document.querySelector('.livetoggle');
    var panel = document.getElementById('activity');
    if (btn && panel) {
      btn.addEventListener('click', function () {
        var open = panel.classList.toggle('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }
    // Live "disappear when finished": poll the deterministic signals.json; any WIP
    // item no longer in the current stream has finished — fade + collapse it out.
    var tower = document.getElementById('tower');
    if (!tower) return;
    var cnt = document.querySelector('.shead .cnt');
    function live() {
      fetch('signals.json?_=' + Date.now()).then(function (r) { return r.json(); }).then(function (d) {
        var stream = (d.signals || []).filter(function (s) { return s.key === 'stream'; })[0] || {};
        var items = stream.items || [];
        var keep = {}, inflight = 0;
        items.forEach(function (i) { keep[i.url] = 1; if (i.kind === 'converging') inflight++; });
        // Keep the "N in flight" header honest as items finish (was stale before).
        if (cnt) cnt.textContent = inflight + ' in flight';
        Array.prototype.forEach.call(tower.querySelectorAll('.wip-item'), function (el) {
          if (!keep[el.getAttribute('data-key')] && !el.classList.contains('done')) {
            el.classList.add('done');
            setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 700);
          }
        });
      }).catch(function () {});
    }
    setInterval(live, 25000);
  })();
  </script>
</body>
</html>
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "dry-run"
    if cmd == "render":
        dest = Path(argv[1]) if len(argv) > 1 else Path("rsi-witness-site")
        render_site(load_signals(), dest)
        print(f"rendered witness site -> {dest}")
        return 0
    if cmd == "selftest":
        ok = selftest_idempotent()
        print(f"idempotent: {ok}")
        return 0 if ok else 1
    if cmd == "refresh":
        n = refresh_converging()
        print(f"refreshed converging snapshot: {n} open PR(s)")
        return 0
    if cmd == "deploy":
        result = live_publish(allow_push="--push" in argv)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    result = dry_run_publish()
    print(json.dumps(result, indent=2))
    return 0 if result["publishable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
