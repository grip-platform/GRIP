"""grip_adapter — MCP tool bridging external agents to GRIP's A2A protocol.

Accepts a task description + optional provider hint, creates an A2A task,
runs it through converge(), and returns the result. This is the callable
referenced by agents/bridge/a2a_bridge.md and agents/bridge/broly_gemini.md.

Usage:
    from lib.grip_adapter import grip_adapter, GripAdapterResult

    result = grip_adapter(
        task="Improve code quality to score >= 90",
        provider="gemini",
        template="increment",
        max_depth=5,
    )
    print(result.satisfied, result.task_id)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# Ensure lib/ is importable
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.a2a_adapter import A2AAdapter, TaskState
from lib.a2a_provider_detect import ProviderInfo, detect_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in improve/criterion pairs (reusable templates)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, tuple[Callable, Callable]] = {
    "echo": (
        lambda state: state,
        lambda state: True,
    ),
    "increment": (
        lambda state: _increment(state),
        lambda state: _threshold_met(state),
    ),
}


def _increment(state: Any) -> Any:
    """Increment a numeric state or dict with 'value' key."""
    if isinstance(state, (int, float)):
        return state + 1
    if isinstance(state, dict) and "value" in state:
        return {**state, "value": state["value"] + 1}
    return state


def _threshold_met(state: Any) -> bool:
    """Check if state meets its threshold (default 10)."""
    if isinstance(state, (int, float)):
        return state >= 10
    if isinstance(state, dict):
        return state.get("value", 0) >= state.get("threshold", 10)
    return False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GripAdapterResult:
    """Outcome of a grip_adapter() call."""
    task_id: str
    state: str
    satisfied: bool
    result: Any
    provider: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def grip_adapter(
    task: Any,
    provider: str = "",
    template: str = "",
    improve: Optional[Callable] = None,
    criterion: Optional[Callable] = None,
    max_depth: int = 5,
    metadata: Optional[dict] = None,
    adapter: Optional[A2AAdapter] = None,
) -> GripAdapterResult:
    """Bridge an external agent task to GRIP's A2A converge() kernel.

    Args:
        task: Task input data (any serialisable value).
        provider: Provider hint (gemini, codex, copilot, or auto-detect).
        template: Named template from built-in registry (echo, increment).
        improve: Custom improve function. Overrides template.
        criterion: Custom criterion function. Overrides template.
        max_depth: Maximum convergence depth (1-50).
        metadata: Additional metadata dict.
        adapter: Optional A2AAdapter instance (for testing/reuse).

    Returns:
        GripAdapterResult with task outcome.
    """
    _adapter = adapter or A2AAdapter()
    resolved = _resolve_provider(provider, metadata)
    improve_fn, criterion_fn = _resolve_functions(template, improve, criterion)

    task_metadata = {**(metadata or {}), "provider": resolved.name}
    if resolved.model:
        task_metadata["model"] = resolved.model

    a2a_task = _adapter.create_task(input_data=task, metadata=task_metadata)

    a2a_task = _adapter.execute_task(
        task_id=a2a_task.id,
        criterion=criterion_fn,
        improve=improve_fn,
        max_depth=max_depth,
        label=f"grip-adapter-{a2a_task.id[:8]}",
    )

    return _build_result(a2a_task, resolved.name)


# ---------------------------------------------------------------------------
# Helpers (each focused, low CC)
# ---------------------------------------------------------------------------

def _resolve_provider(
    provider: str,
    metadata: Optional[dict],
) -> ProviderInfo:
    """Resolve provider from hint or metadata."""
    if provider:
        return ProviderInfo(name=provider.lower())
    return detect_provider(metadata=metadata)


def _resolve_functions(
    template: str,
    improve: Optional[Callable],
    criterion: Optional[Callable],
) -> tuple[Callable, Callable]:
    """Resolve improve/criterion from explicit callables or template name.

    Explicit callables take precedence over template. If neither is
    provided, defaults to the 'echo' template.
    """
    if improve and criterion:
        return improve, criterion

    template_name = template or "echo"
    if template_name not in _TEMPLATES:
        raise ValueError(
            f"Unknown template '{template_name}'. "
            f"Available: {list(_TEMPLATES.keys())}"
        )
    tmpl_improve, tmpl_criterion = _TEMPLATES[template_name]
    return improve or tmpl_improve, criterion or tmpl_criterion


def _build_result(a2a_task: Any, provider_name: str) -> GripAdapterResult:
    """Build a GripAdapterResult from a completed A2ATask."""
    satisfied = False
    if a2a_task.result and isinstance(a2a_task.result, dict):
        satisfied = a2a_task.result.get("satisfied", False)

    return GripAdapterResult(
        task_id=a2a_task.id,
        state=a2a_task.state.value,
        satisfied=satisfied,
        result=a2a_task.result,
        provider=provider_name,
        error=a2a_task.error,
    )
