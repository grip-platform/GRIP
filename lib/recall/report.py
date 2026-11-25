"""Forensic Report — deterministic renderings of the RED chain (issue #4052).

One machine-readable report built purely from ``forensic.jsonl`` content —
NO wall-clock reads, so repeated runs over an unchanged chain are
byte-identical (f∘f=f; the ``as_of`` anchor is the LAST NODE's timestamp,
never "now"). Three renderings for three consumers:

  build_report()   -> dict      (the RSI loop / machine consumer)
  render_terminal  -> str       (operator card — machine-LOCAL detail)
  render_receipt   -> str       (external auditor doc — publish-safe ONLY)

Publish-safety tiers (council seal 970973d5, HMAC-linkage-aware):
  machine/terminal renderings are MACHINE-LOCAL — they may show verbatim
  claims and forensic_uuid prefixes. The receipt rendering is the
  publish-shaped one and carries NO claim text, NO forensic_uuids (a
  recurring identity HMAC is itself an identifier), NO identity masks —
  only per-NODE content-hash prefixes (unique per node, non-linking
  across an identity), publish-safe dims (model/provider/harness),
  severity/where-caught/month aggregates, and chain-integrity proof.

Goodhart stance (council seal 970973d5): the ledger scores
FAILURE-REDUCTION over time (remediation ratio, open-contradiction age),
never detection counts — and carries a suppression watch: a gate-fire
rate trending to zero while external contradictions persist is a
detection-suppression signal, not an improvement.

Egress is not implemented here BY DESIGN: ``publish_gate`` is the
fail-closed floor a publish path must clear; the final step of any
publish remains an operator gate (issue #4052 HELD list).
"""

from __future__ import annotations

import re

from lib.recall import forensic
from lib.recall.schema import canonical_json, content_hash

# executive verdict states
VERDICT_CHAIN_BROKEN = "CHAIN_BROKEN"
VERDICT_OPEN_CONTRADICTIONS = "OPEN_CONTRADICTIONS"
VERDICT_CLEAN = "CLEAN"

_SUPPRESSION_NOTE = (
    "Shadow metric: a gate-fire rate trending to zero while external "
    "contradictions persist is detection SUPPRESSION, not improvement — "
    "score failure-reduction (remediation ratio, open-age), never "
    "detection counts."
)
_GOODHART_NOTE = (
    "This report scores failure-reduction over time. Raw fire counts are "
    "telemetry, never a target: driving them down by catching less is the "
    "failure mode this note exists to name."
)

# publish-safe linkage floor: a full forensic_uuid (or any 64-hex token —
# signatures and full content hashes included) must never leave the machine
# in a publish rendering; 12-char node-hash prefixes are the allowed form.
_LONG_HEX = re.compile(r"\b[0-9a-f]{64}\b")


def _sealed(nodes: list[dict]) -> list[dict]:
    # emit() appends a node ONLY after the libel guard passes
    # (falsifier_survived=True), so every node on the append-only chain is
    # sealed. Default a MISSING field to sealed — a legacy / future-schema node
    # is counted, never silently dropped (a silent drop would under-count and
    # fake a misleadingly clean report). An explicit False still excludes.
    return [n for n in nodes
            if "_unparseable" not in n and n.get("falsifier_survived", True)]


def _month(ts: str) -> str:
    return ts[:7] if ts else "unknown"


def _bump(d: dict, key: str) -> None:
    d[key] = d.get(key, 0) + 1


def _aggregate(sealed: list[dict]) -> dict:
    by_severity: dict = {}
    by_where: dict = {}
    by_source: dict = {}
    by_month: dict = {}
    by_model: dict = {}
    by_provider: dict = {}
    by_harness: dict = {}
    remediated_by_month: dict = {}
    for n in sealed:
        _bump(by_severity, n.get("severity", "unknown"))
        _bump(by_where, n.get("where_caught", "unknown"))
        _bump(by_source, n.get("claim_source", "unknown"))
        month = _month(n.get("ts", ""))
        _bump(by_month, month)
        if n.get("remediation_edge"):
            _bump(remediated_by_month, month)
        dims = n.get("publish_dims") or {}
        _bump(by_model, dims.get("model", "unknown"))
        _bump(by_provider, dims.get("provider", "unknown"))
        _bump(by_harness, dims.get("harness", "unknown"))
    remediated = sum(remediated_by_month.values())
    total = len(sealed)
    return {
        "by_severity": by_severity,
        "by_where_caught": by_where,
        "by_claim_source": by_source,
        "by_month": by_month,
        "by_publish_dims": {"model": by_model, "provider": by_provider,
                            "harness": by_harness},
        "remediated_total": remediated,
        "remediation_ratio": round(remediated / total, 4) if total else None,
        "suppression_watch": {
            "fires_by_month": by_month,
            "remediated_by_month": remediated_by_month,
            "note": _SUPPRESSION_NOTE,
        },
    }


