"""The generic tool-call policy engine under GRIP's MCP capability locks.

Second instance (YSH at N=2). ``lib/rea_tool_policy.py`` was the first lock:
a prefix, two classes, one operator switch, unknown names denied. The
grip-browser lock needs the same mechanics plus effect classes, per-argument
rules and context rules, so the mechanics live here once and each server
carries a configuration:

- ``lib/rea_tool_policy.py`` stays text-identical (its tests pin the source
  and ``lib/auto_harness.py`` execs it); ``tests/test_mcp_tool_policy.py``
  proves by conformance that a ``Policy`` built from REA's frozensets returns
  REA's verdicts over REA's own fixture. At N=3 REA's body moves onto this
  engine in one edit.
- ``lib/grip_browser_tool_policy.py`` is the first configuration written
  directly against it.

The contract (``rules/untrusted-content-capability-lock.md``): an allowlist
whose unknown names degrade to FEWER capabilities, never more. So
``evaluate`` denies an unknown name, a class whose operator switch is unset,
a class whose context rule refuses, and any argument rule that refuses or
RAISES: a rule that cannot decide is a deny, not an allow. Every switch is
exactly the string ``"1"``; nothing looser opens a class.

Pure: stdlib only, no I/O. The caller resolves the environment into a
``context`` dict (which classes the operator opened, what the artefact root
is, whether a persistent profile is configured) and passes it in, so the
engine can be scored against labelled examples with no environment at all.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import chain
from typing import Callable, Mapping

UNKNOWN = "unknown"
OPEN_CLASSES = "open_classes"

# A rule returns None to pass, or a sentence saying why not. Raising is a deny.
ArgumentRule = Callable[[dict, dict], "str | None"]
ContextRule = Callable[[dict], "str | None"]
Reclassifier = Callable[[str, dict], "str | None"]


def class_index(classes: Mapping[str, frozenset]) -> dict:
    """``tool -> class``, one hash lookup per call. A tool in two classes keeps
    the LAST one here; ``check_policy`` reports the duplicate."""
    index: dict = {}
    for cls, members in classes.items():
        index.update(dict.fromkeys(members, cls))
    return index


@dataclass(frozen=True)
class Policy:
    """One server's capability lock as data."""

    server: str                                   # the name in messages, e.g. "grip-browser"
    prefix: str                                   # "mcp__grip-browser__"
    module: str                                   # where an unknown tool gets classified
    catalogue: str                                # the upstream version the names came from
    classes: Mapping[str, frozenset]              # class -> bare tool names
    default_allow: frozenset                      # classes allowed with no switch
    switches: Mapping[str, str] = field(default_factory=dict)       # class -> env var
    class_notes: Mapping[str, str] = field(default_factory=dict)    # class -> what it does
    argument_rules: Mapping[str, ArgumentRule] = field(default_factory=dict)  # tool -> rule
    context_rules: Mapping[str, ContextRule] = field(default_factory=dict)    # class -> rule
    switch_exempt: frozenset = frozenset()        # tools whose class switch is not required
    reclassify: Reclassifier | None = None        # (tool, context) -> class, or None

    def index(self) -> dict:
        return class_index(self.classes)

    def known_tools(self) -> frozenset:
        return frozenset(chain.from_iterable(self.classes.values()))


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    tool: str
    tool_class: str
    reason: str = ""


def check_policy(policy: Policy) -> list[str]:
    """Structural problems a configuration must not have: a tool in two
    classes, a switch or rule naming a class or tool that does not exist."""
    problems = []
    counts = Counter(chain.from_iterable(policy.classes.values()))
    problems += [f"{name!r} is in more than one class" for name, n in counts.items() if n > 1]
    referenced = set(policy.default_allow) | set(policy.switches) | set(policy.context_rules)
    problems += [f"class {cls!r} is referenced but not defined"
                 for cls in sorted(referenced - set(policy.classes))]
    ruled = set(policy.argument_rules) | set(policy.switch_exempt)
    problems += [f"tool {name!r} has a rule but no class"
                 for name in sorted(ruled - policy.known_tools())]
    return problems


def bare_name(policy: Policy, tool: str) -> str:
    """``mcp__<server>__navigate`` and ``navigate`` name the same tool; a name
    under ANOTHER server's prefix stays as it is, so it classifies as unknown."""
    name = (tool or "").strip()
    return name[len(policy.prefix):] if name.startswith(policy.prefix) else name


