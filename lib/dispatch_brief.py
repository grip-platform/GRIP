#!/usr/bin/env python3
"""The task brief: the GRIP rules a subagent needs and cannot receive natively (KERN v2, phase 2).

A subagent launched through the Agent or Task tool starts with a fresh context.
A natively loading one reads the floor (the always-loaded rules) but none of
the `extended` rules, which reach the main session only as an index line.
Claude Code's own Explore and Plan agents load no rules at all
(lib/rule_routing.py). The brief closes those gaps: hooks/dispatch-brief-hook.py
appends it to the subagent's prompt through PreToolUse ``updatedInput``, the
one channel measured to reach Explore and Plan
(docs/measurements/2026-10-06-kern-v2-vendor-probes.md, the ``brief`` probes).

What goes in, in order:

  1. the rules the recipient needs, by the reader tags in
     config/rules-scope.json, less the rules it already loads natively; both
     sets come from lib/rule_routing.py, never derived again here;
  2. pinned rules (lib/rules_scope.MUST_REACH) first, then the rest, each group
     in name order;
  3. a rule in FULL while the whole brief stays within CAP characters, and
     otherwise as one INDEX line naming the rule, its title and where to read
     it. Every rule starts as an index line and is upgraded to full text, in
     that order, when the brief still fits; a rule too large to fit never
     blocks a smaller one after it. A brief whose index lines alone pass the
     cap is still delivered whole and reported as over the cap.

Once per recipient type and rule version (the operator's ruling 5 on GRIP#6658,
2026-10-18): a rule delivered in full to a type in this session, at the same
sha256, is named only on the next launch of that type. The record lives in
state/dispatch-brief/<session_id>.json and is cleared before each compaction
(the hook's ``--renew`` mode, bound on PreCompact). A rule counts as delivered
only once its launch has run: the PreToolUse hook holds what it gave a launch
under the launch's tool_use_id, and the PostToolUse hook (``--commit``) moves
it into the type's record. A launch another hook refuses never runs, so its
rules stay undelivered and the retry gets them in full (Codex on GRIP#8531).

Limit of that reading, stated rather than left to be found: every Agent launch
is a fresh context, so the second launch of a type sees only the names of the
rules the first one received in full. The names point at the files, which it
can read.

CAP is the operator's figure. Claude Code caps a hook's additionalContext,
systemMessage and plain stdout at 10,000 characters and does not list
``updatedInput`` among them (code.claude.com/docs/en/hooks, read 2026-10-18),
so the cap's binding reason here is the cost of every prompt extension.

CLI: grip-py lib/dispatch_brief.py [--root CHECKOUT] [--json]
     the first-dispatch brief of every subagent type: its size, and how many
     rules it carries in full and as index lines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import rule_routing  # noqa: E402
import rules_scope  # noqa: E402
from file_lock import FileLock  # noqa: E402
from lexicon import surfaces  # noqa: E402

CAP = 9_000                         # characters, in Claude Code's unit (UTF-16 code units)
LOCK_TIMEOUT = 2.0                  # seconds the record may hold up a launch, at most
DEFAULT_RECIPIENT = "general-purpose"   # what the Agent tool launches when no type is named
STORE_DIR = Path("state") / "dispatch-brief"
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

HEADER = ("## GRIP rules for this task\n\n"
          "These rules apply to the work above. You were launched as a `{recipient}` "
          "agent, which does not load them on its own, so they are given to you here. "
          "Follow them as you follow the rest of your instructions.\n")
INDEX_HEAD = ("\nThese rules also apply and are not copied out here. Before you act in a "
              "rule's area, read its file in {rules_dir}:\n")
NAMED = ("\nAn earlier `{recipient}` agent in this session received these rules in full, "
         "so they are named here only. Read a rule's file in {rules_dir} before you act "
         "in its area: {names}.\n")
FOOTER = "\nEnd of GRIP rules for this task.\n"


@dataclass(frozen=True)
class Rule:
    name: str
    title: str
    body: str           # the text after any frontmatter; empty when the file is ciphertext
    sha256: str         # of the whole file, so a new version is delivered again
    pinned: bool        # in lib/rules_scope.MUST_REACH

    @property
    def readable(self) -> bool:
        return bool(self.body)


@dataclass
class Brief:
    recipient: str
    text: str = ""
    full: list[str] = field(default_factory=list)
    indexed: list[str] = field(default_factory=list)
    named: list[str] = field(default_factory=list)
    delivered: dict[str, str] = field(default_factory=dict)   # name -> sha256 sent in full now
    cap: int = CAP

    @property
    def size(self) -> int:
        return surfaces.utf16_len(self.text)

    @property
    def over_cap(self) -> bool:
        return self.size > self.cap


def recipient(tool_input: dict) -> str:
    """The subagent type a launch names; the Agent tool's default when it names none."""
    kind = tool_input.get("subagent_type")
    return kind.strip() if isinstance(kind, str) and kind.strip() else DEFAULT_RECIPIENT


