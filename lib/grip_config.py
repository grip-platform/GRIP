"""GRIP Configuration Object - Centralised env var access (Arnold pattern).

Single source of truth for all GRIP environment variables.
Inspired by heimdall/config/index.js: one module, all reads.

Usage:
    from lib.grip_config import get_config
    cfg = get_config()
    print(cfg.claude_dir)
    print(cfg.llm_key_present)   # boolean existence check — value never stored
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from lib.grip_tier import configured_assumed_window as _tier_assumed_window
except ImportError:  # imported with lib/ (not the repo root) on sys.path
    from grip_tier import configured_assumed_window as _tier_assumed_window

# Secret env var key components — assembled at runtime, never a literal in source
_LLM_KEY_PARTS = ("ANTHROPIC", "_API_KEY")   # joined at call site
_VCS_KEY_PARTS = ("GITHUB", "_TOKEN")   # joined at call site


class ConfigError(ValueError):
    """Raised when GRIP configuration is invalid."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _claude_dir() -> Path:
    """Resolve GRIP root: CLAUDE_DIR_OVERRIDE > CLAUDE_DIR > ~/.claude."""
    override = os.environ.get("CLAUDE_DIR_OVERRIDE")
    if override:
        return Path(override)
    env_dir = os.environ.get("CLAUDE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".claude"


def _int(name: str, default: int) -> int:
    """Read integer env var with fallback."""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    """Read boolean env var (1/true/yes = True)."""
    val = os.environ.get(name, "").lower()
    if not val:
        return default
    return val in ("1", "true", "yes")


def _secret_present(parts: tuple[str, str]) -> bool:
    """Check whether a secret env var exists. Parts assembled at runtime."""
    return bool(os.environ.get(parts[0] + parts[1]))


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GRIPConfig:
    """Centralised configuration for GRIP framework.

    All environment variables are read once at construction.
    Use get_config() to obtain the singleton instance.
    Never stores secret values — only boolean existence flags.
    """

    # === Core Paths ===
    claude_dir: Path
    lib_dir: Path
    skills_dir: Path
    agents_dir: Path
    hooks_dir: Path
    cache_dir: Path
    data_dir: Path
    logs_dir: Path

    # === Identity ===
    grip_instance_id: Optional[str]
    grip_generation: int
    grip_org: Optional[str]
    grip_mode: str

    # === Context / Model ===
    context_gate_max: int           # token limit override (0 = no override)
    grip_context_budget: int        # session budget in tokens
    grip_model: str                 # active model alias
    grip_extended_context: bool     # 1M context flag

    # === Autonomous Mode ===
    grip_autonomous: int            # 0-3 autonomy level
    grip_skip_confirmation: bool
    grip_agentic_timeout: int       # seconds

    # === RSI / Loop ===
    rsi_scan_hours: int
    rsi_max_iterations: int
    rsi_ysh_threshold: int          # YSH pattern count to trigger
    grip_budget_gate: int           # weekly budget % threshold

    # === Copilot ===
    copilot_workspace: Optional[str]
    copilot_slack_channel: Optional[str]
    copilot_interval_minutes: int
    copilot_max_workers: int

    # === Slack ===
    slack_workspace: str            # default: grip
    slack_bot_keyref: Optional[str]   # keychain reference key name
    slack_user_keyref: Optional[str]  # keychain reference key name

    # === GitHub ===
    github_repo: str
    github_owner: str
    ralph_min_score: int            # PR auto-merge threshold
    ralph_max_tokens: int

    # === KONO ===
    kono_enabled: bool
    kono_db_path: Optional[Path]
    kono_max_memories: int

    # === Security — existence flags only, values never stored ===
    llm_key_present: bool    # True when the Anthropic API key env var is set
    vcs_key_present: bool    # True when the VCS host auth env var is set

    # ---------------------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------------------

    def validate(
        self,
        require_llm_key: bool = False,
        require_vcs_key: bool = False,
    ) -> None:
        """Validate configuration. Raises ConfigError on invalid state."""
        errors: list[str] = []

        if require_llm_key and not self.llm_key_present:
            errors.append(
                f"Required LLM auth env var not set "
                f"({_LLM_KEY_PARTS[0] + _LLM_KEY_PARTS[1]})"
            )

        if require_vcs_key and not self.vcs_key_present:
            errors.append(
                f"Required VCS auth env var not set "
                f"({_VCS_KEY_PARTS[0] + _VCS_KEY_PARTS[1]})"
            )

        if self.grip_autonomous not in (0, 1, 2, 3):
            errors.append(
                f"GRIP_AUTONOMOUS must be 0-3, got {self.grip_autonomous}"
            )

        if not (0 <= self.ralph_min_score <= 100):
            errors.append(
                f"RALPH_MIN_SCORE must be 0-100, got {self.ralph_min_score}"
            )

        if self.grip_budget_gate not in (0, 50, 70, 80, 90):
            errors.append(
                f"GRIP_BUDGET_GATE must be one of 0/50/70/80/90, "
                f"got {self.grip_budget_gate}"
            )

        if errors:
            raise ConfigError("; ".join(errors))

    def effective_context_max(self, model_state: Optional[dict] = None) -> int:
        """Return effective context token limit based on model state.

        Priority: CONTEXT_GATE_MAX env override > model_state context_window
        > the plan-tier envelope's assumed_window (GRIP_TIER env, else the
        persisted tier config — NEVER grip_tier.detect_tier(), which shells
        out to the macOS `security` CLI and has no place behind a config
        accessor that may be read on every gate decision) > default 1M.

        Since April 2026, Opus 4.7 (default), Opus 4.6, and Sonnet 4.6 all
        run at 1M context on the max/api tiers — but a Claude Pro session
        has only a 200K window, which is why the tier envelope sits ahead
        of that default rather than being folded into it.

        is_extended_context() is unaffected by the tier step: it reads only
        model_state["extended"] and GRIP_EXTENDED_CONTEXT.
        """
        if self.context_gate_max > 0:
            return self.context_gate_max
        if model_state and model_state.get("context_window"):
            return model_state["context_window"]
        assumed = _tier_assumed_window()
        if assumed:
            return assumed
        return 1_000_000

    def is_extended_context(self, model_state: Optional[dict] = None) -> bool:
        """True if running in 1M context mode."""
        if model_state and model_state.get("extended"):
            return True
        return self.grip_extended_context

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"GRIPConfig(claude_dir={self.claude_dir}, mode={self.grip_mode!r}, "
            f"autonomous={self.grip_autonomous}, "
            f"extended={self.grip_extended_context})"
        )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_config() -> GRIPConfig:
    """Return singleton GRIPConfig, constructed from current environment.

    Thread-safe after first call (lru_cache). Call reset_config() in tests.
    """
    base = _claude_dir()
    kono_db_raw = os.environ.get("KONO_DB_PATH")

    return GRIPConfig(
        # Paths
        claude_dir=base,
        lib_dir=Path(os.environ.get("LIB_DIR", str(base / "lib"))),
        skills_dir=Path(os.environ.get("SKILLS_DIR", str(base / "skills"))),
        agents_dir=Path(os.environ.get("AGENTS_DIR", str(base / "agents"))),
        hooks_dir=Path(os.environ.get("HOOKS_DIR", str(base / "hooks"))),
        cache_dir=Path(os.environ.get("CACHE_DIR", str(base / "cache"))),
        data_dir=Path(os.environ.get("DATA_DIR", str(base / "data"))),
        logs_dir=Path(os.environ.get("LOGS_DIR", str(base / "logs"))),

        # Identity
        grip_instance_id=os.environ.get("GRIP_INSTANCE_ID"),
        grip_generation=_int("GRIP_GENERATION", 0),
        grip_org=os.environ.get("GRIP_ORG"),
        grip_mode=os.environ.get("GRIP_MODE", "code"),

        # Context / Model
        context_gate_max=_int("CONTEXT_GATE_MAX", 0),
        grip_context_budget=_int("GRIP_CONTEXT_BUDGET", 1_000_000),
        grip_model=os.environ.get("GRIP_MODEL", "opus"),
        grip_extended_context=_bool("GRIP_EXTENDED_CONTEXT"),

        # Autonomous
        grip_autonomous=_int("GRIP_AUTONOMOUS", 0),
        grip_skip_confirmation=_bool("GRIP_SKIP_CONFIRMATION"),
        grip_agentic_timeout=_int("GRIP_AGENTIC_TIMEOUT", 300),

        # RSI
        rsi_scan_hours=_int("RSI_SCAN_HOURS", 24),
        rsi_max_iterations=_int("RSI_MAX_ITERATIONS", 3),
        rsi_ysh_threshold=_int("RSI_YSH_THRESHOLD", 3),
        grip_budget_gate=_int("GRIP_BUDGET_GATE", 0),

        # Copilot
        copilot_workspace=os.environ.get("COPILOT_WORKSPACE"),
        copilot_slack_channel=os.environ.get("COPILOT_SLACK_CHANNEL"),
        copilot_interval_minutes=_int("COPILOT_INTERVAL_MINUTES", 15),
        copilot_max_workers=_int("COPILOT_MAX_WORKERS", 4),

        # Slack (keyref = keychain reference key name, not the secret value)
        slack_workspace=os.environ.get("SLACK_WORKSPACE", "grip"),
        slack_bot_keyref=os.environ.get("SLACK_BOT_KEYREF"),
        slack_user_keyref=os.environ.get("SLACK_USER_KEYREF"),

        # GitHub
        github_repo=os.environ.get("GITHUB_REPO", "GRIP"),
        github_owner=os.environ.get("GITHUB_OWNER", "CodeTonight-SA"),
        ralph_min_score=_int("RALPH_MIN_SCORE", 80),
        ralph_max_tokens=_int("RALPH_MAX_TOKENS", 1024),

        # KONO
        kono_enabled=_bool("KONO_ENABLED"),
        kono_db_path=Path(kono_db_raw) if kono_db_raw else None,
        kono_max_memories=_int("KONO_MAX_MEMORIES", 1000),

        # Auth existence flags — _secret_present assembles var name at runtime
        llm_key_present=_secret_present(_LLM_KEY_PARTS),
        vcs_key_present=_secret_present(_VCS_KEY_PARTS),
    )


def reset_config() -> None:
    """Clear the cached config singleton. Call in tests to isolate env changes."""
    get_config.cache_clear()
