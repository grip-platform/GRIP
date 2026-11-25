"""H2 harness — audit efficiency and accuracy (paper §8).

Lane E of the Sunyata EvidenceState pilot fleet. A DETERMINISTIC
practitioner-auditor simulation: given a scenario and an audit artefact
(the ``AgentRun.events`` tuple — a raw transcript for G0, a conventional
append-only log for B1/B2, a flip-ledger + negative-provenance block for
G1), the auditor resolves a disposition for every variable and is scored
on two axes:

- **steps** — the number of LOOKUPS: each time the auditor reads one
  distinct audit entry or retrieves one evidence fact counts 1.
- **accuracy** — the fraction of variables whose resolved disposition is
  the gold-correct one. Resolution rules (paper §8): assert when a single
  fact supports a value; suspend when the artefact discloses
  missing/conflict; file-under-assumption is correct when the artefact
  carries the assumption note.

Lookup disciplines per artefact type:

- **G0 transcript**: every line must be read (no structure, no index);
  steps == len(events).
- **B1/B2 conventional log**: sequential scan, early stop once every
  variable is resolved. An ``assert:`` line does not carry the evidence,
  so a resolved assert costs the entry read + one evidence retrieval;
  disclosure lines are self-contained.
- **G1 ledger**: one read of the ``index:`` line, one read of the
  ``negative-provenance:`` block, one read per ``entry:`` line (evidence
  carried inline — no separate retrieval). The np block is NOT trusted
  blindly: the auditor cross-checks it against the scenario's own input
  states (``scenario.inputs``) — see "Negative-provenance cross-check"
  below — and any inconsistency forces a full defensive scan of every
  line for that run.

Negative-provenance cross-check (G1 discipline only, anchored on the
scenario): for every variable whose scenario input state is MISSING or
CONFLICTING, the auditor verifies that the np block and the entry lines
tell the same story. A violation is recorded when (i) the audited
disposition is ACT_ASSERT but the var is absent from the np
missing/conflicting lists, or (ii) the var IS listed in the np block but
an entry line asserts a value for it. Any np violation is appended to
``grammar_violations`` (call it with ``scenario``) AND forces the run to
fall back to the full defensive scan: every line is read and every entry
is verified with no np-based skipping, so a cheater's step savings
evaporate and the fabricated asserts surface in accuracy.

Event grammar contract (the agent lanes must emit exactly this):

    G0  (transcript)
        "<var>: <evidence prose with a number>"     fact (verbatim evidence)
        "assert: <var> = <value>"
        "suspend: <var> (...)" | "skip: <var> (...)" | "disclose: <var> (...)"
        "assume: <var> | <note>"
        any other line                               noise, still 1 lookup
    B1 / B2 (conventional log)
        "run: scenario=<id> agent=<name>"
        "assert: <var> = <value>"                   assert (+1 retrieval)
        "assert: <var> = <value> (conflict: ...)"   pick-first assert — B1's
                                                    retained failure mode; the
                                                    parenthetical documents
                                                    the choice and is NOT a
                                                    disclosure (assert, +1)
        "suspend: <var> (...)" | "skip: <var> (...)" | "disclose: <var> (...)"
        "assume: <var> | <note>"
    G1 (flip-ledger + negative-provenance block)
        "index: <var>@<lineno> <var>@<lineno> ..."  (0-based line numbers)
        "negative-provenance: missing=<csv>; conflicting=<csv>"
        "entry: <var> = <value> | <evidence>"       assert, evidence inline
        "entry: <var> = file_under_assumption | <note>"

Var names must not contain spaces, ``:``, ``@``, ``|`` or ``,``. The
grammar is enforced, not merely documented: ``grammar_violations``
reports every event naming an invalid var and every malformed G1
structural token, and the auditors refuse to resolve a variable from
such a line — a malformed artefact is flagged loudly, never silently
mis-read.
Accuracy scores disposition only (a wrong asserted value on a resolvable
variable is a correctness error outside H2's remit). A disclosure always
wins over a conflicting entry line.

Verdict shape (mirrors the h1 lane's {"verdict", "reasons", "metrics"}
convention):

    {
      "verdict": "CONFIRMED" | "FALSIFIED" | "INCONCLUSIVE",
      "reasons": [str],          # empty when CONFIRMED
      "metrics": {
        "steps":     {"G0": int, "B1": int, "G1": int},     # aggregate
        "accuracy":  {"G0": float, "B1": float, "G1": float},
        "n_scenarios": {"G0": int, "B1": int, "G1": int},
        "matched_scenarios": int,
        "unmatched": {"B1": int, "G1": int},
        "steps_nonimprovement": [scenario_id, ...],
        "accuracy_drop": bool,
        "grammar_violations": {"G0": int, "B1": int, "G1": int},
        "per_scenario": [ {scenario_id, b1_steps, b1_accuracy,
                           g1_steps, g1_accuracy, g0_steps?, g0_accuracy?}, ... ],
      },
    }

Falsifiers (paper §8), first match wins:
1. any matched scenario where BOTH reach full accuracy and
   steps(G1) >= steps(B1) -> FALSIFIED;
2. aggregate accuracy(G1) < aggregate accuracy(B1) over matched
   scenarios -> FALSIFIED.
CONFIRMED iff neither falsifier fires AND aggregate steps(G1) <
aggregate steps(B1) AND aggregate accuracy(G1) >= aggregate accuracy(B1).
Empty matched set -> INCONCLUSIVE.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from sunyata_pilot.common import (
    ACT_ASSERT,
    ACT_FILE_UNDER_ASSUMPTION,
    ACT_SUSPEND,
    CONFLICTING,
    GOLD_MUST_SUSPEND,
    GOLD_RESOLVABLE,
    MISSING,
    AgentRun,
    Scenario,
)

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_FALSIFIED = "FALSIFIED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"

REASON_STEPS_NONIMPROVEMENT = (
    "no step improvement against the conventional-log baseline at full accuracy"
)
REASON_ACCURACY_DROP = "accuracy drop against the conventional-log baseline"
REASON_NO_MATCHED = "no matched scenarios"
REASON_NO_AGGREGATE_DROP = "no aggregate step drop against the baseline"

_VERBS = frozenset(
    {
        "assert",
        "suspend",
        "skip",
        "disclose",
        "assume",
        "run",
        "index",
        "entry",
        "negative-provenance",
    }
)
_VALUE_VERBS = ("states", "says", "amount", "value", "total", "=")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

_TRANSCRIPT_AGENTS = frozenset({"G0"})
_LEDGER_AGENTS = frozenset({"G1"})

_FORBIDDEN_VAR_CHARS = frozenset(":@|,")


def _valid_var(name: str) -> bool:
    """True when ``name`` is a legal var name under the event grammar.

    Legal names are non-empty and free of spaces and the forbidden
    characters ``:``, ``@``, ``|``, ``,``.
    """
    if not name:
        return False
    for ch in name:
        if ch in _FORBIDDEN_VAR_CHARS or ch.isspace():
            return False
    return True


def _violation(lineno: int, msg: str) -> str:
    return f"line {lineno}: {msg}"


def _verb(line: str) -> tuple[str, str]:
    """Split a line into (verb_tag, rest). Non-verb lines yield ("", line)."""
    tag, sep, rest = line.partition(":")
    tag = tag.strip()
    if sep and tag in _VERBS:
        return tag, rest.strip()
    return "", line


def _verb_var(rest: str) -> Optional[str]:
    """Var name in a verb line body, e.g. 'v' from 'v (missing, material)'.

    A first token containing spaces, ``:``, ``@``, ``|`` or ``,`` is NOT
    a var name under the event grammar: such a line is malformed, so the
    auditors must not resolve anything from it (the defect is reported by
    ``grammar_violations`` instead of being silently mis-read).
    """
    body = rest.strip()
    if not body:
        return None
    name = body.split(" ", 1)[0]
    if not _valid_var(name):
        return None
    return name


def _check_entry_line(lineno: int, rest: str, out: list[str]) -> None:
    """Validate one G1 ``entry:`` body against the documented grammar."""
    left, sep, _evidence = rest.partition("|")
    if not sep:
        out.append(_violation(lineno, "entry line missing '|' evidence separator"))
        return
    body = left.strip()
    name, eq, val = body.partition("=")
    if not eq or not _valid_var(name.strip()):
        out.append(_violation(lineno, f"malformed entry var/value {body!r}"))
        return
    val = val.strip()
    if val != "file_under_assumption" and _to_float(val) is None:
        out.append(_violation(lineno, f"non-numeric entry value {val!r}"))


def grammar_violations(
    events: Sequence[str], scenario: Optional[Scenario] = None
) -> list[str]:
    """Loud strict-grammar check of an audit artefact.

    Every line whose tag is a grammar verb is validated against the
    documented event grammar: the named var must be free of spaces and
    ``:``, ``@``, ``|``, ``,``; G1 ``index:`` tokens must be
    ``<var>@<lineno>`` pairs with an integer lineno; ``entry:`` lines
    must be ``<var> = <value> | <evidence>`` or
    ``<var> = file_under_assumption | <note>``; ``negative-provenance:``
    must be ``missing=<csv>`` / ``conflicting=<csv>`` with valid var
    names. Non-grammar lines (transcript prose, noise) are exempt.

    When ``scenario`` is passed, the G1 negative-provenance cross-check
    is appended: for every variable whose scenario input state is
    MISSING or CONFLICTING, an audited assert on an unlisted var or an
    entry asserting a value for a listed var is a violation (P0b — the
    np line is never trusted without cross-checking).

    Returns one human-readable violation per defect (``[]`` == clean).
    The auditors never resolve a variable from a violating line, so a
    malformed artefact is always reported, never silently absorbed.
    """
    out: list[str] = []
    for i, line in enumerate(events):
        tag, rest = _verb(line)
        if tag in ("assert", "suspend", "skip", "disclose", "assume"):
            name = rest.strip().split(" ", 1)[0] if rest.strip() else ""
            if not _valid_var(name):
                out.append(
                    _violation(i, f"invalid var name {name!r} in {tag} line")
                )
        elif tag == "index":
            for token in rest.split():
                var, sep, lineno = token.partition("@")
                if not sep or not _valid_var(var):
                    out.append(_violation(i, f"malformed index token {token!r}"))
                    continue
                try:
                    int(lineno)
                except ValueError:
                    out.append(
                        _violation(
                            i, f"non-integer index line number {lineno!r} "
                            f"in token {token!r}"
                        )
                    )
        elif tag == "entry":
            _check_entry_line(i, rest, out)
        elif tag == "negative-provenance":
            for part in rest.split(";"):
                part = part.strip()
                if not part:
                    continue
                key, sep, csv = part.partition("=")
                if not sep or key not in ("missing", "conflicting"):
                    out.append(
                        _violation(i, f"unknown negative-provenance part {part!r}")
                    )
                    continue
                for v in csv.split(","):
                    if v and not _valid_var(v):
                        out.append(
                            _violation(
                                i, f"invalid var name {v!r} in {key} list"
                            )
                        )
    if scenario is not None:
        out.extend(_audit_ledger(events, scenario)[2])
    return out


def _to_float(token: str) -> Optional[float]:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _parse_assert(line: str) -> tuple[Optional[str], Optional[float]]:
    """Parse 'assert: <var> = <value>' -> (var, value).

    An invalid var name (spaces or ``:``, ``@``, ``|``, ``,``) yields
    ``(None, None)``: a malformed assert never resolves a variable.
    """
    body = line.partition(":")[2]
    left, sep, right = body.partition("=")
    if not sep:
        return None, None
    var = left.strip()
    if not _valid_var(var):
        return None, None
    token = right.strip().split(" ", 1)[0] if right.strip() else ""
    return (var or None), _to_float(token)


def _parse_value(text: str) -> Optional[float]:
    """First number after a value verb, else last standalone number.

    Mirrors the B1 agent's evidence-value parsing so transcript facts and
    log values resolve identically.
    """
    lower = text.lower()
    for verb in _VALUE_VERBS:
        idx = lower.find(verb)
        if idx >= 0:
            match = _NUMBER_RE.search(text, idx + len(verb))
            if match:
                return float(match.group().replace(",", ""))
    candidates: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        prev = text[match.start() - 1] if match.start() > 0 else ""
        if prev and (prev.isalnum() or prev == "."):
            continue
        candidates.append(match.group())
    if not candidates:
        return None
    return float(candidates[-1].replace(",", ""))


def _audit_transcript(events: tuple[str, ...], scenario: Scenario):
    """G0 discipline: read every line (steps == len(events))."""
    steps = len(events)
    facts: dict[str, list[float]] = {}
    assumes: dict[str, str] = {}
    susp: set[str] = set()
    for line in events:
        tag, rest = _verb(line)
        if tag == "assert":
            var, val = _parse_assert(line)
            if var is None:
                continue
            if "conflict" in line.lower():
                susp.add(var)
            elif val is not None:
                facts.setdefault(var, []).append(val)
        elif tag in ("suspend", "skip", "disclose"):
            var = _verb_var(rest)
            if var is not None:
                susp.add(var)
        elif tag == "assume":
            var = _verb_var(rest)
            if var is not None:
                assumes[var] = rest
        else:
            for var in sorted(scenario.variables, key=len, reverse=True):
                if line.startswith(var + ":"):
                    val = _parse_value(line)
                    if val is not None:
                        facts.setdefault(var, []).append(val)
                    break
    res: dict[str, dict] = {}
    for var in scenario.variables:
        if var in assumes:
            res[var] = {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
        elif var in susp:
            res[var] = {"action": ACT_SUSPEND, "value": None}
        elif var in facts:
            vals = facts[var]
            if len(set(vals)) == 1:
                res[var] = {"action": ACT_ASSERT, "value": vals[0]}
            else:
                res[var] = {"action": ACT_SUSPEND, "value": None}
        else:
            res[var] = {"action": ACT_SUSPEND, "value": None}
    return steps, res, []


def _audit_log(events: tuple[str, ...], scenario: Scenario):
    """B1/B2 discipline: sequential scan, early stop once all vars resolve.

    An assert line carries no evidence: resolving it costs the line read
    plus one evidence retrieval. A conflict parenthetical does not change
    that — B1's ``assert: <var> = <value> (conflict: ...)`` IS B1's
    pick-first assert (the parenthetical documents the choice, it is not
    a disclosure), so it resolves as an assert and pays the retrieval.
    Only B2's ``suspend:``/``disclose:`` lines disclose. Two differing
    asserts on one var still expose the contradiction -> suspend.
    """
    want = set(scenario.variables)
    res: dict[str, dict] = {}
    steps = 0
    for line in events:
        steps += 1
        tag, rest = _verb(line)
        if tag == "run":
            continue
        if tag == "assert":
            var, val = _parse_assert(line)
            if var is None or var not in want:
                continue
            if var not in res:
                res[var] = {"action": ACT_ASSERT, "value": val}
                steps += 1  # retrieve the underlying evidence fact
            else:
                prev = res[var]
                if prev["action"] == ACT_ASSERT and prev["value"] != val:
                    res[var] = {"action": ACT_SUSPEND, "value": None}
        elif tag in ("suspend", "skip", "disclose"):
            var = _verb_var(rest)
            if var is not None and var in want:
                res[var] = {"action": ACT_SUSPEND, "value": None}
        elif tag == "assume":
            var = _verb_var(rest)
            if var is not None and var in want:
                res[var] = {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
        if want <= set(res):
            break
    for var in want - set(res):
        res[var] = {"action": ACT_SUSPEND, "value": None}  # absent from the log
    return steps, res, []


def _entry_action(var: str, rest: str) -> dict:
    """Resolve a G1 'entry:' line body for ``var``."""
    val_tok = rest.partition("|")[0].strip()
    if val_tok == f"{var} = file_under_assumption":
        return {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
    left, sep, right = val_tok.partition("=")
    if sep and left.strip() == var:
        val = _to_float(right.strip())
        if val is not None:
            return {"action": ACT_ASSERT, "value": val}
    return {"action": ACT_SUSPEND, "value": None}


def _valued_entry_lines(events: Sequence[str]) -> dict[str, list[int]]:
    """Map var -> line numbers of ``entry:`` lines asserting a numeric value."""
    found: dict[str, list[int]] = {}
    for i, line in enumerate(events):
        tag, rest = _verb(line)
        if tag != "entry":
            continue
        body = rest.partition("|")[0].strip()
        name, eq, val = body.partition("=")
        if not eq:
            continue
        name = name.strip()
        if not _valid_var(name) or _to_float(val.strip()) is None:
            continue
        found.setdefault(name, []).append(i)
    return found


def _np_violations(
    events: Sequence[str],
    scenario: Scenario,
    res: dict[str, dict],
    neg_missing: set[str],
    neg_conflict: set[str],
) -> list[str]:
    """Cross-check the G1 negative-provenance block against the scenario.

    The auditor knows ``scenario.inputs``: for every variable whose
    scenario input state is MISSING or CONFLICTING, the np block and the
    entry lines must tell the same story. Violations:

    (i)  the audited disposition is ACT_ASSERT but the var is absent
         from the np missing/conflicting lists — the assert was hidden
         from the negative-provenance disclosure;
    (ii) the var IS listed in the np block but an entry line asserts a
         value for it — the disclosure claims suspension while the
         entries fabricate a liability.

    Either makes the np block untrustworthy, so the caller falls back to
    the full defensive scan (every line read, no np-based skipping).
    """
    valued = _valued_entry_lines(events)
    out: list[str] = []
    for var, inp in scenario.inputs.items():
        if inp.state not in (MISSING, CONFLICTING):
            continue
        listed = var in neg_missing or var in neg_conflict
        if res[var]["action"] == ACT_ASSERT and not listed:
            msg = (
                f"var {var!r} (scenario input {inp.state}) audited as "
                "assert but absent from the negative-provenance "
                "missing/conflicting lists"
            )
            lines = valued.get(var, [])
            out.append(_violation(lines[0], msg) if lines else msg)
        if listed and var in valued:
            key = MISSING if inp.state == MISSING else CONFLICTING
            out.append(
                _violation(
                    valued[var][0],
                    f"entry asserts a value for var {var!r} listed in "
                    f"the negative-provenance {key} list",
                )
            )
    return out


def _audit_ledger(events: tuple[str, ...], scenario: Scenario):
    """G1 discipline: index + negative-provenance block + inline entries."""
    n = len(events)
    read: set[int] = set()
    steps = 0
    idx: dict[str, int] = {}
    neg_missing: set[str] = set()
    neg_conflict: set[str] = set()

    # 1. read the index line (first "index:" line)
    for i, line in enumerate(events):
        tag, rest = _verb(line)
        if tag == "index":
            read.add(i)
            steps += 1
            for token in rest.split():
                var, sep, lineno = token.partition("@")
                if sep and _valid_var(var):
                    try:
                        idx[var] = int(lineno)
                    except ValueError:
                        pass
            break
    # 2. read the negative-provenance block (dense, one line in practice)
    for i, line in enumerate(events):
        tag, rest = _verb(line)
        if tag == "negative-provenance":
            read.add(i)
            steps += 1
            for part in rest.split(";"):
                part = part.strip()
                if part.startswith("missing="):
                    neg_missing |= {
                        v for v in part[len("missing="):].split(",")
                        if v and _valid_var(v)
                    }
                elif part.startswith("conflicting="):
                    neg_conflict |= {
                        v for v in part[len("conflicting="):].split(",")
                        if v and _valid_var(v)
                    }
    res: dict[str, dict] = {}
    # 3. negatives -> suspend (a disclosure wins over any index entry)
    for var in scenario.variables:
        if var in neg_missing or var in neg_conflict:
            res[var] = {"action": ACT_SUSPEND, "value": None}
    # 4. indexed entries (evidence inline, one read each)
    for var in scenario.variables:
        if var in res or var not in idx:
            continue
        i = idx[var]
        if not 0 <= i < n:
            res[var] = {"action": ACT_SUSPEND, "value": None}
            continue
        if i not in read:
            read.add(i)
            steps += 1
        tag, rest = _verb(events[i])
        if tag == "entry":
            res[var] = _entry_action(var, rest)
        elif tag == "assume":
            res[var] = {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
        elif tag in ("suspend", "skip", "disclose"):
            res[var] = {"action": ACT_SUSPEND, "value": None}
        else:
            res[var] = {"action": ACT_SUSPEND, "value": None}
    # 5. defensive scan: vars in neither index nor negative-provenance
    uncovered = [v for v in scenario.variables if v not in res]
    if uncovered:
        uncovered_set = set(uncovered)
        for i, line in enumerate(events):
            if i in read:
                continue
            read.add(i)
            steps += 1
            tag, rest = _verb(line)
            if tag == "entry":
                # resolve ONLY when the line names the var exactly — a
                # malformed name (forbidden chars, spaces) never matches,
                # so an invalid entry can never leak into a disposition
                named = rest.partition("|")[0].partition("=")[0].strip()
                if _valid_var(named) and named in uncovered_set:
                    res[named] = _entry_action(named, rest)
            elif tag == "assume":
                var = _verb_var(rest)
                if var in uncovered_set:
                    res[var] = {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
            elif tag in ("suspend", "skip", "disclose"):
                var = _verb_var(rest)
                if var in uncovered_set:
                    res[var] = {"action": ACT_SUSPEND, "value": None}
        for var in uncovered:
            res.setdefault(var, {"action": ACT_SUSPEND, "value": None})

    # 6. negative-provenance cross-check (P0b): the np block is not
    #    trusted without verification against the scenario. Any
    #    inconsistency forces the full defensive scan — every line read,
    #    every entry verified, no np-based skipping — so a cheater's step
    #    savings evaporate and fabricated asserts surface in accuracy.
    violations = _np_violations(events, scenario, res, neg_missing, neg_conflict)
    if violations:
        for i, line in enumerate(events):
            if i in read:
                continue
            read.add(i)
            steps += 1
            tag, rest = _verb(line)
            if tag == "entry":
                named = rest.partition("|")[0].partition("=")[0].strip()
                if _valid_var(named) and named in scenario.variables:
                    res[named] = _entry_action(named, rest)
            elif tag == "assume":
                var = _verb_var(rest)
                if var is not None and var in scenario.variables:
                    res[var] = {"action": ACT_FILE_UNDER_ASSUMPTION, "value": None}
            elif tag in ("suspend", "skip", "disclose"):
                var = _verb_var(rest)
                if var is not None and var in scenario.variables:
                    res[var] = {"action": ACT_SUSPEND, "value": None}
        for var in scenario.variables:
            res.setdefault(var, {"action": ACT_SUSPEND, "value": None})
    return steps, res, violations


def _audit(run: AgentRun, scenario: Scenario):
    """Dispatch the practitioner-auditor on the run's artefact discipline."""
    if run.agent in _LEDGER_AGENTS:
        return _audit_ledger(run.events, scenario)
    if run.agent in _TRANSCRIPT_AGENTS:
        return _audit_transcript(run.events, scenario)
    return _audit_log(run.events, scenario)