def tool_class(policy: Policy, tool: str, context: dict | None = None) -> str:
    """The class a call is judged as, after any context-driven reclassification."""
    name = bare_name(policy, tool)
    if policy.reclassify is not None:
        try:
            override = policy.reclassify(name, context or {})
        except Exception:  # noqa: BLE001 -- a reclassifier that cannot decide decides nothing
            override = None
        if override in policy.classes:
            return override
    return policy.index().get(name, UNKNOWN)


def open_classes_from_env(policy: Policy, environ: Mapping | None) -> frozenset:
    """The classes whose switch is exactly ``"1"`` in ``environ``."""
    env = environ or {}
    return frozenset(cls for cls, var in policy.switches.items() if env.get(var) == "1")


def _unknown_reason(policy: Policy, name: str) -> str:
    return (f"[GRIP] {policy.server} tool '{name}' is not classified in {policy.module} "
            f"({policy.catalogue} catalogue). An unknown capability degrades to fewer "
            f"capabilities, never more: classify it there (and mark it in the keep-up "
            f"ledger) and the call is allowed on the next session.")


def _switch_reason(policy: Policy, name: str, cls: str) -> str:
    note = policy.class_notes.get(cls, f"is in the {cls} class")
    var = policy.switches.get(cls)
    way_out = (f"It is denied unless the operator launches with {var}=1"
               if var else "No switch opens it in this version")
    return f"[GRIP] {policy.server} tool '{name}' {note}. {way_out} ({policy.module})."


def _run_rule(call, name: str, cls: str, policy: Policy) -> str | None:
    try:
        reason = call()
    except Exception as exc:  # noqa: BLE001 -- a rule that raises is a deny, by contract
        return (f"[GRIP] {policy.server} tool '{name}' ({cls}) was refused because its "
                f"rule could not decide: {type(exc).__name__}: {exc}. A rule that cannot "
                f"decide denies ({policy.module}).")
    if reason is None or reason is True:
        return None
    if reason is False:
        return f"[GRIP] {policy.server} tool '{name}' ({cls}) was refused by its rule ({policy.module})."
    return str(reason)


def evaluate(policy: Policy, tool: str, args: dict | None, context: dict | None) -> Verdict:
    """The lock. Order: unknown -> switch -> class context rule -> argument rule.
    The first refusal wins and names the way out."""
    name = bare_name(policy, tool)
    ctx = dict(context or {})
    cls = tool_class(policy, name, ctx)
    if cls == UNKNOWN:
        return Verdict(False, name, cls, _unknown_reason(policy, name))
    opened = frozenset(ctx.get(OPEN_CLASSES) or ())
    needs_switch = cls not in policy.default_allow and name not in policy.switch_exempt
    if needs_switch and cls not in opened:
        return Verdict(False, name, cls, _switch_reason(policy, name, cls))
    rule = policy.context_rules.get(cls)
    reason = _run_rule(lambda: rule(ctx), name, cls, policy) if rule is not None else None
    if reason:
        return Verdict(False, name, cls, reason)
    arg_rule = policy.argument_rules.get(name)
    reason = _run_rule(lambda: arg_rule(args or {}, ctx), name, cls, policy) if arg_rule is not None else None
    if reason:
        return Verdict(False, name, cls, reason)
    return Verdict(True, name, cls, "")


def is_legal(policy: Policy, tool: str, args: dict | None, context: dict | None) -> bool:
    """The AutoHarness-shaped predicate: True iff ``evaluate`` allows."""
    return evaluate(policy, tool, args, context).allowed


def _scored(policy: Policy, example: dict, prepare) -> bool:
    ctx = example.get("context") or {}
    if prepare is not None:
        ctx = prepare(dict(ctx))
    try:
        verdict = is_legal(policy, example["tool"], example.get("args") or {}, ctx)
    except Exception:  # noqa: BLE001
        verdict = False
    return verdict == bool(example["legal"])


def score(policy: Policy, examples: list[dict], prepare: Callable[[dict], dict] | None = None) -> float:
    """Legal-action rate over labelled examples ``{tool, args, context, legal}``,
    the ``lib/auto_harness.evaluate_harness`` measure without exec: a policy
    that is data cannot be scored by exec'ing its source, so it is scored by
    calling it. ``prepare`` maps a fixture's context (JSON) onto the live shape
    (sets, paths) before evaluation."""
    if not examples:
        return 0.0
    return sum(1 for ex in examples if _scored(policy, ex, prepare)) / len(examples)
