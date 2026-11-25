"""Public surface for the harness-in-harness abstraction.

Design doc: docs/harness-in-harness.md. Importing this package registers
every built-in kind, so `make(kind, ...)` works immediately.

The 2026-07-17 harness wave added 19 CLI coding-agent adapters (every
entry carries research provenance + a capability-floor default in its
module docstring): shell-gpt, fabric, open-interpreter, continue-cli,
goose, openhands, plandex, kilo, metagpt, chatdev, gpt-pilot, devika,
agent-zero, swe-agent, omnigent, codewhale, agy, qwen-code, pi.

Later leaves: dsh (2026-08-19, DeepSeek Harness) and kimi-code (2026-09-07,
Moonshot's Kimi Code CLI through bin/grip-kimi; lib/harness/kimi.py). The
import line here is load-bearing — ``@register`` runs only when the module is
imported, and tests/test_harness_kimi.py proves it from a fresh interpreter.
"""
from .agent_zero import AgentZeroHarness
from .agy import AgyHarness
from .base import (HARNESS_KINDS, Harness, HarnessContractViolation,
                   HarnessResult, Task, make, register)
from .chatdev import ChatDevHarness
from .cli import (DEFAULT_CLAUDE_TOOLS, CLIHarness, ClaudeCodeHarness,
                  CodexHarness, GeminiHarness, OpenCodeHarness)
from .codewhale import CodeWhaleHarness
from .continue_cli import ContinueCLIHarness
from .devika import DevikaHarness
from .dsh import DshHarness
from .fabric import FabricHarness
from .goose import GooseHarness
from .gpt_pilot import GPTPilotHarness
from .grok_build import GrokBuildHarness
from .hal import HALHarness
from .kilo import KiloHarness
from .kimi import KimiCodeHarness
from .metagpt import MetaGPTHarness
from .nested import FallbackHarness, HostedHarness, OrchestratorHarness
from .omnigent import OmnigentHarness
from .open_interpreter import OpenInterpreterHarness
from .openhands import OpenHandsHarness
from .pi import PiHarness
from .plandex import PlandexHarness
from .qwen_code import QwenCodeHarness
from .shell_gpt import ShellGPTHarness
from .swe_agent import SWEAgentHarness

__all__ = [
    "HARNESS_KINDS",
    "Harness",
    "HarnessContractViolation",
    "HarnessResult",
    "Task",
    "make",
    "register",
    "CLIHarness",
    "DEFAULT_CLAUDE_TOOLS",
    "AgentZeroHarness",
    "AgyHarness",
    "ChatDevHarness",
    "ClaudeCodeHarness",
    "CodeWhaleHarness",
    "CodexHarness",
    "ContinueCLIHarness",
    "DevikaHarness",
    "DshHarness",
    "FabricHarness",
    "GeminiHarness",
    "GooseHarness",
    "GPTPilotHarness",
    "GrokBuildHarness",
    "KiloHarness",
    "KimiCodeHarness",
    "MetaGPTHarness",
    "OmnigentHarness",
    "OpenCodeHarness",
    "OpenInterpreterHarness",
    "OpenHandsHarness",
    "PiHarness",
    "PlandexHarness",
    "QwenCodeHarness",
    "ShellGPTHarness",
    "SWEAgentHarness",
    "HALHarness",
    "FallbackHarness",
    "HostedHarness",
    "OrchestratorHarness",
]
