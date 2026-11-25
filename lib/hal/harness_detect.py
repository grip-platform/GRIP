"""HAL Harness Detection — auto-detect which agentic tool (if any) is running.

HAL works standalone or inside any harness. This module detects the active
harness so HAL can adapt its behaviour (e.g., delegate tool dispatch to
the harness when available, or run tools natively when standalone).

Usage:
    from lib.hal.harness_detect import detect_harness, Harness

    harness = detect_harness()
    if harness == Harness.STANDALONE:
        # HAL runs everything natively
    elif harness == Harness.CLAUDE_CODE:
        # Can optionally delegate to CC for premium features
"""
import os
from enum import Enum


class Harness(str, Enum):
    """Known agentic harnesses."""
    STANDALONE = "standalone"
    CLAUDE_CODE = "claude-code"
    GEMINI_CLI = "gemini-cli"
    CODEX = "codex"
    CURSOR = "cursor"
    OPENCODE = "opencode"
    UNKNOWN = "unknown"


# Detection rules: env var -> harness
_DETECTION_RULES = [
    ("CLAUDE_CODE_SESSION_ID", Harness.CLAUDE_CODE),
    ("GEMINI_CLI_SESSION", Harness.GEMINI_CLI),
    ("CODEX_SESSION_ID", Harness.CODEX),
    ("CURSOR_SESSION_ID", Harness.CURSOR),
    ("OPENCODE_SESSION_ID", Harness.OPENCODE),
]


def detect_harness() -> Harness:
    """Detect which agentic harness is active. Returns STANDALONE if none."""
    for env_var, harness in _DETECTION_RULES:
        if os.environ.get(env_var):
            return harness
    return Harness.STANDALONE


def is_standalone() -> bool:
    """True when HAL is running without any external harness."""
    return detect_harness() == Harness.STANDALONE
