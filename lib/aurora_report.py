"""Thin orchestrator + renderers for the /aurora canonical team work-update.

Public API
----------
report(hours_back, target, eli5) -> str
    Gather → Summarise → Render. Returns the rendered string for the target.

Render targets
--------------
terminal  — aurora_card delight + tldr header + plain summary (+ eli5 when available).
markdown  — changelog-style markdown block.
slack     — Block Kit blocks list (does NOT auto-post; caller decides).

CLI
---
  python3 lib/aurora_report.py [duration] [--target terminal|slack|markdown|all]
                               [--eli5] [--tldr] [--post CHANNEL]

Design constraints (gate compliance)
--------------------------------------
- No nested for-loops in one function (BIG-O gate) — row-builders are extracted.
- No hollow stubs: every path produces real output or fails-soft with a message.
- Each external seam (gather/summarise/slack_transport) fail-soft with plain message.
"""

from __future__ import annotations

import argparse
import sys

from lib.aurora_card import format_count
from lib.work_digest import WorkDigest, all_unmeasured, gather_digest, unmeasured_note
from lib.work_summary import WorkSummary, summarise
import lib.aurora_card as aurora_card


# ---------------------------------------------------------------------------
# Redaction shield — route outbound FREE TEXT through the canonical PII shield
# ---------------------------------------------------------------------------
#
# The terminal/markdown/slack renders embed PR titles + commit subjects + the
# composed tldr/plain/eli5 strings VERBATIM, and slack/markdown reach an
# audience (Slack channels, committed .md) beyond the operator. Those free-text
# fields can carry operator/client PII (client names in a PR title, a personal
# email in a commit subject). We route ONLY those free-text fields through
# Shield(..., profile='public') at the render boundary.
#
# Deliberately NOT redacted (structural, never PII): commit/test/hypothesis
# COUNTS, category names (conventional-commit types), and bare PR NUMBERS
# (`#196` is a reference id, not PII). Over-redaction is a failure — a sibling
# fix ate legitimate timestamps by over-matching; we shield free text only.
#
# FAIL-OPEN: the aurora audience is an internal team surface. If the shield is
# unusable (denylist missing / git-crypt-locked ciphertext / import error), we
# degrade to the un-redacted render rather than crash the outbound path.

def _shield():
    """Lazily build the public-profile Shield once; cache on the module.

    Returns a Shield or None. None (fail-open) whenever the shield cannot be
    constructed — a locked git-crypt checkout reads the denylist as ciphertext
    and load_denylist raises; an internal-surface render must still go out."""
    cached = getattr(_shield, "_cached", "unset")
    if cached != "unset":
        return cached
    shield = None
    try:
        from lib.redaction_shield import Shield, load_denylist
        shield = Shield(load_denylist(), profile="public")
    except Exception as exc:  # noqa: BLE001 — shield-unusable must never crash the render
        _warn(f"redaction shield unavailable — rendering un-redacted "
              f"(internal team surface): {exc}")
    _shield._cached = shield
    return shield


def _shield_text(text):
    """Redact one free-text string through the public shield; fail-open.

    Only free-text fields (PR titles, commit subjects, composed tldr/plain/eli5)
    are passed here. Structural fields (counts, numbers, category enums) are
    NOT routed through this. Returns ``text`` unchanged when the shield is
    unusable or ``text`` is falsy."""
    if not text:
        return text
    shield = _shield()
    if shield is None:
        return text
    try:
        return shield.redact(text).text
    except Exception as exc:  # noqa: BLE001 — a redact error must not break the render
        _warn(f"redaction failed on one field — leaving un-redacted: {exc}")
        return text


# ---------------------------------------------------------------------------
# Target: terminal
# ---------------------------------------------------------------------------

# Card metric name -> the WorkDigest field it is measured from. The card and
# the digest use different vocabularies; this is the one place they are joined.
_CARD_FIELDS = (
    ("prs", "merged_prs"),
    ("commits", "commit_count"),
    ("tests", "test_units"),
    ("hypotheses", "hypothesis_count"),
)


