#!/usr/bin/env python3
"""Jev predicts a PR's AGORA verdict before AGORA renders one -- the Jev plan's
Phase 5.1 ("calibration you can watch", docs: ~/.grip/plans/snug-questing-quill.md).

The first time GRIP's merge sweep (``bin/grip-pr-monitor --sweep``) sees an open
PR's head commit with no AGORA verdict yet, a decision model (TypeSafe's Jev,
through HAL decide) answers two yes/no questions from the PR's title, diff stat
and touched paths alone -- never the diff itself, never the PR body:

    "Will AGORA's verdict be MERGE?"
    "Will every required check pass on this commit's first run?"

The prediction is sealed as a signed IDR leaf BEFORE the outcome can exist: the
leaf is written, and only once that succeeds is a "prediction" row appended to
``state/jev-calibration.jsonl`` (gitignored runtime state). A PR whose current
head already carries a genuine AGORA verdict is skipped outright -- nothing is
ever predicted after the fact (``_current_verdict``). Once a real verdict and/or
a CI result become available, a "settlement" row is appended beside the
prediction, and ``lib.jev_calibration`` turns the joined rows into a Brier score.

Off by default since 17 October 2026; requires `GRIP_JEV_VERDICT_PREDICTOR=1`.
See docs/jev-shadows.md for the measurements and retained reporting.

SAFETY -- this is advisory delight, never merge machinery. Nothing in this
module is read by anything that decides what merges, and ``tick()`` is the one
entry point the sweep calls; every path through it is wrapped so a raised
exception degrades to a logged no-op. The thing that actually makes a hang here
harmless is NOT in this file -- it is that ``bin/grip-pr-monitor`` launches
``tick`` detached (a backgrounded subshell that returns immediately), so even
an infinite loop in this module could not delay, hold, or change a merge
decision.

SCOPE, stated rather than discovered later:
  - The two questions ask about Jev's PREDICTION; settling them is this
    module's own empirical read of what actually happened, which is narrower
    in one place: "every required check" is read via ``gh pr checks
    --required``, which only reflects the PR's CURRENT head. If the branch
    advances past the predicted head before this module next looks, the CI
    half of that older prediction is left unsettled (null) permanently --
    cheaper and safer than reconstructing a historical required-check set via
    the commits/check-runs API, and the Brier scorer reports its denominator
    honestly rather than guessing.
  - "First run" means whatever this module observes the first time it finds
    every required check in a terminal state for that head. A check an
    operator manually reruns before this module ever reads it would record
    that rerun's result, not the true first attempt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

IDR_KIND = "jev-verdict-prediction"
#: Explicit opt-in; the suite's fence still overrides it.
_OPT_IN = "GRIP_JEV_VERDICT_PREDICTOR"
_OFF = (("GRIP_CLASSIFY_NO_DECIDE", "1"),)
#: Jev's two questions, phrased verbatim from the Jev plan's Phase 5.1. Static
#: text only -- every PR-specific detail lives in the state, never here.
QUESTION_MERGE = {"type": "noul", "instructions": "Will AGORA's verdict be MERGE?"}
QUESTION_CI = {"type": "noul",
               "instructions": "Will every required check pass on this commit's first run?"}
#: State budget: never the diff, never the PR body -- just enough of the title,
#: diff stat and touched paths for Jev to read, capped well under the vendor's
#: 32k-token state limit.
STATE_FILES_LIMIT = 200
STATE_CHAR_LIMIT = 6000
#: A required check's bucket (gh's own category) once it is no longer running.
_PASSING_BUCKETS = {"pass", "skipping"}
_FAILING_BUCKETS = {"fail", "cancel"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

Decide = Callable[[str, dict], Optional[dict]]


def enabled() -> bool:
    return (os.environ.get(_OPT_IN) == "1"
            and not any(os.environ.get(name, "") == value for name, value in _OFF))


def _questions() -> Dict[str, dict]:
    return {"merge": QUESTION_MERGE, "ci_pass": QUESTION_CI}


def question_config_sha256() -> str:
    """sha256 of the exact question dict sent to Jev -- so a later wording change
    is visible in the calibration log rather than silently reinterpreting old rows."""
    body = json.dumps(_questions(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# gh data gathering -- pure parsing kept separate from the subprocess calls so
# tests exercise the parsing without ever touching a shell.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PRState:
    """Everything ``tick`` needs about one PR, fetched once per call."""
    title: str
    head_sha: str
    additions: int
    deletions: int
    files: Tuple[str, ...]
    comments: Tuple[dict, ...] = field(default_factory=tuple)


def _run_gh(args: List[str], *, timeout: float = 15.0) -> Optional[str]:
    """``gh``'s stdout, or None on any failure (missing binary, non-zero exit,
    a hung call past ``timeout``). Never raises."""
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return out.stdout if out.returncode == 0 else None


#: Matches zero-or-more whitespace -- used to step past the gap between two
#: concatenated top-level JSON values without a second (nested) loop.
_WS = re.compile(r"\s*")


def parse_concatenated_json(text: str) -> list:
    """Flatten ``gh api --paginate``'s output into one list.

    Without ``--jq``, each page of an array-returning endpoint prints as its
    own top-level JSON array, back to back -- not one parseable blob, and not
    fixed by ``--slurp`` either (that wraps pages into an array OF arrays).
    ``json.JSONDecoder.raw_decode`` finds successive top-level values in a
    string directly, so this works for 0, 1 or N pages with no dependency on
    jq's streaming behaviour. Pure; never raises -- a malformed tail is
    dropped rather than losing the pages already parsed.
    """
    decoder = json.JSONDecoder()
    out: list = []
    text = text.strip()
    idx, n = 0, len(text)
    while idx < n:
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        out.extend(obj) if isinstance(obj, list) else out.append(obj)
        idx = _WS.match(text, end).end()
    return out


def _gh_api_list(path: str) -> Optional[list]:
    out = _run_gh(["api", "--paginate", path])
    return parse_concatenated_json(out) if out is not None else None


def _default_gh_fetch(repo: str, pr: int) -> Optional[PRState]:
    """The real PR-state fetch: one ``pr view``, one files page-set, one
    comments page-set. Returns None on any failure -- the caller falls back
    to doing nothing this tick, and tries again next time."""
    meta_raw = _run_gh(["pr", "view", str(pr), "--repo", repo, "--json",
                        "title,headRefOid,additions,deletions"])
    if meta_raw is None:
        return None
    try:
        meta = json.loads(meta_raw)
        title = str(meta["title"])
        head = str(meta["headRefOid"])
        additions = int(meta.get("additions") or 0)
        deletions = int(meta.get("deletions") or 0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not _SHA_RE.match(head):
        return None
    files_raw = _gh_api_list(f"repos/{repo}/pulls/{pr}/files") or []
    files = tuple(str(f.get("filename")) for f in files_raw
                 if isinstance(f, dict) and f.get("filename") is not None)
    comments = tuple(_gh_api_list(f"repos/{repo}/issues/{pr}/comments") or [])
    return PRState(title=title, head_sha=head, additions=additions,
                  deletions=deletions, files=files, comments=comments)


def _current_verdict(comments: Tuple[dict, ...], head_sha: str) -> Optional[str]:
    """The genuine AGORA verdict for ``head_sha``, or None if there isn't one yet."""
    from lib.agora_verdict_router import genuine_verdict_for_sha
    return genuine_verdict_for_sha(list(comments), head_sha)


