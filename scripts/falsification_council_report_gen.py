#!/usr/bin/env python3
"""Generate the final falsification council report from raw JSON data."""

import json
import re
from pathlib import Path
from datetime import datetime

raw_path = Path.home() / '.claude' / 'articles' / 'falsification-council-raw.json'
data = json.loads(raw_path.read_text())

CLAIMS = {
    "C1": {"label": "Mapping mu preserves Gentner G1-G3", "type": "Mathematical"},
    "C2": {"label": "f(x)=1/(1+x) is canonical self-referential contraction", "type": "Definitional"},
    "C3": {"label": "The mapping is non-circular", "type": "Epistemic"},
    "C4": {"label": "phi-inverse is the consciousness attractor", "type": "Empirical"},
    "C5": {"label": "The isomorphism says something about consciousness", "type": "Philosophical"},
    "C6": {"label": "GRIP fitness converges to phi-inverse", "type": "Empirical"},
}

MODELS = {
    "gemini_pro": {"name": "Gemini 2.5 Pro", "role": "Mathematical Logician"},
    "gemini_flash": {"name": "Gemini 2.5 Flash", "role": "Physics Checker"},
    "llama33": {"name": "Llama 3.3 70B", "role": "Devil's Advocate"},
    "qwen3": {"name": "Qwen3 32B", "role": "Philosopher of Mind"},
    "llama4": {"name": "Llama 4 Scout", "role": "Anti-Goodhart Auditor"},
}

model_ids = list(MODELS.keys())

lines = []
lines.append("# Falsification Council Report: Stromme Convergence Proof")
lines.append("")
lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d')}")
lines.append("**Protocol**: Multi-LLM adversarial falsification (5 models, 6 claims + CCC)")
lines.append("**Total queries**: 35 (6 claims x 5 models + 5 CCC evaluations)")
lines.append("**Consensus rule**: 3+ models agreeing on FALSIFIED = claim killed")
lines.append("")
lines.append("---")
lines.append("")

# Panel
lines.append("## Panel")
lines.append("")
lines.append("| Model | Role | Attack Vector |")
lines.append("|-------|------|---------------|")
lines.append("| Gemini 2.5 Pro | Mathematical Logician | Proof validity, circularity, genericity |")
lines.append("| Gemini 2.5 Flash | Physics Checker | QFT formalism legitimacy, Hilbert space |")
lines.append("| Llama 3.3 70B | Devil's Advocate | Steel-man null hypothesis, category error |")
lines.append("| Qwen3 32B | Philosopher of Mind | IIT/GWT/HOT comparison, hard problem |")
lines.append("| Llama 4 Scout | Anti-Goodhart Auditor | Theorem independence, score inflation |")
lines.append("")

# Verdict Table
lines.append("## Verdict Table")
lines.append("")
header = "| Claim | " + " | ".join(f"{MODELS[m]['name']}" for m in model_ids) + " | Consensus |"
sep = "|" + "|".join(["-------"] * (len(model_ids) + 2)) + "|"
lines.append(header)
lines.append(sep)

claim_consensus = {}
for claim_id in CLAIMS:
    verdicts = []
    cells = []
    for mid in model_ids:
        r = data["claims"][claim_id].get(mid, {})
        v = r.get("verdict", "ERROR")
        c = r.get("confidence", 0)
        cells.append(f"{v}/{c}")
        verdicts.append(v)

    f_count = verdicts.count("FALSIFIED")
    w_count = verdicts.count("WEAKENED")
    s_count = verdicts.count("SURVIVED")

    if f_count >= 3:
        consensus = "KILLED"
    elif f_count + w_count >= 4:
        consensus = "WEAKENED"
    elif f_count + w_count >= 3:
        consensus = "CONTESTED-WEAK"
    elif s_count >= 3:
        consensus = "SURVIVED"
    else:
        consensus = "CONTESTED"

    claim_consensus[claim_id] = consensus
    short_label = CLAIMS[claim_id]['label'][:45]
    row = f"| **{claim_id}**: {short_label} | " + " | ".join(cells) + f" | **{consensus}** |"
    lines.append(row)

