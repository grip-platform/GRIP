"""Renders the AGORA council-picker scorecard flip-cards page (GRIP#9404, pick 1).

One self-contained HTML file: inline CSS and JS, no network, no external fonts.
A CSS 3D flip shows a model's scorecard on the back of its card; under
``prefers-reduced-motion: reduce`` both faces are shown by a plain toggle
instead of a rotation, and either way both faces are always present in the
DOM. A click on "Lock into this seat" only reveals a pre-written, copyable
proposal (file, current model, proposed model) -- it never edits code. Moving
a seat stays the ``lib/hal_council_dispatch.py`` change V>> merges (GRIP#9404).

Pure: :func:`render_page` takes data, returns a string. The one write belongs
to the caller (``bin/grip-council-picker``).
"""
from __future__ import annotations

import html as _html
from typing import Optional

from lib.council_picker_scorecard import Candidate, Scorecard, build_proposal_text

_SEAT_ORDER = ("judge_fallback", "technical", "quality")


def _metric_lines(sc: Scorecard) -> list[tuple[str, str]]:
    """Every metric ``sc`` actually carries, as (label, value) pairs -- a field
    a seat's trial shape does not produce is simply absent, never invented."""
    lines = [("Sittings", str(sc.sittings))]
    if sc.answered is not None:
        lines.append(("Answered", f"{sc.answered} of {sc.sittings}"))
    if sc.valid is not None:
        lines.append(("Valid verdicts", f"{sc.valid} of {sc.sittings}"))
    if sc.agrees_with_reference is not None and sc.valid:
        lines.append(("Agrees with reference", f"{sc.agrees_with_reference} of {sc.valid} valid"))
    if sc.concerns is not None:
        lines.append(("Concerns raised", str(sc.concerns)))
    if sc.verified is not None:
        lines.append(("Quotes verified", f"{sc.verified}{_verified_detail(sc)}"))
    if sc.median_seconds is not None:
        lines.append(("Median time", f"{sc.median_seconds}s"))
    if sc.slowest_seconds is not None:
        lines.append(("Slowest", f"{sc.slowest_seconds}s"))
    if sc.price_in is not None and sc.price_out is not None:
        lines.append(("Price / 1M tokens", f"${sc.price_in:.2f} in / ${sc.price_out:.2f} out"))
    return lines


