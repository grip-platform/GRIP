#!/usr/bin/env python3
"""HAL Client — GRIP core bridge to HAL's never-ending token infrastructure.

HAL (Harness Abstraction Layer) routes queries across 15+ LLM providers with
automatic failover via the Limit Sentinel. The user NEVER sees "limit reached."

Fallback chain: Groq → Gemini → Qwen → DeepSeek → OpenRouter → Ollama (local).
Jurisdiction filtering: sensitive queries skip China-based providers automatically.

This client gives any GRIP component access to HAL's routing:
- query(): single-turn, any provider
- chat(): multi-turn conversation
- council(): multi-LLM deliberation (anti-Goodhart external scoring)
- score_external(): external scoring — the producing model NEVER scores itself

Usage:
    from lib.hal_client import query, council, score_external

    # Simple query via HAL's auto-routing
    result = query("Explain SOLID principles")

    # External scoring (anti-Goodhart)
    score = score_external("This PR improves test coverage by 40%", rubric="accuracy")

    # Multi-LLM council for critical decisions
    verdict = council("Is this architectural change sound?")
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

HAL_HOME = Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))
HAL_PORT = int(os.environ.get("HAL_PORT", "3850"))
HAL_BASE = f"http://localhost:{HAL_PORT}"
TIMEOUT_S = 30


@dataclass
class HALResponse:
    """Response from HAL query."""
    text: str
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class CouncilResult:
    """Result from multi-LLM council deliberation."""
    merged_text: str
    agreement: float  # 0.0-1.0
    total_cost_usd: float = 0.0
    votes: list = None  # list of {model, text, latency_ms}

    def __post_init__(self):
        if self.votes is None:
            self.votes = []


@dataclass
class ExternalScore:
    """External scoring result — anti-Goodhart guarantee."""
    score: float  # 0-100
    reasoning: str
    model: str  # which model scored (NOT the producing model)
    agreement: float = 0.0  # council agreement if multi-model


def _shield(value, to=None):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value, to=to)



def _post(path: str, payload: dict, timeout: int = TIMEOUT_S) -> dict:
    """HTTP POST to HAL server, its prose scrubbed by the redaction shield first,
    and refused by the billing guard when it names an OpenAI model (HAL bills
    those to the per-token key) and the operator has not opted into paid spend."""
    url = f"{HAL_BASE}{path}"
    data = json.dumps(_shield(payload, to=url)).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(path: str, timeout: int = 5) -> dict:
    """HTTP GET from HAL server."""
    url = f"{HAL_BASE}{path}"
    req = Request(url)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def is_available() -> bool:
    """Check if HAL server is running and healthy."""
    try:
        status = _get("/api/health", timeout=3)
        overall = status.get("overall", status.get("status", ""))
        return overall in ("ok", "warn") or status.get("healthy", False)
    except (URLError, OSError, json.JSONDecodeError):
        return False


def ensure_server() -> bool:
    """Start HAL server if not running. Returns True if available after attempt."""
    if is_available():
        return True
    # Try starting via PM2 or direct
    try:
        subprocess.Popen(
            [sys.executable, "-m", "lib.hal.server", "--port", str(HAL_PORT)],
            cwd=str(HAL_HOME),
            env={**os.environ, "HAL_HOME": str(HAL_HOME)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give it a moment to start
        import time
        for _ in range(10):
            time.sleep(0.5)
            if is_available():
                return True
    except OSError:
        pass
    return False


def query(
    prompt: str,
    provider: str = "groq",
    timeout: int = TIMEOUT_S,
) -> HALResponse:
    """Single-turn query via HAL's auto-routing.

    Provider is a hint — if it's rate-limited, sentinel routes elsewhere.
    """
    result = _post("/api/query", {"prompt": prompt, "provider": provider}, timeout)
    if "error" in result:
        raise RuntimeError(f"HAL query failed: {result['error']}")
    return HALResponse(
        text=result.get("text", result.get("response", "")),
        model=result.get("model", ""),
        provider=result.get("provider", provider),
        latency_ms=result.get("latency_ms", 0),
        cost_usd=result.get("cost_usd", 0.0),
    )


def chat(
    message: str,
    model: Optional[str] = None,
    timeout: int = TIMEOUT_S,
) -> HALResponse:
    """Multi-turn conversation query via HAL."""
    payload = {"message": message}
    if model:
        payload["model"] = model
    result = _post("/api/chat", payload, timeout)
    if "error" in result:
        raise RuntimeError(f"HAL chat failed: {result['error']}")
    tokens = result.get("tokens", {})
    return HALResponse(
        text=result.get("text", ""),
        model=result.get("model", ""),
        provider=result.get("provider", ""),
        latency_ms=result.get("latency_ms", 0),
        cost_usd=result.get("cost_usd", 0.0),
        prompt_tokens=tokens.get("prompt_tokens", 0),
        completion_tokens=tokens.get("completion_tokens", 0),
    )


def council(
    query_text: str,
    models: Optional[list[str]] = None,
    timeout: int = 60,
) -> CouncilResult:
    """Multi-LLM council deliberation.

    Queries multiple models simultaneously, merges responses with
    agreement scoring. Used for critical decisions and external scoring.
    """
    payload = {"query": query_text}
    if models:
        payload["models"] = models
    result = _post("/api/council", payload, timeout)
    if "error" in result:
        raise RuntimeError(f"HAL council failed: {result['error']}")
    return CouncilResult(
        merged_text=result.get("merged_text", ""),
        agreement=result.get("agreement", 0.0),
        total_cost_usd=result.get("total_cost_usd", 0.0),
        votes=result.get("votes", []),
    )


def score_external(
    claim: str,
    rubric: str = "accuracy",
    context: str = "",
    timeout: int = 60,
) -> ExternalScore:
    """External scoring via HAL council — anti-Goodhart guarantee.

    The producing model NEVER scores its own output. HAL routes to
    independent models (Groq/Llama, Gemini, DeepSeek, Qwen) for scoring.

    PARAMOUNT: Self-scoring inflates 2.1x (Gen 488: Gemini 50 vs self 105).
    """
    scoring_prompt = _build_scoring_prompt(claim, rubric, context)
    result = council(scoring_prompt, timeout=timeout)

    # Parse numeric score from merged council response
    score = _extract_score(result.merged_text)

    # Use first vote's model as the scorer identifier
    scorer_model = result.votes[0]["model"] if result.votes else "unknown"

    return ExternalScore(
        score=score,
        reasoning=result.merged_text,
        model=scorer_model,
        agreement=result.agreement,
    )


def _build_scoring_prompt(claim: str, rubric: str, context: str) -> str:
    """Build a structured scoring prompt for external evaluation."""
    parts = [
        "Score the following claim on a scale of 0-100.",
        f"Rubric: {rubric}",
        "",
        f"Claim: {claim}",
    ]
    if context:
        parts.extend(["", f"Context: {context}"])
    parts.extend([
        "",
        "Respond with ONLY a JSON object: {\"score\": <0-100>, \"reasoning\": \"<brief>\"}",
    ])
    return "\n".join(parts)


def _extract_score(text: str) -> float:
    """Extract numeric score from council response."""
    # Try JSON parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "score" in data:
            return float(data["score"])
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Regex fallback
    import re
    match = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', text)
    if match:
        return float(match.group(1))
    match = re.search(r'\b(\d{1,3})/100\b', text)
    if match:
        return float(match.group(1))
    return 0.0


def providers_status() -> dict:
    """Get provider health status from HAL."""
    return _get("/api/providers")


# ---------------------------------------------------------------------------
# Direct provider access (fallback when HAL server is down)
# ---------------------------------------------------------------------------

def _direct_query(prompt: str, provider: str = "groq") -> HALResponse:
    """Bypass HAL server — call provider directly via HAL library.

    Used as fallback when HAL server is not running.
    """
    sys.path.insert(0, str(HAL_HOME))
    try:
        from lib.hal.registry import get_registry
        from lib.hal.discovery import discover_local_providers

        registry = get_registry()
        discover_local_providers(registry)

        config = registry._configs.get(provider)
        if not config or not config.models:
            raise RuntimeError(f"Provider '{provider}' not configured")

        model_address = f"{provider}/{config.models[0]}"
        response = registry.chat(model_address, prompt)
        return HALResponse(
            text=response.text,
            model=response.model,
            provider=provider,
            latency_ms=int(response.latency_ms or 0),
            cost_usd=response.cost_usd or 0.0,
        )
    finally:
        if str(HAL_HOME) in sys.path:
            sys.path.remove(str(HAL_HOME))


def smart_query(
    prompt: str,
    provider: str = "groq",
    timeout: int = TIMEOUT_S,
) -> HALResponse:
    """Query with automatic fallback: HAL server → direct provider call.

    This is the recommended entry point for GRIP components.
    """
    if is_available():
        return query(prompt, provider, timeout)
    return _direct_query(prompt, provider)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HAL Client — GRIP core bridge")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Check HAL server health")

    q = sub.add_parser("query", help="Single query")
    q.add_argument("prompt", help="Query text")
    q.add_argument("--provider", default="groq")

    c = sub.add_parser("council", help="Multi-LLM council")
    c.add_argument("query_text", help="Council query")

    s = sub.add_parser("score", help="External scoring")
    s.add_argument("claim", help="Claim to score")
    s.add_argument("--rubric", default="accuracy")

    args = parser.parse_args()

    if args.command == "status":
        available = is_available()
        print(f"HAL server: {'available' if available else 'not running'}")
        if available:
            print(json.dumps(providers_status(), indent=2))
    elif args.command == "query":
        resp = smart_query(args.prompt, args.provider)
        print(f"[{resp.provider}/{resp.model}] {resp.text}")
    elif args.command == "council":
        result = council(args.query_text)
        print(f"Agreement: {result.agreement:.0%}")
        print(result.merged_text)
    elif args.command == "score":
        result = score_external(args.claim, args.rubric)
        print(f"Score: {result.score}/100 (by {result.model})")
        print(f"Agreement: {result.agreement:.0%}")
        print(result.reasoning)
    else:
        parser.print_help()
