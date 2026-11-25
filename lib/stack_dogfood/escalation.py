"""Overnight stack-dogfood escalation: ideate a fix, then open a DRAFT PR.

This is the second, sensitive phase of the overnight loop. The check phase
(``loop.run_once``) only reads state and writes reports; this phase can prepare
a fix and open a pull request. Four safety properties are structural, not
conventions:

* **Public-products floor.** A product reaches ``draft-pr`` only through
  ``loop.wave_mode``, which can never return ``draft-pr`` for HAL or GRIP
  (they hold the merge machinery). ``gate_wave_mode`` re-checks it here, so the
  floor is enforced at both the plan step and the push step.
* **Agent holds no credentials.** The fix-preparation agent runs under
  ``agent_env``: every GitHub token is stripped, ``GH_CONFIG_DIR`` points at an
  empty directory, and ``GIT_TERMINAL_PROMPT=0`` — so even though the agent has
  Bash, a ``git push`` or ``gh`` call cannot authenticate. Only the runner,
  with the ambient environment, can push.
* **Runner gates before it pushes.** ``preflight`` runs, in order, the
  wave-mode floor, a configured escalation repo, the public-profile leak sweep
  (``redaction_shield verify --strict --profile public``) over the changed
  files, and then the push gate. For a code repo that is the product's own
  tests; for a docs-only repo (``gate_profile: docs``, e.g. the HAPPI spec,
  which has no test suite) it is instead a check that every changed file is
  documentation -- the leak sweep still runs first, unchanged. The first
  failure stops it; a missing repo, a missing test command, or a non-doc change
  in a docs repo fails CLOSED.
* **Draft only, never merge.** ``draft_pr_argv`` always passes ``--draft`` and
  there is no merge call anywhere in this module. The human merges.

Dry-run is the default. ``run_wave(..., live=False)`` writes the ideation brief
and reports what it WOULD do; nothing is spawned, swept, or pushed. The live
path is a deliberate operator step (see ``cli`` / the launchd template).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent  # lib/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from claude_signin import signin_folder  # noqa: E402  -- GRIP#8918/#9388

from .loop import LoopConfig, _iso, _log, wave_mode
from .report import findings_for, read_report

# The fix-preparation agent's capability allowlist. Bash is needed to run the
# product's own ideation/tests, so this lock is defence-in-depth, NOT the push
# guarantee — ``agent_env`` (credential stripping) is what actually stops a
# push. Allowlist, never denylist: a renamed/unknown tool degrades to fewer
# capabilities (untrusted-content-capability-lock.md mandate #3).
AGENT_TOOLS = "Read,Grep,Glob,Bash,Write,Edit"
WAVES_REL = "state/stack-dogfood/waves"
GH_TOKEN_VARS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)
_DRY_RUN_SUFFIX = (
    "\n\n[DRY-RUN] Report ONLY the single highest-value safe fix you WOULD "
    "prepare. Change NOTHING: no edits, no commits, no pushes, no PRs."
)


@dataclass(frozen=True)
class WaveItem:
    """One product's place in a wave: its mode and the findings to act on."""

    product: str
    mode: str  # "draft-pr" | "brief"
    findings: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    """One preflight gate outcome."""

    gate: str
    ok: bool
    reason: str

    def as_dict(self) -> dict:
        return {"gate": self.gate, "ok": self.ok, "reason": self.reason}


@dataclass(frozen=True)
class Runners:
    """Injected side-effect boundaries, so the whole module is testable without
    spawning an agent, sweeping a repo, running tests, or touching GitHub.

    Each returns ``(exit_code, detail)`` with ``0`` meaning success, except
    ``changed_paths`` which returns the changed files in the prepared workspace.
    The defaults are wired in ``default_runners`` and only run in ``live`` mode.
    """

    run_agent: object  # (argv, env, cwd) -> (int, str)
    leak_sweep: object  # (paths) -> (int, str)
    run_tests: object  # (cmd, cwd) -> (int, str)
    open_pr: object  # (argv, cwd) -> (int, str)
    changed_paths: object  # (cwd) -> list[str]
    prepare_branch: object  # (branch, base, cwd) -> (int, str)
    push_branch: object  # (branch, cwd) -> (int, str)


