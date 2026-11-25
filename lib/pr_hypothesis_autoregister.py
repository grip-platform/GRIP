"""PR Hypothesis Autoregister — extracts or synthesises a falsifiable
hypothesis from a PR body and registers it with the hypothesis engine.

This module is part of the HAL Council Day 0 toolchain (item 2 of plan
fluffy-twirling-waffle). It closes the scientific-method loop by ensuring
every merged PR has a registered, falsifiable prediction.

Behaviour:
    1. Parse the PR body for an explicit hypothesis block (fenced
       ``## Hypothesis`` section OR labelled ``Claim:/Metric:/Prediction:/Deadline:``
       lines). If well-formed, return it verbatim.
    2. If no explicit block is present, synthesise one via Haiku 4.5 through
       the HAL registry. Haiku returns a strict JSON object which is parsed
       into the same HypothesisSpec.
    3. Register the spec via ``hypothesis_engine.register(...)``.

Design notes:
    * Pure stdlib parsing. Haiku is invoked ONLY when extraction fails.
    * Dependency-injected HAL registry + engine make the module hermetically
      testable — fake registry + fake engine cover every path.
    * No filesystem, git, or network calls in this module.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Parsing constants — extracted to module level so tests can adjust thresholds.
# ---------------------------------------------------------------------------

DEFAULT_DEADLINE_DAYS = 30
MIN_CLAIM_LEN = 10
MIN_PREDICTION_LEN = 2
# Verbatim fail-safe — the resolver returns this unchanged when HAL's
# catalogue/shim is unavailable. Kept as a named constant so the
# fail-safe value is auditable, not hidden in a call argument.
HAIKU_MODEL_ADDRESS = "anthropic/claude-haiku-4-5"


def _haiku_model_address() -> str:
    """Resolve the Haiku-tier provider/model ADDRESS from HAL's canonical
    catalogue, fail-safe to HAIKU_MODEL_ADDRESS. Never raises.

    Uses resolve_address (full provider/model form) since the registry
    chat API expects an HAL-style address, not a bare id.
    """
    import os
    import sys
    claude_dir = os.environ.get(
        "CLAUDE_DIR", os.path.expanduser("~/.claude"))
    if claude_dir not in sys.path:
        sys.path.insert(0, claude_dir)
    try:
        from lib.hal_model_resolver import resolve_address
        return resolve_address("anthropic/claude-haiku-", HAIKU_MODEL_ADDRESS)
    except Exception:
        return HAIKU_MODEL_ADDRESS

# Fenced-block pattern: ## Hypothesis (heading) up to next ## or end.
_FENCED_HEADING_RE = re.compile(
    r"(?im)^\s*##+\s*hypothesis\s*\n(.*?)(?=\n\s*##+\s|\Z)",
    re.DOTALL,
)

# Labelled-line patterns. Order matters for deterministic extraction.
_LABELS = ("Claim", "Metric", "Prediction", "Deadline")
_LABEL_RE = {
    label: re.compile(rf"(?im)^\s*{label}\s*:\s*(.+?)\s*$")
    for label in _LABELS
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class HypothesisSpec:
    """A parsed or synthesised hypothesis ready for registration."""

    claim: str = ""
    metric: str = ""
    prediction: str = ""
    deadline: str = ""
    source: str = "explicit"  # "explicit" | "synthesised" | "invalid"
    notes: str = ""

    def is_valid(self) -> bool:
        """A spec is registerable if all four fields are non-empty and the
        deadline parses as ISO date (YYYY-MM-DD)."""
        if len(self.claim.strip()) < MIN_CLAIM_LEN:
            return False
        if not self.metric.strip():
            return False
        if len(self.prediction.strip()) < MIN_PREDICTION_LEN:
            return False
        if not _ISO_DATE_RE.match(self.deadline.strip()):
            return False
        return True


@dataclass
class AutoregisterResult:
    """Outcome of autoregister() — used by callers for audit logging."""

    registered: bool = False
    hypothesis_id: str = ""
    spec: Optional[HypothesisSpec] = None
    reason: str = ""
    synthesised: bool = False
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public parsing helpers
# ---------------------------------------------------------------------------


def extract_hypothesis(pr_body: str) -> Optional[HypothesisSpec]:
    """Extract an explicit hypothesis from PR body.

    Tries the fenced-heading form first, then falls back to bare labelled
    lines anywhere in the body. Returns None if no valid spec is found.
    """
    if not pr_body or not pr_body.strip():
        return None

    fenced = _FENCED_HEADING_RE.search(pr_body)
    search_text = fenced.group(1) if fenced else pr_body

    fields = {}
    for label, pattern in _LABEL_RE.items():
        match = pattern.search(search_text)
        if match:
            fields[label.lower()] = match.group(1).strip()

    if not {"claim", "metric", "prediction"}.issubset(fields):
        return None

    spec = HypothesisSpec(
        claim=fields.get("claim", ""),
        metric=fields.get("metric", ""),
        prediction=fields.get("prediction", ""),
        deadline=fields.get("deadline", _default_deadline()),
        source="explicit",
    )
    return spec if spec.is_valid() else None


def _default_deadline(days: int = DEFAULT_DEADLINE_DAYS) -> str:
    """ISO date string N days from now (UTC)."""
    due = datetime.now(timezone.utc) + timedelta(days=days)
    return due.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Haiku synthesis — injected registry only.
# ---------------------------------------------------------------------------


_SYNTHESIS_PROMPT = """You are the GRIP hypothesis synthesiser. Given a PR title
and body, produce ONE falsifiable hypothesis that can be verified via an
observable metric by a deadline.

