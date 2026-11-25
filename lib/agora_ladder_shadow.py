"""AGORA's judge council: three cheap judges answer before today's judge.

Synthesis Ladder, phases A4 and A5. The council is asked the question today's
judge is asked, from the same three reviews and gate notes, and the review reports
what it said and what it cost. Three Gemini 2.5 Flash-Lite judges cost about
$0.0007 a sitting; the Gemini 3.1 Pro judge about $0.037.

Option A (V>> 2026-10-01): the council may wave a PR through, never block one.
When AGORA_COUNCIL_MAY_MERGE=1, the council answered within its budget with all
three seats, every seat said MERGE, and the deterministic checks raise nothing
(no quote-verified blocker, a fully readable diff, and no change to AGORA's
verdict path or the merge machinery, judged from GitHub's full list of the PR's
files rather than the budget-cut diff), that unanimous MERGE is the verdict and
Gemini Pro is not asked. ``hold_reason`` is the full list; the first check that
fails is recorded. Anything else goes to the judge exactly as before, and the
council's answer is kept as the shadow record. A replay of 30 past
sittings (10 the judge blocked) found no blocker the council would wave through.

HAL runs the council (``python -m lib.hal.synthesis``): GRIP and HAL both name
their package ``lib``, so it cannot be imported here. Off unless
AGORA_LADDER_SHADOW=1. Any failure becomes a "shadow unavailable" note and sends
the PR to the judge.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.agora_verdict_router import BOOTSTRAP_EXCEPTED_PATHS  # noqa: E402
from lib.hal_council_dispatch import record_usage  # noqa: E402
from lib.review_redaction import redact_outbound  # noqa: E402

VERDICTS = ("MERGE", "DEFER_CHANGES", "REQUEST_CHANGES")
# The verdicts that let a PR merge, as AGORA's check conclusion reads them
# (lib/agora_pr_review.py, _GREEN_VERDICTS).
GREEN_VERDICTS = frozenset({"MERGE", "DEFER_CHANGES"})
COUNCIL_MODEL = "gemini/gemini-2.5-flash-lite"
USAGE_SCHEME = "hal-synthesis"
EVIDENCE_CAP_CHARS = 6_000
DEFAULT_BUDGET_USD = 0.03
MAY_MERGE_ENV = "AGORA_COUNCIL_MAY_MERGE"
COUNCIL_DESC = "Council of three Gemini 2.5 Flash-Lite judges (unanimous MERGE; Gemini Pro not asked)"

# AGORA's own judge rubric (lib/agora_pr_review.py, _JUDGE_PROMPT_TEMPLATE: the
# verdict definitions and critical rules 2 to 4), copied rather than tuned, so it
# could not be fitted to the replay it was measured on. The earlier one-line rule
# made any small leftover item a DEFER_CHANGES: 19 of 21 live sittings.
_RULES = (
    "Answer with exactly one verdict token. MERGE: no blockers, or blockers that are "
    "exclusively cosmetic (typos, comment wording, doc style). REQUEST_CHANGES: real "
    "blockers: incorrect logic, missing tests for behaviour change, security concerns, "
    "broken contracts, type errors; things that, if shipped, ship a regression. "
    "DEFER_CHANGES: worth addressing but not blocking ship: scope-bleed concerns, "
    "suggested refactors, future-proofing, doc additions, performance under hypothetical "
    "load. If you cannot quote a blocker from review B or C, the verdict must be MERGE. "
    "A concern the gate notes mark UNQUOTABLE, QUOTE_NOT_FOUND or refuted must not drive "
    "REQUEST_CHANGES. If review C lists concerns that are scope suggestions, "
    "future-proofing, or 'I would falsify if I had time', the verdict is DEFER_CHANGES."
)
ROLES = {
    "explorer": ("You are one of three judges deciding whether a pull request should "
                 "merge. Consider at least two readings of each concern before deciding "
                 "whether it is real. " + _RULES),
    "verifier": ("You are one of three judges deciding whether a pull request should "
                 "merge. Trust a concern only when it quotes a specific line or names a "
                 "specific behaviour; treat the rest as unproven. " + _RULES),
    "devil": ("You are one of three judges deciding whether a pull request should merge. "
              "Argue against the most convenient verdict: if the reviews lean to merge, "
              "find the strongest reason not to; if they lean to change, test whether "
              "the concern is real. " + _RULES),
}

# Address prefix -> the family that trained the model, for vendor exclusion.
# A host such as groq names its model's family in the second segment.
_FAMILIES = {"qwen": "alibaba", "dashscope": "alibaba", "gemini": "google",
             "moonshot": "moonshot", "kimi": "moonshot", "claude": "anthropic"}
_HOSTS = frozenset({"groq", "openrouter", "together", "ollama"})
# AGORA's verdict path and the rest of the merge machinery. A PR that changes it
# is the bootstrap case -- a gate must never ratify a change to itself -- so the
# council never waves it through: AGORA's own code, the diff it is shown
# (lib/pr_diff.py), every CI workflow (they define what the required checks
# check), the poller that merges on a green verdict (bin/grip-pr-monitor), the
# convergence loop's own merge path, and the documents that grant the merge
# authority (GRIP#7904). It is the convergence loop's list, read rather than
# copied, so the two cannot drift (GRIP#7927).
_VERDICT_PATH_PREFIXES = tuple(sorted(BOOTSTRAP_EXCEPTED_PATHS))
# GitHub's list of a PR's files stops at 3,000 entries, so a list that long may be cut.
_GITHUB_FILE_LIST_CAP = 3_000


class ShadowError(RuntimeError):
    """HAL could not run the council, or answered with something unreadable."""


def enabled(env: Mapping[str, str] = os.environ) -> bool:
    return env.get("AGORA_LADDER_SHADOW", "").strip() == "1"


def family(address: str) -> str:
    """The model family behind a provider address, as the exclusion list needs it."""
    parts = [p for p in address.lower().split("/") if p]
    if not parts:
        return ""
    head = parts[1] if parts[0] in _HOSTS and len(parts) > 2 else parts[0]
    return _FAMILIES.get(head, head)


def build_request(review, gate_notes: str, budget_usd: float) -> dict:
    """The JSON HAL's ladder command reads, for one sitting."""
    excluded = sorted({family(a) for a in review._speaker_addresses()} | {"anthropic"})
    # The shadow seats sit outside GRIP too, so their evidence passes the same
    # redaction shield as every speaker and judge prompt (lib/review_redaction.py).
    # Every text goes through _cap, which scrubs it before cutting it.
    evidence = [
        f"Review A (plain language):\n{_cap(review.plain_language)}",
        f"Review B (quality):\n{_cap(review.quality)}",
        f"Review C (technical):\n{_cap(review.technical)}",
        f"Gate notes:\n{_cap(gate_notes) or '(none)'}"]
    seats = [{"model": COUNCIL_MODEL, "vendor": "google", "role": role, "billing": "postpaid",
              "system": system, "max_tokens": 1_024} for role, system in ROLES.items()]
    return {"task": {"question": f"Should pull request #{review.pr_number} in {review.repo} "
                                 "merge? Decide from the three reviews and the gate notes below.",
                     "evidence": evidence, "verdicts": list(VERDICTS),
                     "excluded_vendors": [f for f in excluded if f]},
            "rungs": [{"effort": "HIGH", "seats": seats}],
            "floor": "HIGH", "ceiling": "HIGH", "budget_usd": budget_usd, "agreement": 1.0}


