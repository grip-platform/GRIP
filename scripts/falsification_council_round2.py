#!/usr/bin/env python3
"""
Falsification Council Round 2: Three novel sections of the Stromme convergence proof.

Claims: CCC Experimental Battery, Unified Theory Framework, Failure Mode Analysis.
Kill condition: 3+ models say "not novel" or "unfalsifiable" -> claim KILLED.
"""

import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path.home() / '.claude' / 'lib'))
from providers.gemini import GeminiProvider
from providers.groq import GroqProvider

# --- Claims (the 3 novel sections) ---

CLAIMS = {
    "N1": {
        "label": "CCC Experimental Battery",
        "type": "Empirical/Experimental",
        "text": (
            "Three experimental protocols (fMRI oscillation at phi^-2 frequency, EEG spectral "
            "gap at 0.618, meditation entropy reduction) can empirically falsify the Contraction "
            "Criterion for Consciousness. Each protocol has specific equipment, sample sizes, and "
            "numerical falsification thresholds that distinguish CCC from IIT, GWT, HOT, and PP."
        ),
    },
    "N2": {
        "label": "Unified Theory Framework",
        "type": "Theoretical/Metatheoretical",
        "text": (
            "IIT, GWT, HOT, and Predictive Processing are different coordinate systems on the "
            "same (S,P,I) phase space. CCC provides the coordinate-free contraction criterion. "
            "Each theory implicitly specifies a state space, criterion function, and iterative "
            "refinement expressible as converge(s0, P_T, I_T)."
        ),
    },
    "N3": {
        "label": "Failure Mode Analysis",
        "type": "Epistemic/Methodological",
        "text": (
            "The paper's claims degrade gracefully through 4 independent failure modes (F1-F4). "
            "Even if all failure modes trigger, the paper still contributes novel mathematics of "
            "self-referential contractions."
        ),
    },
}

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
    "epistemic status. The paper claims structural consistency, not physical truth. "
    "Three NEW SECTIONS are being evaluated: (1) CCC Experimental Battery proposing "
    "three falsifiable neuroscience experiments, (2) Unified Theory Framework mapping "
    "IIT/GWT/HOT/PP as coordinate systems on a shared phase space, (3) Failure Mode "
    "Analysis showing graceful degradation through 4 independent failure modes."
)

