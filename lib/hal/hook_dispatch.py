"""HAL Hook Dispatch Engine — in-process Python hook runner.

Runs GRIP's enforcement hooks without CC's subprocess invocation.
HARD hooks run synchronously (deny blocks action). SOFT hooks fail-open.

Two entry modes:
1. In-process: hooks expose run(event: dict) -> dict (HAL-native, zero-latency)
2. Subprocess: falls back to stdin/stdout JSON protocol (CC-compatible)

Usage:
    from lib.hal.hook_dispatch import HookDispatcher

    dispatcher = HookDispatcher()
    result = dispatcher.dispatch("PreToolUse", {"tool_name": "Bash", "tool_input": {...}})
    if result.blocked:
        print(f"Blocked: {result.reason}")
"""
import importlib.util
import json
import os
import subprocess
import sys
from typing import Optional

from lib.hal.home import HAL_HOME as _HAL_HOME
_HOOKS_DIR = os.path.join(_HAL_HOME, "hooks")
_CLASSIFICATION_PATH = os.path.join(_HAL_HOME, "config", "hook-classification.json")


class HookResult:
    """Result of dispatching hooks for an event."""

    def __init__(
        self,
        blocked: bool = False,
        reason: str = "",
        hook_name: str = "",
        results: Optional[list[dict]] = None,
    ):
        self.blocked = blocked
        self.reason = reason
        self.hook_name = hook_name
        self.results = results or []


class HookDispatcher:
    """Dispatches hooks by event type with HARD/SOFT classification."""

    def __init__(self, hooks_dir: Optional[str] = None):
        self._hooks_dir = hooks_dir or _HOOKS_DIR
        self._classification = _load_classification()
        self._hard_hooks = self._classification.get("hard", {}).get("hooks", {})
        self._soft_hooks = self._classification.get("soft", {}).get("hooks", {})

    def dispatch(self, event: str, data: dict) -> HookResult:
        """Run all hooks matching an event. HARD hooks block on deny."""
        results = []

        # HARD hooks first — any deny blocks the action
        for hook_name, config in self._hard_hooks.items():
            if not _matches_event(config, event):
                continue
            result = self._run_hook(hook_name, event, data)
            results.append(result)
            if result.get("decision") == "deny" or result.get("decision") == "block":
                return HookResult(
                    blocked=True,
                    reason=result.get("reason", f"Blocked by {hook_name}"),
                    hook_name=hook_name,
                    results=results,
                )

        # SOFT hooks — run all, never block, log failures
        for hook_name, config in self._soft_hooks.items():
            if not _matches_event(config, event):
                continue
            result = self._run_hook(hook_name, event, data)
            results.append(result)

        return HookResult(blocked=False, results=results)

    def list_hooks(self, event: Optional[str] = None) -> dict[str, list[str]]:
        """List hooks grouped by classification, optionally filtered by event."""
        hard = [
            name for name, cfg in self._hard_hooks.items()
            if event is None or _matches_event(cfg, event)
        ]
        soft = [
            name for name, cfg in self._soft_hooks.items()
            if event is None or _matches_event(cfg, event)
        ]
        return {"hard": hard, "soft": soft}

    def _run_hook(self, hook_name: str, event: str, data: dict) -> dict:
        """Run a single hook, preferring in-process over subprocess."""
        hook_path = os.path.join(self._hooks_dir, hook_name)

        if not os.path.exists(hook_path):
            return {"decision": "allow", "error": f"{hook_name} not found"}

        # Try in-process first (if hook has run() function)
        result = _try_in_process(hook_path, event, data)
        if result is not None:
            return result

        # Fall back to subprocess (CC-compatible)
        return _run_subprocess(hook_path, event, data)


def _matches_event(config: dict, event: str) -> bool:
    """Check if a hook config matches the given event."""
    hook_event = config.get("event", "")
    if isinstance(hook_event, list):
        return event in hook_event
    return hook_event == event


def _try_in_process(hook_path: str, event: str, data: dict) -> Optional[dict]:
    """Try to run hook in-process via its run() function.

    Returns None if the hook doesn't have a run() entry point.
    """
    try:
        spec = importlib.util.spec_from_file_location("_hal_hook", hook_path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)

        # Add necessary paths before loading
        if _HOOKS_DIR not in sys.path:
            sys.path.insert(0, _HOOKS_DIR)
        lib_dir = os.path.join(_CLAUDE_DIR, "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)

        spec.loader.exec_module(module)

        if hasattr(module, "run"):
            result = module.run({"event": event, **data})
            if isinstance(result, dict):
                return result
            return {"decision": "allow"}

        return None  # No run() function — use subprocess
    except Exception:
        return None  # In-process failed — fall through to subprocess


def _run_subprocess(hook_path: str, event: str, data: dict) -> dict:
    """Run hook as subprocess with JSON stdin/stdout protocol."""
    payload = json.dumps({"event": event, **data})

    try:
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 2:
            return {"decision": "block", "reason": result.stdout.strip()[:500]}

        if result.stdout.strip():
            try:
                return json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                pass

        return {"decision": "allow"}

    except subprocess.TimeoutExpired:
        return {"decision": "allow", "error": f"{hook_path}: timeout (30s)"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"decision": "allow", "error": str(e)[:200]}


def _load_classification() -> dict:
    """Load hook classification from config."""
    if not os.path.exists(_CLASSIFICATION_PATH):
        return {"hard": {"hooks": {}}, "soft": {"hooks": {}}}
    try:
        with open(_CLASSIFICATION_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"hard": {"hooks": {}}, "soft": {"hooks": {}}}