def _verified_detail(sc: Scorecard) -> str:
    """The parenthetical after a verified-quotes count, e.g. " (1 unquotable)"."""
    parts = []
    if sc.unquotable:
        parts.append(f"{sc.unquotable} unquotable")
    if sc.not_found:
        parts.append(f"{sc.not_found} not found")
    return f" ({', '.join(parts)})" if parts else ""


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _metrics_html(scorecard: Optional[Scorecard]) -> str:
    if scorecard is None:
        return '<p class="no-trial">no trial yet</p>'
    rows = "".join(
        f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>" for label, value in _metric_lines(scorecard))
    return (
        f'<dl class="metrics">{rows}</dl>'
        f'<p class="source">Source: {_esc(scorecard.source_file)} '
        f'({scorecard.sittings} sittings)</p>'
    )


def _seat_pairs(candidates: list[Candidate]) -> dict[str, dict[str, Candidate]]:
    """Candidates grouped by seat then role, in the order seats should render."""
    by_seat: dict[str, dict[str, Candidate]] = {}
    for cand in candidates:
        by_seat.setdefault(cand.seat, {})[cand.role] = cand
    ordered_keys = [k for k in _SEAT_ORDER if k in by_seat]
    ordered_keys += [k for k in by_seat if k not in ordered_keys]
    return {k: by_seat[k] for k in ordered_keys}


def _seated_of(seat_cards: dict[str, Candidate]) -> Candidate:
    """The candidate at this seat that is currently seated (falls back to the
    incumbent role if, oddly, neither card matches the live roster)."""
    for cand in seat_cards.values():
        if cand.seated:
            return cand
    return seat_cards.get("incumbent") or next(iter(seat_cards.values()))


def _card_html(cand: Candidate, seated: Candidate, card_id: str) -> str:
    badge = '<span class="badge">Seated</span>' if cand.seated else ""
    proposal = build_proposal_text(cand, seated)
    return f"""
<article class="card" data-seat="{_esc(cand.seat)}" data-role="{_esc(cand.role)}">
  <div class="card-inner">
    <div class="card-face card-front">
      <h3>{_esc(cand.display)}</h3>
      <p class="vendor">{_esc(cand.vendor)}</p>
      {badge}
      <button type="button" class="flip-btn" data-flip="{card_id}">Flip for scorecard</button>
    </div>
    <div class="card-face card-back">
      <h3>{_esc(cand.display)}</h3>
      {_metrics_html(cand.scorecard)}
      <div class="card-back-actions">
        <button type="button" class="flip-btn" data-flip="{card_id}">Back to front</button>
        <button type="button" class="lock-btn" data-lock="{card_id}">Lock into this seat</button>
      </div>
      <div class="proposal-panel" id="proposal-{card_id}" hidden>
        <textarea class="proposal-text" readonly rows="6">{_esc(proposal)}</textarea>
        <button type="button" class="copy-btn" data-copy="{card_id}">Copy proposal</button>
        <p class="copy-status" id="copy-status-{card_id}" aria-live="polite"></p>
      </div>
    </div>
  </div>
</article>"""


def _seat_section_html(seat_key: str, seat_cards: dict[str, Candidate]) -> str:
    seated = _seated_of(seat_cards)
    label = next(iter(seat_cards.values())).seat_label
    cards = "".join(
        _card_html(cand, seated, f"{seat_key}-{role}")
        for role, cand in seat_cards.items())
    return f"""
<section class="seat" aria-labelledby="seat-{_esc(seat_key)}">
  <h2 id="seat-{_esc(seat_key)}">{_esc(label)}</h2>
  <div class="grid">{cards}</div>
</section>"""


_CSS = """
:root {
  --bg: #f4f4f2; --fg: #16181d; --card-bg: #ffffff; --card-border: #d7d9dc;
  --accent: #b5502f; --muted: #5a5f66; --badge-bg: #16181d; --badge-fg: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14161a; --fg: #eceef1; --card-bg: #1c1f24; --card-border: #33373d;
          --accent: #e08a5f; --muted: #a8adb5; --badge-bg: #e08a5f; --badge-fg: #14161a; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
       font-family: -apple-system, system-ui, "Segoe UI", sans-serif; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
.subtitle { color: var(--muted); margin: 0 0 24px; max-width: 60ch; }
.seat { margin-bottom: 32px; }
.seat h2 { font-size: 1.1rem; border-bottom: 1px solid var(--card-border); padding-bottom: 6px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
        gap: 16px; margin-top: 12px; }
.card { perspective: 1200px; min-height: 260px; }
.card-inner { position: relative; width: 100%; height: 100%; min-height: 260px;
              transition: transform 0.5s; transform-style: preserve-3d; }
.card.flipped .card-inner { transform: rotateY(180deg); }
.card-face { position: absolute; inset: 0; backface-visibility: hidden;
             border: 1px solid var(--card-border); border-radius: 10px;
             background: var(--card-bg); padding: 16px; overflow: auto; }
.card-back { transform: rotateY(180deg); }
.card-front h3, .card-back h3 { margin: 0 0 4px; }
.vendor { color: var(--muted); margin: 0 0 12px; }
.badge { display: inline-block; background: var(--badge-bg); color: var(--badge-fg);
         font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; margin-bottom: 12px; }
button { font: inherit; cursor: pointer; border-radius: 6px; border: 1px solid var(--card-border);
         background: var(--card-bg); color: var(--fg); padding: 8px 10px; margin: 4px 4px 0 0; }
button:hover, button:focus-visible { border-color: var(--accent); outline: 2px solid var(--accent); }
.metrics { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 0 0 8px;
           font-size: 0.9rem; }
.metrics dt { color: var(--muted); }
.metrics dd { margin: 0; text-align: right; }
.no-trial { color: var(--muted); font-style: italic; }
.source { color: var(--muted); font-size: 0.75rem; }
.proposal-panel { margin-top: 10px; }
.proposal-text { width: 100%; font-family: ui-monospace, monospace; font-size: 0.8rem;
                  background: var(--bg); color: var(--fg); border: 1px solid var(--card-border);
                  border-radius: 6px; padding: 8px; }
.copy-status { font-size: 0.8rem; color: var(--accent); min-height: 1.2em; margin: 4px 0 0; }
footer { color: var(--muted); font-size: 0.8rem; margin-top: 24px; max-width: 70ch; }
@media (prefers-reduced-motion: reduce) {
  .card-inner { transition: none; transform: none !important; min-height: 0; position: static; }
  .card-face { position: static; backface-visibility: visible; transform: none; display: none; }
  .card-front { display: block; }
  .card.flipped .card-front { display: none; }
  .card.flipped .card-back { display: block; }
}
@media (max-width: 480px) {
  .grid { grid-template-columns: 1fr; }
}
"""

_JS = """
(function () {
  'use strict';
  document.querySelectorAll('[data-flip]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var card = btn.closest('.card');
      if (card) { card.classList.toggle('flipped'); }
    });
  });
  document.querySelectorAll('[data-lock]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-lock');
      var panel = document.getElementById('proposal-' + id);
      if (panel) { panel.hidden = false; panel.querySelector('textarea').select(); }
    });
  });
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-copy');
      var panel = document.getElementById('proposal-' + id);
      var status = document.getElementById('copy-status-' + id);
      var textarea = panel ? panel.querySelector('textarea') : null;
      if (!textarea) { return; }
      textarea.select();
      var done = function (ok) {
        if (status) { status.textContent = ok ? 'Copied.' : 'Select the text above to copy.'; }
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(textarea.value).then(function () { done(true); },
                                                            function () { done(false); });
      } else {
        done(false);
      }
    });
  });
})();
"""


def render_page(candidates: list[Candidate], generated_at: str = "") -> str:
    """The full HTML page for every candidate in ``candidates``.

    Refuses an empty list: a page with no cards is a builder bug, not a
    legitimate empty state, and must not be shipped as if it were one.
    """
    if not candidates:
        raise ValueError("render_page requires at least one candidate -- an empty page "
                          "would hide a broken builder, not show an empty state")
    sections = "".join(
        _seat_section_html(seat_key, seat_cards)
        for seat_key, seat_cards in _seat_pairs(candidates).items())
    stamp = f'<p class="subtitle">Generated {_esc(generated_at)}.' if generated_at else '<p class="subtitle">'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AGORA Council Picker</title>
<style>{_CSS}</style>
</head>
<body>
<h1>AGORA Council Picker -- Scorecard</h1>
{stamp} Every number on a card comes from a shadow-trial record (named on the card's
back); a model without one says so plainly, never a guessed number. A click on
"Lock into this seat" only writes a copyable proposal below -- it never edits code. Moving
a seat stays a <code>lib/hal_council_dispatch.py</code> change for a human to merge.</p>
{sections}
<footer>GRIP#9404 pick 1 -- Scorecard flip-cards. Source data:
docs/measurements/2026-11-24-agora-seat-trials.md.</footer>
<script>{_JS}</script>
</body>
</html>"""


__all__ = ["render_page"]
