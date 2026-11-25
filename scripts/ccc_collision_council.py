"""CCC Collision Council — Contraction vs Conformal.

Multi-provider LLM council with Gentner gate to determine whether
CCC-1 (Contraction Criterion for Consciousness, V>> 2026)
and CCC-2 (Conformal Cyclic Cosmology, Penrose & Meissner 2025)
share deeper structural connections beyond the acronym.

Pre-registered hypotheses:
  H0: Pure naming coincidence.
  H1: Meaningful Gentner-level structural mapping exists.

Phases:
  0  Print claims for HITL approval (no API calls)
  1  Falsification council (30 API calls)
  2  Novelty council (10 API calls, only if structural claims survived)
  3  Gentner mechanical gate (local, no API calls)

Usage:
  python3 scripts/ccc_collision_council.py --phase 0
  python3 scripts/ccc_collision_council.py --phase 1
  python3 scripts/ccc_collision_council.py --phase 2
  python3 scripts/ccc_collision_council.py --phase 3
  python3 scripts/ccc_collision_council.py --report
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
if str(CLAUDE_DIR / "lib") not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR / "lib"))

from multi_llm_council import Claim, Council, CouncilResult
from gentner_enforcer import MappingProposal, MappingRelation, enforce

# ── Paths ───────────────────────────────────────────────────────────

RESULTS_DIR = CLAUDE_DIR / "articles"
PHASE1_JSON = RESULTS_DIR / "ccc-collision-phase1.json"
PHASE2_JSON = RESULTS_DIR / "ccc-collision-phase2.json"
REPORT_PATH = RESULTS_DIR / "ccc-collision-council-report.md"


# ── Custom Model Panel ──────────────────────────────────────────────

CCC_MODELS = {
    "gemini_flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "role": "conformal geometer",
        "attack": "Does this mapping survive conformal invariance? "
                  "Check whether the mathematical objects transform compatibly.",
    },
    "gemini_pro": {
        "name": "Gemini 3.1 Pro",
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "role": "category theorist",
        "attack": "Is there a genuine functor between these mathematical structures, "
                  "or is this surface-level diagram matching?",
    },
    "llama33": {
        "name": "Llama 3.3 70B",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "role": "mathematical physicalist",
        "attack": "Would a working physicist accept this structural correspondence? "
                  "Check dimensional analysis, physical plausibility, and mechanism.",
    },
    "qwen3": {
        "name": "Qwen3 32B",
        "provider": "groq",
        "model": "qwen/qwen3-32b",
        "role": "analogy sceptic (Gentner SMT)",
        "attack": "Apply Gentner's systematicity principle rigorously. "
                  "Is this structural (relational) mapping or mere attribute matching?",
    },
    "llama4": {
        "name": "Llama 4 Scout",
        "provider": "groq",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "role": "entropy specialist",
        "attack": "Do the opposite entropy directions (topological vs thermodynamic) "
                  "kill the mapping? Are these categorically different entropy types?",
    },
}


# ── Context ─────────────────────────────────────────────────────────

CCC_CONTEXT = """TWO INDEPENDENT THEORIES SHARING THE ACRONYM "CCC":

CCC-1: Contraction Criterion for Consciousness (V>> 2026)
- Banach fixed-point convergence on metric state spaces
- 4 axioms: B1 (complete metric state space), B2 (contraction mapping on cognitive updates),
  B3 (self-referential closure), B4 (spectral gap > golden ratio)
- Key results: unique conscious fixed point via Banach theorem,
  golden ratio attractor (phi = 0.618...), zero topological entropy at convergence,
  damped oscillation with Lyapunov dissipation, topological conjugacy as scale-free
  structure preservation
- Domain: consciousness science / mathematical neuroscience

CCC-2: Conformal Cyclic Cosmology (Penrose & Meissner 2025)
- Infinite cyclic aeons separated by conformal crossover surfaces
- Each aeon begins with a Big Bang and ends with infinite expansion
- Conformal rescaling at crossover: metric g -> omega^2 * g preserves causal structure
- Key properties: increasing thermodynamic entropy per aeon, Weyl curvature propagation
  across boundaries, conformal invariance preserving null cone structure,
  discrete gravitational spectrum in cyclic framework, mass conservation across aeons
