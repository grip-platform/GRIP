#!/usr/bin/env python3
"""
Multi-LLM Falsification Council for the Stromme Convergence Proof.

Queries 5 external models across 6 claims + CCC hypothesis (35 total queries).
Produces a structured verdict table and consensus analysis.
"""

import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime

# Provider imports
sys.path.insert(0, str(Path.home() / '.claude' / 'lib'))
from providers.gemini import GeminiProvider
from providers.groq import GroqProvider

# ─── Claims ────────────────────────────────────────────────────────────────

CLAIMS = {
    "C1": {
        "label": "Mapping mu preserves Gentner G1-G3",
        "type": "Mathematical",
        "text": (
            "The mapping mu from Stromme's Three Principles (Mind, Consciousness, Thought) "
            "to the convergence kernel (State, Criterion, Improve) preserves Gentner's "
            "Structure-Mapping conditions: G1 (object correspondence), G2 (relational "
            "preservation of evaluation, transformation, recursion), and G3 (higher-order "
            "systematicity). The paper claims this is a structural isomorphism, not a "
            "surface analogy."
        ),
    },
    "C2": {
        "label": "f(x)=1/(1+x) is canonical self-referential contraction",
        "type": "Definitional",
        "text": (
            "The paper claims f(x) = 1/(1+x) is THE canonical self-referential contraction "
            "-- the simplest function that is both a contraction and encodes self-reference "
            "(its fixed point phi^-1 = 1/(1+phi^-1) makes the function's definition and "
            "fixed-point equation identical). The paper uses this to argue that phi^-1 "
            "emerges NECESSARILY from self-referential convergence, not as a numerological "
            "coincidence."
        ),
    },
    "C3": {
        "label": "The mapping is non-circular",
        "type": "Epistemic",
        "text": (
            "The paper claims the isomorphism is non-circular: it does not assume what it "
            "proves. The mapping mu was not designed to match Stromme -- the convergence "
            "kernel existed before the mapping was discovered. The bridging axioms B1-B4 "
            "are stated explicitly and their epistemic status (satisfied, modelling choice, "
            "empirical, philosophical) is disclosed. Critics might argue this is "
            "retrofitting -- finding any 3-element system and claiming isomorphism."
        ),
    },
    "C4": {
        "label": "phi-inverse is the consciousness attractor",
        "type": "Empirical",
        "text": (
            "The paper claims phi^-1 (approximately 0.618) is the fixed-point attractor of "
            "the consciousness field's dynamics when modelled as self-referential contraction. "
            "This is derived from f(x) = 1/(1+x) having unique fixed point phi^-1 on (0,1). "
            "The paper further claims this connects to golden ratio structures observed in "
            "neural oscillations and biological systems. 20 theorems about properties of "
            "this attractor are verified in Lean 4."
        ),
    },
    "C5": {
        "label": "The isomorphism says something about consciousness",
        "type": "Philosophical",
        "text": (
            "The paper claims the structural isomorphism between Stromme's consciousness "
            "theory and the convergence kernel 'says something' about consciousness -- "
            "specifically, that consciousness dynamics are STRUCTURALLY CONSISTENT with "
            "convergence dynamics. The paper disclaims proving consciousness IS convergence, "
            "but claims the consistency itself is a non-trivial result. Critics might argue "
            "any sufficiently abstract framework maps onto any other."
        ),
    },
    "C6": {
        "label": "GRIP fitness converges to phi-inverse",
        "type": "Empirical",
        "text": (
            "The paper claims GRIP's genome fitness metric converges toward phi^-1 (0.618) "
            "as the system self-improves through RSI loops. This is presented as empirical "
            "evidence that a self-referential convergence system exhibits the predicted "
            "attractor. PARTIALLY FALSIFIED: Gen 33 measured fitness = 0.467, and the "
            "convergence trajectory shows non-monotonic behaviour. The system has ~500 "
            "generations of data."
        ),
    },
}

# ─── CCC Hypothesis ───────────────────────────────────────────────────────