def _cap(text: str) -> str:
    """``text`` through the redaction shield, then cut to the evidence cap.

    In that order: a cut first could split a name in two, and the shield
    recognises neither half, so the first half would go out unredacted.
    """
    text = redact_outbound((text or "").strip())
    return text if len(text) <= EVIDENCE_CAP_CHARS else text[:EVIDENCE_CAP_CHARS] + " [cut]"


def call_hal(request: dict, timeout: int = 300) -> dict:
    """Run the council in HAL and return its synthesis as a dict."""
    hal_home = Path(os.environ.get("HAL_HOME") or Path.home() / ".hal").expanduser()
    env = {**os.environ, "PYTHONPATH": str(hal_home), "HAL_HOME": str(hal_home)}
    proc = subprocess.run([sys.executable, "-m", "lib.hal.synthesis"], input=json.dumps(request),
                          capture_output=True, text=True, cwd=hal_home, env=env, timeout=timeout)
    if proc.returncode != 0:
        raise ShadowError(f"HAL synthesis exited {proc.returncode}: {proc.stderr.strip()[-200:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ShadowError(f"HAL synthesis printed no JSON: {exc}") from exc


def summarise(synthesis: dict, judge_verdict: str) -> dict:
    """What the review keeps: the council's verdict, its votes, cost, and agreement.

    Two kinds of agreement are kept apart. ``agrees_with_judge`` is the same
    word; ``same_gate`` is the same effect on the merge gate. MERGE against
    DEFER_CHANGES differs in wording but both let the PR merge, and only a flip
    of the gate would make the council a worse judge. The judge's own verdict
    rides in the record, so a later count needs nothing but the marker.
    """
    unanimous = bool(synthesis.get("decided")) and synthesis.get("agreement", 0) >= 1.0
    verdict = synthesis.get("verdict") or ""
    seats = synthesis.get("seats") or []
    gate = _gate(verdict)
    return {"verdict": verdict, "unanimous": unanimous, "would_escalate": not unanimous,
            "judge_verdict": judge_verdict,
            "agrees_with_judge": bool(verdict) and verdict == judge_verdict,
            "same_gate": bool(gate) and gate == _gate(judge_verdict),
            "budget_capped": bool(synthesis.get("budget_capped")),
            "cost_usd": round(float(synthesis.get("cost_usd") or 0.0), 6),
            "seats": [{"model": s.get("model", ""), "role": s.get("role", ""),
                       "verdict": s.get("verdict", ""), "ok": bool(s.get("ok"))} for s in seats]}


def _record_seat_usage(synthesis: dict) -> None:
    """Put each seat's tokens in the sitting's usage log, so the cost line counts them."""
    for seat in synthesis.get("seats") or []:
        usage = ({"in": seat.get("tokens_in", 0), "out": seat.get("tokens_out", 0)}
                 if seat.get("ok") else {})
        record_usage(str(seat.get("model", "")), USAGE_SCHEME, usage)


def consult(review, notes: Callable[[], str], call: Callable[[dict], dict] = call_hal,
            env: Mapping[str, str] = os.environ) -> dict:
    """The council's synthesis for one sitting; {} when off, {"error": ...} on failure.

    Never raises. ``notes`` builds the gate notes. It is called only when shadow
    mode is on, and inside the guard, so formatting them can neither cost a
    disabled sitting anything nor break an enabled one.
    """
    if not enabled(env):
        return {}
    try:
        budget = float(env.get("AGORA_LADDER_SHADOW_BUDGET") or DEFAULT_BUDGET_USD)
        synthesis = call(build_request(review, notes(), budget))
        _record_seat_usage(synthesis)
        return synthesis
    except Exception as exc:  # noqa: BLE001 -- a council failure only sends the PR to the judge
        return {"error": f"{type(exc).__name__}: {exc}"[:240]}


def hold_reason(synthesis: dict, review, env: Mapping[str, str] = os.environ,
                list_files: Callable[[], Optional[Sequence[str]]] = lambda: None) -> str:
    """Why the council may NOT stand in for the judge; "" when it may wave the PR through.

    Every check can only send a PR to Gemini Pro, never let one through, so a
    wrong answer here costs money, not safety. ``list_files`` returns every file
    the PR changes (``changed_files``), or None when that cannot be read. It is
    called only once every other check has passed, so the lookup happens only on
    a PR the council would otherwise wave through.
    """
    seats = synthesis.get("seats") or []
    checks = (
        (env.get(MAY_MERGE_ENV, "").strip() != "1", f"{MAY_MERGE_ENV} is not 1"),
        (not synthesis or synthesis.get("error"), "the council did not answer"),
        (bool(synthesis.get("budget_capped")), "the council hit its budget"),
        (len(seats) != len(ROLES) or not all(s.get("ok") for s in seats),
         "not every council seat answered"),
        (any(s.get("verdict") != "MERGE" for s in seats), "a council seat did not say MERGE"),
        (any(v.blocking_eligible for v in getattr(review, "quote_verdicts", [])),
         "the quote gate verified a concern"),
        (bool(getattr(getattr(review, "readability", None), "unreadable", None)),
         "part of the diff could not be read"),
    )
    held = next((reason for failed, reason in checks if failed), "")
    if held:
        return held
    files = list_files()
    if files is None:
        return "the PR's full list of changed files could not be read"
    if touches_verdict_path([*files, *getattr(review, "diff_files", [])]):
        return "the PR changes AGORA's verdict path or the merge machinery"
    return ""


def touches_verdict_path(paths: Sequence[str]) -> bool:
    return any(str(p).startswith(_VERDICT_PATH_PREFIXES) for p in paths)


def changed_files(pr_number: int, repo: str, timeout: int = 60) -> Optional[list]:
    """Every file the PR changes, from GitHub; None when that cannot be read in full.

    The diff AGORA reviews is cut at lib/pr_diff.py's PR_REVIEW_DIFF_BUDGET, so on
    a very large PR a file late in it is missing from ``review.diff_files``; the
    guard on the merge machinery must see every file, so it asks GitHub.
    """
    try:
        proc = subprocess.run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{pr_number}/files",
                               "--jq", ".[].filename"], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or not files or len(files) >= _GITHUB_FILE_LIST_CAP:
        return None
    return files


