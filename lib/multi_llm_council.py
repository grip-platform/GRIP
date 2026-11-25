"""Multi-LLM Falsification Council — reusable library.

Queries multiple LLM providers to attack claims adversarially.
Used for Popperian falsification of any set of claims.

Usage:
    from lib.multi_llm_council import Council, Claim

    claims = [
        Claim("C1", "Structural Isomorphism", "Mathematical",
              "The mapping preserves Gentner G1-G3..."),
    ]
    council = Council(context="Paper about consciousness convergence")
    results = council.evaluate(claims)

    # Matrix walk over the claims x models verdict grid (O(claims*models) — the
    # minimal cost to visit each cell once; not a quadratic over one collection).
    for claim_id, verdicts in results.items():
        for model, v in verdicts.items():
            print(f"{claim_id} x {model}: {v['verdict']} ({v['confidence']}%)")

CLI:
    python3 lib/multi_llm_council.py --claims claims.json --context "..."
    python3 lib/multi_llm_council.py --claim "CCC is novel" --context "..."
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))


@dataclass
class Claim:
    """A claim to be evaluated by the council."""
    id: str
    label: str
    claim_type: str  # Mathematical, Empirical, Epistemic, etc.
    text: str


@dataclass
class Verdict:
    """A single model's verdict on a claim."""
    verdict: str  # SURVIVED/WEAKENED/FALSIFIED or NOVEL/INCREMENTAL/REDUNDANT
    confidence: int
    argument: str
    raw: str = ""
    model: str = ""
    error: Optional[str] = None


@dataclass
class CouncilResult:
    """Complete council results for all claims."""
    claims: dict[str, dict[str, Verdict]] = field(default_factory=dict)
    timestamp: str = ""
    context: str = ""
    kill_threshold: int = 3

    def is_killed(self, claim_id: str, kill_verdicts: tuple = ("FALSIFIED", "UNFALSIFIABLE", "NOT_NOVEL")) -> bool:
        """Check if a claim meets the kill condition."""
        if claim_id not in self.claims:
            return False
        kill_count = sum(
            1 for v in self.claims[claim_id].values()
            if v.verdict in kill_verdicts or v.error is not None
        )
        return kill_count >= self.kill_threshold

    def summary_table(self) -> str:
        """Generate a markdown summary table."""
        from collections import Counter
        lines = ["| Claim | Verdict | Models |", "|-------|---------|--------|"]
        for claim_id, verdicts in self.claims.items():
            counts = Counter(v.verdict for v in verdicts.values())
            summary = ", ".join(f"{v}:{c}" for v, c in sorted(counts.items()))
            killed = "KILLED" if self.is_killed(claim_id) else "ALIVE"
            lines.append(f"| {claim_id} | {killed} | {summary} |")
        return "\n".join(lines)


# ── Model Configuration ──────────────────────────────────────────────

DEFAULT_MODELS = {
    "gemini_flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "role": "empirical scientist",
        "attack": "Find the weakest empirical assumption",
    },
    "gemini_pro": {
        "name": "Gemini 3.1 Pro",
        "provider": "gemini",
        "model": "gemini-3.1-pro-preview",
        "role": "mathematical logician",
        "attack": "Find the logical gap or hidden assumption",
    },
    "llama33": {
        "name": "Llama 3.3 70B",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "role": "philosophical critic",
        "attack": "Is this genuinely novel or repackaged existing theory?",
    },
    "gpt_oss": {
        "name": "GPT-OSS 120B",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "role": "Popperian falsificationist",
        "attack": "Is this falsifiable? What observation would refute it?",
    },
    "llama4": {
        # llama-4-scout retired at Groq (2026-08-30) — qwen3.8-27b keeps the
        # fast/open slot on the same hardware.
        "name": "Qwen 3.8 27B",
        "provider": "groq",
        "model": "qwen/qwen3.8-27b",
        "role": "interdisciplinary reviewer",
        "attack": "Does this hold up across domains?",
    },
}


# ── Prompt Building ──────────────────────────────────────────────────

def build_attack_prompt(claim: Claim, model_config: dict, context: str) -> str:
    """Build adversarial prompt for a claim."""
    return (
        f"You are a {model_config['role']} on a falsification council. Your job is to "
        f"find the STRONGEST possible argument AGAINST this claim. Do NOT be charitable. "
        f"Find the fatal flaw.\n\n"
        f"CLAIM ({claim.claim_type}): {claim.text}\n\n"
        f"CONTEXT: {context}\n\n"
        f"Your SPECIFIC attack vector: {model_config['attack']}\n\n"
        f"Give your verdict: SURVIVED / WEAKENED / FALSIFIED\n"
        f"Give your confidence: 0-100\n"
        f"Give your strongest argument in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [SURVIVED|WEAKENED|FALSIFIED]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your argument]"
    )


