#!/usr/bin/env python3
"""
mirror/runner.py — GRIP MIRROR orchestrator.

Applies converge() to any GitHub repository, recursively improving it
until a quality gate is met or max depth is reached.

The convergence loop:
    state     = RepoHealth snapshot (quality_score, issues, PRs, CI status)
    criterion = quality_score >= threshold (default 80)
    improve   = close top issue via sprint-loop, or create a targeted PR

This is stateless at the call level but persistent via KONO memory:
each run stores its findings, and subsequent runs load prior context
to avoid re-analysing what's already known.

Usage:
    mirror = GripAnywhere("owner/repo", quality_threshold=85)
    result = mirror.run(max_depth=3)                  # stealth by default:
                                                      # analysis only, no
                                                      # upstream writes
    result = mirror.run(max_depth=3, dry_run=False)   # explicit opt-out:
                                                      # writes upstream
    print(result.summary())
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure the repo root is importable when run as a script. Guard the
# path being INSERTED (the repo root), not the lib dir itself — guarding
# _LIB while inserting _LIB.parent skips the bootstrap exactly when a bare
# lib/ directory is already on sys.path.
_LIB = Path(__file__).resolve().parent.parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))

from lib.converge import converge, converge_iter, ConvergenceResult
from lib.grip_anywhere.analyzer import RepoHealth, analyze_repo, quality_gate
from lib.wave_enforcer import load_mode_wave_map

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
MIRROR_CACHE_DIR = CLAUDE_DIR / "cache" / "mirror"

# Each convergence depth maps to a Fibonacci wave position.
# This gives each iteration a distinct epistemic character:
#   depth 0 = detect, depth 1 = understand, depth 2 = codify, depth 3+ = embody.
CONVERGENCE_WAVE_MAP = {
    0: {"wave": 1, "cube_w": "DETECTED", "activity": "Analyze repo health, scan issues"},
    1: {"wave": 2, "cube_w": "UNDERSTOOD", "activity": "Root cause analysis, prioritize"},
    2: {"wave": 3, "cube_w": "CODIFIED", "activity": "Create targeted fix PR"},
    3: {"wave": 5, "cube_w": "EMBODYING", "activity": "Run tests, verify fix"},
    4: {"wave": 8, "cube_w": "EMBODIED", "activity": "Merge, update score, close issue"},
}

# Mode recommendations by health dimension weakness
_MODE_RECOMMENDATIONS: dict[str, list[str]] = {
    "issue_health":    ["analysis", "decision", "strategy"],
    "pr_velocity":     ["review", "code", "git-ops"],
    "ci_quality":      ["architect", "testing", "automation"],
    "code_quality":    ["code", "testing", "security"],
    "community_health": ["writing", "teaching", "planning"],
}


@dataclass
class GripAnywhereState:
    """The state object passed through converge() iterations.

    Wraps a RepoHealth snapshot with run metadata.
    """
    health: RepoHealth
    repo: str
    run_id: str
    actions_taken: list = None
    dry_run: bool = True  # stealth by default — writes are the explicit opt-out
    convergence_depth: int = 0  # Current depth in the convergence loop

    def __post_init__(self):
        if self.actions_taken is None:
            self.actions_taken = []

    @property
    def wave_info(self) -> dict:
        """Current wave mapping for this convergence depth."""
        max_key = max(CONVERGENCE_WAVE_MAP)
        key = min(self.convergence_depth, max_key)
        return CONVERGENCE_WAVE_MAP[key]

    def summary(self) -> str:
        wave = self.wave_info
        dims = self.health.dimensions
        lines = [
            f"GRIP MIRROR — {self.repo}",
            f"Quality score : {self.health.quality_score}/100",
            f"Wave position : W{wave['wave']} {wave['cube_w']}",
        ]
        if dims:
            lines.append(
                f"Health dims   : issue={dims.issue_health} pr={dims.pr_velocity} "
                f"ci={dims.ci_quality} code={dims.code_quality} "
                f"community={dims.community_health} (total={dims.total_score}, {dims.priority_label})"
            )
        lines.extend([
            f"P0 issues     : {len(self.health.p0_issues)}",
            f"P1 issues     : {len(self.health.p1_issues)}",
            f"Open PRs      : {self.health.open_prs}",
            f"Has CI        : {self.health.has_ci}",
            f"Actions taken : {len(self.actions_taken)}",
            f"Dry run       : {'yes' if self.dry_run else 'no'}",
        ])
        if self.actions_taken:
            lines.append("Actions:")
            for a in self.actions_taken:
                lines.append(f"  - {a}")
        return "\n".join(lines)


class GripAnywhere:
    """GRIP MIRROR — applies the RSI convergence loop to any GitHub repo.

    Example:
        mirror = GripAnywhere("torvalds/linux", quality_threshold=70)
        result = mirror.run(max_depth=2, dry_run=True)
    """

    def __init__(
        self,
        repo: str,
        quality_threshold: int = 85,
        local_path: Optional[str] = None,
    ):
        self.repo = repo
        self.quality_threshold = quality_threshold
        self.local_path = local_path
        self._cache_file = MIRROR_CACHE_DIR / f"{repo.replace('/', '_')}.json"

    # ── Public API ────────────────────────────────────────────────────────

    def run(
        self,
        max_depth: int = 3,
        dry_run: bool = True,
        use_iterative: bool = False,
    ) -> ConvergenceResult:
        """Run the GRIP MIRROR convergence loop against the target repo.

        Args:
            max_depth     : Maximum improvement cycles (each cycle may
                            create one PR or close one issue).
            dry_run       : Analyse only — do not create PRs or issues.
                            Defaults to True (stealth by default): the
                            import door writes upstream only when the
                            caller explicitly passes dry_run=False.
            use_iterative : Use converge_iter() instead of recursive
                            converge() (safer for large max_depth).

        Returns:
            ConvergenceResult where best_state is the highest-quality
            GripAnywhereState observed across all iterations.
        """
        import uuid
        run_id = str(uuid.uuid4())[:8]
        logger.info("[mirror] Starting run %s on %s (dry_run=%s)", run_id, self.repo, dry_run)

        # Build initial state from repo analysis
        health = analyze_repo(self.repo, self.local_path)
        initial_state = GripAnywhereState(
            health=health,
            repo=self.repo,
            run_id=run_id,
            dry_run=dry_run,
        )

        # Define the convergence functions
        def criterion(state: GripAnywhereState) -> bool:
            return quality_gate(state.health, self.quality_threshold)

        def improve(state: GripAnywhereState) -> GripAnywhereState:
            return self._improve_repo(state)

        def scorer(state: GripAnywhereState) -> float:
            return float(state.health.quality_score)

        # Run convergence
        engine = converge_iter if use_iterative else converge
        result = engine(
            state=initial_state,
            criterion=criterion,
            improve=improve,
            max_depth=max_depth,
            _scorer=scorer,
            label=f"mirror-{self.repo}-{run_id}",
        )

        # Persist findings to cache for next session
        self._save_run_summary(result)

        return result

    # ── Improvement cycle ─────────────────────────────────────────────────

    def _reanalyse_state(self, state: GripAnywhereState, actions: list, depth: int) -> GripAnywhereState:
        """Re-run the analyser and emit a fresh state at depth + 1."""
        logger.info("[mirror] No actionable issues found — re-analysing")
        new_health = analyze_repo(self.repo, self.local_path)
        return GripAnywhereState(
            health=new_health,
            repo=state.repo,
            run_id=state.run_id,
            actions_taken=actions,
            dry_run=state.dry_run,
            convergence_depth=depth + 1,
        )

    def _apply_action(self, dry_run: bool, issue_num: int, issue_title: str, action_desc: str) -> str:
        """Execute or simulate the action; return the entry to append to actions_taken."""
        if dry_run:
            logger.info("[mirror:dry-run] Would act on: %s", action_desc)
            return f"[dry-run] {action_desc}"
        success = self._act_on_issue(issue_num, issue_title)
        return action_desc if success else f"[failed] {action_desc}"

    def _improve_repo(self, state: GripAnywhereState) -> GripAnywhereState:
        """One wave-aware improvement cycle.

        Each convergence depth maps to a wave position, selecting the
        appropriate activity type:
            depth 0 (W1 DETECTED)  — scan and catalog issues
            depth 1 (W2 UNDERSTOOD) — rank and prioritize by dimensions
            depth 2 (W3 CODIFIED)  — create a fix PR
            depth 3+ (W5-W8 EMBODIED) — verify, test, merge

        In dry_run mode: logs the action without executing.
        """
        health = state.health
        new_actions = list(state.actions_taken)
        depth = state.convergence_depth
        wave = state.wave_info

        logger.info(
            "[mirror] Depth %d — W%d %s: %s",
            depth, wave["wave"], wave["cube_w"], wave["activity"],
        )

        if not health.top_issue:
            return self._reanalyse_state(state, new_actions, depth)

        issue_num = health.top_issue["number"]
        issue_title = health.top_issue["title"]
        action_desc = (
            f"[W{wave['wave']} {wave['cube_w']}] "
            f"Targeting issue #{issue_num}: {issue_title[:60]}"
        )

        new_actions.append(self._apply_action(state.dry_run, issue_num, issue_title, action_desc))

        # Re-analyse after acting
        new_health = analyze_repo(self.repo, self.local_path)
        return GripAnywhereState(
            health=new_health,
            repo=state.repo,
            run_id=state.run_id,
            actions_taken=new_actions,
            dry_run=state.dry_run,
            convergence_depth=depth + 1,
        )

    def _act_on_issue(self, issue_number: int, title: str) -> bool:
        """Post a GRIP MIRROR analysis comment on the issue.

        In a full deployment, this would delegate to the sprint-loop agent.
        For now: posts a structured improvement suggestion as a comment.
        """
        body = (
            f"**GRIP MIRROR Analysis**\n\n"
            f"Issue #{issue_number} has been identified as the highest-priority "
            f"improvement for this repository.\n\n"
            f"**Suggested approach**:\n"
            f"- Analyse the root cause using `rg` pattern matching\n"
            f"- Create a focused fix PR (target: < 200 lines changed)\n"
            f"- Verify with existing test suite before merging\n\n"
            f"*This comment was generated by GRIP MIRROR — "
            f"the autonomous RSI engine.*"
        )
        try:
            result = subprocess.run(
                ["gh", "issue", "comment", str(issue_number),
                 "--repo", self.repo, "--body", body],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info("[mirror] Commented on issue #%d", issue_number)
                return True
            logger.warning("[mirror] Failed to comment: %s", result.stderr[:100])
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("[mirror] gh CLI error: %s", e)
            return False

    # ── Mode recommendation ──────────────────────────────────────────────

    def mode_recommendation(self, health: Optional[RepoHealth] = None) -> dict[str, list[str]]:
        """Suggest GRIP modes to activate based on repo health dimensions.

        Identifies the weakest health dimensions and maps them to modes
        from the wave_enforcer classification. Only recommends modes that
        exist in the loaded mode-wave map.

        Args:
            health: RepoHealth snapshot. If None, runs a fresh analysis.

        Returns:
            Dict mapping dimension names to lists of recommended mode names.
            Only includes dimensions scoring below 15 (out of 20).
        """
        if health is None:
            health = analyze_repo(self.repo, self.local_path)

        dims = health.dimensions
        if dims is None:
            from lib.grip_anywhere.analyzer import compute_health_dimensions
            dims = compute_health_dimensions(health)

        known_modes = set(load_mode_wave_map().keys())
        recommendations: dict[str, list[str]] = {}
        weakness_threshold = 15

        dim_scores = {
            "issue_health": dims.issue_health,
            "pr_velocity": dims.pr_velocity,
            "ci_quality": dims.ci_quality,
            "code_quality": dims.code_quality,
            "community_health": dims.community_health,
        }

        for dim_name, score in dim_scores.items():
            if score < weakness_threshold:
                candidates = _MODE_RECOMMENDATIONS.get(dim_name, [])
                valid = [m for m in candidates if m in known_modes]
                if valid:
                    recommendations[dim_name] = valid

        return recommendations

    # ── Cache ─────────────────────────────────────────────────────────────

    def _save_run_summary(self, result: ConvergenceResult):
        """Persist run summary for cross-session continuity."""
        MIRROR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            best: GripAnywhereState = result.best_state
            dims = best.health.dimensions
            summary = {
                "repo": self.repo,
                "quality_score": best.health.quality_score,
                "dimensions": dims.as_dict() if dims else None,
                "wave_info": best.wave_info,
                "satisfied": result.satisfied,
                "iterations": result.iterations,
                "elapsed_sec": result.elapsed_sec,
                "actions_taken": best.actions_taken,
                "p0_issues": best.health.p0_issues,
                "p1_issues": best.health.p1_issues,
            }
            self._cache_file.write_text(json.dumps(summary, indent=2))
            logger.info("[mirror] Run summary saved to %s", self._cache_file)
        except (OSError, AttributeError) as e:
            logger.warning("[mirror] Failed to save run summary: %s", e)

    def load_prior_context(self) -> Optional[dict]:
        """Load findings from the previous Mirror run on this repo."""
        if not self._cache_file.exists():
            return None
        try:
            return json.loads(self._cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None


def render_rsi_report(
    state: GripAnywhereState,
    repo: str,
    mode_recommendations: dict[str, list[str]],
    gate_met: bool | None = None,
) -> str:
    """Render a deterministic markdown RSI report for an analysed repo.

    Stdlib-only and side-effect-free: the same inputs always produce the
    same report structure (the UTC timestamp is the only wall-clock
    element). No LLM and no upstream mutation — this is an analysis-only
    rendering of a ``RepoHealth`` snapshot.

    ``gate_met`` (optional) prints an explicit Gate: met/not-met footer so
    a report consumer can read the quality-gate verdict without parsing
    the exit code.
    """
    health = state.health
    dims = health.dimensions
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ONE score source: the dimensions table is canonical when present;
    # the backward-compat quality_score field is only the fallback, so a
    # single report can never print two disagreeing totals.
    header_score = dims.total_score if dims is not None else health.quality_score
    lines: list[str] = [
        f"# GRIP-Anywhere RSI Report — {repo}",
        "",
        f"**Generated (UTC)**: {timestamp}",
        "",
        f"**Quality score**: {header_score}/100",
    ]
    if gate_met is not None:
        lines.append(f"**Gate**: {'met' if gate_met else 'NOT met'}")
    lines.extend([
        "",
        "## Health dimensions",
        "",
        "| Dimension | Score |",
        "|-----------|-------|",
    ])

    if dims is not None:
        rows = [
            ("Issues", dims.issue_health),
            ("PR velocity", dims.pr_velocity),
            ("CI quality", dims.ci_quality),
            ("Code quality", dims.code_quality),
            ("Community health", dims.community_health),
        ]
        for name, score in rows:
            lines.append(f"| {name} | {score}/20 |")
        lines.append(
            f"| **Total** | **{dims.total_score}/100** ({dims.priority_label}) |"
        )
    else:
        lines.append("| (unavailable) | — |")

    lines.extend([
        "",
        "## Issue severity",
        "",
        f"- P0 issues: {len(health.p0_issues)}",
        f"- P1 issues: {len(health.p1_issues)}",
        "",
        "## Actions taken",
        "",
    ])

    # Only real upstream mutations count as "actions taken". Dry-run and
    # failed entries are placeholders, not actions, so a stealth/report
    # run (analysis-only) always reports zero real actions.
    real_actions = [
        a for a in state.actions_taken
        if not str(a).startswith("[dry-run]") and not str(a).startswith("[failed]")
    ]
    lines.append(f"Actions taken: {len(real_actions)}")
    for action in real_actions:
        lines.append(f"- {action}")

    lines.extend([
        "",
        "## Mode recommendations",
        "",
    ])
    if mode_recommendations:
        for dim_name, modes in mode_recommendations.items():
            lines.append(f"- {dim_name}: {', '.join(modes)}")
    else:
        lines.append("- None (all health dimensions within a healthy range)")

    lines.extend([
        "",
        "## How this was produced",
        "",
        "Deterministic modulo the generated-at UTC timestamp, stdlib-only "
        "render of a `RepoHealth` snapshot by "
        "`lib.grip_anywhere.runner.render_rsi_report`. The analyser reads "
        "public GitHub metadata via the `gh` CLI and, when `--local-path` "
        "is given, local `rg` file statistics. No LLM and no upstream "
        "mutation: this report is analysis-only.",
        "",
    ])

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────

def _validate_stealth_args(parser, args):
    """Block flag combinations that would compromise stealth defaults."""
    if args.non_stealth and not args.owner_consent:
        parser.error(
            "--non-stealth requires --owner-consent <TOKEN>. "
            "Stealth mode protects upstream maintainers from unsolicited bot activity."
        )
    if args.mirror_clone:
        parser.error(
            "--mirror-clone is reserved for a follow-up release. "
            "For now, run with --dry-run or --report to analyse without upstream contact."
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="grip-anywhere — RSI convergence loop for any GitHub repository (stealth-default)"
    )
    parser.add_argument("repo", help="GitHub repo in owner/name format")
    parser.add_argument("--max-depth", type=int, default=3,
                        help="Maximum improvement cycles (default: 3)")
    parser.add_argument("--threshold", type=int, default=85,
                        help="Quality score target 0-100 (default: 85)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyse only — never post comments or create PRs "
                             "(default: analysis-only; writes require BOTH "
                             "--non-stealth and --owner-consent)")
    parser.add_argument("--report", action="store_true",
                        help="Emit a full markdown RSI report to stdout (analysis only, never mutates upstream)")
    parser.add_argument("--mirror-clone", action="store_true",
                        help="Clone target into a private sister repo on V>>'s account and run RSI there (no upstream contact)")
    parser.add_argument("--non-stealth", action="store_true",
                        help="Enable upstream writes (post comments/PRs) — REQUIRES "
                             "--owner-consent <TOKEN>; without this flag the run is analysis-only")
    parser.add_argument("--owner-consent", metavar="TOKEN",
                        help="Maintainer-consent attestation (presence-checked; "
                             "enables writes only together with --non-stealth)")
    parser.add_argument("--local-path", help="Local clone path for rg analysis")
    parser.add_argument("--fail-on-unconverged", action="store_true",
                        help="Exit 1 when the quality gate was not met "
                             "(both default and --report modes; without it, "
                             "exit 0 means the run completed)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    explicit_dry_run = args.dry_run

    _validate_stealth_args(parser, args)

    # Stealth-by-default: a bare `python3 -m lib.grip_anywhere owner/repo`
    # is analysis-only. Upstream writes require BOTH --non-stealth and
    # --owner-consent <TOKEN>; every other invocation stays a dry run.
    args.dry_run = args.dry_run or not args.non_stealth

    # --report is always analysis-only, even alongside --non-stealth.
    if args.report:
        args.dry_run = True

    # Never let explicit write intent downgrade silently: a consent token
    # without --non-stealth, or --report alongside --non-stealth, means the
    # run is analysis-only — say so on stderr instead of a triumphant exit 0.
    if args.owner_consent and not args.non_stealth:
        print("warning: --owner-consent ignored without --non-stealth — "
              "this run is analysis-only (no upstream writes)",
              file=sys.stderr)
    if args.report and args.non_stealth:
        print("warning: --report is analysis-only — no upstream writes "
              "will be made", file=sys.stderr)
    if explicit_dry_run and args.non_stealth and args.owner_consent:
        print("warning: --dry-run given — no upstream writes will be made "
              "despite --non-stealth and --owner-consent", file=sys.stderr)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    mirror = GripAnywhere(args.repo, quality_threshold=args.threshold,
                    local_path=args.local_path)

    prior = mirror.load_prior_context()
    if prior:
        logger.info("[mirror] Prior run found: score=%d, actions=%d",
                    prior.get("quality_score", 0), len(prior.get("actions_taken", [])))

    try:
        result = mirror.run(max_depth=args.max_depth, dry_run=args.dry_run)
        best: GripAnywhereState = result.best_state

        if args.report:
            # The mode-recommendation section is OPTIONAL: wave-map file I/O
            # can fail, but a missing recommendation section must never kill
            # the successfully computed report. Degrade with a single stderr
            # warning (no logger duplicate — the CLI contract is one line).
            try:
                recommendations = mirror.mode_recommendation(best.health)
            except Exception as rec_exc:
                print("warning: mode recommendations unavailable "
                      f"({rec_exc}) — continuing with an empty section",
                      file=sys.stderr)
                recommendations = {}
            print(render_rsi_report(best, args.repo, recommendations,
                                    gate_met=result.satisfied))
            # Report mode is analysis-only: rendering the report IS the
            # deliverable. Exit 0 once rendered, unless strict CI gating
            # was requested and the quality gate was not met.
            if args.fail_on_unconverged and not result.satisfied:
                return 1
            return 0

        print(best.summary())
        print(f"\nConverged: {result.satisfied} | Depth: {result.depth_reached} | "
              f"Time: {result.elapsed_sec:.1f}s")
        # Backward-compatible exit contract: 0 means the run COMPLETED.
        # Strict gating is opt-in via --fail-on-unconverged.
        if args.fail_on_unconverged and not result.satisfied:
            return 1
        return 0
    except Exception as exc:
        # ONE clean stderr line, no logger duplicate, never a traceback.
        print(f"error: grip-anywhere run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