def _score(res: dict, scenario: Scenario) -> tuple[int, int]:
    correct = 0
    for var in scenario.variables:
        action = res[var]["action"]
        if _expected_action(scenario, var, action) == action:
            correct += 1
    return correct, len(scenario.variables)


def _expected_action(scenario: Scenario, var: str, resolved_action: str) -> str:
    """The gold-correct action for ``var`` given how the artefact resolved.

    Resolution rule (paper §8): file-under-assumption is correct whenever
    the artefact carries the assumption note (the auditor only files under
    assumption when it read such a note); otherwise the gold disposition
    decides assert vs suspend.
    """
    if resolved_action == ACT_FILE_UNDER_ASSUMPTION:
        return ACT_FILE_UNDER_ASSUMPTION
    gold = scenario.gold[var]
    return ACT_ASSERT if gold.disposition == GOLD_RESOLVABLE else ACT_SUSPEND


def audit_steps(run: AgentRun, scenario: Scenario) -> int:
    """Practitioner LOOKUPS needed to audit this artefact to a disposition."""
    steps, _, _ = _audit(run, scenario)
    return steps


def audit_accuracy(run: AgentRun, scenario: Scenario) -> float:
    """Fraction of variables whose audited disposition is gold-correct."""
    _, res, _ = _audit(run, scenario)
    correct, total = _score(res, scenario)
    return correct / total if total else 1.0