def build_novelty_prompt(claim: Claim, model_config: dict, context: str) -> str:
    """Build novelty evaluation prompt."""
    return (
        f"You are a {model_config['role']} evaluating a novel hypothesis.\n\n"
        f"HYPOTHESIS: {claim.text}\n\n"
        f"CONTEXT: {context}\n\n"
        f"Your SPECIFIC evaluation angle: {model_config['attack']}\n\n"
        f"Evaluate whether this adds anything genuinely novel. Be rigorous.\n\n"
        f"Give your verdict: NOVEL / INCREMENTAL / REDUNDANT\n"
        f"Give your confidence: 0-100\n"
        f"Give your assessment in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [NOVEL|INCREMENTAL|REDUNDANT]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your assessment]"
    )


def build_evaluate_prompt(claim: Claim, model_config: dict, context: str) -> str:
    """Build balanced evaluation prompt (for null hypotheses and conservative claims).

    Unlike attack mode, evaluate mode asks the model to assess the claim fairly
    rather than adversarially. This prevents the reasoning trap where instruction-
    tuned models comply with "attack" instructions while their reasoning supports
    the claim.

    Origin: CCC council insight (Gen 487, learned-insights.md).
    """
    return (
        f"You are a {model_config['role']} evaluating a claim with balanced rigour.\n\n"
        f"CLAIM ({claim.claim_type}): {claim.text}\n\n"
        f"CONTEXT: {context}\n\n"
        f"Your SPECIFIC evaluation angle: {model_config['attack']}\n\n"
        f"Evaluate this claim FAIRLY. Consider both supporting and opposing evidence. "
        f"Do not assume the claim is wrong — assess it on its merits.\n\n"
        f"Give your verdict: SURVIVED / WEAKENED / FALSIFIED\n"
        f"Give your confidence: 0-100\n"
        f"Give your balanced assessment in 200 words max.\n\n"
        f"Format your response EXACTLY as:\n"
        f"VERDICT: [SURVIVED|WEAKENED|FALSIFIED]\n"
        f"CONFIDENCE: [0-100]\n"
        f"ARGUMENT: [your assessment]"
    )


# ── Response Parsing ─────────────────────────────────────────────────

_VERDICT_VALUES = (
    "FALSIFIED", "WEAKENED", "SURVIVED", "NOVEL", "INCREMENTAL",
    "REDUNDANT", "UNFALSIFIABLE", "NOT_NOVEL",
)


def _extract_verdict(raw: str) -> str:
    """Extract verdict keyword from raw verdict line."""
    upper = raw.upper()
    for v in _VERDICT_VALUES:
        if v in upper:
            return v
    return "UNKNOWN"


def _extract_confidence(raw: str) -> int:
    """Extract confidence integer from raw confidence line."""
    match = re.search(r'\d+', raw)
    return min(100, int(match.group())) if match else 0


def _strip_thinking_blocks(text: str) -> str:
    """Remove Qwen3-style <think> blocks."""
    if "<think>" in text:
        return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return text


def parse_response(text: str) -> tuple[str, int, str]:
    """Parse verdict, confidence, and argument from model response."""
    text = _strip_thinking_blocks(text)

    verdict = "UNKNOWN"
    confidence = 0
    argument = ""

    lines = text.strip().split('\n')
    for i, line in enumerate(lines):
        line_upper = line.strip().upper()
        if line_upper.startswith("VERDICT:"):
            verdict = _extract_verdict(line.split(":", 1)[1])
        elif line_upper.startswith("CONFIDENCE:"):
            confidence = _extract_confidence(line.split(":", 1)[1])
        elif line_upper.startswith("ARGUMENT:"):
            argument = line.split(":", 1)[1].strip()
            remaining = "\n".join(lines[i + 1:]).strip()
            if remaining:
                argument += "\n" + remaining
            break

    if not argument:
        argument = text[-500:] if len(text) > 500 else text

    return verdict, confidence, argument


# ── Council Engine ───────────────────────────────────────────────────