MODELS = {
    "gemini_pro": {
        "name": "Gemini 2.5 Pro",
        "role": "Experimental Methodology Critic",
        "attack": (
            "Focus on experimental design: Are the proposed experiments actually feasible? "
            "Are the sample sizes adequate? Would the specific numerical thresholds (phi^-2 "
            "frequency, 0.618 spectral gap) actually distinguish CCC from noise? Are the "
            "equipment specifications realistic? Could IIT or GWT equally predict these results?"
        ),
        "provider": "gemini",
        "model": "gemini-2.5-pro",
    },
    "gemini_flash": {
        "name": "Gemini 2.5 Flash",
        "role": "Physics & Neuroscience Checker",
        "attack": (
            "Focus on neuroscience plausibility: Is 'fMRI oscillation at phi^-2 frequency' "
            "physically meaningful? Does EEG actually have discrete spectral gaps? Is 'entropy "
            "reduction during meditation' already well-studied without needing CCC? Would a "
            "neuroscientist find these protocols publishable? Are the specific golden-ratio-"
            "based thresholds numerology dressed as science?"
        ),
        "provider": "gemini",
        "model": "gemini-2.5-flash",
    },
    "llama33": {
        "name": "Llama 3.3 70B",
        "role": "Devil's Advocate",
        "attack": (
            "Steel-man the null hypothesis for ALL three claims: (1) The experiments are "
            "unfalsifiable because any negative result can be explained by 'wrong parameters'. "
            "(2) The unified framework is trivially true -- any theory can be expressed in any "
            "sufficiently abstract coordinate system. (3) The failure mode analysis is a "
            "rhetorical trick -- claiming 'even if wrong, still useful' makes the paper "
            "unfalsifiable. Attack the NOVELTY of each claim."
        ),
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
    },
    "qwen3": {
        "name": "Qwen3 32B",
        "role": "Philosopher of Science",
        "attack": (
            "Focus on falsifiability and novelty: (1) Are the experimental protocols genuinely "
            "falsifiable or do they have escape hatches? (2) Is the unified framework novel or "
            "is this just Lakatos's research programmes / Kuhn's paradigm theory in new notation? "
            "(3) Does the failure mode analysis make CCC unfalsifiable? Compare rigorously against "
            "existing theory unification attempts in philosophy of mind."
        ),
        "provider": "groq",
        "model": "qwen/qwen3-32b",
    },
    "llama4": {
        "name": "Llama 4 Scout",
        "role": "Anti-Goodhart Auditor",
        "attack": (
            "Focus on whether these sections are genuine contributions or impression management: "
            "(1) Do the experiments actually test CCC or just test known neuroscience predictions? "
            "(2) Is the coordinate-system metaphor doing real mathematical work or is it just "
            "relabelling? (3) Is the failure mode analysis an honest epistemic assessment or a "
            "rhetorical device to make the paper seem more rigorous than it is?"
        ),
        "provider": "groq",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
}


def build_prompt(claim_id, claim, model_config):
    """Build the falsification prompt for a specific claim + model."""
    return (
        f"You are a {model_config['role']} on a falsification council. Your job is to "
        f"find the STRONGEST possible argument AGAINST this claim. Do NOT be charitable. "
        f"Find the fatal flaw.\n\n"
        f"CLAIM ({claim['type']}): {claim['text']}\n\n"
        f"CONTEXT: {CONTEXT}\n\n"
        f"Your SPECIFIC attack vector: {model_config['attack']}\n\n"
        f"Evaluate this claim on TWO axes:\n"
        f"1. VALIDITY: Is the claim logically sound and falsifiable?\n"
        f"2. NOVELTY: Does this claim add something genuinely new beyond existing literature?\n\n"
        f"Give your verdict: SURVIVED / WEAKENED / FALSIFIED\n"
        f"Give your novelty assessment: NOVEL / INCREMENTAL / NOT_NOVEL\n"
        f"Give your falsifiability assessment: FALSIFIABLE / UNFALSIFIABLE\n"
        f"Give your confidence: 0-100\n"
        f"Give your strongest argument in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [SURVIVED|WEAKENED|FALSIFIED]\n"
        f"NOVELTY: [NOVEL|INCREMENTAL|NOT_NOVEL]\n"
        f"FALSIFIABILITY: [FALSIFIABLE|UNFALSIFIABLE]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your argument]"
    )


def parse_response(text):
    """Parse verdict, novelty, falsifiability, confidence, and argument."""
    verdict = "UNKNOWN"
    novelty = "UNKNOWN"
    falsifiability = "UNKNOWN"
    confidence = 0
    argument = ""

    # Handle Qwen3's <think> blocks
    if "<think>" in text:
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    lines = text.strip().split('\n')
    for i, line in enumerate(lines):
        line_upper = line.strip().upper()
        if line_upper.startswith("VERDICT:"):
            raw = line.strip().split(":", 1)[1].strip().upper()
            for v in ["FALSIFIED", "WEAKENED", "SURVIVED"]:
                if v in raw:
                    verdict = v
                    break
        elif line_upper.startswith("NOVELTY:"):
            raw = line.strip().split(":", 1)[1].strip().upper()
            for v in ["NOT_NOVEL", "NOT NOVEL", "INCREMENTAL", "NOVEL"]:
                if v in raw:
                    novelty = "NOT_NOVEL" if "NOT" in v else v
                    break
        elif line_upper.startswith("FALSIFIABILITY:") or line_upper.startswith("FALSIFIABLE:"):
            raw = line.strip().split(":", 1)[1].strip().upper()
            if "UNFALSIFIABLE" in raw:
                falsifiability = "UNFALSIFIABLE"
            elif "FALSIFIABLE" in raw:
                falsifiability = "FALSIFIABLE"
        elif line_upper.startswith("CONFIDENCE:"):
            try:
                conf_str = re.search(r'\d+', line.split(":", 1)[1])
                if conf_str:
                    confidence = int(conf_str.group())
            except (ValueError, IndexError):
                pass
        elif line_upper.startswith("ARGUMENT:"):
            argument = line.split(":", 1)[1].strip()
            argument += "\n" + "\n".join(lines[i+1:])
            argument = argument.strip()
            break

    if not argument:
        argument = text[-500:] if len(text) > 500 else text

    return verdict, novelty, falsifiability, confidence, argument


def query_model(gemini, groq, model_config, prompt):
    """Query a model via the appropriate provider."""
    if model_config["provider"] == "gemini":
        result = gemini.generate(prompt, model=model_config["model"], temperature=0.3, max_tokens=1024)
    elif model_config["model"] == "qwen/qwen3-32b":
        result = groq.generate_reasoning(prompt, max_tokens=4096)
    elif model_config["model"] == "meta-llama/llama-4-scout-17b-16e-instruct":
        result = groq.generate_llama4(prompt, max_tokens=4096)
    else:
        result = groq.generate(prompt, model=model_config["model"], temperature=0.3, max_tokens=2048)
    return result.text


def main():
    print("=" * 70)
    print("FALSIFICATION COUNCIL ROUND 2: Novel Sections")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Claims: {len(CLAIMS)} | Models: {len(MODELS)} | Total queries: {len(CLAIMS) * len(MODELS)}")
    print(f"Kill condition: 3+ models say NOT_NOVEL or UNFALSIFIABLE -> KILLED")
    print("=" * 70)

    gemini = GeminiProvider()
    groq = GroqProvider()

    results = {}
    errors = []

    for claim_id, claim in CLAIMS.items():
        results[claim_id] = {}
        for model_id, model_config in MODELS.items():
            prompt = build_prompt(claim_id, claim, model_config)
            print(f"\n[{claim_id}] x [{model_config['name']}] ... ", end="", flush=True)

            try:
                raw = query_model(gemini, groq, model_config, prompt)
                verdict, novelty, falsifiability, confidence, argument = parse_response(raw)
                results[claim_id][model_id] = {
                    "verdict": verdict,
                    "novelty": novelty,
                    "falsifiability": falsifiability,
                    "confidence": confidence,
                    "argument": argument,
                    "raw": raw,
                }
                print(f"{verdict} | Novelty:{novelty} | Falsif:{falsifiability} | Conf:{confidence}%")

            except Exception as e:
                error_msg = f"{claim_id} x {model_config['name']}: {e}"
                errors.append(error_msg)
                results[claim_id][model_id] = {
                    "verdict": "ERROR",
                    "novelty": "ERROR",
                    "falsifiability": "ERROR",
                    "confidence": 0,
                    "argument": str(e),
                    "raw": "",
                }
                print(f"ERROR: {e}")

            if model_config["provider"] == "groq":
                time.sleep(1.5)
            else:
                time.sleep(0.5)

    # --- Generate Report ---
    report = generate_report(results, errors)

    output_path = Path.home() / '.claude' / 'articles' / 'round2-council-report.md'
    output_path.write_text(report)
    print(f"\n\nReport written to: {output_path}")

    raw_path = Path.home() / '.claude' / 'articles' / 'round2-council-raw.json'
    raw_data = {
        "timestamp": datetime.now().isoformat(),
        "claims": results,
        "errors": errors,
    }
    raw_path.write_text(json.dumps(raw_data, indent=2, default=str))
    print(f"Raw data written to: {raw_path}")


def _render_header(now):
    """Title block: report name, run date, protocol, query count, kill rule."""
    return [
        "# Falsification Council Round 2: Novel Sections",
        "",
        f"**Date**: {now}",
        "**Protocol**: Multi-LLM adversarial falsification",
        f"**Total queries**: {len(CLAIMS) * len(MODELS)} (3 claims x 5 models)",
        "**Kill condition**: 3+ models say NOT_NOVEL or UNFALSIFIABLE = claim KILLED",
        "",
    ]


def _render_panel():
    """Table of the models sitting on the council, with role and provider."""
    lines = ["## Panel", "", "| Model | Role | Provider |", "|-------|------|----------|"]
    for _model_id, mc in MODELS.items():
        lines.append(f"| {mc['name']} | {mc['role']} | {mc['provider'].capitalize()} |")
    lines.append("")
    return lines


def _claim_votes(results, claim_id, model_ids):
    """One claim's per-model cells plus the two counts the kill rule needs."""
    cells = []
    not_novel_count = 0
    unfalsifiable_count = 0
    for model_id in model_ids:
        r = results[claim_id].get(model_id, {})
        v = r.get("verdict", "ERROR")
        n = r.get("novelty", "UNKNOWN")
        f = r.get("falsifiability", "UNKNOWN")
        cells.append(f"{v} N:{n} F:{f}")
        if n == "NOT_NOVEL":
            not_novel_count += 1
        if f == "UNFALSIFIABLE":
            unfalsifiable_count += 1
    return cells, not_novel_count, unfalsifiable_count


def _kill_status(not_novel_count, unfalsifiable_count):
    """Apply the kill rule: 3+ NOT_NOVEL or 3+ UNFALSIFIABLE kills the claim."""
    kill_reasons = []
    if not_novel_count >= 3:
        kill_reasons.append(f"NOT_NOVEL x{not_novel_count}")
    if unfalsifiable_count >= 3:
        kill_reasons.append(f"UNFALSIFIABLE x{unfalsifiable_count}")
    if kill_reasons:
        return f"KILLED ({', '.join(kill_reasons)})"
    return f"ALIVE (NOT_NOVEL:{not_novel_count}, UNFALS:{unfalsifiable_count})"


def _classify_claims(results, model_ids):
    """Single pass over the claims: cells and kill status for each."""
    data = {}
    for claim_id in CLAIMS:
        cells, not_novel_count, unfalsifiable_count = _claim_votes(results, claim_id, model_ids)
        data[claim_id] = {
            "cells": cells,
            "status": _kill_status(not_novel_count, unfalsifiable_count),
        }
    return data


def _render_verdict_table(model_ids, claim_data):
    """Verdict grid: one row per claim, one column per model, plus kill check."""
    header = "| Claim | " + " | ".join(MODELS[m]["name"] for m in model_ids) + " | Kill Check |"
    sep = "|-------|" + "|".join(["-------"] * len(model_ids)) + "|-----------|"
    lines = ["## Verdict Table", "", header, sep]
    for claim_id, data in claim_data.items():
        lines.append(
            f"| {claim_id}: {CLAIMS[claim_id]['label'][:35]} | "
            + " | ".join(data["cells"])
            + f" | **{data['status']}** |"
        )
    lines.append("")
    return lines


def _render_detailed_arguments(results, model_ids, claim_data):
    """Each claim: its text, status, and every model's full argument."""
    lines = ["## Detailed Arguments", ""]
    for claim_id, claim in CLAIMS.items():
        lines.append(f"### {claim_id}: {claim['label']} ({claim['type']})")
        lines.append("")
        lines.append(f"**Status: {claim_data[claim_id]['status']}**")
        lines.append("")
        lines.append(f"> {claim['text']}")
        lines.append("")
        for model_id in model_ids:
            r = results[claim_id].get(model_id, {})
            mc = MODELS[model_id]
            v = r.get("verdict", "ERROR")
            n = r.get("novelty", "UNKNOWN")
            f = r.get("falsifiability", "UNKNOWN")
            c = r.get("confidence", 0)
            arg = r.get("argument", "No response")
            if len(arg) > 1000:
                arg = arg[:1000] + "..."
            lines.append(
                f"**{mc['name']}** ({mc['role']}): **{v}** | Novelty: {n} "
                f"| Falsifiability: {f} | Confidence: {c}%"
            )
            lines.append("")
            lines.append(f"{arg}")
            lines.append("")
        lines.append("---")
        lines.append("")
    return lines


def _render_executive_summary(claim_data):
    """Which claims were killed and which survived."""
    killed = [cid for cid, d in claim_data.items() if "KILLED" in d["status"]]
    alive = [cid for cid, d in claim_data.items() if "ALIVE" in d["status"]]
    killed_str = ", ".join(f"{c} ({CLAIMS[c]['label']})" for c in killed) if killed else "None"
    alive_str = ", ".join(f"{c} ({CLAIMS[c]['label']})" for c in alive) if alive else "None"
    return [
        "## Executive Summary",
        "",
        f"- **KILLED**: {killed_str}",
        f"- **ALIVE**: {alive_str}",
        "",
    ]


def _vote_tally(values):
    """Count each distinct vote, rendered as a stable 'VALUE:n' list."""
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))