def _card_reasons(digest: WorkDigest) -> dict:
    """Why each card counter is unknown, keyed by the card's own metric names."""
    reasons = {}
    for metric, field_name in _CARD_FIELDS:
        why = digest.unmeasured_reason(field_name)
        if why is not None:
            reasons[metric] = why
    return reasons


def _render_terminal(digest: WorkDigest, summary: WorkSummary, eli5: bool) -> str:
    """Aurora delight + tldr header + plain summary (+ eli5 when available).

    The delight card receives only structural COUNTS (never shielded). The
    free-text tldr/plain/eli5 strings are routed through the public shield.

    ``digest.pr_count`` is used rather than ``len(digest.merged_prs)`` because
    an unread PR list is also an empty one, and the card must print '?' for it
    rather than a zero."""
    delight = aurora_card.render(
        {
            "prs": digest.pr_count,
            "commits": digest.commit_count,
            "tests": digest.test_units,
            "hypotheses": digest.hypothesis_count,
        },
        _card_reasons(digest),
    )
    parts = [delight, f"── {_shield_text(summary.tldr)} ──", "",
             _shield_text(summary.plain)]
    if eli5 and summary.eli5:
        parts.extend(["", "Plain English:", _shield_text(summary.eli5)])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Target: markdown — changelog row-builders (no nested loops)
# ---------------------------------------------------------------------------

def _md_pr_row(pr) -> str:
    """Format one PR as a markdown list item.

    TITLE is free text → shielded (may carry client name / PII). NUMBER is a
    structural reference id → kept VERBATIM (bare `#196` is not PII)."""
    return f"- {_shield_text(pr.title)} (#{pr.number})"


def _md_category_block(t: str, prs) -> list[str]:
    """Render lines for one changelog category block."""
    lines = [f"\n### {t}"]
    lines.extend(_md_pr_row(pr) for pr in prs)
    return lines


def _md_open_section(open_prs) -> list[str]:
    """Render the open/in-flight markdown section."""
    if not open_prs:
        return []
    lines = ["\n### open / in-flight"]
    lines.extend(_md_pr_row(pr) for pr in open_prs)
    return lines


def _group_merged_by_type(digest: WorkDigest) -> dict:
    """Group merged PRs by conventional-commit type — single pass."""
    groups: dict = {}
    for pr in digest.merged_prs:
        groups.setdefault(pr.type, []).append(pr)
    return groups


def _counts_footer(digest: WorkDigest) -> str:
    """The commits/tests/hypotheses footer, honest about anything unmeasured."""

    def _one(field_name: str, value) -> str:
        return format_count(value, digest.unmeasured_reason(field_name) or "")

    return (
        f"commits: {_one('commit_count', digest.commit_count)} "
        f"· tests: {_one('test_units', digest.test_units)} "
        f"· hypotheses: {_one('hypothesis_count', digest.hypothesis_count)}"
    )