def _node_row(n: dict, *, machine_local: bool) -> dict:
    """Digest row. The publish-safe shape identifies the NODE (content-hash
    prefix — unique per node) but never the IDENTITY (no forensic_uuid,
    which recurs across one identity's nodes and is therefore linkable)."""
    row = {
        "node_hash12": content_hash(forensic._strip_sig(n))[:12],
        "ts": n.get("ts", ""),
        "severity": n.get("severity", "unknown"),
        "where_caught": n.get("where_caught", "unknown"),
        "remediated": bool(n.get("remediation_edge")),
        "publish_dims": n.get("publish_dims") or {},
    }
    if machine_local:
        row["uuid8"] = (n.get("forensic_uuid") or "")[:8]
        row["claim_verbatim"] = n.get("claim_verbatim", "")
        row["evidence_count"] = len(n.get("evidence") or ())
        row["remediation_edge"] = n.get("remediation_edge")
    return row


def _verdict(chain: dict, open_nodes: list) -> str:
    if not chain.get("ok"):
        return VERDICT_CHAIN_BROKEN
    return VERDICT_OPEN_CONTRADICTIONS if open_nodes else VERDICT_CLEAN


def build_report(store=None, key_path=None) -> dict:
    """Machine rendering — deterministic over chain content, wall-clock-free."""
    chain = forensic.verify_chain(store=store, key_path=key_path)
    sealed = _sealed(forensic._read_nodes(store))
    open_nodes = [n for n in sealed if not n.get("remediation_edge")]
    closed = [n for n in sealed if n.get("remediation_edge")]
    return {
        "report": "grip-recall forensic report",
        "as_of": {
            "last_node_ts": sealed[-1].get("ts", "") if sealed else "",
            "node_count": len(sealed),
        },
        "executive": {
            "verdict": _verdict(chain, open_nodes),
            "sealed_total": len(sealed),
            "open_contradictions": len(open_nodes),
            "remediated": len(closed),
        },
        "chain": chain,
        "open_contradictions": [
            _node_row(n, machine_local=True) for n in open_nodes],
        "remediation_ledger": [
            _node_row(n, machine_local=True) for n in closed],
        "aggregates": _aggregate(sealed),
        "goodhart_note": _GOODHART_NOTE,
    }


def _fmt_counts(d: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items())) or "none"


def render_terminal(report: dict) -> str:
    """Operator card — machine-local (claims + uuid prefixes allowed)."""
    ex = report["executive"]
    agg = report["aggregates"]
    lines = [
        "FORENSIC REPORT (RED chain) — machine-local",
        f"verdict: {ex['verdict']}  sealed={ex['sealed_total']} "
        f"open={ex['open_contradictions']} remediated={ex['remediated']}",
        f"chain: ok={report['chain']['ok']} nodes={report['chain']['count']} "
        f"breaks={len(report['chain']['breaks'])}",
        f"as-of: {report['as_of']['last_node_ts'] or '(empty chain)'}",
        f"severity: {_fmt_counts(agg['by_severity'])}",
        f"caught-at: {_fmt_counts(agg['by_where_caught'])}",
        f"remediation ratio: {agg['remediation_ratio']}",
    ]
    lines += [f"  OPEN {n['node_hash12']} [{n['severity']}] {n['ts']} "
              f"uuid={n['uuid8']} — {n['claim_verbatim'][:80]}"
              for n in report["open_contradictions"]]
    lines += [f"  CLOSED {n['node_hash12']} -> green {n['remediation_edge']}"
              for n in report["remediation_ledger"]]
    lines.append(report["goodhart_note"])
    return "\n".join(lines) + "\n"


def _receipt_head(report: dict) -> list[str]:
    ex = report["executive"]
    return [
        "# Forensic Report — RED-chain receipt (publish-safe rendering)",
        "",
        f"Verdict: **{ex['verdict']}** — {ex['sealed_total']} sealed "
        f"witness record(s); {ex['open_contradictions']} open "
        f"contradiction(s); {ex['remediated']} remediated (RED→GREEN).",
        f"As of last sealed record: "
        f"{report['as_of']['last_node_ts'] or '(empty chain)'}.",
        "",
        "## What a record is",
        "",
        "Each record witnesses ONE proven-incorrect assertion: a confident",
        "claim contradicted by evidence that re-verified at seal time. A",
        "record may seal only after its own falsification attempt survives",
        "(the libel guard) — an unproven accusation is dropped, never",
        "appended. Records are intent-free: they encode the contradiction,",
        "never motive.",
    ]


