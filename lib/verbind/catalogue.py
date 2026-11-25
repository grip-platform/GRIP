"""Verbind catalogue -- known verbs, a prompt's leading verb, the read-only list.

``leading_verb`` reads the one leading verb v1 handles, in either of two forms:

  bare      ``/shipwreck the stale PRs``
  explicit  ``/verbind shipwreck the stale PRs``

The explicit form exists because of a measured property of Claude Code
2.1.288 (2026-10-03, a fenced interactive session driven through a
pseudo-terminal): an unknown leading slash command is rejected by the CLI with
"Unknown command: /x" and the prompt never queries, so no UserPromptSubmit hook
runs for it. A plain prompt in the same session did fire the hook. In headless
``claude -p`` the same prompt does reach the hook. So in an interactive session
the bare form cannot reach an adapter, and ``/verbind`` -- a real skill -- is
how the operator coins a verb there.

The argument span is bounded: the rest of the first line, at most
``MAX_ARG_WORDS`` words and ``MAX_ARG_CHARS`` characters.

"Known" errs toward known. A coined verb mistaken for a known one costs
nothing, because the prompt runs exactly as before. A known verb mistaken for a
coined one would interrupt the operator with a question, so every source of
names counts: the skill, mode and agent catalogue (``lib/skill_reachability``),
the command files, the harness's built-in commands, and any namespaced name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Optional

from .gate import digest

__all__ = [
    "BUILTIN_COMMANDS",
    "Catalogue",
    "EXPLICIT_VERB",
    "Entry",
    "KnownVerbs",
    "LeadingVerb",
    "MAX_ARG_CHARS",
    "MAX_ARG_WORDS",
    "READONLY_FILE",
    "READONLY_TOOLS",
    "SUBCOMMANDS",
    "agent_tools",
    "bound_args",
    "is_verb_shape",
    "leading_verb",
    "readonly_operations",
]

EXPLICIT_VERB = "verbind"
#: Words after ``/verbind`` that are its own subcommands, never coined verbs.
SUBCOMMANDS = frozenset({"gate", "refine", "list", "forget", "ultralearned", "help"})
MAX_ARG_WORDS = 64
MAX_ARG_CHARS = 512
READONLY_FILE = Path("config") / "verbind-readonly.json"
#: The only tools an agent may declare and still count as read-only.
READONLY_TOOLS = frozenset({"Read", "Grep", "Glob"})

#: Commands built into Claude Code itself, local and prompt-type, plus its
#: bundled skills. Over-inclusion is harmless (see the module docstring).
BUILTIN_COMMANDS = frozenset({
    "add-dir", "agents", "artifact-capabilities", "artifact-design",
    "artifact-diagramming", "bashes", "branch", "btw", "bug", "claude-api",
    "claude-in-chrome", "clear", "code-review", "compact", "config", "context",
    "copy", "cost", "dataviz", "doctor", "effort", "exit", "export", "fast",
    "feedback", "fewer-permission-prompts", "fork", "goal", "heapdump", "help",
    "hooks", "ide", "init", "insights", "install-github-app", "keybindings-help",
    "login", "logout", "loop", "mcp", "memory", "migrate-installer", "model",
    "output-style", "permissions", "plugin", "plugin-authoring", "pr-comments",
    "privacy-settings", "release-notes", "remote-control", "rename", "resume",
    "review", "rewind", "run", "sandbox", "schedule", "security-review",
    "simplify", "status", "statusline", "stickers", "tasks", "terminal-setup",
    "theme", "todos", "ultraplan", "ultrareview", "update-config", "upgrade",
    "usage", "vim", "voice", "workflow-authoring",
})

_VERB_RE = re.compile(r"[a-z][a-z0-9-]{1,40}")
_LEADING_RE = re.compile(r"^/(?P<token>\S+)(?:[ \t]+(?P<rest>[^\n]*))?")


@dataclass(frozen=True)
class LeadingVerb:
    verb: str
    args: str
    form: str  # "bare" | "explicit"


@dataclass(frozen=True)
class Entry:
    """One catalogue entry: a skill, a mode or an agent."""

    kind: str
    slug: str
    name: str
    description: str
    triggers: tuple = ()

    @property
    def summary(self) -> str:
        return " ".join(self.description.split())[:160]


def is_verb_shape(token: str) -> bool:
    """A coinable verb: lowercase, hyphenated, not namespaced, not a path."""
    return bool(_VERB_RE.fullmatch(token))


def bound_args(rest: str) -> str:
    """The bounded argument span: first line, ``MAX_ARG_WORDS``, ``MAX_ARG_CHARS``."""
    words = (rest or "").split("\n", 1)[0].split()[:MAX_ARG_WORDS]
    return " ".join(words)[:MAX_ARG_CHARS]


def _explicit(rest: str) -> Optional[LeadingVerb]:
    words = (rest or "").split(None, 1)
    if not words:
        return None
    verb = words[0].lower().lstrip("/")
    if verb in SUBCOMMANDS or verb == EXPLICIT_VERB or not is_verb_shape(verb):
        return None
    return LeadingVerb(verb, bound_args(words[1] if len(words) > 1 else ""), "explicit")


def leading_verb(prompt: str) -> Optional[LeadingVerb]:
    """The prompt's one leading verb, or None. Does not decide known/unknown."""
    match = _LEADING_RE.match((prompt or "").lstrip())
    if not match:
        return None
    token, rest = match.group("token"), match.group("rest") or ""
    if token.lower() == EXPLICIT_VERB:
        return _explicit(rest)
    if not is_verb_shape(token):  # paths, namespaced and MCP names, capitals
        return None
    return LeadingVerb(token, bound_args(rest), "bare")


