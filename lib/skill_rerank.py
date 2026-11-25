"""Jev re-ranks the skill recommender's shortlist, in shadow until it earns the banner.

V>>'s decision D-2 (2026-10-10, the Jev integration plan, Phase 2b.2): the TF-IDF
recommender (lib/skill_recommender.py) keeps building the shortlist, and a decision
model (TypeSafe's Jev, through HAL decide) re-ranks its top candidates against the
checkout's git context in one call. The swap stands only if the top-3 hit rate
rises, so this module runs in shadow: at session start it logs both rankings to
logs/skill-rerank-shadow.jsonl, and the banner keeps showing TF-IDF's until the
pre-registered measurement settles.

First-listed bias: Jev leans towards the first option of a Choice (its vendor's
jaggedness page). So the one call carries two questions, the candidates in TF-IDF
order and reversed, and each candidate's two probabilities are averaged: the order
the shortlist arrives in cannot decide the ranking.

Privacy: what is sent is the checkout's branch name, recent commit subjects,
changed file paths and file types, plus the skills' own descriptions, all internal.
The decision-model privacy floor (lib/decision_privacy.may_send) still runs on it,
and nothing is sent from the guardian repository. The log keeps skill names,
probabilities, the working directory and a SHA-256 of the context, never the
context itself.

Records: a shadow row decides nothing, so it is not sealed into the IDR chain
(the plan's scope, ratified with V>>: seal only decisions that gate an action,
never routine classifications). The decision that does act, moving the banner to
Jev's order when H805 settles, is sealed with the shadow log's digest as its
evidence; each row's context digest lets a replay check what it was asked.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))

#: Candidates re-ranked: the TF-IDF top 8, so a skill TF-IDF puts 4th to 8th can
#: still reach the top three.
SHORTLIST = 8
_QUESTION = "Which of these skills best fits the work this checkout's recent activity shows?"
_DESC_CHARS = 200
#: Either switch turns it off: the operator's, and the test suite's fence for
#: every decision-model call (tests/conftest.py).
_OFF = (("GRIP_SKILL_RERANK_SHADOW", "0"), ("GRIP_CLASSIFY_NO_DECIDE", "1"))

Decide = Callable[[str, dict], Optional[dict]]


@dataclass(frozen=True)
class Rerank:
    """Jev's ranking of the shortlist, or why there is none. Uncalibrated and advisory."""
    ranking: Tuple[str, ...] = ()
    probabilities: Dict[str, float] = field(default_factory=dict)
    model: str = ""
    cost_usd: str = ""
    skipped: str = ""


def enabled() -> bool:
    return not any(os.environ.get(name, "") == value for name, value in _OFF)


def context_text(ctx) -> str:
    """The git context as a short plain-text state: branch, commits, changed files, file types."""
    lines = []
    if ctx.branch_name:
        lines.append(f"Branch: {ctx.branch_name}")
    lines += [f"Recent commit: {subject}" for subject in ctx.recent_commits[:5]]
    lines += [f"Changed file: {path}" for path in ctx.git_diff_files[:15]]
    if ctx.working_dir_extensions:
        lines.append("File types: " + ", ".join(sorted(set(ctx.working_dir_extensions))[:10]))
    return "\n".join(lines)


def _options(recs: Sequence) -> Dict[str, str]:
    return {r.skill_name: ((r.skill_info.description if r.skill_info else "") or "")[:_DESC_CHARS]
            for r in recs}


def _choice(names: Sequence[str], options: Dict[str, str]) -> dict:
    return {"type": "choice", "instructions": _QUESTION,
            "criteria": {name: options[name] or None for name in names}}


def _probabilities(answer, names: Sequence[str]) -> Dict[str, float]:
    """One question's probability for every candidate, or {} when any is missing or malformed.

    An incomplete reading is dropped whole: averaging a missing candidate in as zero
    (or over fewer readings than its rivals) would rank by how complete each answer
    was, not by what it said."""
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    probs = probs if isinstance(probs, dict) else {}
    valid = {n: float(probs[n]) for n in names
             if isinstance(probs.get(n), (int, float)) and not isinstance(probs.get(n), bool)
             and 0 <= probs[n] <= 1}
    return valid if len(valid) == len(names) else {}


def _averaged(answers: dict, names: Sequence[str]) -> Dict[str, float]:
    """Each candidate's mean over the complete readings; {} when there is none."""
    readings = [_probabilities(answers.get(q), names) for q in ("forward", "reversed")]
    readings = [r for r in readings if r]
    return {n: sum(r[n] for r in readings) / len(readings) for n in names} if readings else {}


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=10.0)


def _floor(text: str, cwd: Optional[str]) -> Tuple[bool, str]:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def rerank(recs: Sequence, ctx, *, cwd: Optional[str] = None, decide: Decide = _default_decide) -> Rerank:
    """Jev's order for ``recs`` (TF-IDF's shortlist); ties keep TF-IDF's order."""
    names = [r.skill_name for r in recs][:SHORTLIST]
    state = context_text(ctx)
    if len(names) < 2 or not state:
        return Rerank(skipped="too_few_candidates" if len(names) < 2 else "no_context")
    options = _options(recs[:SHORTLIST])
    allowed, why = _floor(state + "\n" + "\n".join(options.values()), cwd)
    if not allowed:
        return Rerank(skipped=f"privacy_floor:{why}")
    questions = {"forward": _choice(names, options), "reversed": _choice(names[::-1], options)}
    try:
        result = decide(state, questions)
    except Exception:  # noqa: BLE001 -- advisory; never raises
        result = None
    if not isinstance(result, dict) or not result.get("ok"):
        return Rerank(skipped="no_answer")
    probs = _averaged(result.get("answers") or {}, names)
    if not probs:
        return Rerank(skipped="no_answer")
    ranking = tuple(sorted(names, key=lambda n: (-probs[n], names.index(n))))
    return Rerank(ranking, probs, str(result.get("model") or ""), str(result.get("cost_usd") or ""))


def shadow_row(recs: Sequence, result: Rerank, *, ts: float, cwd: str, context: str = "") -> dict:
    """The log row: both rankings, the probabilities, the working directory and a digest
    of the context, never the context itself. The digest binds the row to the exact
    state that was sent, so a replay can check it without the log holding it."""
    return {"ts": ts, "cwd": cwd, "machine_reading": True, "calibrated": False,
            "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest() if context else "",
            "tfidf": [r.skill_name for r in recs][:SHORTLIST], "jev": list(result.ranking),
            "probabilities": {n: round(p, 4) for n, p in result.probabilities.items()},
            "model": result.model, "cost_usd": result.cost_usd, "skipped": result.skipped}


def shadow(log_path: Optional[Path] = None, decide: Decide = _default_decide) -> int:
    """At session start: rank both ways and log them. Always exits 0."""
    if not enabled():
        return 0
    try:
        from lib.claude_dir import get_claude_dir
        from lib.skill_recommender import gather_context_signals, recommend
        cwd = os.getcwd()
        ctx = gather_context_signals()
        recs = recommend(top_n=SHORTLIST, context=ctx)
        result = rerank(recs, ctx, cwd=cwd, decide=decide)
        path = log_path or (get_claude_dir() / "logs" / "skill-rerank-shadow.jsonl")
        with Path(path).open("a", encoding="utf-8") as handle:
            row = shadow_row(recs, result, ts=time.time(), cwd=cwd, context=context_text(ctx))
            handle.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 -- shadow work never disturbs a session start
        pass
    return 0


if __name__ == "__main__":
    sys.exit(shadow() if sys.argv[1:2] == ["shadow"] else 2)