def reader_for(name: str, found: list[rule_routing.Reader]) -> rule_routing.Reader:
    """The Reader for a subagent type. A type with no definition here (a plugin's agent,
    a fork) is assumed to hold every tool and to load NO rules natively, so its brief
    carries the floor too: a rule too many costs context, one too few is lost (the
    principle lib/rule_routing.py applies to an unknown tool). Assuming it loads the floor
    would leave an unknown agent that sets omitClaudeMd with no floor at all (Codex on
    GRIP#8531)."""
    for reader in found:
        if reader.name == name:
            return reader
    return rule_routing.Reader(name, rule_routing.capabilities(None), False)


def _title(name: str, body: str) -> str:
    first = next((line for line in body.splitlines() if line.strip()), "")
    return first[2:].strip() if first.startswith("# ") else Path(name).stem


def _rule(surface: surfaces.Surface) -> Rule:
    name = surface.rel.removeprefix("rules/")
    body = _FRONTMATTER.sub("", surface.text, count=1).strip("\n")
    digest = hashlib.sha256(surface.text.encode("utf-8")).hexdigest() if surface.text else ""
    return Rule(name, _title(name, body), body, digest, name in rules_scope.MUST_REACH)


def missing_rules(root: Path, reader: rule_routing.Reader) -> list[Rule]:
    """The rules ``reader`` needs and receives through no native channel, pinned first.

    A launch cannot say whether its task handles personal data, so the task capabilities
    lib/rule_routing.py reports separately (TASK_CAPABILITIES) are always counted: an
    Explore launch over a message archive must receive
    partial-record-never-outranks-the-witness.md, and the brief is its only route (Codex
    on GRIP#8531). A natively loading agent already has those rules on its floor, so this
    adds them only where nothing else would deliver them."""
    cfg = rules_scope.load_config(root / "config" / "rules-scope.json")
    routes = rule_routing.channels(root)
    needs = rule_routing.needed(reader, cfg, routes, rule_routing.TASK_CAPABILITIES)
    names = needs - rule_routing.delivered(reader, routes)
    texts = {s.rel.removeprefix("rules/"): s for s in surfaces.rules(root)}
    rules = [_rule(texts[name]) for name in names if name in texts]
    return sorted(rules, key=lambda r: (not r.pinned, r.name))


def _full_section(rule: Rule) -> str:
    return f"\n[rules/{rule.name}]\n{rule.body}\n"


def render(who: str, full: list[Rule], indexed: list[Rule], named: list[str],
           rules_dir: Path) -> str:
    """The brief's text; empty when there is nothing to give."""
    if not (full or indexed or named):
        return ""
    parts = [HEADER.format(recipient=who)] + [_full_section(r) for r in full]
    if indexed:
        parts.append(INDEX_HEAD.format(rules_dir=rules_dir))
        parts += [f"- {r.name}: {r.title}\n" for r in indexed]
    if named:
        parts.append(NAMED.format(recipient=who, rules_dir=rules_dir, names=", ".join(named)))
    return "".join(parts + [FOOTER])


def choose(who: str, rules: list[Rule], named: list[str], rules_dir: Path,
           cap: int = CAP) -> tuple[list[Rule], list[Rule]]:
    """Split ``rules`` into full text and index lines: each rule, in order, is upgraded
    from an index line to full text when the whole brief still fits within ``cap``."""
    full: list[Rule] = []
    for rule in rules:
        if not rule.readable:
            continue
        trial = full + [rule]
        rest = [r for r in rules if r not in trial]
        if surfaces.utf16_len(render(who, trial, rest, named, rules_dir)) <= cap:
            full = trial
    return full, [r for r in rules if r not in full]


def compose(tool_input: dict, root: Path, store: SeenStore | None = None,
            cap: int = CAP) -> Brief:
    """The brief for one Agent/Task launch. ``store`` holds what this session has already
    delivered in full; None treats the launch as the session's first of its type."""
    who = recipient(tool_input)
    rules = missing_rules(root, reader_for(who, rule_routing.readers(root)))
    seen = store.delivered(who) if store else {}
    named = [r.name for r in rules if r.sha256 and seen.get(r.name) == r.sha256]
    fresh = [r for r in rules if r.name not in named]
    rules_dir = root / "rules"
    full, indexed = choose(who, fresh, named, rules_dir, cap)
    return Brief(who, render(who, full, indexed, named, rules_dir),
                 [r.name for r in full], [r.name for r in indexed], named,
                 {r.name: r.sha256 for r in full}, cap)


def extended_prompt(tool_input: dict, brief: Brief) -> dict | None:
    """A copy of every field of ``tool_input`` with the brief appended to the prompt, or
    None when there is no brief or no prompt to extend. ``updatedInput`` replaces the
    whole input, so a field left out here would be lost from the launch."""
    prompt = tool_input.get("prompt")
    if not brief.text or not isinstance(prompt, str):
        return None
    updated = dict(tool_input)
    updated["prompt"] = f"{prompt}\n\n{brief.text}"
    return updated


