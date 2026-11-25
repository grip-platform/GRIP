"""
token_budget.py — Single source of truth for budget state across all GRIP subsystems.

The Oracle Gradient: smooth degradation from cloud abundance to local oracle mode.

Tier model:
    abundant  (>50 tok/s, <50% weekly spend)  — Full depth, all agents, evolve genome
    moderate  (15-50 tok/s, 50-79% spend)     — Capped depth 2, max FORK, evolve
    scarce   (5-15 tok/s, 80-89% spend)       — Depth 1, THREAD only, read-only genome
    oracle   (<5 tok/s, >=90% spend)           — Single-shot, no agents, lookup only

Each tier drops the most expensive operation first and increases reliance on
pre-computed knowledge (genome fitness, compressed skills).

Usage:
    from token_budget import TokenBudget

    budget = TokenBudget.detect()
    if budget.can_afford(estimated_cost):
        ...
    max_level = budget.max_broly_level()
    subsystems = budget.recall_subsystems()
"""

# TokenBudget.detect() is annotated "-> TokenBudget", a forward reference to the
# class it is being defined inside. Without this import Python evaluates
# annotations eagerly at class-creation time, so the name is not bound yet and
# importing this module raises NameError -- which made lib/token_budget.py
# entirely unimportable, along with the four test modules that import it.
# PEP 563; unnecessary from Python 3.14 (PEP 649) but harmless there.
from __future__ import annotations

import os
from dataclasses import dataclass

# Oracle Gradient: budget mode thresholds (weekly_spend_pct -> mode)
# Checked from highest to lowest; first match wins.
BUDGET_MODE_THRESHOLDS = {
    90: "oracle",
    80: "scarce",
    50: "moderate",
}

# Valid budget modes, ordered from most to least abundant
BUDGET_MODES = ("abundant", "moderate", "scarce", "oracle")

# Hard output-token limits per downstream provider.
# Used by LiteLLM proxy (litellm_params.max_tokens) and any GRIP component
# that builds direct API requests. DeepSeek and Ollama cap at 8192;
# cloud providers are higher but still bounded to prevent runaway cost.
PROVIDER_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "deepseek": 8192,    # DeepSeek V3, R1 — confirmed via API error [1, 8192]
    "ollama": 8192,      # Local models vary; 8192 is the safe conservative cap
    "lemonade": 8192,    # Local AMD inference — same conservative cap
    "vllm": 8192,        # vLLM local — model-dependent, use conservative cap
    "lmstudio": 8192,    # LM Studio local — same
    "mlx_vlm": 8192,     # MLX local — same
    "groq": 32768,       # Groq Llama 3.3 70B — documented 32K output limit
    "gemini": 65536,     # Gemini 2.5 Flash/Pro — 65K output
    "mistral": 16384,    # Mistral Small — 16K output
    "openrouter": 16384, # Mixed pool; conservative across routed models
    "openai": 16384,     # GPT-4o — 16K output
    "anthropic": 64000,  # Claude native — 64K output (claude-sonnet-4-6)
    "soruxgpt": 8192,    # Proxy — unknown backend; use conservative cap
    "qwen": 16384,       # Qwen 3.5 Plus — 16K output
    "moonshot": 131072,  # kimi-k3 default max_completion_tokens (platform.kimi.ai/docs/api/chat.md)
    "zhipu": 8192,       # GLM 5.1 — conservative cap
}