- Domain: theoretical cosmology / general relativity

QUESTION: Beyond sharing the acronym, is there a meaningful structural (Gentner-level)
mapping between these two theories? Or is this pure naming coincidence?"""


# ── Source/Target Elements ──────────────────────────────────────────

CCC1_ELEMENTS = [
    "complete_metric_space",
    "contraction_mapping",
    "fixed_point",
    "self_referential_closure",
    "golden_spectral_gap",
    "zero_topological_entropy",
    "damped_oscillation",
    "Lyapunov_dissipation",
]

CCC2_ELEMENTS = [
    "conformal_manifold",
    "conformal_rescaling",
    "aeon_crossover",
    "cyclic_continuation",
    "Weyl_propagation",
    "gravitational_spectrum",
    "increasing_entropy",
    "mass_conservation",
]


# ── Claims ──────────────────────────────────────────────────────────

def build_falsification_claims() -> list[Claim]:
    """Build the 6 falsification claims (C1-C6)."""
    return [
        Claim(
            "C1", "Structural Isomorphism", "Mathematical",
            "A structural isomorphism exists between CCC-1 (Contraction Criterion "
            "for Consciousness) and CCC-2 (Conformal Cyclic Cosmology) beyond the "
            "shared acronym. There are 3 or more causal (not attribute) "
            "correspondences between their mathematical structures."
        ),
        Claim(
            "C2", "Fixed Point ~ Crossover Surface", "Mathematical",
            "The Banach fixed point in CCC-1 is structurally analogous to the aeon "
            "crossover surface in CCC-2: both represent a state where the previous "
            "phase's endpoint becomes the next phase's starting point. This is a "
            "causal mapping, not just sequential ordering."
        ),
        Claim(
            "C3", "Topological Conjugacy ~ Conformal Invariance", "Mathematical",
            "Topological conjugacy in CCC-1 (structure preservation under continuous "
            "deformation) is structurally analogous to conformal invariance in CCC-2 "
            "(structure preservation under conformal rescaling). Both preserve "
            "scale-free structure. There exists a natural functor between these "
            "mathematical objects."
        ),
        Claim(
            "C4", "Entropy Directions as Fatal Disanalogy", "Mathematical",
            "The opposite entropy directions — zero topological entropy at convergence "
            "in CCC-1 vs increasing thermodynamic entropy per aeon in CCC-2 — "
            "constitute a FATAL structural disanalogy that kills any mapping. "
            "These are categorically different entropy types with no higher-order "
            "structure that accommodates both."
        ),
        Claim(
            "C5", "Golden Ratio ~ Discrete Spectrum", "Mathematical",
            "The golden ratio spectral gap (phi = 0.618) in CCC-1 is structurally "
            "analogous to discrete spectral properties in the cyclic aeon framework "
            "of CCC-2. Both involve eigenvalue-like quantities constraining system "
            "dynamics. The spectral objects live in compatible mathematical spaces "
            "with a natural bridge between them."
        ),
        Claim(
            "C6", "Pure Coincidence (Null Hypothesis)", "Epistemic",
            "The shared CCC acronym is pure naming coincidence. Any perceived "
            "structural parallels between Contraction Criterion for Consciousness "
            "and Conformal Cyclic Cosmology are apophenia — pattern-matching on "
            "surface attributes. No genuine structural mapping exists."
        ),
    ]


def build_novelty_claims() -> list[Claim]:
    """Build the 2 novelty claims (N1-N2)."""
    return [
        Claim(
            "N1", "Cross-Theory Comparison Novel", "Epistemic",
            "The structural comparison between CCC-1 (Banach contraction on "
            "consciousness state spaces) and CCC-2 (conformal cyclic cosmology) "
            "is a genuinely novel research direction. No published work attempts "
            "this specific cross-domain structural comparison between these two "
            "frameworks."
        ),
        Claim(
            "N2", "Unifying Meta-Theory Novel", "Mathematical",
            "A meta-theory that naturally subsumes both Banach contractions on "
            "state spaces AND conformal crossovers between cosmological aeons "
            "would be genuinely new mathematics. No existing framework (category "
            "theory, dynamical systems, or otherwise) naturally accommodates both "
            "as instances of a single abstract pattern."
        ),
    ]


# ── Mapping Extraction ──────────────────────────────────────────────

# Maps claim IDs to (source, target) element pairs for mapping construction
_CLAIM_MAPPING_SEEDS = {
    "C1": [],  # Meta-claim — surviving sub-claims provide the mappings
    "C2": [("fixed_point", "aeon_crossover"),
           ("contraction_mapping", "conformal_rescaling")],
    "C3": [("complete_metric_space", "conformal_manifold"),
           ("damped_oscillation", "cyclic_continuation")],
    "C4": [("zero_topological_entropy", "increasing_entropy")],
    "C5": [("golden_spectral_gap", "gravitational_spectrum")],
    "C6": [],  # Null hypothesis — no mapping
}


def build_mapping_from_results(phase1: CouncilResult) -> MappingProposal | None:
    """Extract surviving claims and build a MappingProposal.

    Returns None if all structural claims (C1-C3, C5) were killed,
    meaning the null hypothesis (C6) prevails.
    """
    structural_claims = ["C1", "C2", "C3", "C5"]
    survivors = [cid for cid in structural_claims if not phase1.is_killed(cid)]

    if not survivors:
        return None

    relations = []
    mapped_sources = set()
    mapped_targets = set()

    for cid in survivors:
        seeds = _CLAIM_MAPPING_SEEDS.get(cid, [])
        for source, target in seeds:
            # Extract the strongest surviving argument as justification
            justification = _best_surviving_argument(phase1, cid)
            relations.append(MappingRelation(
                source=source,
                target=target,
                relation="causal",
                justification=justification,
            ))
            mapped_sources.add(source)
            mapped_targets.add(target)

    # C4 (entropy disanalogy): if it survived as FATAL, add as surface relation
    if not phase1.is_killed("C4"):
        relations.append(MappingRelation(
            source="zero_topological_entropy",
            target="increasing_entropy",
            relation="surface",
            justification="Entropy types differ categorically — acknowledged disanalogy",
        ))
        mapped_sources.add("zero_topological_entropy")
        mapped_targets.add("increasing_entropy")

    unmapped_source = [e for e in CCC1_ELEMENTS if e not in mapped_sources]
    unmapped_target = [e for e in CCC2_ELEMENTS if e not in mapped_targets]

    return MappingProposal(
        source_domain="CCC-1 (Contraction Criterion for Consciousness)",
        target_domain="CCC-2 (Conformal Cyclic Cosmology)",
        source_elements=list(CCC1_ELEMENTS),
        target_elements=list(CCC2_ELEMENTS),
        relations=relations,
        unmapped_source=unmapped_source,
        unmapped_target=unmapped_target,
    )


def _best_surviving_argument(result: CouncilResult, claim_id: str) -> str:
    """Get the strongest SURVIVED argument for a claim."""
    if claim_id not in result.claims:
        return ""
    best = ""
    best_conf = -1
    for v in result.claims[claim_id].values():
        if v.verdict == "SURVIVED" and v.confidence > best_conf:
            best = v.argument[:200]
            best_conf = v.confidence
    return best if best else "Claim survived council evaluation"


# ── Phase Executors ─────────────────────────────────────────────────

def run_phase_0() -> None:
    """Print all claims for HITL approval. No API calls."""
    print("=" * 70)
    print("CCC COLLISION COUNCIL — Phase 0: HITL Claim Approval")
    print("=" * 70)

    print("\nPre-registered hypotheses:")
    print("  H0: Pure naming coincidence. Structural parallels are apophenia.")
    print("  H1: Meaningful Gentner-level structural mapping exists.")
    print("  Falsification of H1: ALL structural claims KILLED AND Gentner < 40")
    print("  Falsification of H0: 2+ structural claims SURVIVE AND Gentner >= 60")

    print("\n" + "-" * 70)
    print("FALSIFICATION CLAIMS (attack mode)")
    print("-" * 70)

    for claim in build_falsification_claims():
        print(f"\n[{claim.id}] {claim.label} ({claim.claim_type})")
        print(f"  {claim.text[:120]}...")
        if claim.id == "C4":
            print("  Falsified if: mechanisms are categorically incompatible")
        elif claim.id == "C6":
            print("  Falsified if: 2+ of C1/C2/C3/C5 survive with structural mappings")
        else:
            print("  Falsified if: no genuine structural correspondence found")

    print("\n" + "-" * 70)
    print("NOVELTY CLAIMS (novelty mode)")
    print("-" * 70)

    for claim in build_novelty_claims():
        print(f"\n[{claim.id}] {claim.label} ({claim.claim_type})")
        print(f"  {claim.text[:120]}...")

    print("\n" + "-" * 70)
    print("PANEL (5 models, 3 providers)")
    print("-" * 70)

    for mid, mc in CCC_MODELS.items():
        print(f"  {mc['name']:20s} | {mc['role']}")

    print(f"\nBudget: 30 (Phase 1) + 10 (Phase 2) = 40 API calls")
    print("Approve claims before running --phase 1")


def run_phase_1() -> CouncilResult:
    """Falsification council: 6 claims x 5 models = 30 API calls."""
    print("=" * 70)
    print("CCC COLLISION COUNCIL — Phase 1: Falsification")
    print("=" * 70)

    claims = build_falsification_claims()
    council = Council(
        context=CCC_CONTEXT,
        models=CCC_MODELS,
        kill_threshold=3,
        prompt_mode="attack",
    )

    print(f"\n{len(claims)} claims x {len(council.models)} models = "
          f"{len(claims) * len(council.models)} API calls")
    print()

    result = council.evaluate(claims)

    print("\n" + "=" * 70)
    print(result.summary_table())

    # Save raw results
    council.save_results(result, PHASE1_JSON)
    print(f"\nResults saved to {PHASE1_JSON}")

    # Determine outcome
    structural = ["C1", "C2", "C3", "C5"]
    survivors = [c for c in structural if not result.is_killed(c)]
    print(f"\nStructural claims surviving: {len(survivors)}/4 — {survivors or 'NONE'}")

    if not survivors:
        print("ALL structural claims KILLED. C6 (coincidence) prevails.")
        print("Phase 2 (novelty) skipped — no structural mapping to evaluate.")
    elif len(survivors) >= 2:
        print("2+ structural claims survived — H0 falsification path open.")
        print("Run --phase 2 for novelty evaluation.")
    else:
        print("1 structural claim survived — inconclusive. Run --phase 2.")

    return result


def run_phase_2() -> CouncilResult:
    """Novelty council: 2 claims x 5 models = 10 API calls."""
    print("=" * 70)
    print("CCC COLLISION COUNCIL — Phase 2: Novelty")
    print("=" * 70)

    claims = build_novelty_claims()
    council = Council(
        context=CCC_CONTEXT,
        models=CCC_MODELS,
        kill_threshold=3,
        prompt_mode="novelty",
    )

    print(f"\n{len(claims)} claims x {len(council.models)} models = "
          f"{len(claims) * len(council.models)} API calls")
    print()

    result = council.evaluate(claims)

    print("\n" + "=" * 70)
    print(result.summary_table())

    council.save_results(result, PHASE2_JSON)
    print(f"\nResults saved to {PHASE2_JSON}")

    return result


def run_phase_3() -> None:
    """Gentner mechanical gate on Phase 1 results."""
    print("=" * 70)
    print("CCC COLLISION COUNCIL — Phase 3: Gentner Gate")
    print("=" * 70)

    if not PHASE1_JSON.exists():
        print("ERROR: Run --phase 1 first. No Phase 1 results found.")
        sys.exit(1)

    phase1 = _load_council_result(PHASE1_JSON)
    proposal = build_mapping_from_results(phase1)

    if proposal is None:
        print("\nAll structural claims were killed.")
        print("No mapping proposal to evaluate — H0 (coincidence) confirmed.")
        print("Gentner score: N/A (no proposal)")
        return

    print(f"\nSource domain: {proposal.source_domain}")
    print(f"Target domain: {proposal.target_domain}")
    print(f"Relations: {len(proposal.relations)}")
    print(f"Unmapped source: {proposal.unmapped_source}")
    print(f"Unmapped target: {proposal.unmapped_target}")

    result = enforce(proposal)

    print(f"\nGentner Score: {result.score.total_score:.1f}/100")
    print(f"Decision: {result.decision.upper()}")
    print(f"Reason: {result.reason}")

    if result.violations:
        print("\nViolations:")
        for v in result.violations:
            print(f"  {v}")

    if result.blocked:
        print("\nH1 BLOCKED by Gentner gate.")
        if result.score.total_score < 40:
            print("Score < 40: Surface-level analogy. H0 confirmed.")
        else:
            print("Score 40-59: Structural issues. Inconclusive.")
    else:
        print("\nH1 ALLOWED by Gentner gate.")
        print("Score >= 60: Meaningful structural mapping found.")


# ── Report Generation ───────────────────────────────────────────────

def generate_report() -> None:
    """Generate final markdown report from all phase results."""
    print("=" * 70)
    print("CCC COLLISION COUNCIL — Report Generation")
    print("=" * 70)

    if not PHASE1_JSON.exists():
        print("ERROR: Run --phase 1 first.")
        sys.exit(1)

    phase1 = _load_council_result(PHASE1_JSON)
    phase2 = _load_council_result(PHASE2_JSON) if PHASE2_JSON.exists() else None

    structural = ["C1", "C2", "C3", "C5"]
    survivors = [c for c in structural if not phase1.is_killed(c)]

    # Gentner result
    proposal = build_mapping_from_results(phase1)
    gentner_result = enforce(proposal) if proposal else None
    gentner_score = gentner_result.score.total_score if gentner_result else 0

    # Determine verdict
    if not survivors:
        verdict = "H0 CONFIRMED: Pure naming coincidence"
        verdict_detail = "All structural claims killed by council."
    elif gentner_result and gentner_result.blocked:
        if gentner_score < 40:
            verdict = "H0 CONFIRMED: Surface-level analogy only"
            verdict_detail = f"Gentner score {gentner_score:.0f}/100 — below surface threshold."
        else:
            verdict = "INCONCLUSIVE: Structural issues in mapping"
            verdict_detail = f"Gentner score {gentner_score:.0f}/100 — between thresholds."
    elif gentner_result and not gentner_result.blocked:
        verdict = "H1 SUPPORTED: Meaningful structural mapping found"
        verdict_detail = f"Gentner score {gentner_score:.0f}/100 — above structural threshold."
    else:
        verdict = "INCONCLUSIVE"
        verdict_detail = "Insufficient data for determination."

    lines = [
        "# CCC Collision Council Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Executive Summary",
        "",
        f"**Verdict: {verdict}**",
        "",
        verdict_detail,
        "",
        "## Pre-Registered Hypotheses",
        "",
        "- **H0**: Pure naming coincidence. Structural parallels are apophenia.",
        "- **H1**: Meaningful Gentner-level structural mapping exists.",
        "",
        "## Phase 1: Falsification Council",
        "",
        f"30 API calls across 5 models (3 providers). Kill threshold: 3.",
        "",
        phase1.summary_table(),
        "",
        f"**Structural claims surviving**: {len(survivors)}/4 — "
        f"{', '.join(survivors) if survivors else 'NONE'}",
        "",
    ]

    # Per-claim detail
    for claim_id in ["C1", "C2", "C3", "C4", "C5", "C6"]:
        if claim_id not in phase1.claims:
            continue
        status = "KILLED" if phase1.is_killed(claim_id) else "ALIVE"
        lines.append(f"### {claim_id} ({status})")
        lines.append("")
        for model_id, v in phase1.claims[claim_id].items():
            lines.append(f"- **{v.model}**: {v.verdict} ({v.confidence}%) — "
                         f"{v.argument[:150]}")
        lines.append("")

    # Phase 2
    if phase2:
        lines.extend([
            "## Phase 2: Novelty Council",
            "",
            f"10 API calls. Kill threshold: 3.",
            "",
            phase2.summary_table(),
            "",
        ])
        for claim_id in ["N1", "N2"]:
            if claim_id not in phase2.claims:
                continue
            status = "KILLED" if phase2.is_killed(claim_id) else "ALIVE"
            lines.append(f"### {claim_id} ({status})")
            lines.append("")
            for model_id, v in phase2.claims[claim_id].items():
                lines.append(f"- **{v.model}**: {v.verdict} ({v.confidence}%) — "
                             f"{v.argument[:150]}")
            lines.append("")
    else:
        lines.extend([
            "## Phase 2: Novelty Council",
            "",
            "Skipped — all structural claims killed in Phase 1.",
            "",
        ])

    # Phase 3
    lines.extend([
        "## Phase 3: Gentner Gate",
        "",
    ])
    if gentner_result:
        lines.extend([
            f"**Score**: {gentner_score:.1f}/100",
            f"**Decision**: {gentner_result.decision.upper()}",
            f"**Reason**: {gentner_result.reason}",
            "",
        ])
        if proposal:
            lines.extend([
                f"Relations evaluated: {len(proposal.relations)}",
                f"Unmapped source elements: {proposal.unmapped_source}",
                f"Unmapped target elements: {proposal.unmapped_target}",
                "",
            ])
        if gentner_result.violations:
            lines.append("Violations:")
            lines.append("")
            for v in gentner_result.violations:
                lines.append(f"- {v}")
            lines.append("")
    else:
        lines.extend([
            "No mapping proposal generated — all structural claims killed.",
            "",
        ])

    # Conclusion
    lines.extend([
        "## Conclusion",
        "",
        f"**{verdict}**",
        "",
        verdict_detail,
        "",
        "## Methodology",
        "",
        "- 5-model panel: Gemini 2.5 Flash, Gemini 3.1 Pro, "
        "Llama 3.3 70B, Qwen3 32B, Llama 4 Scout",
        "- Providers: Google Gemini, Groq (Meta Llama, Alibaba Qwen)",
        "- Kill threshold: 3/5 FALSIFIED = KILLED",
        "- Gentner enforcement: < 40 = surface, 40-59 = structural issues, "
        ">= 60 = structural mapping accepted",
        "- Pre-registered hypotheses with explicit falsification criteria",
        "",
    ])

    report = "\n".join(lines)
    REPORT_PATH.write_text(report)
    print(f"\nReport written to {REPORT_PATH}")
    print(f"\nVerdict: {verdict}")


# ── Utilities ───────────────────────────────────────────────────────

def _load_council_result(path: Path) -> CouncilResult:
    """Load CouncilResult from saved JSON."""
    from multi_llm_council import Verdict

    data = json.loads(path.read_text())
    result = CouncilResult(
        timestamp=data.get("timestamp", ""),
        context=data.get("context", ""),
        kill_threshold=data.get("kill_threshold", 3),
    )
    for claim_id, verdicts in data.get("claims", {}).items():
        result.claims[claim_id] = {}
        for model_id, vd in verdicts.items():
            result.claims[claim_id][model_id] = Verdict(
                verdict=vd["verdict"],
                confidence=vd["confidence"],
                argument=vd["argument"],
                model=vd.get("model", ""),
                error=vd.get("error"),
            )
    return result


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CCC Collision Council — Contraction vs Conformal"
    )
    parser.add_argument(
        "--phase", type=int, choices=[0, 1, 2, 3],
        help="Phase to execute (0=claims, 1=falsification, 2=novelty, 3=gentner)"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Generate final markdown report"
    )
    args = parser.parse_args()

    if args.phase is None and not args.report:
        parser.print_help()
        sys.exit(1)

    if args.phase == 0:
        run_phase_0()
    elif args.phase == 1:
        run_phase_1()
    elif args.phase == 2:
        run_phase_2()
    elif args.phase == 3:
        run_phase_3()

    if args.report:
        generate_report()


if __name__ == "__main__":
    main()