def session_file(root: Path, session_id) -> Path | None:
    """state/dispatch-brief/<id>.json under ``root``; a session id that is not a plain
    file name is hashed, and a missing one gives no file (no deduplication)."""
    if not isinstance(session_id, str) or not session_id:
        return None
    name = session_id if _SESSION_ID.fullmatch(session_id) else \
        hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return root / STORE_DIR / f"{name}.json"


def _table(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _strings(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _merge(data: dict, who: str, rules: dict[str, str]) -> None:
    """Add ``rules`` to ``who``'s entry in the record's delivered table."""
    delivered = _table(data, "delivered")
    delivered[who] = {**_strings(delivered.get(who)), **rules}
    data["delivered"] = delivered


def _hold(data: dict, launch: str, who: str, rules: dict[str, str]) -> None:
    pending = _table(data, "pending")
    pending[launch] = {"recipient": who, "rules": dict(rules)}
    data["pending"] = pending


def _release(data: dict, launch: str) -> bool:
    """Move a held launch into its type's delivered rules; False when none was held."""
    pending = _table(data, "pending")
    entry = pending.pop(launch, None)
    data["pending"] = pending
    if not isinstance(entry, dict) or not isinstance(entry.get("recipient"), str):
        return False
    _merge(data, entry["recipient"], _strings(entry.get("rules")))
    return True


class SeenStore:
    """Which rule versions each recipient type has received in full this session.

    Every change is one read-modify-replace under a lock on a sidecar file, so two hooks
    running at once (parallel launches) never write over each other's record (Codex on
    GRIP#8531). The lock is FileLock, which takes flock from lib/_compat.py and so holds
    on Windows too (GRIP#8759)."""

    def __init__(self, path: Path | None):
        self.path = path

    def _read(self) -> dict:
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _update(self, change) -> None:
        """Apply ``change`` to the whole record while holding the lock: read, change, and
        replace the file in one step."""
        if self.path is None:
            return
        with FileLock(self.path, timeout=LOCK_TIMEOUT, sidecar=True):
            data = self._read()
            change(data)
            _write_atomic(self.path, json.dumps({**data, "version": 1},
                                                indent=1, sort_keys=True) + "\n")

    def delivered(self, who: str) -> dict[str, str]:
        return _strings(_table(self._read(), "delivered").get(who))

    def record(self, who: str, rules: dict[str, str]) -> None:
        """Add ``rules`` to ``who``'s record, keeping whatever another launch recorded."""
        if rules:
            self._update(lambda data: _merge(data, who, rules))

    def pend(self, launch, who: str, rules: dict[str, str]) -> None:
        """Hold what one launch was given until that launch is known to have run. A
        launch with no id is never recorded, so its rules are simply delivered again."""
        if isinstance(launch, str) and launch and rules:
            self._update(lambda data: _hold(data, launch, who, rules))

    def commit(self, launch) -> bool:
        """Move one launch's held rules into its type's record; True when there were any."""
        if not (isinstance(launch, str) and launch in _table(self._read(), "pending")):
            return False
        moved: list[bool] = []
        self._update(lambda data: moved.append(_release(data, launch)))
        return bool(moved and moved[0])

    def clear(self) -> bool:
        """Forget every delivery of this session; True when there was a record."""
        if self.path is None:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False)
    try:
        with handle:
            handle.write(text)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def measure(root: Path) -> list[dict]:
    """The first-dispatch brief of every subagent type under ``root``."""
    rows = []
    for reader in rule_routing.readers(root):
        brief = compose({"subagent_type": reader.name, "prompt": ""}, root)
        rows.append({"recipient": reader.name, "native": reader.native,
                     "needs": len(brief.full) + len(brief.indexed), "full": len(brief.full),
                     "indexed": len(brief.indexed), "chars": brief.size,
                     "over_cap": brief.over_cap})
    return rows


def render_table(rows: list[dict]) -> str:
    lines = [f"First-dispatch brief per subagent type (cap {CAP:,} characters):",
             "| Type | Loads rules natively | Rules it needs and lacks | In full | Indexed | Chars |",
             "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda row: (-row["chars"], row["recipient"])):
        flag = " (over the cap)" if r["over_cap"] else ""
        lines.append(f"| {r['recipient']} | {'yes' if r['native'] else 'no'} | {r['needs']} | "
                     f"{r['full']} | {r['indexed']} | {r['chars']:,}{flag} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch_brief", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(_LIB.parent), help="checkout to read (default: this one)")
    ap.add_argument("--json", action="store_true", help="print the rows as JSON instead of a table")
    args = ap.parse_args(argv)
    rows = measure(Path(args.root).resolve())
    print(json.dumps(rows, indent=2) if args.json else render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