def _required_checks(repo: str, pr: int) -> Optional[Dict[str, str]]:
    """``{check name: bucket}`` for the PR's CURRENT required checks, or None
    when that cannot be read (no permission, gh failure). Reflects whatever
    head the PR carries right now -- see the module docstring's scope note."""
    raw = _run_gh(["pr", "checks", str(pr), "--repo", repo, "--required",
                  "--json", "name,bucket"])
    if raw is None:
        return None
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    return {str(r.get("name")): str(r.get("bucket")) for r in rows if isinstance(r, dict)}


def _ci_first_run_pass(buckets: Optional[Dict[str, str]]) -> Optional[bool]:
    """True/False once every required check is terminal, None while any is
    still pending or the required set could not be read at all."""
    if not buckets:
        return None  # unreadable, or gh reports no required checks at all
    values = set(buckets.values())
    if values & _FAILING_BUCKETS:
        return False
    if "pending" in values:
        return None
    return values <= _PASSING_BUCKETS


# --------------------------------------------------------------------------- #
# state text + the decide call
# --------------------------------------------------------------------------- #

def build_state_text(title: str, additions: int, deletions: int,
                     files: Tuple[str, ...]) -> str:
    """The text sent to Jev: title, diff stat, touched paths. Never the diff
    content and never the PR body."""
    shown = list(files[:STATE_FILES_LIMIT])
    omitted = len(files) - len(shown)
    lines = [
        f"Pull request title: {title}",
        f"Diff stat: +{additions} -{deletions} across {len(files)} file(s)",
        "Touched paths:",
        *shown,
    ]
    if omitted > 0:
        lines.append(f"...and {omitted} more file(s) not shown")
    return "\n".join(lines)[:STATE_CHAR_LIMIT]


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=10.0)