def _render_confidence_distribution(results, model_ids):
    """Average confidence per claim, with the novelty and falsifiability spread."""
    lines = [
        "### Confidence Distribution",
        "",
        "| Claim | Avg Confidence | Novelty Votes | Falsifiability Votes |",
        "|-------|---------------|---------------|---------------------|",
    ]
    for claim_id in CLAIMS:
        confs = [results[claim_id][m].get("confidence", 0) for m in model_ids]
        novelties = [results[claim_id][m].get("novelty", "?") for m in model_ids]
        falsifs = [results[claim_id][m].get("falsifiability", "?") for m in model_ids]
        avg = sum(confs) / len(confs) if confs else 0
        lines.append(
            f"| {claim_id} | {avg:.0f}% | {_vote_tally(novelties)} | {_vote_tally(falsifs)} |"
        )
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


def generate_report(results, errors):
    """Generate the markdown report."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    model_ids = list(MODELS.keys())
    claim_data = _classify_claims(results, model_ids)

    lines = []
    lines.extend(_render_header(now))
    lines.extend(_render_panel())
    lines.extend(_render_verdict_table(model_ids, claim_data))
    lines.extend(_render_detailed_arguments(results, model_ids, claim_data))
    lines.extend(_render_executive_summary(claim_data))
    lines.extend(_render_confidence_distribution(results, model_ids))
    lines.extend(_render_errors(errors))
    lines.append("---")
    lines.append("")
    lines.append("*Generated by GRIP Falsification Council Protocol (Round 2)*")

    return "\n".join(lines)




if __name__ == "__main__":
    main()
