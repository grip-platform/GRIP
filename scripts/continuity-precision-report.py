#!/usr/bin/env python3
"""Congruent Continuity — the precision report (build-order D3), re-runnable.

Writes the measurement ``rules/congruent-continuity.md`` and
``docs/congruent-continuity-genesis.md:81`` require before any watcher arms:

1. the LABEL corpus by source (hypothesis ledger, git proxies, chain verdicts)
   and its refutation base rates;
2. the PAIRED corpus — the subset whose label was joined to the transcript
   event that made the decision (``lib/continuity_pairing.py``) — reported
   separately from the label-only remainder, which can never inflate n;
3. per predictor (``lib/continuity_trio.py``), cross-validated precision of
   its wrong-track flags on the paired corpus, with a Wilson interval and the
   verdict against the only computable bar, the paired base rate;
4. the plain-language verdict: whether precision is establishable at this n,
   and that the watcher stays WARN-only regardless (arming is V>>-gated).

Every number in the markdown carries the invocation that produced it. Same
inputs and seed reproduce the same report apart from the timestamp and the
elapsed line.

    python3 scripts/continuity-precision-report.py            # defaults: live stores
    python3 scripts/continuity-precision-report.py --out docs/x.md --json-out docs/x.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from itertools import chain as _chain
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.continuity_chain import COMMIT_KIND, _STATE, read_idr_chain  # noqa: E402
from lib.continuity_labels import mine_chain, mine_git, mine_hypotheses  # noqa: E402
from lib.continuity_pairing import (  # noqa: E402
    commit_subjects,
    pair_chain_rows,
    pair_git_rows,
    rg_candidates,
    subject_key,
)
from lib.continuity_precision import base_rate, measure, partition  # noqa: E402

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
# docs/* is gitignored; docs/measurements/ is the repo's tracked home for measurement artefacts.
REPORT_MD = _ROOT / "docs" / "measurements" / "2026-09-10-congruent-continuity-precision.md"
REPORT_JSON = _ROOT / "docs" / "measurements" / "2026-09-10-congruent-continuity-precision.json"
REPORT_CSV = _ROOT / "docs" / "measurements" / "2026-09-10-congruent-continuity-precision-paired.csv"


def portable(path) -> str:
    """Render a path without this machine's home directory, so a committed
    artefact carries no machine path (the repo mirrors to a client org)."""
    text = str(path)
    home = str(Path.home())
    if text.startswith(home):
        text = "~" + text[len(home):]
    # the harness names project directories by the slugified cwd, which
    # carries the same home path with slashes turned into dashes
    return text.replace(home.replace("/", "-"), "~")

_VERDICT_NOT_ESTABLISHABLE = (
    "NOT ESTABLISHABLE. Fewer than two paired rows of one class exist, so no cross-validated "
    "precision can be computed. The watcher stays WARN-only and the paired corpus must grow "
    "before D3 can be re-tested.")
_VERDICT_BAR_NOT_CLEARED = (
    "MEASURED, BAR NOT CLEARED. No predictor's flag-precision interval has a lower bound above "
    "the paired base rate, so none demonstrably beats flagging everything. The watcher stays "
    "WARN-only.")
_VERDICT_BAR_CLEARED = (
    "MEASURED, BAR CLEARED BY {models} against the paired base rate. The comparison the rule "
    "actually names, the self-assessed-confidence baseline, is UNMEASURED because no commitment "
    "record carries that confidence, so D3 is not satisfied on its own terms. The watcher stays "
    "WARN-only; arming is a V>>-gated decision.")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def chain_files(state: Path) -> list[Path]:
    """Every chain file, session-namespaced ones included; never the HEAD
    pointers or the contamination logs that share the prefix."""
    return sorted(p for p in state.glob("continuity*.jsonl") if "contamination" not in p.name)


def _transcripts(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


def _exclusions(transcripts: list[Path], session_ids: list[str]) -> list[Path]:
    wanted = {f"{sid}.jsonl" for sid in session_ids if sid}
    return [t for t in transcripts if t.name in wanted]


def _git_head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _commit_ids(chain_file: Path) -> list[str]:
    if not chain_file.is_file():
        return []
    return [node.id for node in read_idr_chain(path=chain_file) if node.kind == COMMIT_KIND]


def _shared_candidates(git_rows: list[dict], chains: list[Path], repo: Path, searchable: list[Path]) -> dict:
    """The single ripgrep pass both pairings read from."""
    subjects = commit_subjects(repo)
    keys = {subject_key(subjects[r["ref"]]) for r in git_rows if r["ref"] in subjects}
    ids = list(_chain.from_iterable(_commit_ids(c) for c in chains))
    return rg_candidates([k for k in keys if k] + ids, searchable)


def _pair_chains(chains: list[Path], transcripts: list[Path], *, window: int,
                 excluded: list[Path], candidates: dict) -> tuple[list, list[dict]]:
    paired: list = []
    stats: list[dict] = []
    for c in chains:
        pc, sc = pair_chain_rows(c, transcripts, window=window, exclude=excluded, candidates=candidates)
        paired += pc
        stats.append({"chain": portable(c), **sc})
    return paired, stats


def _verdict(meas: dict) -> str:
    models = meas["models"]
    if not any(m.get("establishable") for m in models.values()):
        return _VERDICT_NOT_ESTABLISHABLE
    clears = [name for name, m in models.items() if m.get("clears_bar") is True]
    if not clears:
        return _VERDICT_BAR_NOT_CLEARED
    return _VERDICT_BAR_CLEARED.format(models=", ".join(clears))


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> dict:
    t0 = time.time()
    repo = Path(args.repo)
    chains = [Path(c) for c in args.chain] if args.chain else chain_files(_STATE)
    transcripts = _transcripts(Path(args.transcripts))
    excluded = _exclusions(transcripts, args.exclude_session)
    hyp_rows = mine_hypotheses(args.hypotheses)
    git_rows = mine_git(repo, window=args.window_commits, limit=args.git_limit)
    chain_rows = list(_chain.from_iterable(mine_chain(c) for c in chains))
    label_rows = hyp_rows + git_rows + chain_rows

    t_rg = time.time()
    searchable = [t for t in transcripts if t not in set(excluded)]
    candidates = _shared_candidates(git_rows, chains, repo, searchable)
    rg_elapsed = time.time() - t_rg

    t_pair = time.time()
    paired_git, stats_git = pair_git_rows(git_rows, repo, transcripts, window=args.window,
                                          exclude=excluded, candidates=candidates)
    paired_chain, stats_chain = _pair_chains(chains, transcripts, window=args.window,
                                             excluded=excluded, candidates=candidates)
    pair_elapsed = time.time() - t_pair
    # a fixed row order makes the seeded folds, and so every figure, reproducible
    paired = sorted(paired_git + paired_chain, key=lambda pr: (pr.source, pr.transcript, pr.epoch, pr.ref))
    paired_git = sorted(paired_git, key=lambda pr: (pr.transcript, pr.epoch, pr.ref))
    meas = measure(paired, k=args.k, seed=args.seed)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "invocation": " ".join(["python3", "scripts/continuity-precision-report.py", *[portable(a) for a in sys.argv[1:]]]),
        "seed": args.seed, "k": args.k, "window": args.window,
        "repo": portable(repo), "repo_head": _git_head(repo),
        "chains": [portable(c) for c in chains],
        "hypotheses": portable(args.hypotheses) if args.hypotheses else "(default ledger)",
        "transcripts_root": portable(args.transcripts), "transcripts": len(transcripts),
        "transcripts_bytes": sum(t.stat().st_size for t in transcripts),
        "excluded_transcripts": [portable(e) for e in excluded],
        "paired_rows": [{**pr.as_dict(), "transcript": portable(pr.transcript)} for pr in paired],
        "labels": {"hypothesis": len(hyp_rows), "git": len(git_rows), "chain": len(chain_rows),
                   "total": len(label_rows)},
        "base_rates": {name: base_rate([r["label"] for r in rows]) for name, rows in
                       (("hypothesis", hyp_rows), ("git", git_rows), ("chain", chain_rows), ("all_labels", label_rows))},
        "pairing": {"git": stats_git, "chain": stats_chain,
                    "pairability_upper_bound_git": (stats_git["candidate_commits"] / len(git_rows)) if git_rows else None},
        "partition": partition(label_rows, paired),
        "measurement": meas,
        "measurement_git_only": measure(paired_git, k=args.k, seed=args.seed) if paired_git else None,
        "verdict": _verdict(meas),
        "elapsed_s": {"rg_pass": round(rg_elapsed, 1), "pairing": round(pair_elapsed, 1),
                      "total": round(time.time() - t0, 1)},
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _fmt_ci(ci) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]"


def _pct(x) -> str:
    return "n/a" if x is None else f"{100.0 * x:.1f}%"


def _md_head(r: dict) -> list[str]:
    return [
        "# Congruent Continuity — precision measurement on the post-hoc corpus",
        "",
        f"Generated {r['generated_at']} by `{r['invocation']}` (seed {r['seed']}, k={r['k']}, "
        f"feature window {r['window']} events). Re-run the same command to reproduce every number below.",
        "",
        "## What this report is for",
        "",
        "Build-order decision D3 (`docs/congruent-continuity-genesis.md:81`) keeps the pre-hoc watcher "
        "WARN-only until its precision has been measured on a post-hoc corpus, and "
        "`rules/congruent-continuity.md` falsifies the whole primitive if that precision proves no better "
        "than the self-assessed-confidence baseline. This report computes the number the decision waits on. "
        "The number being honest is the pass condition; the number being high is not.",
        "",
        "## Verdict",
        "",
        r["verdict"],
        "",
        "## Inputs",
        "",
        f"- Repository `{r['repo']}` at `{r['repo_head']}`.",
        f"- Chain files: {', '.join('`' + c + '`' for c in r['chains']) or 'none'}.",
        f"- Hypothesis ledger: `{r['hypotheses']}`.",
        f"- Transcripts: {r['transcripts']} files, {r['transcripts_bytes'] / 1e9:.1f} GB under "
        f"`{r['transcripts_root']}`; {len(r['excluded_transcripts'])} excluded as the predicting session (leakage rule).",
        "",
    ]


def _md_labels(r: dict) -> list[str]:
    rows = [f"| {name} | {b['n']} | {b['positives']} | {_pct(b['rate'])} | {_fmt_ci(b['ci95'])} |"
            for name, b in r["base_rates"].items()]
    return [
        "## The label corpus and its base rates",
        "",
        "A label says whether a claim held (0) or was refuted (1). It is base-rate evidence on its own; "
        "it says nothing about a predictor until it is paired with the trajectory that preceded the decision.",
        "",
        "| Source | Labels | Refuted | Refutation rate | Wilson 95% |",
        "|---|---|---|---|---|",
        *rows,
        "",
    ]


def _md_chain_line(sc: dict) -> str:
    return (f"Chain `{sc['chain']}`: {sc['commits']} commitments, {sc['settled']} settled, {sc['unlabelled']} "
            f"without a usable label; {sc['declared_transcript']} declared their transcript "
            f"({sc['declared_missing']} missing); {sc['mention_search']} searched by id mention; "
            f"{sc['paired']} paired ({sc['paired_by_label'][1]} refuted, {sc['paired_by_label'][0]} held).")


def _md_pairing(r: dict) -> list[str]:
    p, g = r["partition"], r["pairing"]["git"]
    ub = r["pairing"]["pairability_upper_bound_git"]
    source_rows = [f"| {name} | {b['paired']} | {b['label_only']} |" for name, b in p["by_source"].items()]
    return [
        "## The paired corpus",
        "",
        f"Paired rows (label joined to the decision event, vector extracted as-of that event): **{p['paired']}**. "
        f"Label-only rows: **{p['label_only']}**. The hypothesis ledger is label-only by construction: a hypothesis "
        "carries no transcript, so no vector can exist for it.",
        "",
        "| Source | Paired | Label-only |",
        "|---|---|---|",
        *source_rows,
        "",
        f"Git proxies: {g['rows']} rows; {g['no_subject']} with no subject in this clone; {g['short_subject']} with a "
        f"subject too short to key on; {g['candidate_commits']} whose subject appears somewhere in a transcript "
        f"({_pct(ub)} — an upper bound on pairability, since a mention is not a decision); {g['located']} located "
        f"at a real `git commit` command; {g['paired']} paired ({g['paired_by_label'][1]} refuted, "
        f"{g['paired_by_label'][0]} held).",
        *[_md_chain_line(sc) for sc in r["pairing"]["chain"]],
        "",
    ]


def _md_model_row(name: str, res: dict) -> str:
    if not res.get("establishable"):
        return f"| {name} | no — {res['reason']} | | | | | | | | |"
    return (f"| {name} | yes ({res['folds']} folds) | {res['flagged']} | {res['tp']} | {res['fp']} | {res['abstain']} | "
            f"{_pct(res['precision'])} | {_fmt_ci(res['ci95'])} | {_pct(res['recall'])} | {res['clears_bar']} |")


def _md_git_only(mg: dict | None) -> list[str]:
    if not mg:
        return []
    cells = "; ".join(
        f"{k}: " + (f"{_pct(v['precision'])} {_fmt_ci(v['ci95'])}" if v.get("establishable") else "not establishable")
        for k, v in mg["models"].items())
    return ["", f"Git-proxy rows only (n = {mg['paired_n']}, base rate {_pct(mg['base_rate']['rate'])}): {cells}."]


def _md_models(r: dict) -> list[str]:
    m = r["measurement"]
    br = m["base_rate"]
    return [
        "## Predictor precision on the paired corpus",
        "",
        f"Paired n = {m['paired_n']}; paired base rate {_pct(br['rate'])} {_fmt_ci(br['ci95'])} — the precision of "
        "the trivial predictor that flags every row, and the only baseline this corpus can supply. "
        f"Self-assessed-confidence baseline: {m['confidence_baseline']}.",
        "",
        "| Model | Establishable | Flagged | TP | FP | Abstained | Precision | Wilson 95% | Recall | Clears base-rate bar |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[_md_model_row(name, res) for name, res in m["models"].items()],
        *_md_git_only(r.get("measurement_git_only")),
        "",
    ]


def _md_limits(r: dict) -> list[str]:
    e = r["elapsed_s"]
    return [
        "## How to read the limits",
        "",
        "- A Wilson interval is stated instead of a bare point because at small n the interval IS the finding.",
        "- A git proxy label means 'reverted, or a fix touching the same files landed within five commits'; "
        "it is a proxy for wrong-track, not a verdict, and it is paired only where the commit's own `git commit` "
        "command was found in a transcript on this machine.",
        "- Chain-state features are extracted with no chain for every paired row, because the chain's state as-of a "
        "past instant is not reconstructible without replay; the remaining seventeen features are transcript-only.",
        "- Nothing here arms anything. The PreToolUse binding of the watcher is the V>>-gated step.",
        "- The paired feature matrix ships beside this report as a CSV (one row per label and vector), so the "
        "cross-validation can be reproduced without re-parsing the transcript corpus.",
        "",
        f"Elapsed: ripgrep pass {e['rg_pass']}s, pairing {e['pairing']}s, total {e['total']}s.",
        "",
    ]


def write_paired_csv(path: Path, rows: list[dict]) -> None:
    """The paired feature matrix, one row per (label, vector), so downstream
    statistical work reproduces from the artefact without re-parsing the
    transcript corpus (AGORA GRIP#5387, deferred suggestion)."""
    import csv
    from lib.continuity_features import FEATURE_NAMES
    head = ["source", "ref", "label", "transcript", "epoch", *FEATURE_NAMES]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(head)
        for row in rows:
            writer.writerow([row["source"], row["ref"], row["label"], row["transcript"], row["epoch"],
                             *[row["features"][name] for name in FEATURE_NAMES]])


def render(r: dict) -> str:
    return "\n".join(_md_head(r) + _md_labels(r) + _md_pairing(r) + _md_models(r) + _md_limits(r))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=str(CLAUDE_DIR), help="git repo mined for wrong-track proxies")
    p.add_argument("--transcripts", default=str(CLAUDE_DIR / "projects"), help="root scanned for session transcripts")
    p.add_argument("--chain", action="append", help="chain file (repeatable); default: every continuity*.jsonl chain")
    p.add_argument("--hypotheses", default=None, help="hypothesis ledger path; default: the live ledger")
    p.add_argument("--out", default=str(REPORT_MD))
    p.add_argument("--json-out", default=str(REPORT_JSON))
    p.add_argument("--csv-out", default=str(REPORT_CSV), help="paired feature matrix (CSV)")
    p.add_argument("--exclude-session", action="append",
                   default=[s for s in [os.environ.get("CLAUDE_SESSION_ID", "")] if s],
                   help="session id whose transcript must not pair (default: $CLAUDE_SESSION_ID)")
    p.add_argument("--window", type=int, default=50, help="feature window (events)")
    p.add_argument("--window-commits", type=int, default=5, help="git proxy fix-follow window (commits)")
    p.add_argument("--git-limit", type=int, default=None)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run(args)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render(report), encoding="utf-8")
    Path(args.json_out).write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    write_paired_csv(Path(args.csv_out), report["paired_rows"])
    print(f"paired n={report['partition']['paired']} label-only={report['partition']['label_only']}; "
          f"{report['verdict'].split('.')[0]}; wrote {args.out}")
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
