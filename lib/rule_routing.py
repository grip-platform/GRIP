#!/usr/bin/env python3
"""Which rules reach which subagent under the routes live today (KERN v2, phase 1).

config/rules-scope.json tags every rule with its readers, the capabilities a
reader must hold to need it (lib/rules_scope.READERS). This module works out who
each rule actually REACHES, so the two can be compared:

  channel   where a rule is delivered, read from the live configuration rather
            than stored a second time: `floor` (loads natively in every session
            and in every subagent that loads rules), `file` (loads natively when a
            file matching its `paths:` is read or written), `main` (kept out of
            native loading; the main session gets the full text from
            hooks/main-tier-inject.py, and no subagent gets anything) or `index`
            (kept out of native loading; the main session gets one line of an
            index from hooks/subagent-preload-main-inject.py, and no subagent gets
            anything).
  reader    a subagent type, with the capabilities its declared tools give it and
            whether it loads rules natively at all: Claude Code's Explore and Plan
            agents load no CLAUDE.md files or rules.
  gap       a rule a subagent needs, by those capabilities, that no live channel
            delivers to it.

Gaps are reported, never enforced: phase 2's task brief is what closes them, and
until then they are the measured cost of keeping extended rules out of native
loading. Pinned rules are different: lib/rules_scope.validate() refuses a pinned
rule (MUST_REACH) any route that misses its readers.

A `file` rule counts as delivered to every subagent that loads rules natively,
although it loads only when a matching file is read or written through Claude
Code's file tools, and only at that write: a file written through Bash never
loads it, and the first write ignores it (docs/measurements/2026-10-06-kern-v2-
vendor-probes.md). The report says so beside the file channel.

CLI: grip-py lib/rule_routing.py [--root CHECKOUT] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import yaml

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import rules_scope  # noqa: E402
from lexicon import surfaces  # noqa: E402

# What a tool lets a subagent do. Bash can do all of it: write code through a
# redirect, commit and push, and send text out with curl (GPT-6 Astra on the KERN
# v2 routing design: declared tools understate what Bash can do).
TOOL_CAPABILITIES = {
    "Bash": frozenset({"runs-shell", "writes-code", "writes-repo", "sends-outside"}),
    "Edit": frozenset({"writes-code"}),
    "MultiEdit": frozenset({"writes-code"}),
    "Write": frozenset({"writes-code"}),
    "NotebookEdit": frozenset({"writes-code"}),
    "WebFetch": frozenset({"sends-outside"}),
    "WebSearch": frozenset({"sends-outside"}),
    "Agent": frozenset({"dispatches"}),
    "Task": frozenset({"dispatches"}),
}

# Tools that only read, plan, or talk to this machine: no capability beyond the
# claims every agent makes.
QUIET_TOOLS = frozenset({
    "Read", "Grep", "Glob", "LS", "TodoWrite", "TodoRead", "TaskCreate", "TaskList", "TaskGet",
    "TaskUpdate", "TaskOutput", "TaskStop", "SendMessage", "Skill", "ToolSearch", "ExitPlanMode",
    "AskUserQuestion", "ListMcpResourcesTool", "ReadMcpResourceTool",
})

# A tool this table does not know, an MCP tool among them, is assumed able to do
# everything a tool can: routing a rule too many costs context, one too few loses it.
EVERY_TOOL_CAPABILITY = frozenset().union(*TOOL_CAPABILITIES.values())

# Capabilities a subagent takes from its task, never from a tool: a task that handles
# personal data needs the rules for it whatever tools the agent holds. The report
# counts them separately rather than as absent (Codex on GRIP#7691).
TASK_CAPABILITIES = rules_scope.SUBAGENT_READERS - EVERY_TOOL_CAPABILITY - {"makes-claims"}

TIER_CHANNEL = {"core-rule": "floor", "path-scoped": "file", "main-tier": "main",
                "command-moment": "command", "extended": "index"}
# A command-moment rule reaches a subagent the way a path-scoped one does: at the
# moment it matters, from the teach-once gate on its first matching Bash call.
NATIVE_CHANNELS = frozenset({"floor", "file", "command"})


@dataclass(frozen=True)
class Reader:
    name: str
    capabilities: frozenset[str]
    native: bool          # loads rules natively; False for Explore, Plan, and omitClaudeMd


def capabilities(tools) -> frozenset[str]:
    """The capabilities a tool list gives; None or "*" means every tool."""
    if tools is None or tools == "*" or "*" in tools:
        return EVERY_TOOL_CAPABILITY | {"makes-claims"}
    caps = {"makes-claims"}
    for tool in tools:
        if tool in TOOL_CAPABILITIES:
            caps |= TOOL_CAPABILITIES[tool]
        elif tool not in QUIET_TOOLS:
            caps |= EVERY_TOOL_CAPABILITY
    return frozenset(caps)


# Claude Code's built-in subagents, which have no file in agents/: (tools, loads
# rules natively). Tools as Claude Code 2.1.289 lists them (2026-10-06): Explore and
# Plan may use every tool but Agent and the file writers, and load no CLAUDE.md
# files or rules at all.
_READ_AND_RUN = ("Bash", "Glob", "Grep", "Read", "WebFetch", "WebSearch")
BUILTIN_AGENTS = {
    "Explore": (_READ_AND_RUN, False),
    "Plan": (_READ_AND_RUN, False),
    "general-purpose": (None, True),
    "claude-code-guide": (("Bash", "Read", "WebFetch", "WebSearch"), True),
    "statusline-setup": (("Read", "Edit"), True),
}


def _definition(path: Path) -> Reader | None:
    """A Reader from an agents/*.md file, or None when the file defines no agent."""
    meta = yaml.safe_load(surfaces.frontmatter(path.read_text(encoding="utf-8"))) or {}
    if not isinstance(meta, dict) or not meta.get("name"):
        return None
    tools = meta.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    return Reader(str(meta["name"]), capabilities(tools), not meta.get("omitClaudeMd", False))


def readers(root: Path) -> list[Reader]:
    """Every subagent type: the definitions anywhere under agents/ (Claude Code also loads
    agents/bridge/, Codex on GRIP#7691) and Claude Code's built-ins."""
    found = [_definition(p) for p in sorted((root / "agents").rglob("*.md")) if not p.name.startswith("_")]
    defined = {r.name: r for r in found if r is not None}
    builtins = {n: Reader(n, capabilities(tools), native)
                for n, (tools, native) in BUILTIN_AGENTS.items() if n not in defined}
    return sorted({**builtins, **defined}.values(), key=lambda r: r.name)


def channels(root: Path) -> dict[str, str]:
    """The live channel of every rule under ``root``."""
    return {s.rel.removeprefix("rules/"): TIER_CHANNEL[s.tier] for s in surfaces.rules(root)}


def needed(reader: Reader, cfg: dict, names, task: frozenset[str] = frozenset()) -> set[str]:
    """The rules ``reader`` needs for its tools, plus any capabilities its task brings."""
    caps = reader.capabilities | task
    return {n for n in names if rules_scope.readers(n, cfg) & caps}


def delivered(reader: Reader, routes: dict[str, str]) -> set[str]:
    if not reader.native:
        return set()
    return {n for n, c in routes.items() if c in NATIVE_CHANNELS}


def _gaps(found: list[Reader], routes: dict[str, str], cfg: dict,
          task: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    return {r.name: sorted(needed(r, cfg, routes, task) - delivered(r, routes)) for r in found}


def gaps(root: Path, cfg: dict | None = None,
         task: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """For each subagent type, the rules it needs, with ``task``'s capabilities added, that
    no live channel delivers to it."""
    cfg = cfg or rules_scope.load_config(root / "config" / "rules-scope.json")
    return _gaps(readers(root), channels(root), cfg, task)


def task_gaps(found: list[Reader], routes: dict[str, str], cfg: dict, missing: dict) -> dict:
    """The rules a subagent misses only when its task handles personal data (or another
    task-derived capability): the gaps with those capabilities added, less the plain gaps."""
    extra = {name: sorted(set(rules) - set(missing[name]))
             for name, rules in _gaps(found, routes, cfg, TASK_CAPABILITIES).items()}
    return {"capabilities": sorted(TASK_CAPABILITIES),
            "native": sorted(set(chain.from_iterable(extra[r.name] for r in found if r.native))),
            "not_native": {r.name: extra[r.name] for r in found if not r.native}}


def unneeded_on_floor(routes: dict[str, str], cfg: dict) -> list[str]:
    """Floor rules no subagent needs: every subagent that loads rules pays for them, so
    they are the main-session tier's candidates (AGORA's falsifier on GRIP#7687)."""
    return sorted(n for n, c in routes.items()
                  if c == "floor" and not rules_scope.readers(n, cfg) & rules_scope.SUBAGENT_READERS)


def report(root: Path) -> dict:
    """Channel counts, readers, and the gaps split by whether the reader loads rules at all.

    The checkout is read once, so every figure describes the same state; it used to be
    read three times per report, and an edit between reads could mix two (AGORA on
    GRIP#7691)."""
    cfg = rules_scope.load_config(root / "config" / "rules-scope.json")
    routes = channels(root)
    found = readers(root)
    missing = _gaps(found, routes, cfg)
    # One counted pass: for each rule, how many natively loading agents lack it.
    counts = Counter(chain.from_iterable(missing[r.name] for r in found if r.native))
    return {
        "channels": {c: sum(1 for v in routes.values() if v == c) for c in TIER_CHANNEL.values()},
        "readers": len(found),
        "not_native": sorted(r.name for r in found if not r.native),
        "native_gaps": {n: {"readers": sorted(rules_scope.readers(n, cfg)), "agents": k}
                        for n, k in sorted(counts.items())},
        "not_native_needs": {r.name: len(missing[r.name]) for r in found if not r.native},
        "floor_unneeded": unneeded_on_floor(routes, cfg),
        "task_gaps": task_gaps(found, routes, cfg, missing),
    }


def render(rep: dict) -> str:
    ch = rep["channels"]
    lines = [f"Live channels: {ch['floor']} floor, {ch['file']} file (load on a matching Read, "
             f"Write or Edit, and only at that write), {ch['main']} main (main session only, in "
             f"full), {ch['command']} command (taught at the first matching Bash command, main "
             f"session and subagents alike), {ch['index']} index (main session only, one line each).",
             f"Subagent types: {rep['readers']}; loading no rules natively: "
             f"{', '.join(rep['not_native'])}.",
             f"Rules a natively loading subagent needs and cannot receive: {len(rep['native_gaps'])}."]
    lines += [f"  {n}: needed by {', '.join(v['readers'])} ({v['agents']} agent types)"
              for n, v in rep["native_gaps"].items()]
    lines += [f"{name} needs {count} rules and receives none." for name, count in rep["not_native_needs"].items()]
    lines.append(f"Floor rules no subagent needs, which every subagent still loads: "
                 f"{len(rep['floor_unneeded'])} ({', '.join(rep['floor_unneeded'])}).")
    tg = rep["task_gaps"]
    lines.append(f"When the task brings {', '.join(tg['capabilities'])}: natively loading subagents "
                 f"also miss {len(tg['native'])} rules ({', '.join(tg['native']) or 'none'}).")
    lines += [f"  {name} also misses {len(rules)} ({', '.join(rules) or 'none'})."
              for name, rules in tg["not_native"].items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rule_routing", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(_LIB.parent), help="checkout to read (default: this one)")
    ap.add_argument("--json", action="store_true",
                    help="print the report's raw figures as JSON instead of sentences")
    args = ap.parse_args(argv)
    rep = report(Path(args.root).resolve())
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