def _render_markdown(digest: WorkDigest, summary: WorkSummary) -> str:
    """Changelog-style markdown block."""
    lines = [f"## Work — last {digest.hours_back}h", f"\n> {_shield_text(summary.tldr)}"]
    groups = _group_merged_by_type(digest)
    for t, count in digest.categories:
        prs = groups.get(t, [])
        if prs:
            lines.extend(_md_category_block(t, prs))
    lines.extend(_md_open_section(digest.open_prs))
    lines.extend(["", "---", _counts_footer(digest)])
    note = unmeasured_note(digest)
    if note:
        lines.append(f"\n**Incomplete report** — {note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Target: slack — Block Kit (no auto-post; caller decides)
# ---------------------------------------------------------------------------

def _slack_category_text(t: str, prs) -> str:
    """Build mrkdwn text for one PR category.

    TITLE is free text → shielded. Category label ``t`` and bare ``#number``
    are structural → kept verbatim."""
    rows = [f"• {_shield_text(pr.title)} (#{pr.number})" for pr in prs]
    return f"*{t}*\n" + "\n".join(rows)


def _render_slack(digest: WorkDigest, summary: WorkSummary) -> list[dict]:
    """Return Block Kit blocks. Reuses comms_update helpers; does NOT post."""
    try:
        from lib.comms_update import _header_block, _section_block, _divider, _context_block
    except ImportError:
        # Fallback: build minimal blocks without comms_update
        return _fallback_slack_blocks(digest, summary)

    blocks: list[dict] = [
        _header_block(f":rocket: Work update — last {digest.hours_back}h"),
        _section_block(_shield_text(summary.tldr)),
    ]
    groups = _group_merged_by_type(digest)
    for t, count in digest.categories:
        prs = groups.get(t, [])
        if prs:
            blocks.append(_divider())
            blocks.append(_section_block(_slack_category_text(t, prs)))
    if digest.open_prs:
        blocks.append(_divider())
        open_text = _slack_category_text("open / in-flight", digest.open_prs)
        blocks.append(_section_block(open_text))
    blocks.append(_divider())
    blocks.append(_context_block(_counts_footer(digest)))
    note = unmeasured_note(digest)
    if note:
        # The team reads this post as a statement of fact, so a report built on
        # sources that could not be read says so IN the post, not in a log.
        blocks.append(_section_block(f":warning: *Incomplete report* — {note}"))
    return blocks


def _fallback_slack_blocks(digest: WorkDigest, summary: WorkSummary) -> list[dict]:
    """Minimal Block Kit blocks when comms_update is unavailable."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"Work update — last {digest.hours_back}h"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _shield_text(summary.plain)}},
    ]
    note = unmeasured_note(digest)
    if note:
        blocks.append({"type": "section", "text": {
            "type": "mrkdwn", "text": f":warning: *Incomplete report* — {note}"}})
    return blocks


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def report(
    hours_back: int = 24,
    target: str = "terminal",
    eli5: bool = False,
) -> str:
    """Gather → Summarise → Render. Returns the rendered string.

    Args:
        hours_back: window size in hours (default 24).
        target:     one of terminal|slack|markdown|all (default terminal).
        eli5:       include HAL-enriched narrative when available (fail-soft).

    Returns:
        Rendered string. For 'slack', the string is JSON-serialised blocks.
        For 'all', all three renderings joined by a separator.
    """
    import json as _json

    digest = _safe_gather(hours_back)
    summary = _safe_summarise(digest, eli5)

    if target == "terminal":
        return _render_terminal(digest, summary, eli5)
    if target == "markdown":
        return _render_markdown(digest, summary)
    if target == "slack":
        return _json.dumps(_render_slack(digest, summary), indent=2)
    if target == "all":
        sep = "\n\n" + "─" * 56 + "\n\n"
        import json as _json2
        return sep.join([
            _render_terminal(digest, summary, eli5),
            _render_markdown(digest, summary),
            _json2.dumps(_render_slack(digest, summary), indent=2),
        ])
    return _render_terminal(digest, summary, eli5)


def _safe_gather(hours_back: int) -> WorkDigest:
    """gather_digest fail-soft: returns an ALL-UNMEASURED WorkDigest on error.

    A default ``WorkDigest()`` here would be a night of zeros that nobody ever
    counted — the same falsehood as a single bad counter, applied to all of
    them at once. Nothing was measured, so nothing is reported as measured.
    """
    try:
        return gather_digest(hours_back)
    except Exception as exc:  # noqa: BLE001
        _warn(f"gather_digest failed: {exc}")
        return all_unmeasured(hours_back, f"the gather step failed: {str(exc)[:120]}")


def _safe_summarise(digest: WorkDigest, eli5: bool) -> WorkSummary:
    """summarise fail-soft: returns a minimal WorkSummary on any error."""
    try:
        return summarise(digest, eli5=eli5)
    except Exception as exc:  # noqa: BLE001
        _warn(f"summarise failed: {exc}")
        from lib.work_summary import WorkSummary
        return WorkSummary(
            tldr=f"Work digest for last {digest.hours_back}h (summary unavailable)",
            plain="Summary unavailable.",
            eli5=None,
            verify=(),
        )


def _warn(msg: str) -> None:
    print(f"[aurora] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------

def _parse_duration(raw: str) -> int:
    """Parse '24h', '12h', or bare integer string to hours. Default 24."""
    s = raw.strip().lower()
    if s.endswith("h"):
        s = s[:-1]
    try:
        v = int(s)
        return v if v > 0 else 24
    except ValueError:
        return 24


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser (extracted for KISS compliance)."""
    p = argparse.ArgumentParser(
        description="Team work update — honest digest + delight.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("duration", nargs="?", default="24",
                   help="window: '24h', '12h', or bare hours integer (default: 24)")
    p.add_argument("--target", choices=["terminal", "slack", "markdown", "all"],
                   default="terminal", help="render target (default: terminal)")
    p.add_argument("--eli5", action="store_true",
                   help="include plain-English HAL narrative (fail-soft)")
    p.add_argument("--tldr", action="store_true",
                   help="print only the one-line tldr headline")
    p.add_argument("--post", metavar="CHANNEL", default=None,
                   help=(
                       "OPT-IN: post the Slack render to CHANNEL via slack_transport "
                       "(default workspace: codetonight). Only fires when --post is given."
                   ))
    return p


def main() -> int:
    args = _build_parser().parse_args()
    hours_back = _parse_duration(args.duration)

    if args.tldr:
        digest = _safe_gather(hours_back)
        summary = _safe_summarise(digest, eli5=False)
        print(summary.tldr)
        return 0

    rendered = report(hours_back=hours_back, target=args.target, eli5=args.eli5)
    print(rendered)
    if args.post:
        _do_post(hours_back=hours_back, channel=args.post)
    return 0


def _record_post_provenance(
    digest: WorkDigest,
    summary: WorkSummary,
    *,
    path=None,
    idr_path=None,
    head_pointer=None,
) -> dict:
    """Attach signed, replayable provenance to a POSTED aurora work update.

    ADDITIVE + FAIL-OPEN: only runs AFTER the Slack post has already succeeded,
    and ``record_claim_post`` never raises — so a provenance failure can never
    affect the (already-sent) claim. Builds the claim lines from the digest's
    VERBATIM evidence tokens (each merged-PR ``title (#number)`` line + the commit
    tally) — the same self-canonical record shape the update_team weave uses:
    "GRIP told the channel X, backed by the exact X it posted". ``path`` /
    ``idr_path`` / ``head_pointer`` are injectable for hermetic tests + the audit.
    """
    try:
        from lib.channel_claim_provenance import record_claim_post
    except Exception as exc:  # noqa: BLE001 — import failure must not break the post
        _warn(f"provenance unavailable (post unaffected): {exc}")
        return {"ok": False, "reason": "import-failed"}
    claim_lines = [f"{pr.title} (#{pr.number})" for pr in digest.merged_prs]
    claim_lines.append(f"commits: {digest.commit_count}")
    return record_claim_post(summary.tldr, claim_lines,
                             path=path, idr_path=idr_path, head_pointer=head_pointer)


def _do_post(hours_back: int, channel: str) -> None:
    """Post the Slack render to CHANNEL. Fails-soft with a clear message."""
    import json as _json
    try:
        from lib.slack_transport import post as slack_post
    except ImportError:
        _warn("slack_transport unavailable — cannot post. Render shown above.")
        return

    digest = _safe_gather(hours_back)
    summary = _safe_summarise(digest, eli5=False)
    blocks = _render_slack(digest, summary)
    try:
        result = slack_post(
            workspace="codetonight",
            channel_id=channel,
            text=summary.tldr,
            blocks=blocks,
        )
        if result.get("ok"):
            print(f"[aurora] posted to {channel} (ts={result.get('ts')})")
            rec = _record_post_provenance(digest, summary)  # signed provenance, fail-open
            if rec.get("ok"):
                print(f"[aurora] provenance recorded "
                      f"(grounding={int(rec.get('grounding_rate', 0) * 100)}%)")
        else:
            _warn(f"Slack post failed: {result.get('error')}")
    except Exception as exc:  # noqa: BLE001
        _warn(f"Slack post error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