def shadow_record(synthesis: dict, judge_verdict: str, held: str = "") -> dict:
    """What the review keeps about the council. Never raises.

    ``held`` is the hold reason. An empty one means the council decided, so there
    is no judge verdict to compare with and the record says so instead.
    """
    if not synthesis:
        return {}
    if synthesis.get("error"):
        return {"error": synthesis["error"]}
    try:
        record = summarise(synthesis, judge_verdict if held else "")
    except Exception as exc:  # noqa: BLE001 -- an odd synthesis must not break the review
        return {"error": f"{type(exc).__name__}: {exc}"[:240]}
    if held:
        record["held_because"] = held
    else:
        record["decided_by_council"] = True
    return record


def council_reasoning(synthesis: dict) -> str:
    """The judge-synthesis paragraph for a sitting the council decided."""
    roles = ", ".join(s.get("role", "") for s in synthesis.get("seats") or [])
    return (f"All three council judges ({roles}), each Gemini 2.5 Flash-Lite given AGORA's "
            "judge rubric, answered MERGE. The quote gate verified no blocking concern, the "
            "whole diff was readable, and the PR does not change AGORA's own verdict path. "
            "Under the wave-through rule (V>> 2026-10-01) that unanimous MERGE stands and "
            "Gemini Pro was not asked; any dissent, failure or verified blocker would have "
            "sent this PR to Gemini Pro.")


