"""ultracode adapters — implementations of the kernel's `Adapter` seam.

- `DeterministicAdapter`: a real, complete, deterministic adapter (portable, $0). The
  default for tests and CI — no network, no provider.
- `HALAdapter`: an OPTIONAL Pool-B provider adapter that routes `dispatch` through GRIP's
  HAL substrate (`grip_infer`, non-Anthropic). Availability-guarded so the kernel and the
  tests never require GRIP or a network. This is how "provider-agnostic" is proven: swap
  the adapter, the primitives are unchanged.
- `HarnessAdapter`: routes `dispatch` OUTWARD to a foreign harness CLI in one-shot mode.
  Every harness-specific fact (argv, delivery mode) is read from the registry as data —
  the kernel and this adapter carry no branch on harness identity (H-UC-HARNESS). Unlike
  HALAdapter it never raises into the kernel: one dead peer must DEGRADE a fan-out, not
  abort it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Callable


def to_prompt(task: Any) -> str:
    """THE canonical task → prompt serialisation for provider adapters.

    One home (AGORA #3949): every adapter that must render a task as provider
    text uses this, so consumers (e.g. lib/broly_ultracode.GripInferAdapter's
    dispatch_role path) never re-derive their own variant.
    """
    return task if isinstance(task, str) else json.dumps(task, sort_keys=True,
                                                         default=str)


class HALDispatchError(RuntimeError):
    """A HAL dispatch failed. Structured: carries the exit code and a bounded
    stderr excerpt instead of leaking the raw stream (AGORA #3949)."""

    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr_excerpt = (stderr or "").strip()[:200]
        super().__init__(
            f"HAL dispatch failed (exit {returncode}): "
            f"{self.stderr_excerpt or 'no stderr'}")


class DeterministicAdapter:
    """A real, complete, deterministic adapter. Injects a pure function as the reasoning.

    Counts real calls so idempotency and fan-out are observable, not asserted.
    """
    def __init__(self, fn: Callable[[Any], Any]):
        self.fn = fn
        self.calls = 0

    def dispatch(self, task: Any) -> Any:
        self.calls += 1
        return self.fn(task)


class HALAdapter:
    """Optional Pool-B provider adapter — routes `dispatch` through GRIP's HAL (non-Anthropic).

    Never imported by the tests. `available()` lets a caller fail open when the GRIP
    substrate is absent (portable mode). This is the concrete proof that the P1 primitives
    are provider-agnostic: the same `dispatch/verify/synthesize/recurse` run unchanged
    whether the injected adapter is deterministic or a live Pool-B model.
    """
    def __init__(self, claude_dir: str = "~/.claude", timeout: int = 60):
        self.claude_dir = os.path.expanduser(claude_dir)
        self.timeout = timeout
        self.calls = 0

    def available(self) -> bool:
        return os.path.exists(os.path.join(self.claude_dir, "lib", "hal_llm_adapter.py"))

    def dispatch(self, task: Any) -> Any:
        self.calls += 1
        code = (
            "import sys; sys.path.insert(0, sys.argv[2]); "
            "from lib.hal_llm_adapter import LlamaEngine; "
            "_r = LlamaEngine().infer(sys.argv[1]); print(getattr(_r, 'text', _r))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, to_prompt(task), self.claude_dir],
            capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise HALDispatchError(proc.returncode, proc.stderr)
        return proc.stdout.strip()


def _prompt_from(task: Any) -> str:
    """Extract the prompt a harness should receive from an arbitrary task.

    A task may be a plain string, or a dict carrying a `prompt` key (the shape a
    caller who wants to travel extra metadata alongside the prompt uses). Falling
    back to `to_prompt` for any other shape keeps the adapter total rather than
    raising a KeyError into a fan-out.
    """
    if isinstance(task, str):
        return task
    if isinstance(task, dict) and "prompt" in task:
        return str(task["prompt"])
    return to_prompt(task)


class HarnessAdapter:
    """Routes `dispatch` to a FOREIGN harness CLI in one-shot mode.

    The kernel still imports no harness: this adapter is injected, exactly as
    HALAdapter is. Selection is by registry name, and the harness-specific facts
    — argv, delivery mode, model flag — are READ FROM `HARNESS_REGISTRY` rather
    than branched on here. A branch on harness identity inside the kernel would
    falsify H-UC-HARNESS; the identical branch here would merely move the
    problem, so the registry is the only place a harness is ever named.

    Unlike HALAdapter, `dispatch` NEVER raises. A fan-out across several harnesses
    must DEGRADE when one peer fails — an exception escaping here would abort the
    whole fan-out and discard the surviving peers' work — so every failure path
    returns a structured error dict instead.
    """

    def __init__(self, harness: str, *, model: str | None = None,
                 timeout: int = 300, cwd: str | None = None):
        self.harness = harness
        self.model = model
        self.timeout = timeout
        self.cwd = cwd
        self.calls = 0

    def _spec(self) -> dict:
        """The registry entry for this harness, or {} when unknown.

        Imported lazily so the adapter stays importable without pulling the
        picker's module graph at import time, and so a test can monkeypatch the
        registry the adapter actually reads. Total: a broken import answers {}
        rather than raising into a fan-out.
        """
        try:
            from lib.grip_harness_picker import HARNESS_REGISTRY
        except Exception:
            return {}
        return HARNESS_REGISTRY.get(self.harness) or {}

    def available(self) -> bool:
        """True only when the registry knows this harness AND its one-shot binary
        resolves on PATH.

        A harness with no `oneshot_argv` is NOT dispatchable: refusing it beats
        guessing an argv that happens to open an interactive TUI and hang the
        workflow with no diagnosis. The `verified` flag is deliberately NOT
        consulted — it records whether an invocation has been confirmed against a
        live binary, not whether the binary is present, and gating on it would
        make an installed-but-unverified harness undispatchable.
        """
        spec = self._spec()
        argv = spec.get("oneshot_argv")
        if not argv:
            return False
        # Probe argv[0], the literal executable that dispatch will spawn, rather
        # than the registry's `binary` — they agree today, and if they ever
        # diverge it is argv[0] that decides whether the spawn can succeed.
        return shutil.which(str(argv[0])) is not None

    def dispatch(self, task: Any) -> Any:
        """Spawn the harness one-shot, send the task, return its stdout.

        Never raises into the kernel: an unknown harness, a missing invocation, a
        failed spawn, a timeout or a non-zero exit each return a structured error
        dict carrying enough detail to diagnose the peer.
        """
        self.calls += 1
        argv: list[str] = []
        try:
            spec = self._spec()
            if not spec:
                return self._error(
                    "unknown_harness",
                    f"no registry entry for harness {self.harness!r}")
            template = spec.get("oneshot_argv")
            if not template:
                return self._error(
                    "no_oneshot_invocation",
                    f"harness {self.harness!r} declares no one-shot invocation")
            prompt = _prompt_from(task)
            delivery = spec.get("delivery", "argv")
            argv = [str(tok) for tok in template]
            if delivery != "stdin":
                argv = [tok.replace("{prompt}", prompt) for tok in argv]
            model_flag = spec.get("model_flag")
            if self.model and model_flag:
                argv += [str(part).format(model=self.model) for part in model_flag]
            stdin_text = prompt if delivery == "stdin" else None
        except Exception as exc:                       # total, never raising
            return self._error("bad_task", f"{type(exc).__name__}: {exc}")

        try:
            proc = subprocess.run(
                argv, input=stdin_text, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run kills the child before raising, so nothing leaks;
            # the error names the timeout so an operator can tell a slow peer
            # from a broken one.
            return self._error(
                "timeout",
                f"harness {self.harness!r} exceeded {self.timeout}s", argv=argv)
        except OSError as exc:
            return self._error(
                "spawn_failed", f"{type(exc).__name__}: {exc}", argv=argv)
        except Exception as exc:                       # belt and braces
            return self._error(
                "dispatch_failed", f"{type(exc).__name__}: {exc}", argv=argv)

        if proc.returncode != 0:
            return self._error(
                "nonzero_exit",
                f"harness {self.harness!r} exited {proc.returncode}",
                argv=argv, returncode=proc.returncode,
                stdout=proc.stdout, stderr=proc.stderr)
        try:
            return proc.stdout.strip()
        except Exception as exc:                        # e.g. stdout is None
            return self._error(
                "bad_output", f"{type(exc).__name__}: {exc}", argv=argv)

    def _error(self, kind: str, detail: str, *, argv: Any = None,
               returncode: Any = None, stdout: Any = None,
               stderr: Any = None) -> dict:
        """A structured, bounded error result — the degraded (never aborted) shape.

        stdout/stderr are truncated for the same reason HALDispatchError bounds
        its excerpt: a runaway peer emitting megabytes must not blow up the
        fan-out that is trying to report it.
        """
        result: dict = {
            "error": kind,
            "harness": self.harness,
            "detail": detail,
            "argv": list(argv or []),
            "timeout": self.timeout,
        }
        if returncode is not None:
            result["returncode"] = returncode
        if stdout is not None:
            result["stdout"] = stdout[-2000:]
        if stderr is not None:
            result["stderr"] = (stderr or "").strip()[:200]
        return result
