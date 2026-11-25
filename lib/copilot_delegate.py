#!/usr/bin/env python3
"""Copilot Delegate — token-economy routing to GitHub Copilot CLI (free GPT-4.1).

Reserves Claude's Premium context window for multi-file reasoning, refactors,
and test writing. Routes cheap factual queries, explanations, command
suggestions, and routine diff reviews to Copilot CLI.

Usage:
    from lib.copilot_delegate import CopilotDelegate
    out = CopilotDelegate().ask("What does xargs -I{} do?")

Each method shells out via subprocess to:
    copilot -p "<prompt>" --model gpt-4.1 --allow-all-tools --no-color \
            --output-format text -s

Captures stdout. Surfaces stderr if non-empty. 30s timeout.
"""

from __future__ import annotations

import subprocess
import sys

_BASE_FLAGS: tuple[str, ...] = (
    "--model", "gpt-4.1",
    "--allow-all-tools",
    "--no-color",
    "--output-format", "text",
    "-s",
)
_TIMEOUT_S: int = 30
_NOT_INSTALLED_MSG: str = (
    "[copilot-delegate] Copilot CLI not installed. "
    "Install via `gh extension install github/gh-copilot` "
    "or `npm install -g @githubnext/github-copilot-cli`."
)


class CopilotDelegate:
    """Single class wrapping the four delegation modes."""

    def __init__(self, binary: str = "copilot") -> None:
        self.binary = binary

    def ask(self, prompt: str) -> str:
        """Surface a single factual answer verbatim."""
        return self._run_copilot(prompt)

    def explain(self, topic: str) -> str:
        """Concise explanation of a general topic."""
        shaped = f"Explain concisely (max 6 sentences): {topic}"
        return self._run_copilot(shaped)

    def suggest(self, task: str) -> str:
        """Return ONE shell command for the task. No prose."""
        shaped = (
            f"Return ONLY the single shell command (no prose, no markdown, "
            f"no explanation) that accomplishes: {task}"
        )
        return self._run_copilot(shaped)

    def review(self, diff_or_path: str) -> str:
        """First-pass review. Bullets grouped by severity."""
        shaped = (
            "Review the following diff or code. Output bullets grouped by "
            "severity (Blocker / Warning / Nit). If clean, output exactly "
            "'LGTM' and nothing else.\n\n"
            f"{diff_or_path}"
        )
        return self._run_copilot(shaped)

    def _run_copilot(self, prompt: str) -> str:
        """Shared subprocess invocation. CC = 5."""
        cmd = [self.binary, "-p", prompt, *_BASE_FLAGS]
        try:
            # run_text, not subprocess.run(text=True): this returns an LLM's
            # answer verbatim to the caller, and LLM prose is full of characters
            # cp1252 cannot round-trip.
            from _compat import run_text
            result = run_text(
                cmd, capture_output=True, timeout=_TIMEOUT_S,
            )
        except FileNotFoundError:
            return _NOT_INSTALLED_MSG
        except subprocess.TimeoutExpired:
            return f"[copilot-delegate] timeout after {_TIMEOUT_S}s"
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if err and result.returncode != 0:
            return f"{out}\n[copilot-delegate stderr] {err}".strip()
        return out


def _cli_main() -> int:
    """CLI: python3 lib/copilot_delegate.py {ask|explain|suggest|review} <text>."""
    argv = sys.argv[1:]
    if len(argv) < 2 or argv[0] not in ("ask", "explain", "suggest", "review"):
        print("Usage: copilot_delegate.py {ask|explain|suggest|review} <text>")
        return 1
    method, text = argv[0], " ".join(argv[1:])
    out = getattr(CopilotDelegate(), method)(text)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