def render_shadow(record: dict) -> str:
    """One visible line plus a machine-readable marker; empty when there is no record."""
    if not record:
        return ""
    marker = json.dumps(record, sort_keys=True).replace(">", "\\u003e")
    return f"\n*{_shadow_line(record)}*\n<!-- agora-ladder-shadow {marker} -->\n"


def _shadow_line(record: dict) -> str:
    if record.get("error"):
        return f"Council unavailable, so Gemini Pro judged: {record['error']}"
    votes = ", ".join(f"{s['role']} {s['verdict'] or 'no verdict'}" for s in record["seats"])
    if record.get("decided_by_council"):
        return (f"Decided by the council of three Gemini 2.5 Flash-Lite judges: "
                f"{record['verdict']}, unanimous ({votes}); Gemini Pro was not asked. "
                f"Council cost about ${record['cost_usd']:.4f}.")
    settled = "unanimous" if record["unanimous"] else "split, so it would escalate to Gemini Pro"
    held = f" It went to Gemini Pro because {record['held_because']}." if record.get("held_because") else ""
    return (f"Shadow council of three Gemini 2.5 Flash-Lite judges, not used for this verdict: "
            f"{record['verdict'] or 'no verdict'}, {settled} ({votes}); {_comparison(record)}, "
            f"for about ${record['cost_usd']:.4f}.{held}")


def _comparison(record: dict) -> str:
    """How the council's verdict sits against the judge's: same word, same gate, or a flip."""
    judge = record.get("judge_verdict") or ""
    if record.get("agrees_with_judge"):
        return "it agrees with the judge"
    if record.get("same_gate"):
        outcome = "let the PR merge" if _gate(judge) == "open" else "block it"
        return f"it differs from the judge's {judge} only in wording, since both {outcome}"
    if _gate(record.get("verdict") or "") and _gate(judge):
        return f"it would have flipped the merge gate against the judge's {judge}"
    return f"it cannot be compared with the judge ({judge or 'no verdict'})"


def _gate(verdict: str) -> str:
    """'open' for a verdict that lets the PR merge, 'shut' for one that blocks it, else ''."""
    if verdict in GREEN_VERDICTS:
        return "open"
    return "shut" if verdict == "REQUEST_CHANGES" else ""


def gate_notes_for(notes: Sequence[str]) -> str:
    return "\n".join(note for note in notes if note and note.strip() not in ("", "(none)"))