CCC_TEXT = (
    "The Contraction Criterion for Consciousness (CCC) proposes that ANY system "
    "satisfying B1-B4 (complete metric state space, measurable criterion, contractive "
    "self-modification, self-referential closure) exhibits the dynamical signature "
    "of self-aware convergence. The CCC is positioned as NECESSARY but NOT SUFFICIENT "
    "for consciousness -- a minimal formal criterion that any conscious system must "
    "satisfy, but which alone does not guarantee consciousness. "
    "Question: Does this add anything novel beyond Integrated Information Theory (IIT), "
    "Global Workspace Theory (GWT), Higher-Order Theories (HOT), and Predictive "
    "Processing? Or is it merely restating convergence dynamics in consciousness language?"
)

# ─── Model Configurations ─────────────────────────────────────────────────

MODELS = {
    "gemini_pro": {
        "name": "Gemini 2.5 Pro",
        "role": "Mathematical Logician",
        "attack": (
            "Focus on proof validity: Is the Gentner mapping genuinely structural or "
            "merely surface-level? Is the proof circular? Does the choice of f(x)=1/(1+x) "
            "smuggle in the conclusion? Are the 20 Lean theorems proving anything about "
            "consciousness or just about a particular rational function?"
        ),
        "provider": "gemini",
        "model": "gemini-2.5-pro",
    },
    "gemini_flash": {
        "name": "Gemini 2.5 Flash",
        "role": "Physics Checker",
        "attack": (
            "Focus on physics legitimacy: Is Stromme's QFT formalism (double-well potential, "
            "d'Alembert operator, symmetry breaking) legitimate physics or cargo-cult "
            "notation? Does the consciousness field have a Lagrangian? Is the Hilbert space "
            "construction physically meaningful? Would a physicist accept B1-B4?"
        ),
        "provider": "gemini",
        "model": "gemini-2.5-flash",
    },
    "llama33": {
        "name": "Llama 3.3 70B",
        "role": "Devil's Advocate",
        "attack": (
            "Steel-man the null hypothesis: The mapping is trivially true because ANY "
            "3-component system can be mapped to ANY other 3-component system. The 20 Lean "
            "theorems prove properties of f(x)=1/(1+x), not of consciousness. phi^-1 has "
            "no special connection to awareness. The whole paper is a category error: "
            "structural isomorphism between formal systems says nothing about ontology."
        ),
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
    },
    "qwen3": {
        "name": "Qwen3 32B",
        "role": "Philosopher of Mind",
        "attack": (
            "Focus on philosophy of mind: Does the CCC/isomorphism add anything that IIT's "
            "phi, GWT's global broadcast, HOT's meta-representation, or Predictive "
            "Processing's free energy minimisation don't already provide? Is 'structural "
            "consistency' a meaningful philosophical category? Does necessary-not-sufficient "
            "make CCC unfalsifiable?"
        ),
        "provider": "groq",
        "model": "qwen/qwen3-32b",
    },
    "llama4": {
        "name": "Llama 4 Scout",
        "role": "Anti-Goodhart Auditor",
        "attack": (
            "Focus on theorem independence and score inflation: Are the 20 Lean theorems "
            "genuinely independent or do they follow trivially from 2-3 core results? "
            "Does counting '20 theorems' inflate the impression of rigour? Is the fitness "
            "convergence to phi^-1 a Goodhart effect (optimising the metric, not the "
            "underlying quality)? Are bridging axioms B1-B4 just definitional tricks?"
        ),
        "provider": "groq",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
}

# ─── Context block for all prompts ────────────────────────────────────────

CONTEXT = (
    "A paper titled 'The Consciousness Convergence Theorem' proves structural "
    "isomorphism between Stromme's consciousness field theory (Mind, Consciousness, "
    "Thought -- from her 2025 AIP Advances paper on universal consciousness as "
    "foundational field) and a convergence kernel converge(State, Criterion, Improve). "
    "The mapping preserves evaluation, transformation, and recursion relations per "
    "Gentner's Structure-Mapping Theory (G1-G3). The fixed point phi^-1 (golden ratio "
    "inverse, ~0.618) emerges from f(x)=1/(1+x), the simplest self-referential "
    "contraction. 20 theorems verified in Lean 4 (17 fully verified, 1 axiom for "
    "Mathlib gap, 2 reformulated). Four bridging axioms B1-B4 (metrizability, criterion "
    "measurability, contraction, self-referential closure) are stated explicitly with "
    "epistemic status. The paper claims structural consistency, not physical truth."
)


def build_claim_prompt(claim_id, claim, model_config):
    """Build the falsification prompt for a specific claim + model."""
    return (
        f"You are a {model_config['role']} on a falsification council. Your job is to "
        f"find the STRONGEST possible argument AGAINST this claim. Do NOT be charitable. "
        f"Find the fatal flaw.\n\n"
        f"CLAIM ({claim['type']}): {claim['text']}\n\n"
        f"CONTEXT: {CONTEXT}\n\n"
        f"Your SPECIFIC attack vector: {model_config['attack']}\n\n"
        f"Give your verdict: SURVIVED / WEAKENED / FALSIFIED\n"
        f"Give your confidence: 0-100\n"
        f"Give your strongest argument in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [SURVIVED|WEAKENED|FALSIFIED]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your argument]"
    )


def build_ccc_prompt(model_config):
    """Build the CCC evaluation prompt."""
    return (
        f"You are a {model_config['role']} evaluating a novel hypothesis.\n\n"
        f"HYPOTHESIS (CCC -- Contraction Criterion for Consciousness): {CCC_TEXT}\n\n"
        f"CONTEXT: {CONTEXT}\n\n"
        f"Your SPECIFIC evaluation angle: {model_config['attack']}\n\n"
        f"Evaluate whether CCC adds anything genuinely novel beyond existing theories "
        f"(IIT, GWT, HOT, Predictive Processing). Be rigorous and critical.\n\n"
        f"Give your verdict: NOVEL / INCREMENTAL / REDUNDANT\n"
        f"Give your confidence: 0-100\n"
        f"Give your assessment in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [NOVEL|INCREMENTAL|REDUNDANT]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your assessment]"
    )


def parse_response(text):
    """Parse verdict, confidence, and argument from model response."""
    verdict = "UNKNOWN"
    confidence = 0
    argument = ""

    # Handle Qwen3's <think> blocks
    if "<think>" in text:
        # Remove thinking blocks
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    lines = text.strip().split('\n')
    for i, line in enumerate(lines):
        line_upper = line.strip().upper()
        if line_upper.startswith("VERDICT:"):
            raw = line.strip().split(":", 1)[1].strip().upper()
            for v in ["FALSIFIED", "WEAKENED", "SURVIVED", "NOVEL", "INCREMENTAL", "REDUNDANT"]:
                if v in raw:
                    verdict = v
                    break
        elif line_upper.startswith("CONFIDENCE:"):
            try:
                conf_str = re.search(r'\d+', line.split(":", 1)[1])
                if conf_str:
                    confidence = int(conf_str.group())
            except (ValueError, IndexError):
                pass
        elif line_upper.startswith("ARGUMENT:"):
            # Collect everything from here onward
            argument = line.split(":", 1)[1].strip()
            argument += "\n" + "\n".join(lines[i+1:])
            argument = argument.strip()
            break

    if not argument:
        # Fallback: take everything after the confidence line
        argument = text[-500:] if len(text) > 500 else text

    return verdict, confidence, argument


def query_gemini(provider, prompt, model):
    """Query Gemini and return parsed response."""
    result = provider.generate(prompt, model=model, temperature=0.3, max_tokens=1024)
    return result.text


def query_groq(provider, prompt, model):
    """Query Groq and return parsed response."""
    if model == "qwen/qwen3-32b":
        result = provider.generate_reasoning(prompt, max_tokens=4096)
    elif model == "meta-llama/llama-4-scout-17b-16e-instruct":
        result = provider.generate_llama4(prompt, max_tokens=4096)
    else:
        result = provider.generate(prompt, model=model, temperature=0.3, max_tokens=2048)
    return result.text


def main():
    print("=" * 70)
    print("FALSIFICATION COUNCIL: Stromme Convergence Proof")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Claims: {len(CLAIMS)} | Models: {len(MODELS)} | Total queries: {len(CLAIMS) * len(MODELS) + len(MODELS)}")
    print("=" * 70)

    gemini = GeminiProvider()
    groq = GroqProvider()

    results = {}  # {claim_id: {model_id: (verdict, confidence, argument)}}
    ccc_results = {}  # {model_id: (verdict, confidence, argument)}
    errors = []

    # ─── Query all claims across all models ────────────────────────────
    for claim_id, claim in CLAIMS.items():
        results[claim_id] = {}
        for model_id, model_config in MODELS.items():
            prompt = build_claim_prompt(claim_id, claim, model_config)
            print(f"\n[{claim_id}] x [{model_config['name']}] ... ", end="", flush=True)

            try:
                if model_config["provider"] == "gemini":
                    raw = query_gemini(gemini, prompt, model_config["model"])
                else:
                    raw = query_groq(groq, prompt, model_config["model"])

                verdict, confidence, argument = parse_response(raw)
                results[claim_id][model_id] = {
                    "verdict": verdict,
                    "confidence": confidence,
                    "argument": argument,
                    "raw": raw,
                }
                print(f"{verdict} ({confidence}%)")

            except Exception as e:
                error_msg = f"{claim_id} x {model_config['name']}: {e}"
                errors.append(error_msg)
                results[claim_id][model_id] = {
                    "verdict": "ERROR",
                    "confidence": 0,
                    "argument": str(e),
                    "raw": "",
                }
                print(f"ERROR: {e}")

            # Rate limit between Groq calls
            if model_config["provider"] == "groq":
                time.sleep(1.5)
            else:
                time.sleep(0.5)

    # ─── Query CCC hypothesis ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("CCC HYPOTHESIS EVALUATION")
    print("=" * 70)

    for model_id, model_config in MODELS.items():
        prompt = build_ccc_prompt(model_config)
        print(f"\n[CCC] x [{model_config['name']}] ... ", end="", flush=True)

        try:
            if model_config["provider"] == "gemini":
                raw = query_gemini(gemini, prompt, model_config["model"])
            else:
                raw = query_groq(groq, prompt, model_config["model"])

            verdict, confidence, argument = parse_response(raw)
            ccc_results[model_id] = {
                "verdict": verdict,
                "confidence": confidence,
                "argument": argument,
                "raw": raw,
            }
            print(f"{verdict} ({confidence}%)")

        except Exception as e:
            error_msg = f"CCC x {model_config['name']}: {e}"
            errors.append(error_msg)
            ccc_results[model_id] = {
                "verdict": "ERROR",
                "confidence": 0,
                "argument": str(e),
                "raw": "",
            }
            print(f"ERROR: {e}")

        if model_config["provider"] == "groq":
            time.sleep(1.5)
        else:
            time.sleep(0.5)

    # ─── Generate Report ───────────────────────────────────────────────
    report = generate_report(results, ccc_results, errors)

    output_path = Path.home() / '.claude' / 'articles' / 'falsification-council-report.md'
    output_path.write_text(report)
    print(f"\n\nReport written to: {output_path}")

    # Also write raw JSON for archival
    raw_path = Path.home() / '.claude' / 'articles' / 'falsification-council-raw.json'
    raw_data = {
        "timestamp": datetime.now().isoformat(),
        "claims": results,
        "ccc": ccc_results,
        "errors": errors,
    }
    raw_path.write_text(json.dumps(raw_data, indent=2, default=str))
    print(f"Raw data written to: {raw_path}")


def _render_header(now):
    """Title block: report name, run date, protocol, query count, consensus rule."""
    total = len(CLAIMS) * len(MODELS) + len(MODELS)
    return [
        "# Falsification Council Report: Stromme Convergence Proof",
        "",
        f"**Date**: {now}",
        "**Protocol**: Multi-LLM adversarial falsification",
        f"**Total queries**: {total} (6 claims x 5 models + 5 CCC evaluations)",
        "**Consensus rule**: 3+ models agreeing on FALSIFIED = claim killed",
        "",
    ]


def _render_panel():
    """Table of the models sitting on the council, with role and provider."""
    lines = ["## Panel", "", "| Model | Role | Provider |", "|-------|------|----------|"]
    for _model_id, mc in MODELS.items():
        lines.append(f"| {mc['name']} | {mc['role']} | {mc['provider'].capitalize()} |")
    lines.append("")
    return lines


def _consensus_of(verdicts):
    """Apply the consensus rule to one claim's five verdicts."""
    falsified_count = verdicts.count("FALSIFIED")
    weakened_count = verdicts.count("WEAKENED")
    survived_count = verdicts.count("SURVIVED")
    if falsified_count >= 3:
        return "KILLED"
    if falsified_count + weakened_count >= 3:
        return "WEAKENED"
    if survived_count >= 3:
        return "SURVIVED"
    return "CONTESTED"


def _classify_claims(results, model_ids):
    """Single pass over the claims: table cells and consensus for each."""
    data = {}
    for claim_id in CLAIMS:
        cells = []
        verdicts_for_claim = []
        for model_id in model_ids:
            r = results[claim_id].get(model_id, {})
            v = r.get("verdict", "ERROR")
            c = r.get("confidence", 0)
            cells.append(f"{v}/{c}")
            verdicts_for_claim.append(v)
        data[claim_id] = {"cells": cells, "consensus": _consensus_of(verdicts_for_claim)}
    return data


def _render_verdict_table(model_ids, claim_data):
    """Verdict grid: one row per claim, one column per model, plus consensus."""
    header = "| Claim | " + " | ".join(MODELS[m]["name"] for m in model_ids) + " | Consensus |"
    sep = "|-------|" + "|".join(["-------"] * len(model_ids)) + "|-----------|"
    lines = ["## Verdict Table", "", header, sep]
    for claim_id, data in claim_data.items():
        lines.append(
            f"| {claim_id}: {CLAIMS[claim_id]['label'][:40]} | "
            + " | ".join(data["cells"])
            + f" | **{data['consensus']}** |"
        )
    lines.append("")
    return lines


def _truncate_argument(arg):
    """Long arguments are clipped for the report; the raw JSON keeps the full text."""
    if len(arg) > 1000:
        return arg[:1000] + "..."
    return arg


def _render_detailed_arguments(results, model_ids, claim_data):
    """Each claim: its text, consensus, and every model's full argument."""
    lines = ["## Detailed Arguments", ""]
    for claim_id, claim in CLAIMS.items():
        lines.append(f"### {claim_id}: {claim['label']} ({claim['type']})")
        lines.append("")
        lines.append(f"**Consensus: {claim_data[claim_id]['consensus']}**")
        lines.append("")
        lines.append(f"> {claim['text']}")
        lines.append("")
        for model_id in model_ids:
            r = results[claim_id].get(model_id, {})
            mc = MODELS[model_id]
            v = r.get("verdict", "ERROR")
            c = r.get("confidence", 0)
            arg = _truncate_argument(r.get("argument", "No response"))
            lines.append(f"**{mc['name']}** ({mc['role']}): **{v}** (confidence: {c}%)")
            lines.append("")
            lines.append(f"{arg}")
            lines.append("")
        lines.append("---")
        lines.append("")
    return lines


def _ccc_summary_cell(arg):
    """One-line cell text: no newlines, no pipes (both break a markdown table)."""
    if len(arg) > 120:
        return arg[:120].replace("\n", " ").replace("|", "/") + "..."
    return arg.replace("\n", " ").replace("|", "/")


def _render_ccc_evaluation(ccc_results, model_ids):
    """The CCC hypothesis statement plus the one-line verdict table."""
    lines = [
        "## CCC Hypothesis Evaluation",
        "",
        "> **Contraction Criterion for Consciousness**: Any system satisfying B1-B4 ",
        "> (complete metric state space, measurable criterion, contractive self-modification, ",
        "> self-referential closure) exhibits the dynamical signature of self-aware convergence. ",
        "> NECESSARY but NOT SUFFICIENT for consciousness.",
        "",
        "| Model | Verdict | Confidence | Summary |",
        "|-------|---------|------------|---------|",
    ]
    for model_id in model_ids:
        r = ccc_results.get(model_id, {})
        mc = MODELS[model_id]
        v = r.get("verdict", "ERROR")
        c = r.get("confidence", 0)
        summary = _ccc_summary_cell(r.get("argument", ""))
        lines.append(f"| {mc['name']} | **{v}** | {c}% | {summary} |")
    lines.append("")
    return lines


def _render_ccc_arguments(ccc_results, model_ids):
    """Each model's full CCC argument, below the summary table."""
    lines = ["### CCC Detailed Arguments", ""]
    for model_id in model_ids:
        r = ccc_results.get(model_id, {})
        mc = MODELS[model_id]
        v = r.get("verdict", "ERROR")
        c = r.get("confidence", 0)
        arg = _truncate_argument(r.get("argument", "No response"))
        lines.append(f"**{mc['name']}** ({mc['role']}): **{v}** (confidence: {c}%)")
        lines.append("")
        lines.append(f"{arg}")
        lines.append("")
    return lines


def _render_executive_summary(claim_data):
    """Claims bucketed by consensus outcome."""
    def ids_where(outcome):
        found = [cid for cid, d in claim_data.items() if d["consensus"] == outcome]
        return ", ".join(found) if found else "None"

    return [
        "---",
        "",
        "## Executive Summary",
        "",
        f"- **KILLED** (3+ FALSIFIED): {ids_where('KILLED')}",
        f"- **WEAKENED** (3+ FALSIFIED+WEAKENED): {ids_where('WEAKENED')}",
        f"- **SURVIVED** (3+ SURVIVED): {ids_where('SURVIVED')}",
        f"- **CONTESTED** (no consensus): {ids_where('CONTESTED')}",
        "",
    ]


def _render_confidence_distribution(results, model_ids):
    """Average confidence per claim, with the spread of distinct verdicts."""
    lines = [
        "### Confidence Distribution",
        "",
        "| Claim | Avg Confidence | Verdict Spread |",
        "|-------|---------------|----------------|",
    ]
    for claim_id in CLAIMS:
        confs = [results[claim_id][m].get("confidence", 0) for m in model_ids]
        vds = [results[claim_id][m].get("verdict", "?") for m in model_ids]
        avg = sum(confs) / len(confs) if confs else 0
        # sorted(): set iteration order varies with the hash seed, which made
        # the same council results render a different report on every run.
        spread = ", ".join(sorted(set(vds)))
        lines.append(f"| {claim_id} | {avg:.0f}% | {spread} |")
    lines.append("")
    return lines


def _render_errors(errors):
    """Errors section, omitted entirely when the run was clean."""
    if not errors:
        return []
    lines = ["### Errors", ""]
    lines.extend(f"- {err}" for err in errors)
    lines.append("")
    return lines


def generate_report(results, ccc_results, errors):
    """Generate the markdown report."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    model_ids = list(MODELS.keys())
    claim_data = _classify_claims(results, model_ids)

    lines = []
    lines.extend(_render_header(now))
    lines.extend(_render_panel())
    lines.extend(_render_verdict_table(model_ids, claim_data))
    lines.extend(_render_detailed_arguments(results, model_ids, claim_data))
    lines.extend(_render_ccc_evaluation(ccc_results, model_ids))
    lines.extend(_render_ccc_arguments(ccc_results, model_ids))
    lines.extend(_render_executive_summary(claim_data))
    lines.extend(_render_confidence_distribution(results, model_ids))
    lines.extend(_render_errors(errors))
    lines.append("---")
    lines.append("")
    lines.append("*Generated by GRIP Falsification Council Protocol*")

    return "\n".join(lines)




if __name__ == "__main__":
    main()