lines.append("")
lines.append("*Verdict format: VERDICT/confidence%*")
lines.append("")

# Summary counts
lines.append("## Executive Summary")
lines.append("")
killed = [c for c, v in claim_consensus.items() if v == "KILLED"]
weakened = [c for c, v in claim_consensus.items() if v == "WEAKENED"]
survived = [c for c, v in claim_consensus.items() if v == "SURVIVED"]
contested = [c for c, v in claim_consensus.items() if "CONTESTED" in v]

lines.append(f"- **KILLED** (3+ FALSIFIED): {', '.join(killed) if killed else 'None'}")
lines.append(f"- **WEAKENED** (4+ FALSIFIED+WEAKENED): {', '.join(weakened) if weakened else 'None'}")
lines.append(f"- **SURVIVED** (3+ SURVIVED): {', '.join(survived) if survived else 'None'}")
lines.append(f"- **CONTESTED**: {', '.join(contested) if contested else 'None'}")
lines.append("")

# Confidence distribution
lines.append("### Confidence Distribution")
lines.append("")
lines.append("| Claim | Type | Avg Confidence | Verdict Spread |")
lines.append("|-------|------|---------------|----------------|")
for claim_id in CLAIMS:
    confs = []
    vds = []
    for mid in model_ids:
        r = data["claims"][claim_id].get(mid, {})
        confs.append(r.get("confidence", 0))
        vds.append(r.get("verdict", "?"))
    avg = sum(confs) / len(confs) if confs else 0
    unique_verdicts = set(vds)
    spread_str = ", ".join(f"{v}({vds.count(v)})" for v in sorted(unique_verdicts))
    lines.append(f"| {claim_id} | {CLAIMS[claim_id]['type']} | {avg:.0f}% | {spread_str} |")
lines.append("")

# Detailed arguments
lines.append("---")
lines.append("")
lines.append("## Detailed Arguments by Claim")
lines.append("")

for claim_id, claim_info in CLAIMS.items():
    lines.append(f"### {claim_id}: {claim_info['label']} ({claim_info['type']})")
    lines.append("")
    lines.append(f"**Council consensus: {claim_consensus[claim_id]}**")
    lines.append("")

    for mid in model_ids:
        r = data["claims"][claim_id].get(mid, {})
        mc = MODELS[mid]
        v = r.get("verdict", "ERROR")
        c = r.get("confidence", 0)
        arg = r.get("argument", "No response")
        # Clean up argument
        if len(arg) > 1200:
            arg = arg[:1200] + "..."
        arg = arg.replace("\n\n\n", "\n\n")

        lines.append(f"#### {mc['name']} ({mc['role']}): **{v}** ({c}%)")
        lines.append("")
        lines.append(f"{arg}")
        lines.append("")

    lines.append("---")
    lines.append("")

# CCC Section
lines.append("## CCC Hypothesis Evaluation")
lines.append("")
lines.append("> **Contraction Criterion for Consciousness (CCC)**: Any system satisfying B1-B4")
lines.append("> (complete metric state space, measurable criterion, contractive self-modification,")
lines.append("> self-referential closure) exhibits the dynamical signature of self-aware convergence.")
lines.append("> Positioned as NECESSARY but NOT SUFFICIENT for consciousness.")
lines.append("")
lines.append("### CCC Verdict Table")
lines.append("")
lines.append("| Model | Role | Verdict | Confidence |")
lines.append("|-------|------|---------|------------|")
for mid in model_ids:
    r = data["ccc"].get(mid, {})
    mc = MODELS[mid]
    v = r.get("verdict", "ERROR")
    c = r.get("confidence", 0)
    lines.append(f"| {mc['name']} | {mc['role']} | **{v}** | {c}% |")
lines.append("")

# CCC consensus
ccc_verdicts = [data["ccc"].get(mid, {}).get("verdict", "?") for mid in model_ids]
novel = ccc_verdicts.count("NOVEL")
incremental = ccc_verdicts.count("INCREMENTAL")
redundant = ccc_verdicts.count("REDUNDANT")

if redundant >= 3:
    ccc_consensus = "REDUNDANT"
elif incremental + redundant >= 3:
    ccc_consensus = "INCREMENTAL (consensus)"