class Council:
    """Multi-LLM falsification council."""

    def __init__(
        self,
        context: str = "",
        models: Optional[dict] = None,
        kill_threshold: int = 3,
        prompt_mode: str = "attack",  # "attack" or "novelty"
        rate_limit_delay: float = 1.5,
    ):
        self.context = context
        self.models = models or DEFAULT_MODELS
        self.kill_threshold = kill_threshold
        self.prompt_mode = prompt_mode
        self.rate_limit_delay = rate_limit_delay
        self._gemini = None
        self._groq = None

    def _get_gemini(self):
        if self._gemini is None:
            from providers.gemini import GeminiProvider
            self._gemini = GeminiProvider()
        return self._gemini

    def _get_groq(self):
        if self._groq is None:
            from providers.groq import GroqProvider
            self._groq = GroqProvider()
        return self._groq

    def _query(self, model_config: dict, prompt: str) -> str:
        """Query a model and return raw text."""
        if model_config["provider"] == "gemini":
            result = self._get_gemini().generate(
                prompt, model=model_config["model"],
                temperature=0.3, max_tokens=1024
            )
        else:
            model = model_config["model"]
            if "qwen" in model:
                result = self._get_groq().generate_reasoning(prompt, max_tokens=4096)
            elif "llama-4" in model:
                result = self._get_groq().generate_llama4(prompt, max_tokens=4096)
            else:
                result = self._get_groq().generate(
                    prompt, model=model, temperature=0.3, max_tokens=2048
                )
        return result.text

    def evaluate(
        self,
        claims: list[Claim],
        verbose: bool = True,
    ) -> CouncilResult:
        """Evaluate all claims across all models.

        Returns CouncilResult with verdicts per claim per model.
        """
        result = CouncilResult(
            timestamp=datetime.now().isoformat(),
            context=self.context,
            kill_threshold=self.kill_threshold,
        )

        prompt_builders = {
            "attack": build_attack_prompt,
            "novelty": build_novelty_prompt,
            "evaluate": build_evaluate_prompt,
        }
        build_prompt = prompt_builders.get(self.prompt_mode, build_attack_prompt)

        for claim in claims:
            result.claims[claim.id] = {}
            for model_id, model_config in self.models.items():
                prompt = build_prompt(claim, model_config, self.context)

                if verbose:
                    print(f"  [{claim.id}] x [{model_config['name']}] ... ",
                          end="", flush=True)

                try:
                    raw = self._query(model_config, prompt)
                    verdict_str, confidence, argument = parse_response(raw)

                    # Format retry: if UNKNOWN, re-prompt for structured output
                    if verdict_str == "UNKNOWN" and raw.strip():
                        retry_prompt = (
                            f"Your previous response did not follow the required format. "
                            f"Based on your analysis below, respond ONLY with these three lines:\n\n"
                            f"VERDICT: [SURVIVED|WEAKENED|FALSIFIED]\n"
                            f"CONFIDENCE: [0-100]\n"
                            f"ARGUMENT: [one sentence summary]\n\n"
                            f"Your analysis was:\n{raw[:1000]}"
                        )
                        try:
                            retry_raw = self._query(model_config, retry_prompt)
                            retry_verdict, retry_conf, retry_arg = parse_response(retry_raw)
                            if retry_verdict != "UNKNOWN":
                                verdict_str = retry_verdict
                                confidence = retry_conf
                                argument = retry_arg or argument
                                if verbose:
                                    print(f"(retry) ", end="", flush=True)
                        except Exception:
                            pass  # Retry failed, use original

                    v = Verdict(
                        verdict=verdict_str,
                        confidence=confidence,
                        argument=argument,
                        raw=raw,
                        model=model_config["name"],
                    )
                    if verbose:
                        print(f"{verdict_str} ({confidence}%)")

                except Exception as e:
                    v = Verdict(
                        verdict="ERROR",
                        confidence=0,
                        argument=str(e),
                        model=model_config["name"],
                        error=str(e),
                    )
                    if verbose:
                        print(f"ERROR: {e}")

                result.claims[claim.id][model_id] = v

                # Rate limit
                delay = self.rate_limit_delay if model_config["provider"] == "groq" else 0.5
                time.sleep(delay)

        return result

    def save_results(self, result: CouncilResult, output_path: Path) -> None:
        """Save results as JSON."""
        data = {
            "timestamp": result.timestamp,
            "context": result.context,
            "kill_threshold": result.kill_threshold,
            "claims": {},
        }
        for claim_id, verdicts in result.claims.items():
            data["claims"][claim_id] = {
                model_id: {
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                    "argument": v.argument,
                    "model": v.model,
                    "error": v.error,
                }
                for model_id, v in verdicts.items()
            }
        output_path.write_text(json.dumps(data, indent=2))


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    """CLI entry point for ad-hoc council evaluation."""
    import argparse

    parser = argparse.ArgumentParser(description="Multi-LLM Falsification Council")
    parser.add_argument("--claim", help="Single claim text to evaluate")
    parser.add_argument("--claims", help="Path to JSON file with claims array")
    parser.add_argument("--context", default="", help="Context for the evaluation")
    parser.add_argument("--mode", choices=["attack", "novelty", "evaluate"], default="attack")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--kill-threshold", type=int, default=3)
    args = parser.parse_args()

    claims = []
    if args.claim:
        claims.append(Claim("C1", "Ad-hoc claim", "General", args.claim))
    elif args.claims:
        with open(args.claims) as f:
            raw = json.load(f)
        for i, c in enumerate(raw):
            claims.append(Claim(
                c.get("id", f"C{i+1}"),
                c.get("label", f"Claim {i+1}"),
                c.get("type", "General"),
                c["text"],
            ))
    else:
        parser.error("Provide --claim or --claims")

    council = Council(
        context=args.context,
        prompt_mode=args.mode,
        kill_threshold=args.kill_threshold,
    )

    print(f"Council: {len(claims)} claims x {len(council.models)} models")
    print(f"Mode: {args.mode} | Kill threshold: {args.kill_threshold}")
    print("=" * 60)

    result = council.evaluate(claims)

    print("\n" + "=" * 60)
    print(result.summary_table())

    if args.output:
        council.save_results(result, Path(args.output))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