# ---- configuration -------------------------------------------------------

def load_escalation(path) -> dict:
    """The ``escalation`` block of a stack-dogfood config: a per-product map
    ``{product: {repo, test_cmd, workdir}}``. Absent/malformed → ``{}`` (every
    gate then fails closed). The block lives in the same file as ``LoopConfig``;
    ``LoopConfig.from_dict`` ignores it, so the two never collide."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}
    block = data.get("escalation", {})
    return block if isinstance(block, dict) else {}


def _entry(escalation: dict, product: str) -> dict:
    entry = escalation.get(product)
    return entry if isinstance(entry, dict) else {}


def repo_for(escalation: dict, product: str) -> str | None:
    return _entry(escalation, product).get("repo") or None


def test_cmd_for(escalation: dict, product: str) -> str | None:
    return _entry(escalation, product).get("test_cmd") or None


def workdir_for(escalation: dict, product: str) -> str | None:
    raw = _entry(escalation, product).get("workdir")
    return str(Path(raw).expanduser()) if raw else None


# ---- planning ------------------------------------------------------------

def plan_items(config: LoopConfig, claude_dir=None) -> list[WaveItem]:
    """One ``WaveItem`` per product that has a persisted report WITH findings.
    A product with no report, or a clean report, is skipped (nothing to fix).
    The mode comes from ``wave_mode``, so HAL and GRIP are always ``brief``."""
    claude_dir = claude_dir or get_claude_dir()
    items: list[WaveItem] = []
    for product in config.products:
        report = read_report(claude_dir, product)
        if report is None:
            continue
        findings = findings_for(report)
        if not findings:
            continue
        items.append(
            WaveItem(product, wave_mode(config, product),
                     tuple(f.text for f in findings))
        )
    return items


def ideation_brief(item: WaveItem) -> str:
    """The operator-authored brief handed to the fix-preparation agent. It is
    deterministic text (no model call here — the agent does the ideation), and
    it forbids the agent from pushing or opening a PR; the runner does that,
    only after the gates pass."""
    lines = [
        f"# Stack-dogfood fix wave: {item.product} ({item.mode})",
        "",
        "## Findings to address",
    ]
    lines += [f"- {text}" for text in item.findings]
    lines += [
        "",
        "## Your task",
        "1. Ideate ranked fixes for the findings above (use /ultraideate).",
        "2. Prepare ONLY the single highest-value, lowest-risk fix, as a commit",
        "   on the CURRENT branch (already created for this wave -- do not create",
        "   or switch branches). Keep it small and reviewable.",
        "3. Do NOT push. Do NOT open a pull request. Do NOT merge anything.",
        "   You have no GitHub credentials; the runner opens a DRAFT PR for",
        "   review only after a leak sweep and the product's own tests pass.",
        "4. If no safe fix is clear, change nothing and say so.",
    ]
    return "\n".join(lines) + "\n"


def brief_path(claude_dir, product: str) -> Path:
    return Path(claude_dir) / WAVES_REL / f"{product}.md"


def write_brief(item: WaveItem, claude_dir) -> Path:
    path = brief_path(claude_dir, item.product)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(ideation_brief(item))
    tmp.replace(path)
    return path


# ---- agent spawn (credential-stripped) -----------------------------------

def agent_env(base: dict | None = None, *, gh_config_dir, claude_dir=None) -> dict:
    """The fix-preparation agent's environment: every GitHub token removed,
    ``GH_CONFIG_DIR`` repointed at an empty directory, terminal prompts off and
    the capability lock engaged. A ``git push`` or ``gh`` call then cannot
    authenticate, so the agent cannot push even with Bash.

    Also resolves the operator's Claude sign-in the same way
    ``bin/grip-security-heal`` does (``lib/claude_signin.py``, GRIP#8918/#9388):
    under launchd this agent is a headless ``claude -p`` with no shell profile,
    so without ``CLAUDE_CONFIG_DIR`` it can find another, older sign-in or none.
    A caller that already chose a folder or credential, or an inherited
    environment with a working sign-in, is left exactly as it is; otherwise
    the folder is set only when the GRIP folder itself has a working sign-in.
    """
    env = dict(os.environ if base is None else base)
    for var in GH_TOKEN_VARS:
        env.pop(var, None)
    env["GH_CONFIG_DIR"] = str(gh_config_dir)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GRIP_OVERNIGHT_CAPABILITY_LOCK"] = "on"
    folder, _source = signin_folder(env, claude_dir or get_claude_dir())
    if folder:
        env["CLAUDE_CONFIG_DIR"] = folder
    return env


def agent_argv(prompt_path, *, dry_run: bool, claude_bin: str | None = None) -> list[str]:
    """``claude -p`` for the fix-preparation agent, with an always-on ``--tools``
    lock (not env-gated, unlike the generic overnight wave) and the prompt read
    from ``prompt_path``. ``--dangerously-skip-permissions`` is orthogonal to the
    capability lock: a headless agent has no TTY to answer a prompt, and the
    lock plus ``agent_env`` bound what it can do."""
    prompt = Path(prompt_path).read_text()
    if dry_run:
        prompt += _DRY_RUN_SUFFIX
    exe = claude_bin or os.environ.get("GRIP_OVERNIGHT_CLAUDE_BIN", "claude")
    return [exe, "-p", prompt, "--dangerously-skip-permissions", f"--tools={AGENT_TOOLS}"]


# ---- runner preflight gates ----------------------------------------------

def gate_wave_mode(config: LoopConfig, product: str) -> GateResult:
    mode = wave_mode(config, product)
    return GateResult("wave-mode", mode == "draft-pr", f"mode={mode}")


def gate_repo(escalation: dict, product: str) -> GateResult:
    repo = repo_for(escalation, product)
    return GateResult("repo", repo is not None, repo or "no escalation repo configured")


def gate_leak_sweep(paths, *, runner) -> GateResult:
    if not paths:
        return GateResult("leak-sweep", False, "no changed files to sweep")
    code, detail = runner(paths)
    return GateResult("leak-sweep", code == 0, detail)


def gate_tests(escalation: dict, product: str, workdir, *, runner) -> GateResult:
    cmd = test_cmd_for(escalation, product)
    if not cmd:
        return GateResult("tests", False, "no test_cmd configured (fail-closed)")
    code, detail = runner(cmd, workdir)
    return GateResult("tests", code == 0, detail)


DOC_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc")
DOC_NAMES = ("LICENSE", "LICENCE", "NOTICE", "AUTHORS", "COPYING")


def profile_for(escalation: dict, product: str) -> str:
    """The gate profile for a product: "code" (run the product's tests) or
    "docs" (a documentation-only repo, e.g. the HAPPI spec, which has no test
    suite). An unknown value falls back to "code", the stricter profile."""
    value = _entry(escalation, product).get("gate_profile", "code")
    return value if value in ("code", "docs") else "code"


def _is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES) or Path(path).name in DOC_NAMES


def gate_docs(paths) -> GateResult:
    """The test-gate stand-in for a docs-only repo. There is no code to test, so
    the property to enforce is that the change stays documentation: every changed
    file must be a documentation file. This is a NEW safety property (the agent
    cannot slip code into a docs repo), not a bypass -- the leak sweep still runs
    ahead of it, unchanged."""
    if not paths:
        return GateResult("docs", False, "no changed files")
    non_docs = [p for p in paths if not _is_doc(p)]
    if non_docs:
        return GateResult("docs", False, f"non-documentation files changed: {non_docs[:5]}")
    return GateResult("docs", True, f"{len(paths)} documentation file(s) changed")


def preflight(config: LoopConfig, escalation: dict, product: str, paths, workdir,
              *, leak_runner, test_runner) -> list[GateResult]:
    """Run the gates in order, stopping at the first failure. The order is
    load-bearing: the floor and the repo are cheap and decide eligibility; the
    leak sweep ALWAYS runs; then the push is gated by the product's tests, or,
    for a docs-only repo (``gate_profile: docs``), by the docs-only check."""
    # The changed paths are relative to the product's clone, and the sweep runs
    # from elsewhere, so it is handed absolute paths under the workdir.
    swept = [str(Path(workdir) / p) for p in paths]
    checks = [
        lambda: gate_wave_mode(config, product),
        lambda: gate_repo(escalation, product),
        lambda: gate_leak_sweep(swept, runner=leak_runner),
    ]
    if profile_for(escalation, product) == "docs":
        checks.append(lambda: gate_docs(paths))
    else:
        checks.append(lambda: gate_tests(escalation, product, workdir, runner=test_runner))
    results: list[GateResult] = []
    for check in checks:
        result = check()
        results.append(result)
        if not result.ok:
            break
    return results


# ---- draft-PR argv (pure) ------------------------------------------------

def draft_pr_argv(repo: str, branch: str, title: str, body_file) -> list[str]:
    """``gh pr create`` for a DRAFT PR. ``--body-file`` takes a real path, never
    ``-`` (rtk feeds ``gh pr create`` an empty stdin — rules/bash-safety.md).
    Always ``--draft``; this module has no merge path."""
    return [
        "gh", "pr", "create", "-R", repo, "--draft",
        "--head", branch, "--title", title, "--body-file", str(body_file),
    ]


# ---- execution -----------------------------------------------------------

def _branch_name(product: str, now_epoch: float) -> str:
    stamp = datetime.fromtimestamp(now_epoch, timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"dogfood/{product}-{stamp}"


def _dry_summary(item: WaveItem, escalation: dict, brief: Path) -> dict:
    return {
        "product": item.product,
        "mode": item.mode,
        "action": "dry-run",
        "brief": str(brief),
        "would_open_pr": item.mode == "draft-pr",
        "repo": repo_for(escalation, item.product),
        "findings": list(item.findings),
    }


def base_for(escalation: dict, product: str) -> str:
    """The product repo's base branch for the draft PR (default ``main``)."""
    return _entry(escalation, product).get("base") or "main"


def _run_live_draft_pr(item: WaveItem, config: LoopConfig, escalation: dict,
                       runners: "Runners", *, claude_dir, gh_config_dir,
                       now_epoch: float) -> dict:
    """Live draft-PR path for ONE public product. The branch lifecycle is the
    load-bearing part (#9252): the runner creates an isolated branch off the
    product repo's base BEFORE the agent runs, the credential-stripped agent
    commits its fix onto that branch, and after the gates pass the RUNNER (which
    holds the credentials) PUSHES the branch — so ``gh pr create --head`` has a
    branch to point at. The agent can never push; the runner always pushes
    before it opens the PR, and only if every gate passed."""
    def blocked(stage, reason, **extra):
        return {"product": item.product, "mode": item.mode, "action": "blocked",
                "blocked_on": stage, "reason": reason, **extra}

    workdir = workdir_for(escalation, item.product)
    if workdir is None:
        return blocked("workdir", "no workdir configured")
    branch = _branch_name(item.product, now_epoch)
    base = base_for(escalation, item.product)
    prep_code, prep_detail = runners.prepare_branch(branch, base, workdir)
    if prep_code != 0:
        return blocked("prepare-branch", prep_detail, branch=branch)
    env = agent_env(gh_config_dir=gh_config_dir, claude_dir=claude_dir)
    argv = agent_argv(brief_path(claude_dir, item.product), dry_run=False)
    code, detail = runners.run_agent(argv, env, workdir)
    if code != 0:
        return blocked("agent", detail, branch=branch)
    paths = runners.changed_paths(workdir)
    gates = preflight(config, escalation, item.product, paths, workdir,
                      leak_runner=runners.leak_sweep, test_runner=runners.run_tests)
    if not all(g.ok for g in gates):
        failed = next(g for g in gates if not g.ok)
        return blocked(failed.gate, failed.reason, gates=[g.as_dict() for g in gates])
    push_code, push_detail = runners.push_branch(branch, workdir)
    if push_code != 0:
        return blocked("push-branch", push_detail, branch=branch,
                       gates=[g.as_dict() for g in gates])
    repo = repo_for(escalation, item.product)
    title = f"dogfood({item.product}): overnight fix for {len(item.findings)} finding(s)"
    body = brief_path(claude_dir, item.product)
    pr_code, pr_detail = runners.open_pr(draft_pr_argv(repo, branch, title, body), workdir)
    out = {"product": item.product, "mode": item.mode,
           "action": "opened-draft-pr" if pr_code == 0 else "blocked",
           "repo": repo, "branch": branch, "detail": pr_detail,
           "gates": [g.as_dict() for g in gates]}
    if pr_code != 0:
        out["blocked_on"] = "open-pr"
    return out


def run_item(item: WaveItem, config: LoopConfig, escalation: dict, runners: "Runners",
             *, claude_dir, gh_config_dir, live: bool, now_epoch: float) -> dict:
    """Process one wave item. ``brief`` products only ever get a written brief;
    a ``draft-pr`` product gets a dry-run summary unless ``live``."""
    write_brief(item, claude_dir)
    if item.mode != "draft-pr":
        return {"product": item.product, "mode": "brief", "action": "brief-written",
                "brief": str(brief_path(claude_dir, item.product))}
    if not live:
        return _dry_summary(item, escalation, brief_path(claude_dir, item.product))
    return _run_live_draft_pr(item, config, escalation, runners,
                              claude_dir=claude_dir, gh_config_dir=gh_config_dir,
                              now_epoch=now_epoch)


def _live_gate_skip_reason(config: LoopConfig, now_epoch: float, *,
                           claude_dir, ignore_schedule: bool) -> str | None:
    """Why a LIVE wave must not fire now, or ``None`` to proceed.

    Order is load-bearing. The operator brakes (weekly-spend throttle, global
    outbound-stop, STOP marker, a disabled or past-deadline config) win first,
    via ``should_run`` -- a halt always reports before the clock. Then the
    scheduled-hour window: a late launchd wake outside the configured hour is
    declined (GRIP#9275), unless ``ignore_schedule`` is set for a deliberate
    manual ``--live`` run (``full-power-stop-override``: the operator keeps an
    explicit override rather than being auto-limited by the clock)."""
    from .loop import local_civil_time, should_run, within_schedule_window
    gate = should_run(config, now_epoch=now_epoch, claude_dir=claude_dir)
    if not gate.fire:
        return gate.reason
    if not ignore_schedule and not within_schedule_window(config, now_epoch):
        local_hhmm = local_civil_time(now_epoch).strftime("%H:%M")
        return (f"off-schedule wake at {local_hhmm} local (scheduled hour "
                f"{config.schedule_hour:02d}); live escalation skipped -- the "
                f"check phase is the fallback, pass --ignore-schedule to override")
    return None


def run_wave(config: LoopConfig, escalation: dict, *, now_epoch: float,
             claude_dir=None, gh_config_dir=None, live: bool = False,
             ignore_schedule: bool = False,
             runners: "Runners | None" = None) -> dict:
    """Process every planned item, capped at ``config.max_prs_per_run`` opened
    draft PRs. Dry-run (the default) opens nothing. In LIVE mode the operator
    brakes are honoured first (the weekly-spend throttle, the global
    outbound-stop, the STOP marker, a disabled or past-deadline config), then
    the scheduled-hour window: if the gate is shut, nothing is planned or pushed.

    ``ignore_schedule`` (default ``False``) is the operator override: it relaxes
    ONLY the scheduled-hour window, never a brake, so a deliberate manual
    ``--live`` run proceeds off-hour while the launchd job (which never passes
    it) still falls back to check-only on a late wake. One JSON line is appended
    to the loop log. There is no merge path here, by construction."""
    claude_dir = claude_dir or get_claude_dir()
    if live:
        skip_reason = _live_gate_skip_reason(
            config, now_epoch, claude_dir=claude_dir, ignore_schedule=ignore_schedule)
        if skip_reason is not None:
            summary = {"at": _iso(now_epoch), "phase": "escalate", "live": True,
                       "fired": False, "reason": skip_reason, "opened": 0, "items": []}
            _log(claude_dir, summary)
            return summary
    runners = runners or default_runners()
    items = plan_items(config, claude_dir)
    results: list[dict] = []
    opened = 0
    for item in items:
        if item.mode == "draft-pr" and live and opened >= config.max_prs_per_run:
            results.append({"product": item.product, "mode": item.mode,
                            "action": "skipped", "reason": "max_prs_per_run reached"})
            continue
        result = run_item(item, config, escalation, runners, claude_dir=claude_dir,
                          gh_config_dir=gh_config_dir, live=live, now_epoch=now_epoch)
        if result.get("action") == "opened-draft-pr":
            opened += 1
        results.append(result)
    summary = {"at": _iso(now_epoch), "phase": "escalate", "live": live,
               "opened": opened, "items": results}
    _log(claude_dir, summary)
    return summary


# ---- default (live) runners ----------------------------------------------

def _run(argv, *, cwd=None, env=None, timeout=1800):
    import subprocess
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout,
                              capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    detail = (proc.stdout.strip() or proc.stderr.strip() or "no output")[:200]
    return proc.returncode, detail


def _default_leak_sweep(paths) -> tuple[int, str]:
    """Run the public-profile leak sweep over each changed file via grip-py, one
    file per run, because ``redaction_shield verify`` takes a single target.
    Given two, it stopped with a usage error, so every night run whose fix
    touched more than one file was blocked at this gate without a sweep (the
    grasp run at 00:15 UTC on 2026-11-25). A path that is not a regular file
    has no text to sweep: a file the branch deleted, a folder such as a
    submodule, or a broken link. The count of such paths is reported (AGORA on
    GRIP#9796). If none of the paths is a file, the sweep fails closed: the
    paths may not have been resolved, and that cannot be told apart from a
    change that only deletes files, so a deletion-only change is left to a
    human."""
    grip_py = str(_LIB.parent / "bin" / "grip-py")
    paths = list(paths)
    present = [p for p in paths if Path(p).is_file()]
    skipped = len(paths) - len(present)
    if not present:
        return 1, (f"none of the {len(paths)} changed path(s) is a file to sweep "
                   "(unresolved paths, or a change that only deletes files "
                   "or touches only folders and links)")
    for path in present:
        code, detail = _run([grip_py, "-m", "lib.redaction_shield", "verify", "--strict",
                             "--profile", "public", path])
        if code != 0:
            # The shield's own words come first in length, so a long path cannot
            # crowd out the finding (AGORA on GRIP#9792).
            return code, f"{Path(path).name}: {detail[:160]} ({path})"
    return 0, f"{len(present)} file(s) swept clean, {skipped} path(s) not swept (deleted, or not a file)"


def _default_changed_paths(cwd, base: str = "origin/main") -> list[str]:
    """Every file the prepared branch changed against the product repo's base,
    not just the last commit -- so a multi-commit fix is swept and docs-checked
    in full. This matters because the docs-profile check is the only push gate
    for a docs repo; a last-commit-only diff would let an earlier commit's file
    past it. The three-dot form is the PR's own file set (merge-base diff);
    ``base`` is assumed current in the clone."""
    code, detail = _run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=cwd)
    if code != 0:
        return []
    return [line for line in detail.splitlines() if line.strip()]


def _default_prepare_branch(branch, base, cwd) -> tuple[int, str]:
    """Create an isolated branch off the product repo's FRESH base, checked out
    so the agent commits onto it and the clone's base stays clean. Fetch first
    so the base is current."""
    code, detail = _run(["git", "fetch", "origin", base], cwd=cwd)
    if code != 0:
        return code, detail
    return _run(["git", "checkout", "-B", branch, f"origin/{base}"], cwd=cwd)


def default_runners() -> "Runners":
    """The live side-effect boundaries. Only reached when ``run_wave(live=True)``.
    The agent runs under its stripped env; the runner's own calls (prepare the
    branch, leak sweep, tests, push, gh) use the ambient environment, which is
    where the credentials are -- so only the runner can push.
    """
    return Runners(
        run_agent=lambda argv, env, cwd: _run(argv, cwd=cwd, env=env),
        leak_sweep=_default_leak_sweep,
        run_tests=lambda cmd, cwd: _run(cmd, cwd=cwd) if isinstance(cmd, list)
        else _run(["bash", "-lc", cmd], cwd=cwd),
        open_pr=lambda argv, cwd: _run(argv, cwd=cwd),
        changed_paths=_default_changed_paths,
        prepare_branch=_default_prepare_branch,
        push_branch=lambda branch, cwd: _run(["git", "push", "origin", branch], cwd=cwd),
    )