def _receipt_chain(chain: dict) -> list[str]:
    return [
        "",
        "## Chain integrity",
        "",
        f"- records: {chain['count']}; linkage+signature breaks: "
        f"{len(chain['breaks'])}",
        "- each record is HMAC-SHA256-signed and hash-linked to its",
        "  predecessor (append-only). Externally verifiable NOW: the",
        "  hash-linkage and this report's counts against a supplied chain",
        "  file. Machine-held: the HMAC key — signature verification runs",
        "  on the origin machine (`grip-recall verify`) or in an operator-",
        "  witnessed session; external temporal anchoring (OTS) joins the",
        "  green root's cadence and is stamped in `anchor_ref` when set.",
    ]


def _receipt_aggregates(agg: dict) -> list[str]:
    dims = agg["by_publish_dims"]
    watch = agg["suppression_watch"]
    return [
        "",
        "## Aggregates (publish-safe dimensions only)",
        "",
        f"- severity: {_fmt_counts(agg['by_severity'])}",
        f"- caught at: {_fmt_counts(agg['by_where_caught'])}",
        f"- by month: {_fmt_counts(agg['by_month'])}",
        f"- model: {_fmt_counts(dims['model'])}",
        f"- provider: {_fmt_counts(dims['provider'])}",
        f"- harness: {_fmt_counts(dims['harness'])}",
        f"- remediation ratio: {agg['remediation_ratio']}",
        f"- remediated by month: {_fmt_counts(watch['remediated_by_month'])}",
        "",
        f"Suppression watch: {watch['note']}",
    ]


def _receipt_digest_line(n: dict) -> str:
    state = "closed" if n["remediated"] else "OPEN"
    dim_txt = _fmt_counts(n["publish_dims"]) if n["publish_dims"] else "dims=unknown"
    return (f"- `{n['node_hash12']}` {n['ts']} [{n['severity']}] "
            f"{n['where_caught']} — {state}; {dim_txt}")


def render_receipt(report: dict) -> str:
    """External auditor rendering — publish-safe ONLY (no claims, no
    identity HMACs, no machine-local dims). Rebuilds its digest rows from
    the machine rows by DROPPING machine-local keys, so a machine-local
    field can never leak by rendering-path divergence."""
    rows = report["open_contradictions"] + report["remediation_ledger"]
    lines = _receipt_head(report)
    lines += _receipt_chain(report["chain"])
    lines += _receipt_aggregates(report["aggregates"])
    lines += ["", "## Record digests (node-scoped, identity-free)", ""]
    lines += [_receipt_digest_line(n) for n in rows]
    lines += ["", f"_{report['goodhart_note']}_", ""]
    return "\n".join(lines)


def _linkage_violations(text: str) -> list[str]:
    reasons = []
    if _LONG_HEX.search(text):
        reasons.append("64-hex token present (full uuid/sig/hash) — "
                       "identity-linkable or key-adjacent material")
    if "claim_verbatim" in text:
        reasons.append("machine-local field name 'claim_verbatim' present")
    return reasons


def publish_gate(text: str, denylist_path=None) -> dict:
    """Fail-closed publish floor. NOT an egress path — the caller still ends
    at an operator gate. Refuses (never silently redacts) on: linkage
    violations, denylist hits, or an unavailable/broken redaction shield
    (a missing shield is a refusal, not a pass — the ciphertext-denylist
    incident class from the public-publish-path memory)."""
    reasons = _linkage_violations(text)
    try:
        from lib.redaction_shield import load_denylist
        denylist = (load_denylist(denylist_path) if denylist_path
                    else load_denylist())
        result = denylist.redact(text)
        if result.changed:
            reasons.append(
                f"redaction shield matched {len(result.spans)} span(s): "
                f"{_fmt_counts(result.counts)}")
    except Exception as exc:  # noqa: BLE001 — fail CLOSED, with the cause
        reasons.append(f"redaction shield unavailable — publish refused "
                       f"({type(exc).__name__}: {exc})")
    return {"ok": not reasons, "reasons": reasons}


def report_json(report: dict) -> str:
    """Canonical byte form — the machine consumers' stable contract."""
    return canonical_json(report)