@dataclass
class TokenBudget:
    """Token budget state for the current session.

    Single source of truth consulted by convergence loops, Broly,
    genome evolution, memory recall/save, and safety gates.
    """
    mode: str = "abundant"
    total_tokens: int = 1_000_000
    spent_tokens: int = 0
    reserved_tokens: int = 5_000
    weekly_spend_pct: float = 0.0
    tok_per_second: float = 0.0  # 0 = unknown

    # Override flag: when set via /mode oracle, ignores auto-detection
    _user_override: str = ""

    def __post_init__(self):
        if self.mode not in BUDGET_MODES:
            self.mode = "abundant"

    @property
    def available(self) -> int:
        """Tokens remaining after spend and safety reserve."""
        return max(0, self.total_tokens - self.spent_tokens - self.reserved_tokens)

    def can_afford(self, estimated_cost: int) -> bool:
        """Whether the estimated token cost fits within remaining budget."""
        return estimated_cost <= self.available

    @classmethod
    def detect(cls) -> TokenBudget:
        """Auto-detect budget tier from environment signals.

        Detection priority:
        1. User override (via /mode oracle or GRIP_BUDGET_MODE env var)
        2. Weekly spend percentage from cost_tracker
        3. Model profile context size
        4. Default: abundant
        """
        override = os.environ.get("GRIP_BUDGET_MODE", "")
        if override in BUDGET_MODES:
            return cls(mode=override, _user_override=override)

        # Detect weekly spend percentage
        weekly_pct = 0.0
        try:
            from lib.cost_tracker import load_weekly_budget
            state = load_weekly_budget()
            weekly_pct = state.utilization_pct
        except (ImportError, Exception):
            pass

        # Detect context size from model state (1M default since March 2026)
        total_tokens = 1_000_000
        try:
            from lib.state_manager import load_state
            data = load_state("model-state")
            if data:
                total_tokens = data.get("context_window", 1_000_000)
        except (ImportError, Exception):
            pass

        # Map weekly spend to budget mode using thresholds
        mode = _weekly_pct_to_mode(weekly_pct)

        return cls(
            mode=mode,
            total_tokens=total_tokens,
            weekly_spend_pct=weekly_pct,
        )

    def should_converge(self, estimated_depth_cost: int) -> bool:
        """Whether the next convergence depth is affordable.

        In oracle mode, convergence is never affordable (single-shot only).
        In scarce mode, only depth 0-1 pass the gate.
        """
        if self.mode == "oracle":
            return False
        return self.can_afford(estimated_depth_cost)

    def max_converge_depth(self) -> int:
        """Maximum convergence depth allowed by budget tier.

        abundant: no cap (returns 99 as sentinel)
        moderate: 2
        scarce:   1
        oracle:   0
        """
        caps = {"abundant": 99, "moderate": 2, "scarce": 1, "oracle": 0}
        return caps.get(self.mode, 99)

    def max_broly_level(self) -> str:
        """Highest Broly power level affordable in current budget.

        abundant: mesh (all levels)
        moderate: fork (max 3 agents)
        scarce:   thread (single agent, CoT only)
        oracle:   off (no Broly)
        """
        levels = {
            "abundant": "mesh",
            "moderate": "fork",
            "scarce": "thread",
            "oracle": "off",
        }
        return levels.get(self.mode, "mesh")

    def recall_subsystems(self) -> list[str]:
        """Which memory subsystems to recall given budget.

        Drops the most expensive subsystems first as budget tightens.
        """
        if self.mode == "oracle":
            return ["genome_pulse"]
        if self.mode == "scarce":
            return ["instance", "genome_pulse", "active_state"]
        if self.mode == "moderate":
            return ["instance", "genome_pulse", "active_state", "achievements", "last_plan"]
        # abundant
        return [
            "instance", "genome_pulse", "active_state",
            "achievements", "last_plan", "kono", "history",
        ]

    def genome_mode(self) -> str:
        """Genome operation mode.

        evolve:    Full evolution (fitness update + review + genesis + split)
        read-only: Update fitness only, skip review/genesis/split
        lookup:    No mutation at all — genome is frozen
        """
        if self.mode in ("abundant", "moderate"):
            return "evolve"
        if self.mode == "scarce":
            return "read-only"
        return "lookup"

    def max_output_tokens(self, provider: str = "anthropic") -> int:
        """Maximum output tokens for the given provider.

        Returns the provider's hard cap so callers never exceed it.
        Falls back to 8192 (the most restrictive known limit) for unknowns.
        """
        return PROVIDER_MAX_OUTPUT_TOKENS.get(provider.lower(), 8192)

    def save_subsystems(self) -> list[str]:
        """Which save operations to run given budget.

        Cheap saves always run. Expensive saves gated by tier.
        """
        # Always save (cheap)
        subsystems = ["instance", "memory_brief", "session_sync"]

        # Moderate+ gets insights and genome sync
        if self.mode in ("abundant", "moderate"):
            subsystems.extend(["insights", "dna_sync", "dna_decay", "dna_kono_sync"])

        # Abundant gets KONO session summary
        if self.mode == "abundant":
            subsystems.append("kono_summary")

        return subsystems


def _weekly_pct_to_mode(weekly_pct: float) -> str:
    """Map weekly spend percentage to budget mode using thresholds.

    Uses BUDGET_MODE_THRESHOLDS from _cost_constants.py.
    Thresholds checked from highest to lowest.
    """
    for threshold_pct, mode_name in sorted(
        BUDGET_MODE_THRESHOLDS.items(), key=lambda x: x[0], reverse=True
    ):
        if weekly_pct >= threshold_pct:
            return mode_name
    return "abundant"