class Catalogue:
    """The skill, mode and agent entries of one install root."""

    def __init__(self, root: Path, entries: Optional[list] = None) -> None:
        self.root = Path(root)
        if entries is not None:
            self.__dict__["entries"] = list(entries)

    @cached_property
    def entries(self) -> list:
        from skill_reachability import build_catalog  # noqa: PLC0415 -- lazy: hooks stay cheap

        catalog = build_catalog(claude_dir=self.root)
        return [Entry(e.kind, e.slug, e.name, e.description, tuple(e.triggers))
                for e in catalog.all_entries]

    def skills(self) -> list:
        return [e for e in self.entries if e.kind == "skill"]

    def find(self, kind: str, slug: str) -> Optional[Entry]:
        return next((e for e in self.entries if e.kind == kind and e.slug == slug), None)

    def exists(self, kind: str, slug: str) -> bool:
        return self.find(kind, slug) is not None

    def digest(self) -> str:
        return digest("verbind/catalogue", *sorted(f"{e.kind}:{e.slug}" for e in self.entries))


def _slash_names(triggers) -> set:
    """``/mode decision`` -> ``mode``; ``/plan`` -> ``plan``."""
    return {t.lstrip("/").split()[0].lower() for t in triggers
            if isinstance(t, str) and t.startswith("/") and t.strip("/").strip()}


class KnownVerbs:
    """Is a verb already a name the operator can invoke? Errs toward yes."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def fast_known(self, verb: str) -> bool:
        """Known by a handful of stat calls, before any catalogue is parsed."""
        if verb in BUILTIN_COMMANDS or verb == EXPLICIT_VERB:
            return True
        r = self.catalogue.root
        candidates = (r / "skills" / verb / "SKILL.md", r / "commands" / f"{verb}.md",
                      r / "agents" / f"{verb}.md", r / "modes" / "definitions" / f"{verb}.yaml",
                      r / "modes" / "definitions" / f"{verb}-mode.yaml")
        return any(p.is_file() for p in candidates)

    @cached_property
    def names(self) -> frozenset:
        found = set()
        for e in self.catalogue.entries:
            found.update({e.slug.lower(), e.name.lower()} | _slash_names(e.triggers))
        commands = self.catalogue.root / "commands"
        if commands.is_dir():
            found.update(p.stem.lower() for p in commands.glob("*.md"))
        return frozenset(found)

    def known(self, verb: str) -> bool:
        return self.fast_known(verb) or verb in self.names


def agent_tools(root: Path, slug: str) -> Optional[list]:
    """The ``tools:`` an agent declares (block or flow list), or None if absent."""
    from skill_reachability import _parse_frontmatter  # noqa: PLC0415 -- one parser in the tree

    path = Path(root) / "agents" / f"{slug}.md"
    if not path.is_file():
        return None
    tools = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")).get("tools")
    if isinstance(tools, list):
        return [str(t).strip().strip("\"'") for t in tools if str(t).strip()]
    if isinstance(tools, str) and tools.strip():
        return [t.strip().strip("\"'") for t in tools.split(",") if t.strip()]
    return None


def _readonly_claims(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(str(e["kind"]), str(e["slug"])) for e in data["entries"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def readonly_operations(catalogue: Catalogue) -> frozenset:
    """The reviewed v1 read-only list, checked against the catalogue.

    Fail-closed: a missing or malformed file is an empty list, so nothing is
    bindable. An entry absent from the catalogue is dropped. An agent entry is
    dropped unless every tool it declares is in ``READONLY_TOOLS``: the harness
    enforces an agent's tool list, so for agents the claim is checked rather
    than trusted. A skill declares no enforced tools, so a skill entry is a
    reviewed claim, and the file's ``why`` field records the review.
    """
    allowed = set()
    for kind, slug in _readonly_claims(catalogue.root / READONLY_FILE):
        if not catalogue.exists(kind, slug):
            continue
        if kind == "agent" and not set(agent_tools(catalogue.root, slug) or ["?"]) <= READONLY_TOOLS:
            continue
        allowed.add((kind, slug))
    return frozenset(allowed)