def _floor(text: str, cwd: Optional[str]) -> Tuple[bool, str]:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def _probability(answer) -> Optional[float]:
    p = answer.get("noul") if isinstance(answer, dict) else None
    ok = isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1
    return float(p) if ok else None


def _log(msg: str) -> None:
    print(f"[jev-verdict-predictor] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# calibration log I/O
# --------------------------------------------------------------------------- #

def _calibration_path() -> Path:
    from lib.claude_dir import get_claude_dir
    return get_claude_dir() / "state" / "jev-calibration.jsonl"


def calibration_path() -> Path:
    """The default calibration log path -- public so ``lib.jev_calibration``
    reads the same file without reaching for a private name."""
    return _calibration_path()


def _append_row(row: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _read_rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _matches(row: dict, kind: str, repo: str, pr: int, head: Optional[str] = None) -> bool:
    if row.get("type") != kind or row.get("repo") != repo or row.get("pr") != pr:
        return False
    return head is None or row.get("head_sha") == head


def _already_predicted(repo: str, pr: int, head: str, path: Path) -> bool:
    return any(_matches(r, "prediction", repo, pr, head) for r in _read_rows(path))


def latest_settlement(repo: str, pr: int, head: str, path: Path) -> Optional[dict]:
    """The most recent settlement row for this exact (repo, pr, head), or None."""
    rows = [r for r in _read_rows(path) if _matches(r, "settlement", repo, pr, head)]
    return rows[-1] if rows else None


def predictions_for_pr(repo: str, pr: int, path: Path) -> List[dict]:
    """Every prediction row for (repo, pr), in file order."""
    return [r for r in _read_rows(path) if _matches(r, "prediction", repo, pr)]


def read_calibration_rows(path: Optional[Path] = None) -> List[dict]:
    """Every row in the calibration log, in file order -- the one read path
    ``lib.jev_calibration`` scores from, so the schema lives in one place."""
    return _read_rows(path or _calibration_path())


# --------------------------------------------------------------------------- #
# the IDR leaf -- sealed BEFORE the outcome exists
# --------------------------------------------------------------------------- #

def _fingerprint(repo: str, pr: int, head: str) -> str:
    return hashlib.sha256(f"{repo}#{pr}@{head}".encode("utf-8")).hexdigest()[:16]


def _write_idr_leaf(repo: str, pr: int, head: str, title: str, p_merge: float,
                    p_ci: float, model: str, idr_path: Optional[Path]) -> Optional[dict]:
    """Build + append the signed leaf. Returns {"id", "addr"} or None on any
    failure -- the caller treats a failed seal as "nothing was predicted"."""
    try:
        from dataclasses import asdict
        import lib.precog.idr as idr
        decision = {
            "action": IDR_KIND,
            "repo": repo,
            "pr": pr,
            "head_sha": head,
            "p_merge": p_merge,
            "p_ci_pass": p_ci,
            "model": model,
            "question_config_sha256": question_config_sha256(),
            "machine_reading": True,
            "calibrated": False,
        }
        leaf = idr.build_idr(
            prompt="", fingerprint=_fingerprint(repo, pr, head), decision=decision,
            predecessor_idr=None, depth=0, kind=IDR_KIND,
            inputs={"title": str(title)[:200]},
        )
        idr.append_idr(leaf, path=idr_path)
        return {"id": leaf.id, "addr": idr.content_addr(asdict(leaf))}
    except Exception as exc:  # noqa: BLE001 -- a failed seal predicts nothing
        _log(f"idr_write_failed: {type(exc).__name__}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# predict
# --------------------------------------------------------------------------- #

def predict_once(repo: str, pr: int, *, ps: PRState, decide: Decide = _default_decide,
                 cwd: Optional[str] = None, calibration_path: Optional[Path] = None,
                 idr_path: Optional[Path] = None) -> dict:
    """Ask Jev once about ``ps.head_sha`` and seal the answer, unless a verdict
    already exists for that head or it has already been predicted. Never raises."""
    try:
        return _predict(repo, pr, ps, decide, cwd,
                        calibration_path or _calibration_path(), idr_path)
    except Exception as exc:  # noqa: BLE001 -- advisory; never raises
        _log(f"#{pr}: predict error {type(exc).__name__}: {exc}")
        return {"ok": False, "reason": f"error:{type(exc).__name__}"}


def _predict(repo, pr, ps: PRState, decide: Decide, cwd, calibration_path: Path,
            idr_path: Optional[Path]) -> dict:
    if not enabled():
        return {"ok": False, "reason": "off"}
    if _current_verdict(ps.comments, ps.head_sha) is not None:
        return {"ok": False, "reason": "verdict_exists"}
    if _already_predicted(repo, pr, ps.head_sha, calibration_path):
        return {"ok": False, "reason": "already_predicted"}
    state_text = build_state_text(ps.title, ps.additions, ps.deletions, ps.files)
    allowed, why = _floor(state_text, cwd)
    if not allowed:
        _log(f"#{pr}@{ps.head_sha[:12]}: skip (privacy_floor:{why})")
        return {"ok": False, "reason": f"privacy_floor:{why}"}
    try:
        result = decide(state_text, _questions())
    except Exception as exc:  # noqa: BLE001 -- the predictor never raises on decide
        _log(f"#{pr}@{ps.head_sha[:12]}: decide raised {type(exc).__name__}")
        result = None
    if not isinstance(result, dict) or not result.get("ok"):
        _log(f"#{pr}@{ps.head_sha[:12]}: no_answer")
        return {"ok": False, "reason": "no_answer"}
    answers = result.get("answers") or {}
    p_merge = _probability(answers.get("merge"))
    p_ci = _probability(answers.get("ci_pass"))
    if p_merge is None or p_ci is None:
        _log(f"#{pr}@{ps.head_sha[:12]}: malformed_answer")
        return {"ok": False, "reason": "malformed_answer"}
    model = str(result.get("model") or "")
    sealed = _write_idr_leaf(repo, pr, ps.head_sha, ps.title, p_merge, p_ci, model, idr_path)
    if sealed is None:
        return {"ok": False, "reason": "idr_write_failed"}
    row = {
        "type": "prediction", "ts": _now_iso(), "repo": repo, "pr": pr,
        "head_sha": ps.head_sha, "title": ps.title[:200], "p_merge": p_merge,
        "p_ci_pass": p_ci, "model": model, "cost_usd": str(result.get("cost_usd") or ""),
        "question_config_sha256": question_config_sha256(),
        "idr_id": sealed["id"], "idr_addr": sealed["addr"],
        "machine_reading": True, "calibrated": False,
    }
    _append_row(row, calibration_path)
    _log(f"#{pr}@{ps.head_sha[:12]}: predicted (p_merge={p_merge:.2f} p_ci={p_ci:.2f})")
    return {"ok": True, "reason": "predicted", "row": row}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# settle
# --------------------------------------------------------------------------- #

def settle_once(repo: str, pr: int, *, ps: PRState,
                check_fetch: Callable[[str, int], Optional[Dict[str, str]]] = _required_checks,
                calibration_path: Optional[Path] = None) -> List[dict]:
    """Append a settlement row for every prediction of this PR that has learned
    something new since its last settlement. Never raises; returns the rows
    actually written (possibly empty)."""
    try:
        return _settle(repo, pr, ps, check_fetch, calibration_path or _calibration_path())
    except Exception as exc:  # noqa: BLE001 -- advisory; never raises
        _log(f"#{pr}: settle error {type(exc).__name__}: {exc}")
        return []


def _settlement_candidate(repo, pr, head, ps: PRState, prior: dict, check_fetch) -> dict:
    """What we currently know about ``head`` -- never regresses a field the
    prior settlement already resolved back to unknown."""
    verdict_token = _current_verdict(ps.comments, head) or prior.get("verdict_token")
    actual_merge = None if verdict_token is None else verdict_token == "MERGE"

    ci_pass, required_known = prior.get("actual_ci_pass"), bool(prior.get("required_known"))
    if ci_pass is None and head == ps.head_sha:
        buckets = check_fetch(repo, pr)
        required_known = buckets is not None
        ci_pass = _ci_first_run_pass(buckets)
    return {"verdict_token": verdict_token, "actual_merge": actual_merge,
           "actual_ci_pass": ci_pass, "required_known": required_known}


def _settle(repo, pr, ps: PRState, check_fetch, calibration_path: Path) -> List[dict]:
    written = []
    for pred in predictions_for_pr(repo, pr, calibration_path):
        head = pred.get("head_sha")
        if not head:
            continue
        prior = latest_settlement(repo, pr, head, calibration_path) or {}
        candidate = _settlement_candidate(repo, pr, head, ps, prior, check_fetch)
        # "required_known" is bookkeeping for the calibration report's own
        # transparency, not an outcome -- comparing it here would write a
        # spurious row the FIRST time settlement is attempted (missing key
        # None vs the candidate's always-bool False) even though neither
        # real outcome moved from unknown to known.
        if (candidate["verdict_token"] == prior.get("verdict_token")
                and candidate["actual_ci_pass"] == prior.get("actual_ci_pass")):
            continue
        row = {"type": "settlement", "ts": _now_iso(), "repo": repo, "pr": pr,
              "head_sha": head, "machine_reading": True, "calibrated": False, **candidate}
        _append_row(row, calibration_path)
        _log(f"#{pr}@{head[:12]}: settled (verdict={candidate['verdict_token']} "
            f"ci_pass={candidate['actual_ci_pass']})")
        written.append(row)
    return written


# --------------------------------------------------------------------------- #
# tick -- the one entry point the sweep calls, detached
# --------------------------------------------------------------------------- #

def tick(repo: str, pr: int, *, gh_fetch: Callable[[str, int], Optional[PRState]] = _default_gh_fetch,
         decide: Decide = _default_decide,
         check_fetch: Callable[[str, int], Optional[Dict[str, str]]] = _required_checks,
         cwd: Optional[str] = None, calibration_path: Optional[Path] = None,
         idr_path: Optional[Path] = None) -> dict:
    """Predict a new head and settle any prior ones, in one pass. Never raises."""
    try:
        if not enabled():
            return {"ok": False, "reason": "off"}
        ps = gh_fetch(repo, pr)
        if ps is None:
            _log(f"#{pr}: gh_unavailable")
            return {"ok": False, "reason": "gh_unavailable"}
        predicted = predict_once(repo, pr, ps=ps, decide=decide, cwd=cwd,
                                 calibration_path=calibration_path, idr_path=idr_path)
        settled = settle_once(repo, pr, ps=ps, check_fetch=check_fetch,
                              calibration_path=calibration_path)
        return {"ok": True, "predicted": predicted, "settled": settled}
    except Exception as exc:  # noqa: BLE001 -- the sweep must never see this raise
        _log(f"#{pr}: tick error {type(exc).__name__}: {exc}")
        return {"ok": False, "reason": f"error:{type(exc).__name__}"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Jev verdict prediction (Phase 5.1)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("tick", "predict", "settle"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--pr", required=True, type=int)
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "tick":
            tick(ns.repo, ns.pr)
        else:
            ps = _default_gh_fetch(ns.repo, ns.pr)
            if ps is None:
                _log(f"#{ns.pr}: gh_unavailable")
            elif ns.cmd == "predict":
                predict_once(ns.repo, ns.pr, ps=ps)
            else:
                settle_once(ns.repo, ns.pr, ps=ps)
    except Exception as exc:  # noqa: BLE001 -- a CLI crash must never surface as a merge event
        _log(f"fatal: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    from lib.pq_reexec import reexec_if_incapable
    reexec_if_incapable(Path(__file__))
    sys.exit(main())