Output STRICT JSON (no prose) with exactly these keys:
  - claim: one-sentence statement about the PR's expected impact
  - metric: name of an observable metric (snake_case)
  - prediction: expected direction or threshold (e.g. "increase >= 10%", "<0.5")
  - deadline: ISO date YYYY-MM-DD (30 days from today unless PR implies otherwise)

Rules:
  - The hypothesis MUST be falsifiable (answers "what would disprove it?").
  - Prefer metrics already in GRIP (test_count, genome_fitness, delight_composite,
    hook_health, sentinel_coverage) when plausible.
  - Do NOT fabricate numbers that aren't implied by the PR.

PR title: {title}

PR body:
{body}
"""


def synthesise_hypothesis(
    pr_title: str,
    pr_body: str,
    registry=None,
    today: Optional[str] = None,
) -> Optional[HypothesisSpec]:
    """Synthesise a hypothesis via Haiku 4.5 through the HAL registry.

    Args:
        pr_title: PR title (short, high-signal).
        pr_body: PR description (long-form context).
        registry: HAL registry exposing ``chat(model_address, prompt)``.
            If None, a real registry is lazily imported.
        today: ISO date for deterministic default-deadline in tests.

    Returns:
        HypothesisSpec or None on any failure (invalid JSON, empty response,
        registry error). The caller decides how to surface failure.
    """
    prompt = _SYNTHESIS_PROMPT.format(title=pr_title or "", body=pr_body or "")

    raw = _invoke_haiku(registry, prompt)
    if not raw:
        return None

    spec = _parse_haiku_json(raw, today=today)
    if spec is None:
        return None
    spec.source = "synthesised"
    return spec if spec.is_valid() else None


def _invoke_haiku(registry, prompt: str) -> str:
    """Best-effort Haiku call. Returns empty string on any failure."""
    try:
        registry = registry or _default_registry()
        if registry is None:
            return ""
        response = registry.chat(_haiku_model_address(), prompt)
        return getattr(response, "text", "") or ""
    except Exception:
        return ""


def _default_registry():
    """Lazy import of the real HAL registry. Kept out of module init so
    tests never hit the filesystem or network by accident."""
    try:
        from lib.hal.registry import ProviderRegistry  # noqa: F401

        return ProviderRegistry()
    except Exception:
        return None


def _parse_haiku_json(raw: str, today: Optional[str] = None) -> Optional[HypothesisSpec]:
    """Parse strict JSON output. Tolerates fenced ``` wrappers."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    deadline = str(data.get("deadline", "")).strip() or (today or _default_deadline())
    return HypothesisSpec(
        claim=str(data.get("claim", "")).strip(),
        metric=str(data.get("metric", "")).strip(),
        prediction=str(data.get("prediction", "")).strip(),
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


RegisterFn = Callable[..., object]


def autoregister(
    pr_title: str,
    pr_body: str,
    pr_number: int = 0,
    registry=None,
    register_fn: Optional[RegisterFn] = None,
) -> AutoregisterResult:
    """Extract or synthesise a hypothesis and register it.

    Args:
        pr_title: PR title.
        pr_body: PR body (Markdown).
        pr_number: PR number (for audit trail).
        registry: HAL registry; tests inject a fake.
        register_fn: Callable matching ``hypothesis_engine.register``; tests
            inject a fake. Defaults to the real engine when None.

    Returns:
        AutoregisterResult describing the outcome. Never raises.
    """
    spec = extract_hypothesis(pr_body)
    synthesised = False
    if spec is None:
        spec = synthesise_hypothesis(pr_title, pr_body, registry=registry)
        synthesised = spec is not None

    if spec is None or not spec.is_valid():
        return AutoregisterResult(
            registered=False,
            reason="no_hypothesis_extracted_or_synthesised",
            synthesised=synthesised,
            spec=spec,
        )

    register_fn = register_fn or _default_register_fn()
    if register_fn is None:
        return AutoregisterResult(
            registered=False,
            reason="register_fn_unavailable",
            synthesised=synthesised,
            spec=spec,
        )

    try:
        record = register_fn(
            claim=spec.claim,
            metric=spec.metric,
            prediction=spec.prediction,
            pr_number=pr_number,
            deadline=spec.deadline,
        )
        hid = _extract_id(record)
        return AutoregisterResult(
            registered=True,
            hypothesis_id=hid,
            spec=spec,
            synthesised=synthesised,
            reason="ok",
        )
    except Exception as exc:  # noqa: BLE001 — never crash caller
        return AutoregisterResult(
            registered=False,
            reason=f"register_error:{type(exc).__name__}",
            synthesised=synthesised,
            spec=spec,
            errors=[str(exc)],
        )


def _extract_id(record) -> str:
    """Pull an id off dataclass-ish or dict-ish register() return values."""
    hid = getattr(record, "id", None)
    if hid:
        return hid
    if isinstance(record, dict):
        return record.get("id", "")
    return ""


def _default_register_fn() -> Optional[RegisterFn]:
    """Lazy import of the real hypothesis engine. Isolated so tests inject."""
    try:
        from lib.hypothesis_engine import register  # noqa: F401

        return register
    except Exception:
        return None
