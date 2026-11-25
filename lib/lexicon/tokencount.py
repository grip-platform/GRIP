"""The Tokenizer Tribunal: token counters that say how far they can be trusted.

A character count and a token count disagree, and they disagree differently on
each vendor's tokenizer. Measured on one rule passage, wenyan (classical
Chinese) used 12% of the characters but 34-62% of the tokens, so a meter that
counts characters, or estimates bytes / 4, rewards exactly the encodings that
lose. Every figure KERN reports therefore names the counter that produced it,
and every counter carries a ``kind``:

  exact     the vendor's own tokenizer for its own models
  proxy     a real tokenizer standing in for one we cannot run (gpt2 for a
            weak, older vocabulary)
  estimate  arithmetic, not a tokenizer (bytes / 3.7, the rules budget's unit)

Claude's tokenizer is not published. :class:`ClaudeDifferential` measures it
the only way available without API credit: it runs ``claude -p`` twice, once
with the text and once without, and subtracts the input-token totals. Anything
the harness adds (its system prompt, memory files) appears in both calls and
cancels. It spends real model calls, so it is opt-in and always cached.

The cache is content-addressed: the key is the counter id plus the sha256 of
the text, so an edit can never be served a stale count.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

BYTES_PER_TOKEN = 3.7  # the unit of tests/test_rules_scope.py's 40,000 budget


class Counter(Protocol):
    id: str
    kind: str

    def count(self, text: str) -> int: ...


class CountError(RuntimeError):
    """A counter could not produce a number. Never silently turned into zero."""


@dataclass(frozen=True)
class BytesEstimate:
    """UTF-8 bytes / 3.7: the estimate lib/rules_scope.py budgets in."""

    id: str = "bytes/3.7"
    kind: str = "estimate"

    def count(self, text: str) -> int:
        return round(len(text.encode("utf-8")) / BYTES_PER_TOKEN)


@dataclass(frozen=True)
class Tiktoken:
    """A tiktoken encoding. o200k and cl100k are exact for OpenAI; gpt2 is a proxy."""

    encoding: str
    kind: str = "exact"

    @property
    def id(self) -> str:
        return f"tiktoken:{self.encoding}"

    def count(self, text: str) -> int:
        try:
            import tiktoken
        except ImportError as exc:
            raise CountError(f"{self.id}: tiktoken is not installed") from exc
        return len(tiktoken.get_encoding(self.encoding).encode(text, disallowed_special=()))


_BASELINE = "Reply with the single word OK."


# Where the login lives is the one Claude setting a call inherits. Any other
# would let the calling session steer a measurement: ANTHROPIC_BASE_URL can
# route through a proxy that serves another model under the requested name, an
# effort level changes how hard the model reads, and API-key variables switch
# billing to a key that may have no credit.
_INHERITED = frozenset({"CLAUDE_CONFIG_DIR"})


def isolated_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "ANTHROPIC")) or k in _INHERITED}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def run_claude(model: str, prompt: str, system: str | None = None,
               timeout: int = 300) -> dict:
    """One isolated ``claude -p`` call, returning its final JSON result record.

    The prompt is the only way in. ``--tools ""`` removes every tool and
    ``--strict-mcp-config`` every MCP server: ``--allowedTools ""`` only
    pre-approves nothing, and a canary test on 2026-09-29 found a model under
    it still reading a file in its working directory. Settings, slash commands
    and auto-memory are off, and each call runs in a private empty directory
    that holds nothing but its own system prompt.
    """
    with tempfile.TemporaryDirectory(prefix="kern-claude-") as work:
        argv = ["claude", "-p", "--model", model, "--setting-sources", "",
                "--settings", json.dumps({"autoMemoryEnabled": False}),
                "--disable-slash-commands", "--tools", "", "--strict-mcp-config",
                "--output-format", "json"]
        if system is not None:
            system_file = Path(work) / "system.md"
            system_file.write_text(system, encoding="utf-8")
            argv += ["--system-prompt-file", str(system_file)]
        proc = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                              timeout=timeout, env=isolated_env(), cwd=work)
    if proc.returncode != 0:
        raise CountError(f"claude -p exited {proc.returncode}: {proc.stderr[:200]}")
    data = json.loads(proc.stdout)
    return data[-1] if isinstance(data, list) else data


def input_total(result: dict, model: str) -> int:
    """Total input tokens of one result record, refusing any record that errored.

    ``is_error`` is checked rather than the subtype, because both a served
    answer and an unknown model can report ``subtype: success``. The model must
    also appear in ``modelUsage``, or the count belongs to some other model.
    """
    if result.get("is_error") or model not in (result.get("modelUsage") or {}):
        raise CountError(f"claude -p did not serve {model}: {str(result)[:200]}")
    usage = result.get("usage") or {}
    return sum(int(usage.get(k) or 0) for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


@dataclass
class ClaudeDifferential:
    """Claude's own count: input tokens with the text minus input tokens without it."""

    model: str = "claude-opus-5-5"
    runner: Callable[[str, str], dict] = run_claude
    kind: str = "exact"
    _baseline: int | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return f"claude:{self.model}"

    def _baseline_total(self) -> int:
        if self._baseline is None:
            self._baseline = input_total(self.runner(self.model, _BASELINE), self.model)
        return self._baseline

    def count(self, text: str) -> int:
        with_text = input_total(self.runner(self.model, f"{text}\n\n{_BASELINE}"), self.model)
        return with_text - self._baseline_total()


def text_key(counter_id: str, text: str) -> str:
    return f"{counter_id}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


@dataclass
class Cached:
    """Wraps a counter with a JSON cache keyed by counter id and text sha256."""

    inner: Counter
    path: Path

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def kind(self) -> str:
        return self.inner.kind

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def count(self, text: str) -> int:
        cache = self._load()
        key = text_key(self.inner.id, text)
        if key not in cache:
            cache[key] = self.inner.count(text)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(cache, sort_keys=True, indent=0), encoding="utf-8")
        return cache[key]


def standard(names: list[str]) -> list[Counter]:
    """Counters by short name: o200k, cl100k, gpt2, bytes. Unknown names raise."""
    table: dict[str, Callable[[], Counter]] = {
        "o200k": lambda: Tiktoken("o200k_base"),
        "cl100k": lambda: Tiktoken("cl100k_base"),
        "gpt2": lambda: Tiktoken("gpt2", kind="proxy"),
        "bytes": BytesEstimate,
    }
    unknown = [n for n in names if n not in table]
    if unknown:
        raise ValueError(f"unknown tokenizer(s) {unknown}; choose from {sorted(table)}")
    return [table[n]() for n in names]