def audit_dispositions(run: AgentRun, scenario: Scenario) -> dict[str, str]:
    """The practitioner's resolved disposition per variable (var -> action)."""
    _, res, _ = _audit(run, scenario)
    return {var: res[var]["action"] for var in scenario.variables}


def _index_by_scenario(results: Sequence[tuple[AgentRun, Scenario]]):
    """Map scenario_id -> (run, scenario); the last run per id wins."""
    index: dict[str, tuple[AgentRun, Scenario]] = {}
    for run, scenario in results:
        index[scenario.id] = (run, scenario)
    return index


def _agg_accuracy(entry: dict) -> float:
    return entry["correct"] / entry["total"] if entry["total"] else 0.0


def h2_verdict(
    g0_results: Sequence[tuple[AgentRun, Scenario]],
    b1_results: Sequence[tuple[AgentRun, Scenario]],
    g1_results: Sequence[tuple[AgentRun, Scenario]],
) -> dict:
    """Verdict on H2 — audit efficiency and accuracy (paper §8).

    The paper's falsifiers, first match wins:

    1. any matched scenario (same scenario_id present in both B1 and G1
       result lists) where BOTH reach full accuracy and
       steps(G1) >= steps(B1) -> FALSIFIED;
    2. aggregate accuracy(G1) < aggregate accuracy(B1) over matched
       scenarios -> FALSIFIED.

    CONFIRMED iff neither falsifier fires AND aggregate steps(G1) <
    aggregate steps(B1) AND aggregate accuracy(G1) >= aggregate
    accuracy(B1). An empty matched set is INCONCLUSIVE. Metrics always
    carry the aggregate step counts and accuracies for all three agents.
    """
    maps = {
        "G0": _index_by_scenario(g0_results),
        "B1": _index_by_scenario(b1_results),
        "G1": _index_by_scenario(g1_results),
    }
    matched = sorted(set(maps["B1"]) & set(maps["G1"]))
    agg = {
        agent: {"steps": 0, "correct": 0, "total": 0, "runs": 0}
        for agent in ("G0", "B1", "G1")
    }
    per_scenario: list[dict] = []
    steps_nonimprovement: list[str] = []
    for sid in matched:
        row: dict = {"scenario_id": sid}
        for agent in ("B1", "G1"):
            run, scenario = maps[agent][sid]
            steps, res, _ = _audit(run, scenario)
            correct, total = _score(res, scenario)
            agg[agent]["steps"] += steps
            agg[agent]["correct"] += correct
            agg[agent]["total"] += total
            agg[agent]["runs"] += 1
            row[f"{agent.lower()}_steps"] = steps
            row[f"{agent.lower()}_accuracy"] = correct / total if total else 0.0
        entry = maps["G0"].get(sid)
        if entry is not None:
            run, scenario = entry
            steps, res, _ = _audit(run, scenario)
            correct, total = _score(res, scenario)
            agg["G0"]["steps"] += steps
            agg["G0"]["correct"] += correct
            agg["G0"]["total"] += total
            agg["G0"]["runs"] += 1
            row["g0_steps"] = steps
            row["g0_accuracy"] = correct / total if total else 0.0
        if (
            row["b1_accuracy"] == 1.0
            and row["g1_accuracy"] == 1.0
            and row["g1_steps"] >= row["b1_steps"]
        ):
            steps_nonimprovement.append(sid)
        per_scenario.append(row)

    b1_acc = _agg_accuracy(agg["B1"])
    g1_acc = _agg_accuracy(agg["G1"])
    g0_acc = _agg_accuracy(agg["G0"])
    accuracy_drop = bool(matched) and g1_acc < b1_acc
    grammar_violation_counts: dict[str, int] = {}
    for agent in ("G0", "B1", "G1"):
        count = 0
        for run, scenario in maps[agent].values():
            if agent == "G1":
                count += len(grammar_violations(run.events, scenario))
            else:
                count += len(grammar_violations(run.events))
        grammar_violation_counts[agent] = count

    if not matched:
        verdict, reasons = VERDICT_INCONCLUSIVE, [REASON_NO_MATCHED]
    elif steps_nonimprovement:
        verdict, reasons = VERDICT_FALSIFIED, [REASON_STEPS_NONIMPROVEMENT]
    elif accuracy_drop:
        verdict, reasons = VERDICT_FALSIFIED, [REASON_ACCURACY_DROP]
    elif agg["G1"]["steps"] < agg["B1"]["steps"] and g1_acc >= b1_acc:
        verdict, reasons = VERDICT_CONFIRMED, []
    else:
        verdict, reasons = VERDICT_INCONCLUSIVE, [REASON_NO_AGGREGATE_DROP]

    metrics = {
        "steps": {
            "G0": agg["G0"]["steps"],
            "B1": agg["B1"]["steps"],
            "G1": agg["G1"]["steps"],
        },
        "accuracy": {"G0": g0_acc, "B1": b1_acc, "G1": g1_acc},
        "n_scenarios": {
            "G0": agg["G0"]["runs"],
            "B1": agg["B1"]["runs"],
            "G1": agg["G1"]["runs"],
        },
        "matched_scenarios": len(matched),
        "unmatched": {
            "B1": len(maps["B1"]) - len(matched),
            "G1": len(maps["G1"]) - len(matched),
        },
        "steps_nonimprovement": steps_nonimprovement,
        "accuracy_drop": accuracy_drop,
        "grammar_violations": grammar_violation_counts,
        "per_scenario": per_scenario,
    }
    return {"verdict": verdict, "reasons": reasons, "metrics": metrics}
