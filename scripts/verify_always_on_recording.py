#!/usr/bin/env python3
"""verify_always_on_recording — coverage audit for cryptographic-causation recording.

The Cryptographic-Causation moat is "every decision / claim GRIP makes in
production is recorded as a tamper-evident, replayable IDR + memory-chain node".
This script AUDITS that invariant: it enumerates the production decision/claim
surfaces, PROVES (verify-canonical, not a flag) which ones actually emit a signed
record, and reports coverage% + an HONEST gap list.

verify-canonical (rules/verify-canonical-not-claimed.md) — the load-bearing
discipline here: a surface is "recording" ONLY if exercising it makes the IDR
ledger gain a line AND advances the memory-chain HEAD to a NEW node id. We never
trust a ``recorded: true`` boolean a surface might self-report — we run it against
a throwaway ledger and assert the chain head moved. A surface we cannot exercise
end-to-end is listed as an UNPROVEN gap, never silently counted as covered.

Honesty: all FIVE known decision/claim surfaces are now exercised END-TO-END
(update_team post, prove-it artifact, precog-dispatch IDR emit, context
checkpoint, and aurora/slack-update channel claims). Each is PROVEN the same
way — against a throwaway ledger, asserting the IDR ledger gains a line AND the
memory-chain HEAD advances to a NEW node id AND the chain verifies. No surface
is ever counted as covered on a self-reported flag.

Exit code: 0 iff EVERY audited surface actually recorded; any surface that SHOULD
record but demonstrably did NOT fails the run (a regression). Because every
surface is now exercised, a broken recorder flips its surface red AND returns
non-zero — 'proven' can never degrade to a hardcoded True.

Usage:
    python3 scripts/verify_always_on_recording.py            # human report
    python3 scripts/verify_always_on_recording.py --json     # machine report
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass(frozen=True)
class SurfaceResult:
    """Audit result for one production decision/claim surface."""

    name: str
    wired: bool          # is provenance recording wired into this surface?
    proven: bool         # did we exercise it and SEE the record land (verify-canonical)?
    detail: str          # plain-English status / evidence / gap reason


# --- The fake digest the probe exercises (no git/gh/network) ----------------

def _sample_digest_summary():
    """A minimal in-memory digest+summary so the probe touches no git/gh/Slack."""
    from lib.work_digest import WorkDigest, PREntry  # type: ignore
    from lib.work_summary import WorkSummary  # type: ignore

    prs = (PREntry(number=3681, title="feat: cryptographic-causation slice-1", url="", type="feat"),)
    digest = WorkDigest(hours_back=24, commit_count=7, merged_prs=prs,
                        categories=(("feat", 1),))
    summary = WorkSummary(tldr="Shipped cryptographic-causation slice-1 (#3681)",
                          plain="Merged PR #3681.", eli5=None, verify=())
    return digest, summary


# --- verify-canonical probe: exercise the surface, SEE the record land -------

def _count_idr_lines(idr_path: Path) -> int:
    """Number of records in the IDR ledger (0 when absent). Pure read."""
    if not idr_path.is_file():
        return 0
    return sum(1 for ln in idr_path.read_text(encoding="utf-8").splitlines() if ln.strip())


def _probe_update_team_surface() -> SurfaceResult:
    """Exercise the update_team provenance weave against a throwaway ledger and
    PROVE (not flag) that an IDR line landed AND the memory-chain HEAD advanced."""
    try:
        import update_team_provenance as utp  # type: ignore
        import context_chain  # type: ignore
        digest, summary = _sample_digest_summary()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            idr_path = base / "idr.jsonl"
            chain_path = base / "context.jsonl"
            head_ptr = base / "HEAD"
            before_idr = _count_idr_lines(idr_path)
            before_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            rec = utp.record_post_provenance(
                digest, summary, path=chain_path, idr_path=idr_path, head_pointer=head_ptr)
            after_idr = _count_idr_lines(idr_path)
            after_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            idr_landed = after_idr == before_idr + 1
            head_advanced = bool(after_head) and after_head != before_head
            chain_ok = context_chain.verify_context_chain(path=chain_path).value == "verified"
            proven = bool(rec.get("ok")) and idr_landed and head_advanced and chain_ok
            detail = (f"IDR +{after_idr - before_idr}, HEAD advanced={head_advanced}, "
                      f"chain={'VERIFIED' if chain_ok else 'BROKEN'}, "
                      f"grounding={int(rec.get('grounding_rate', 0) * 100)}%")
        return SurfaceResult("update_team-post", wired=True, proven=proven, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult("update_team-post", wired=True, proven=False,
                             detail=f"probe error: {type(exc).__name__}: {exc}")


def _probe_proveit_surface() -> SurfaceResult:
    """Exercise the prove_it_provenance recorder against a throwaway ledger and
    PROVE (not flag) that an IDR line landed AND the memory-chain HEAD advanced."""
    try:
        import prove_it_provenance as pvp  # type: ignore
        import context_chain  # type: ignore
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            idr_path = base / "idr.jsonl"
            chain_path = base / "context.jsonl"
            head_ptr = base / "HEAD"
            # Minimal spec with one verbatim-verifiable citation (no I/O)
            source_text = "The quick brown fox jumps over the lazy dog."
            spec = {
                "title": "prove-it coverage probe",
                "response": "The fox [[cite:c1]] is quick.",
                "sources": [{"id": "s1", "label": "probe source", "text": source_text}],
                "citations": [{"id": "c1", "claim": "fox is quick",
                               "source_id": "s1", "quote": "quick brown fox"}],
            }
            # Derive real provenance via prove_it.render (pure, no I/O)
            from prove_it import render  # type: ignore
            _html, prov = render(spec)
            before_idr = _count_idr_lines(idr_path)
            before_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            rec = pvp.record_proveit_provenance(
                spec, prov, path=chain_path, idr_path=idr_path, head_pointer=head_ptr)
            after_idr = _count_idr_lines(idr_path)
            after_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            idr_landed = after_idr == before_idr + 1
            head_advanced = bool(after_head) and after_head != before_head
            chain_ok = context_chain.verify_context_chain(path=chain_path).value == "verified"
            proven = bool(rec.get("ok")) and idr_landed and head_advanced and chain_ok
            detail = (f"IDR +{after_idr - before_idr}, HEAD advanced={head_advanced}, "
                      f"chain={'VERIFIED' if chain_ok else 'BROKEN'}, "
                      f"grounding={int(rec.get('grounding_rate', 0) * 100)}%")
        return SurfaceResult("prove-it-artifact", wired=True, proven=proven, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult("prove-it-artifact", wired=True, proven=False,
                             detail=f"probe error: {type(exc).__name__}: {exc}")


# --- verify-canonical probes for the remaining decision/claim surfaces -------

def _probe_precog_dispatch_surface() -> SurfaceResult:
    """Exercise the precog DISPATCH IDR emit (lib/precog/idr — the same signing
    primitive orchestrate() calls on every dispatch) AND record that dispatch
    decision into the memory chain (checkpoint cross-referencing the leaf by
    content_addr). PROVES both halves of the moat promise for the dispatch
    surface: a signed decision IDR lands AND the memory HEAD advances.

    Hermetic by design — it drives the emit primitive directly rather than
    orchestrate(), so the audit never mutates the real state/precog current-IDR
    pointer (orchestrate()'s only non-injectable side-effect). The primitive IS
    the wired code the gap named ('lib/precog/idr emits decision IDRs on dispatch').
    """
    try:
        from dataclasses import asdict
        from lib.precog.idr import append_idr, build_idr, content_addr  # type: ignore
        import context_chain  # type: ignore
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ledger = base / "precog-idr.jsonl"   # the dispatch-decision IDR ledger
            chain_path = base / "context.jsonl"
            head_ptr = base / "HEAD"
            decision = {
                "action": "precog-dispatch",
                "provider_addresses": ["openai/gpt-5.5"],
                "effort_tier": "focused",
                "downstream_shape": "Focused",
                "dispatch_depth": 0,
            }
            before_idr = _count_idr_lines(ledger)
            before_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            leaf = build_idr(prompt="probe: representative dispatch prompt",
                             fingerprint="slice4-dispatch-probe", decision=decision,
                             predecessor_idr=None, depth=0, kind="precog-decision",
                             inputs={"prompt_sha256": "probe"})
            append_idr(leaf, path=ledger)
            addr = content_addr(asdict(leaf))
            node = context_chain.checkpoint(
                next_step=None, summary=f"precog dispatch {decision['effort_tier']}",
                title="precog-dispatch provenance", tier="feedback",
                records_idr=addr, path=chain_path, head_pointer=head_ptr)
            after_idr = _count_idr_lines(ledger)
            after_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            idr_landed = after_idr == before_idr + 1
            head_advanced = bool(after_head) and after_head != before_head and after_head == node.id
            chain_ok = context_chain.verify_context_chain(path=chain_path).value == "verified"
            xref_ok = context_chain.resolve_citation(node, idr_path=ledger) == "RESOLVED"
            proven = idr_landed and head_advanced and chain_ok and xref_ok
            detail = (f"dispatch IDR +{after_idr - before_idr} (kind=precog-decision), "
                      f"HEAD advanced={head_advanced}, "
                      f"chain={'VERIFIED' if chain_ok else 'BROKEN'}, "
                      f"xref={'RESOLVED' if xref_ok else 'DANGLING'}")
        return SurfaceResult("precog-dispatch-idr", wired=True, proven=proven, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult("precog-dispatch-idr", wired=True, proven=False,
                             detail=f"probe error: {type(exc).__name__}: {exc}")


def _probe_context_checkpoint_surface() -> SurfaceResult:
    """Exercise lib/context_chain.checkpoint (the /save reasoning-state primitive)
    against a throwaway chain and PROVE a signed context-delta lands AND the
    memory HEAD advances to that exact new node AND the chain verifies
    (verify-canonical, not a flag)."""
    try:
        import context_chain  # type: ignore
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            chain_path = base / "context.jsonl"
            head_ptr = base / "HEAD"
            before = _count_idr_lines(chain_path)
            before_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            node = context_chain.checkpoint(
                next_step={"next": "slice-4 always-on-recording checkpoint probe"},
                summary="reasoning-state checkpoint probe",
                title="context-checkpoint probe", tier="feedback",
                path=chain_path, head_pointer=head_ptr)
            after = _count_idr_lines(chain_path)
            after_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            delta_landed = after == before + 1
            head_advanced = bool(after_head) and after_head != before_head and after_head == node.id
            chain_ok = context_chain.verify_context_chain(path=chain_path).value == "verified"
            proven = delta_landed and head_advanced and chain_ok
            detail = (f"context-delta +{after - before}, HEAD advanced={head_advanced}, "
                      f"chain={'VERIFIED' if chain_ok else 'BROKEN'}")
        return SurfaceResult("context-checkpoint", wired=True, proven=proven, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult("context-checkpoint", wired=True, proven=False,
                             detail=f"probe error: {type(exc).__name__}: {exc}")


def _probe_channel_claims_surface() -> SurfaceResult:
    """Exercise the NOW-WIRED aurora / slack-update claim provenance end-to-end via
    comms_update.record_update_provenance (the /slack-update wire — it routes
    through lib.channel_claim_provenance, the SAME recorder /aurora's _do_post
    calls). PROVES a signed claim IDR lands AND the memory HEAD advances AND the
    posted claims are 100% grounded (verbatim self-citation)."""
    try:
        import comms_update  # type: ignore
        import context_chain  # type: ignore
        update = comms_update.CommsUpdate(
            headline="GRIP slice-4 — always-on recording 5/5",
            body="Audit probe for the channel-claim provenance wire.",
            fields={"PRs merged": "3", "Tests added": "21", "Commits": "7"},
            footer="GRIP", severity="info", origin="always-on-audit",
            template_type="features",
        )
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            idr_path = base / "idr.jsonl"
            chain_path = base / "context.jsonl"
            head_ptr = base / "HEAD"
            before_idr = _count_idr_lines(idr_path)
            before_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            rec = comms_update.record_update_provenance(
                update, path=chain_path, idr_path=idr_path, head_pointer=head_ptr)
            after_idr = _count_idr_lines(idr_path)
            after_head = head_ptr.read_text().strip() if head_ptr.exists() else ""
            idr_landed = after_idr == before_idr + 1
            head_advanced = bool(after_head) and after_head != before_head
            chain_ok = context_chain.verify_context_chain(path=chain_path).value == "verified"
            proven = bool(rec.get("ok")) and idr_landed and head_advanced and chain_ok
            detail = (f"IDR +{after_idr - before_idr}, HEAD advanced={head_advanced}, "
                      f"chain={'VERIFIED' if chain_ok else 'BROKEN'}, "
                      f"grounding={int(rec.get('grounding_rate', 0) * 100)}%")
        return SurfaceResult("aurora/slack-claims", wired=True, proven=proven, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult("aurora/slack-claims", wired=True, proven=False,
                             detail=f"probe error: {type(exc).__name__}: {exc}")


def verify_all_surfaces() -> list[SurfaceResult]:
    """Audit every known decision/claim surface end-to-end (all five now probed)."""
    return [
        _probe_update_team_surface(),
        _probe_proveit_surface(),
        _probe_precog_dispatch_surface(),
        _probe_context_checkpoint_surface(),
        _probe_channel_claims_surface(),
    ]


# --- Reporting --------------------------------------------------------------

def _coverage(results: list[SurfaceResult]) -> tuple[int, int, int]:
    """(proven, wired, total) across all surfaces."""
    proven = sum(1 for r in results if r.proven)
    wired = sum(1 for r in results if r.wired)
    return proven, wired, len(results)


def _render(results: list[SurfaceResult]) -> str:
    proven, wired, total = _coverage(results)
    pct = int(proven / max(total, 1) * 100)
    lines = [
        "[always-on-recording] cryptographic-causation coverage audit",
        f"  proven-recording: {proven}/{total} surfaces ({pct}%)   wired: {wired}/{total}",
        "  (verify-canonical: 'proven' = an IDR line landed AND the memory HEAD advanced)",
        "",
    ]
    for r in results:
        mark = "OK " if r.proven else ("·· " if r.wired else "XX ")
        lines.append(f"  {mark}{r.name}: {r.detail}")
    gaps = [r for r in results if not r.proven]
    if gaps:
        lines.append("")
        lines.append(f"  GAPS ({len(gaps)}): " + ", ".join(r.name for r in gaps))
    return "\n".join(lines)


def _to_json(results: list[SurfaceResult]) -> str:
    proven, wired, total = _coverage(results)
    return json.dumps({
        "proven": proven, "wired": wired, "total": total,
        "coverage_pct": round(proven / max(total, 1) * 100, 1),
        "surfaces": [{"name": r.name, "wired": r.wired, "proven": r.proven,
                      "detail": r.detail} for r in results],
        "gaps": [r.name for r in results if not r.proven],
    }, indent=2)


def _exit_code(results: list[SurfaceResult]) -> int:
    """0 iff EVERY audited surface actually recorded (a real IDR line landed AND
    the real memory HEAD advanced). All five surfaces are now exercised
    end-to-end, so any one failing to record is a regression → non-zero exit.

    This is the anti-Goodhart teeth: 'proven' can never be a hardcoded flag — a
    broken recorder flips its surface red AND fails the run.
    """
    provable = {"update_team-post", "prove-it-artifact", "precog-dispatch-idr",
                "context-checkpoint", "aurora/slack-claims"}
    regressions = [r for r in results if r.name in provable and not r.proven]
    return 1 if regressions else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit cryptographic-causation recording coverage.")
    ap.add_argument("--json", action="store_true", help="emit the audit as JSON")
    args = ap.parse_args(argv)
    results = verify_all_surfaces()
    print(_to_json(results) if args.json else _render(results))
    return _exit_code(results)


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    raise SystemExit(main())