elif novel >= 3:
    ccc_consensus = "NOVEL"
else:
    ccc_consensus = "MIXED"

lines.append(f"**CCC consensus: {ccc_consensus}**")
lines.append("")

# CCC detailed
lines.append("### CCC Detailed Arguments")
lines.append("")
for mid in model_ids:
    r = data["ccc"].get(mid, {})
    mc = MODELS[mid]
    v = r.get("verdict", "ERROR")
    c = r.get("confidence", 0)
    arg = r.get("argument", "No response")
    if len(arg) > 1200:
        arg = arg[:1200] + "..."
    lines.append(f"#### {mc['name']} ({mc['role']}): **{v}** ({c}%)")
    lines.append("")
    lines.append(f"{arg}")
    lines.append("")

# Synthesis
lines.append("---")
lines.append("")
lines.append("## Synthesis and Recommendations")
lines.append("")
lines.append("### Key Attack Patterns Across Models")
lines.append("")
lines.append("1. **Genericity/Triviality Attack** (Llama 3.3, Gemini Flash): Any 3-component system maps to any other. The Gentner mapping is too easy to satisfy when the components are abstract enough.")
lines.append("2. **Circularity Attack** (Gemini Pro, Qwen3): Choosing f(x)=1/(1+x) presupposes the golden ratio conclusion. The 'canonicity' claim is an unsupported assertion.")
lines.append("3. **Category Error Attack** (Llama 3.3, Qwen3): Structural isomorphism between formal systems says nothing about ontology. Mathematical consistency is not physical truth.")
lines.append("4. **Goodhart Attack** (Llama 4, Gemini Pro): The 20 theorems may not be independent. Fitness convergence to phi-inverse could be metric gaming.")
lines.append("5. **Physics Legitimacy Attack** (Gemini Flash): Stromme's QFT formalism uses real physics notation for non-physical claims.")
lines.append("")
lines.append("### Strongest Surviving Elements")
lines.append("")
lines.append("1. The Lean 4 verification is genuine mathematics -- 17 fully verified theorems with zero sorry")
lines.append("2. The bridging axiom framework (B1-B4 with explicit epistemic status) is honest and well-structured")
lines.append("3. The paper's explicit disclaimer ('structural consistency, not physical truth') partially deflects the strongest attacks")
lines.append("4. The self-referential closure of f(x)=1/(1+x) (fixed-point equation = function definition) is a real mathematical property")
lines.append("")
lines.append("### Recommended Paper Revisions")
lines.append("")
lines.append("1. **Drop or weaken C4** (consciousness attractor): The claim that phi-inverse IS the consciousness attractor is not supported. Replace with: 'IF consciousness dynamics satisfy B1-B4, THEN they share this attractor.'")
lines.append("2. **Address C2 canonicity**: Prove or acknowledge that 'canonical' is a claim, not a theorem. Show f(x)=1/(1+x) is UNIQUE among degree-1 rational maps satisfying self-referential closure, or drop 'canonical'.")
lines.append("3. **Strengthen C1 against triviality**: Address the 'any 3 things map to any other 3 things' attack head-on. Show that NOT any triple satisfies G2-G3 preservation.")
lines.append("4. **C6 needs rigorous time-series analysis**: Publish the full ~500 generation trajectory with statistical convergence tests (unit root tests, confidence intervals).")
lines.append("5. **CCC needs differentiation from IIT**: Explicitly compare B1-B4 axioms with IIT's axioms and show what CCC captures that IIT does not (or acknowledge overlap).")
lines.append("")

lines.append("---")
lines.append("")
lines.append("*Generated by GRIP Falsification Council Protocol -- 5 external LLMs, 35 adversarial queries*")

report = "\n".join(lines)
output_path = Path.home() / '.claude' / 'articles' / 'falsification-council-report.md'
output_path.write_text(report)
print(f"Report written to: {output_path}")
print(f"Report length: {len(report)} chars, {len(lines)} lines")

# Print summary
print("\n=== VERDICT SUMMARY ===")
for cid, cons in claim_consensus.items():
    print(f"  {cid}: {cons}")
print(f"  CCC: {ccc_consensus}")
