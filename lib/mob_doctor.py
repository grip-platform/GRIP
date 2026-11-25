"""mob_doctor -- read-only checks for the AI Craftspeople Guild mob-programming
"before you start" prerequisites.

Source: https://aicraftspeopleguild.github.io/mob-programming-onboarding.html#before

Every check here is READ-ONLY: it inspects versions, configuration and PATH,
and never installs, writes, pushes, or otherwise mutates the machine or a
repo. Two checks need a repository that is not yet cloned anywhere on this
machine (the push round trip, and anything that reads inside the repo); those
return NEEDS_INPUT rather than FAIL when the repo is unknown -- that is not a
failure, it is a precondition the caller can supply via --repo-path /
--repo-url once the Guild's sample repo is known.

Two seams exist for tests to fake, both module-level so a test can
monkeypatch them without touching the real machine:
    run_command(args, cwd=None) -> subprocess.CompletedProcess
    which(name) -> str | None

Usage:
    python3 lib/mob_doctor.py [--repo-path DIR] [--repo-url URL] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PASS = "pass"
FAIL = "fail"
NEEDS_INPUT = "needs-input"

_NO_REPO_DETAIL = "needs the repository path (not yet known)"
_NO_REPO_FIX = "re-run with --repo-path <dir> once the repo is cloned"

# homebrew/xcode-select are macOS-only tools; on Linux or Windows a FAIL here
# would misreport a correctly configured machine as broken (Codex, GRIP#9266
# round 2 thread 1 -- the Guild's own page documents separate Linux/macOS/
# Windows setup paths). Deeper per-platform equivalents (apt/dnf/pacman on
# Linux, nvm-windows parity beyond the one case handled below) are a larger
# follow-up, tracked via the auto-filed issue, not built out here.
_MACOS_ONLY_NOT_APPLICABLE = (
    "not applicable on this platform -- follow the Guild's platform-specific "
    "setup instructions by hand")


@dataclass
class CheckResult:
    """Outcome of one check: PASS / FAIL / NEEDS_INPUT, with a fix if not PASS."""

    status: str
    detail: str
    fix: str = ""


@dataclass
class DoctorContext:
    """Everything a check needs, so no check reaches the real machine directly."""

    repo_path: Optional[Path] = None
    repo_url: Optional[str] = None
    home: Path = field(default_factory=Path.home)
    platform_name: str = field(default_factory=platform.system)


def run_command(args: list[str], cwd: Optional[Path] = None,
                 timeout: int = 30) -> subprocess.CompletedProcess:
    """The one subprocess seam every check goes through. Tests fake this."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)


def which(name: str) -> Optional[str]:
    """The one PATH-lookup seam every check goes through. Tests fake this."""
    return shutil.which(name)


def _probe(args: list[str], cwd: Optional[Path] = None) -> tuple[bool, str]:
    """Run args read-only; return (ok, combined stdout+stderr, stripped).

    Catches OSError (binary not found) AND subprocess.TimeoutExpired (a hung
    probe, e.g. an unresponsive network call) -- TimeoutExpired is NOT an
    OSError subclass, so a bare `except OSError` lets a single hung check take
    down the whole doctor command with a traceback instead of the complete
    report every other check still promises (Codex, GRIP#9266 thread 7).
    """
    try:
        result = run_command(args, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output


def _parse_version(text: str) -> Optional[tuple[int, ...]]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# String literal, OR a line comment, OR a block comment -- tried in that
# order, so a "//" or "/*" INSIDE a string is consumed as part of the string
# token (matched whole by the first alternative) and never reaches the
# comment alternatives. DOTALL so block comments may span lines.
_JSONC_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/', re.DOTALL)


def _strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments from JSONC text, respecting string literals.

    VS Code's settings.json is documented as JSONC (comments + trailing
    commas allowed), and the strict stdlib json parser silently returns {}
    on either, making a correctly configured editor look unconfigured
    (Codex, GRIP#9266 round 2 thread 4). One regex substitution, not a
    hand-rolled scanner: a string token is returned verbatim, anything else
    matched (a comment) is dropped.
    """
    def _keep_strings_drop_comments(match: re.Match) -> str:
        token = match.group(0)
        return token if token.startswith('"') else ""
    return _JSONC_TOKEN.sub(_keep_strings_drop_comments, text)


def _load_jsonc(path: Path) -> dict:
    try:
        text = _strip_jsonc(path.read_text(encoding="utf-8"))
        text = re.sub(r",(\s*[}\]])", r"\1", text)  # one trailing comma, tolerated
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}


_LOCKFILES = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}
_INSTALL_COMMANDS = {
    "npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile",
    "yarn": "yarn install --immutable",
}


def _detect_package_manager(repo_path: Path) -> str:
    """npm, pnpm or yarn, from package.json's own "packageManager" field.

    Defaults to npm when the field is absent (or null -- `or ""` treats a
    JSON null the same as a missing key, since dict.get(key, default) only
    falls back on a MISSING key, not on an explicit null value present at
    that key; see check_test_runner's identical guard, Codex GRIP#9266 round
    2 thread 6, for the failure this same gotcha causes elsewhere).
    """
    pm_field = (_load_json(repo_path / "package.json").get("packageManager") or "")
    if pm_field.startswith("pnpm"):
        return "pnpm"
    if pm_field.startswith("yarn"):
        return "yarn"
    return "npm"


# ---------------------------------------------------------------------------
# Checks -- each takes a DoctorContext, returns a CheckResult, never raises.
# ---------------------------------------------------------------------------

def check_homebrew(ctx: DoctorContext) -> CheckResult:
    if ctx.platform_name != "Darwin":
        return CheckResult(NEEDS_INPUT, "Homebrew is a macOS tool", _MACOS_ONLY_NOT_APPLICABLE)
    ok, output = _probe(["brew", "--version"])
    if not ok:
        return CheckResult(FAIL, f"Homebrew not found: {output}",
                            '/bin/bash -c "$(curl -fsSL '
                            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
    return CheckResult(PASS, output.splitlines()[0] if output else output)


def check_xcode_clt(ctx: DoctorContext) -> CheckResult:
    if ctx.platform_name != "Darwin":
        return CheckResult(NEEDS_INPUT, "Xcode Command Line Tools are a macOS tool",
                            _MACOS_ONLY_NOT_APPLICABLE)
    ok, output = _probe(["xcode-select", "-p"])
    if not ok:
        return CheckResult(FAIL, f"Xcode Command Line Tools not found: {output}",
                            "xcode-select --install")
    return CheckResult(PASS, output)


def check_git_version(ctx: DoctorContext) -> CheckResult:
    ok, output = _probe(["git", "--version"])
    if not ok:
        return CheckResult(FAIL, f"git not found: {output}", "brew install git")
    version = _parse_version(output)
    if version is None or version[:2] < (2, 30):
        return CheckResult(FAIL, f"git version too old: {output}", "brew install git")
    return CheckResult(PASS, output)


def check_git_identity(ctx: DoctorContext) -> CheckResult:
    name_ok, name = _probe(["git", "config", "--global", "user.name"])
    mail_ok, mail = _probe(["git", "config", "--global", "user.email"])
    if name_ok and name and mail_ok and mail:
        return CheckResult(PASS, f"{name} <{mail}>")
    missing = [label for label, ok_flag, value in
               (("user.name", name_ok, name), ("user.email", mail_ok, mail))
               if not (ok_flag and value)]
    return CheckResult(
        FAIL, f"git config missing: {', '.join(missing)}",
        'git config --global user.name "Your Name" '
        '&& git config --global user.email "you@example.com"')


def _check_git_repo_setting(ctx: DoctorContext, key: str, expected: str) -> CheckResult:
    """core.autocrlf / core.ignorecase live PER REPO in the Guild clone, not
    globally -- a global setting is this operator's own habit, and has no
    bearing on what the clone a newcomer makes will actually use (V>>
    decision, GRIP#9266 follow-up). With a repo known, read its EFFECTIVE
    value (`git -C <repo> config --get`, which already falls through
    local -> global -> system) and pass/fail on that. Without a repo yet,
    a correct global value is a reasonable PASS (it will apply to the new
    clone too), but an incorrect/unset one is NEEDS_INPUT, not FAIL -- there
    is nothing to fix yet without the repo path.
    """
    if ctx.repo_path is not None:
        ok, value = _probe(["git", "-C", str(ctx.repo_path), "config", "--get", key])
        if ok and value.strip().lower() == expected:
            return CheckResult(PASS, f"{key}={value} (in the repo)")
        return CheckResult(
            FAIL, f"{key}={value or '(unset)'} in the repo, expected {expected}",
            f"git -C {ctx.repo_path} config {key} {expected}")
    ok, value = _probe(["git", "config", "--global", key])
    if ok and value.strip().lower() == expected:
        return CheckResult(PASS, f"{key}={value} (global, will carry into a new clone)")
    return CheckResult(
        NEEDS_INPUT,
        f"{key}={value or '(unset)'} globally -- this setting belongs in the "
        "Guild clone itself; needs --repo-path to check and fix it there",
        f"git -C <clone> config {key} {expected}")


def check_git_autocrlf(ctx: DoctorContext) -> CheckResult:
    expected = "false" if ctx.platform_name == "Windows" else "input"
    return _check_git_repo_setting(ctx, "core.autocrlf", expected)


def check_git_ignorecase(ctx: DoctorContext) -> CheckResult:
    return _check_git_repo_setting(ctx, "core.ignorecase", "false")


def check_git_auth(ctx: DoctorContext) -> CheckResult:
    ssh_ok, _ = _probe(["ssh-add", "-l"])
    helper_ok, helper = _probe(["git", "config", "--global", "credential.helper"])
    if ssh_ok:
        return CheckResult(PASS, "ssh-agent has loaded identities")
    if helper_ok and helper.strip():
        return CheckResult(PASS, f"credential.helper={helper.strip()}")
    key_names = ("id_ed25519", "id_rsa", "id_ecdsa")
    if any((ctx.home / ".ssh" / name).exists() for name in key_names):
        return CheckResult(PASS, "an SSH private key file is present in ~/.ssh")
    return CheckResult(
        FAIL, "no loaded SSH identity, no credential helper, no key file",
        'ssh-keygen -t ed25519 -C "you@example.com" (add the public key to '
        'your Git host), or: git config --global credential.helper osxkeychain')


def check_git_push_roundtrip(ctx: DoctorContext) -> CheckResult:
    if not ctx.repo_url:
        return CheckResult(NEEDS_INPUT,
                            "needs the repo URL (not yet known -- the Guild's "
                            "sample repo has not been assigned)",
                            "re-run with --repo-url <url> once known")
    ok, output = _probe(["git", "ls-remote", ctx.repo_url])
    roundtrip_fix = (
        "git clone <repo-url> && cd <repo> "
        "&& git checkout -b access-check-<your-name> "
        "&& git push -u origin access-check-<your-name> "
        "&& git push origin --delete access-check-<your-name> "
        "&& git checkout main")
    if not ok:
        return CheckResult(FAIL, f"remote not reachable: {output}"[:500], roundtrip_fix)
    # `git ls-remote` only proves the remote is reachable for READ -- it
    # cannot prove push access, which is what this check's own name promises
    # ("push access is set up"). Returning PASS here would let a read-only
    # collaborator's machine report ready and then fail on their first
    # `mob next` push (Codex, GRIP#9266 thread 2). NEEDS_INPUT until the
    # round trip below has actually been run by hand.
    first_line = output.splitlines()[0] if output else ctx.repo_url
    return CheckResult(
        NEEDS_INPUT,
        f"remote reachable for READ ({first_line}), but that does not prove "
        "push access -- run the round trip by hand",
        roundtrip_fix)


def check_nvm_installed(ctx: DoctorContext) -> CheckResult:
    # nvm-windows is a SEPARATE project from Linux/macOS nvm (the Guild page
    # says so explicitly) and has no nvm.sh -- looking for it on Windows
    # would misreport a correctly installed nvm-windows as missing (Codex,
    # GRIP#9266 round 2 thread 1).
    if ctx.platform_name == "Windows":
        found = which("nvm") or which("nvm.exe")
        if found:
            return CheckResult(PASS, f"nvm-windows found at {found}")
        return CheckResult(
            FAIL, "nvm-windows not found on PATH",
            "winget install --id CoreyButler.NVMforWindows -e")
    nvm_dir = Path(os.environ.get("NVM_DIR", str(ctx.home / ".nvm")))
    if (nvm_dir / "nvm.sh").exists():
        return CheckResult(PASS, f"nvm found at {nvm_dir}")
    return CheckResult(
        FAIL, f"no nvm.sh at {nvm_dir}",
        "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash")


def check_no_homebrew_node_conflict(ctx: DoctorContext) -> CheckResult:
    if ctx.platform_name != "Darwin":
        return CheckResult(PASS, "Homebrew-vs-nvm conflict is a macOS-specific concern")
    ok, _ = _probe(["brew", "list", "node"])
    if ok:
        return CheckResult(
            FAIL, "Homebrew node is installed alongside nvm",
            "brew uninstall node  # use nvm for Node, not Homebrew -- two "
            "installs race for the same PATH entry")
    return CheckResult(PASS, "no Homebrew node formula installed")


# Node's last release under the old odd=Current/even=LTS cadence. From Node 27
# (first alpha 2026-10-29, GA April 2027) every release line is LTS-eligible
# regardless of parity, so major-number parity stops being a signal at all
# (nodejs.org "Evolving the Node.js Release Schedule", checked 2026-11-17;
# Codex, GRIP#9266 thread 9). Past this ceiling the check reports NEEDS_INPUT
# rather than guess with a heuristic that is KNOWN to no longer apply.
_LTS_PARITY_CEILING = 26


def check_node_lts(ctx: DoctorContext) -> CheckResult:
    ok, output = _probe(["node", "--version"])
    if not ok:
        return CheckResult(FAIL, f"node not found: {output}",
                            "nvm install --lts && nvm use --lts")
    version = output.strip().lstrip("v")
    if ctx.repo_path is not None:
        nvmrc = ctx.repo_path / ".nvmrc"
        if nvmrc.exists():
            pinned = nvmrc.read_text(encoding="utf-8").strip()
            # A named alias (lts/*, lts/iron, node, stable, ...) is resolved by
            # nvm itself against its own alias table -- a literal prefix
            # compare against the numeric `node --version` output rejects
            # every correctly-configured alias (Codex, GRIP#9266 thread 6).
            # Only a numeric pin ("22", "22.11.0", "v22") is checked here.
            if pinned and not pinned[0].isdigit() and not pinned.startswith("v"):
                return CheckResult(
                    NEEDS_INPUT,
                    f".nvmrc pins a named alias ({pinned}) this check cannot "
                    "resolve without invoking nvm itself",
                    "confirm by hand, from inside the repo: nvm install && nvm use")
            if pinned and not version.startswith(pinned.lstrip("v")):
                return CheckResult(FAIL, f"node {version} does not match .nvmrc ({pinned})",
                                    "nvm install && nvm use")
            return CheckResult(PASS, f"node {version} matches .nvmrc ({pinned})")
    major = version.split(".")[0]
    if not major.isdigit():
        return CheckResult(FAIL, f"could not parse node version: {version}",
                            "nvm install --lts && nvm use --lts")
    major_num = int(major)
    if major_num > _LTS_PARITY_CEILING:
        return CheckResult(
            NEEDS_INPUT,
            f"node {version} is past Node {_LTS_PARITY_CEILING}, where the "
            "even-is-LTS convention this check relies on stops applying",
            "check https://nodejs.org/en/about/previous-releases by hand")
    if major_num % 2 != 0:
        return CheckResult(FAIL, f"node {version} is an odd-numbered, Current-only line",
                            "nvm install --lts && nvm use --lts")
    return CheckResult(PASS, f"node {version} is on an even-numbered, LTS-track line")


def check_deps_installed(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    # The repo may declare pnpm or Yarn via package.json's "packageManager"
    # field (the Guild page itself says to "enable Corepack instead and let
    # the repo's packageManager field decide"); a hard-coded npm/package-
    # lock.json check false-fails those repos and recommends the wrong
    # lockfile (Codex, GRIP#9266 round 2 thread 2).
    pm = _detect_package_manager(ctx.repo_path)
    lockfile = _LOCKFILES[pm]
    install_cmd = _INSTALL_COMMANDS[pm]
    if not (ctx.repo_path / lockfile).exists():
        return CheckResult(FAIL, f"no {lockfile} in the repo ({pm})",
                            f"commit a lockfile, then run: {install_cmd}")
    # Yarn Plug'n'Play intentionally has no node_modules (it uses .pnp.cjs
    # instead), so the directory-exists check only applies to npm/pnpm/
    # Yarn-classic layouts.
    if pm == "yarn" and (ctx.repo_path / ".pnp.cjs").exists():
        return CheckResult(PASS, f"{lockfile} present; Yarn PnP in use (.pnp.cjs), "
                                  "no node_modules expected")
    if not (ctx.repo_path / "node_modules").is_dir():
        return CheckResult(FAIL, f"{lockfile} present but node_modules is missing", install_cmd)
    return CheckResult(PASS, f"{lockfile} and node_modules are both present ({pm})")


def check_typescript_installed(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    # --no-install: without it, npx silently fetches and installs `typescript`
    # from the registry when it is not already a local dependency -- in a
    # noninteractive shell npm assumes consent, so the "is TypeScript
    # project-local" check would itself trigger a network install, breaking
    # the read-only guarantee and proving nothing about what the project
    # actually declares (Codex, GRIP#9266 thread 1).
    ok, output = _probe(["npx", "--no-install", "tsc", "--version"], cwd=ctx.repo_path)
    if not ok:
        return CheckResult(FAIL, f"npx tsc --version failed: {output}",
                            "npm install --save-dev typescript @types/node")
    return CheckResult(PASS, output)


def check_typescript_typecheck(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    # A project with "incremental"/"composite" in tsconfig has tsc write a
    # .tsbuildinfo file as a side effect of --noEmit, which would dirty the
    # repo or overwrite a real build cache -- not read-only (Codex, GRIP#9266
    # round 2 thread 3). --tsBuildInfoFile redirects that single side effect
    # to a throwaway temp directory that is removed when the probe returns.
    with tempfile.TemporaryDirectory() as tmp_dir:
        buildinfo = str(Path(tmp_dir) / "doctor-probe.tsbuildinfo")
        ok, output = _probe(
            ["npx", "--no-install", "tsc", "--noEmit", "--tsBuildInfoFile", buildinfo],
            cwd=ctx.repo_path)
    if not ok:
        return CheckResult(FAIL, f"npx tsc --noEmit reported errors: {output}"[:500],
                            "fix the reported type errors")
    return CheckResult(PASS, "project type-checks cleanly")


def check_editor_workspace_ts(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    fix = ('In VS Code: "TypeScript: Select TypeScript Version" '
           '-> "Use Workspace Version"')
    settings = ctx.repo_path / ".vscode" / "settings.json"
    if not settings.exists():
        return CheckResult(FAIL, "no .vscode/settings.json in the repo", fix)
    # VS Code documents settings.json as JSONC (comments + trailing commas
    # allowed); strict json.loads silently returns {} on either, so a
    # correctly configured editor looks unconfigured (Codex, GRIP#9266
    # round 2 thread 4).
    data = _load_jsonc(settings)
    if not data.get("typescript.tsdk"):
        return CheckResult(FAIL, "typescript.tsdk is not pinned in .vscode/settings.json", fix)
    return CheckResult(PASS, f"typescript.tsdk = {data['typescript.tsdk']}")


def check_test_runner(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    fix = "npm install --save-dev vitest"
    pkg = ctx.repo_path / "package.json"
    if not pkg.exists():
        return CheckResult(FAIL, "no package.json in the repo", fix)
    data = _load_json(pkg)
    # `or {}`, not `.get(key, {})`: npm tolerates "dependencies": null, and
    # dict.get only falls back to its default on a MISSING key, not on an
    # explicit null VALUE at a present key -- `{**None}` raises TypeError
    # and aborts the whole doctor command instead of returning the promised
    # complete report (Codex, GRIP#9266 round 2 thread 6).
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    found = [name for name in ("jest", "vitest") if name in deps]
    if not found:
        return CheckResult(FAIL, "neither jest nor vitest is a dependency", fix)
    return CheckResult(PASS, f"test runner present: {', '.join(found)}")


def check_stryker_configured(ctx: DoctorContext) -> CheckResult:
    if ctx.repo_path is None:
        return CheckResult(NEEDS_INPUT, _NO_REPO_DETAIL, _NO_REPO_FIX)
    config_names = ("stryker.config.json", "stryker.config.mjs", "stryker.config.js")
    if any((ctx.repo_path / name).exists() for name in config_names):
        return CheckResult(PASS, "a stryker config file is present")
    deps = _load_json(ctx.repo_path / "package.json").get("devDependencies") or {}
    if "@stryker-mutator/core" in deps:
        return CheckResult(PASS, "@stryker-mutator/core is a dependency")
    return CheckResult(FAIL, "no Stryker config or dependency found", "npx stryker init")


def check_mob_on_path(ctx: DoctorContext) -> CheckResult:
    found = which("mob")
    if not found:
        return CheckResult(FAIL, "mob is not on PATH",
                            "brew install remotemobprogramming/brew/mob")
    return CheckResult(PASS, f"mob found at {found}")


@dataclass
class Check:
    id: str
    proves: str
    fn: Callable[[DoctorContext], CheckResult]


CHECKS: list[Check] = [
    Check("homebrew", "Homebrew is installed (most installs on this page route through it)", check_homebrew),
    Check("xcode_clt", "Xcode Command Line Tools are installed (git, native npm module compilers)", check_xcode_clt),
    Check("git_version", "Git is 2.30 or newer", check_git_version),
    Check("git_identity", "git user.name and user.email are set globally", check_git_identity),
    Check("git_autocrlf", "core.autocrlf matches the platform (input on macOS/Linux)", check_git_autocrlf),
    Check("git_ignorecase", "core.ignorecase is false (so a capitalisation-only rename is noticed)", check_git_ignorecase),
    Check("git_auth", "an SSH key or credential helper is configured", check_git_auth),
    Check("git_push_roundtrip", "the sample repo is reachable and push access is set up", check_git_push_roundtrip),
    Check("nvm_installed", "Node is managed through nvm, not a system package", check_nvm_installed),
    Check("no_homebrew_node_conflict", "Homebrew node is not installed alongside nvm", check_no_homebrew_node_conflict),
    Check("node_lts", "the active Node is an LTS line, or matches the repo's .nvmrc", check_node_lts),
    Check("deps_installed", "project dependencies were installed with npm ci", check_deps_installed),
    Check("typescript_installed", "TypeScript is a project-local dev dependency", check_typescript_installed),
    Check("typescript_typecheck", "the project type-checks with npx tsc --noEmit", check_typescript_typecheck),
    Check("editor_workspace_ts", "the editor is pinned to the workspace TypeScript version", check_editor_workspace_ts),
    Check("test_runner", "a test runner (Jest or Vitest) is configured", check_test_runner),
    Check("stryker_configured", "mutation testing (Stryker) is set up", check_stryker_configured),
    Check("mob_on_path", "the mob.sh handover tool is installed and on PATH", check_mob_on_path),
]


def run_all(ctx: DoctorContext) -> dict[str, CheckResult]:
    return {c.id: c.fn(ctx) for c in CHECKS}


def to_json(results: dict[str, CheckResult]) -> str:
    by_id = {c.id: c for c in CHECKS}
    payload = {
        cid: {"proves": by_id[cid].proves, "status": result.status,
              "detail": result.detail, "fix": result.fix}
        for cid, result in results.items()
    }
    return json.dumps(payload, indent=2)


_MARKER = {PASS: "PASS", FAIL: "FAIL", NEEDS_INPUT: "NEEDS INPUT"}


def to_text(results: dict[str, CheckResult]) -> str:
    counts = {PASS: 0, FAIL: 0, NEEDS_INPUT: 0}
    lines = []
    for cid, result in results.items():
        counts[result.status] += 1
        lines.append(f"[{_MARKER[result.status]}] {cid}: {result.detail}")
        if result.status != PASS and result.fix:
            lines.append(f"    fix: {result.fix}")
    lines.append(f"-- {counts[PASS]} pass, {counts[FAIL]} fail, "
                 f"{counts[NEEDS_INPUT]} needs input --")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only checks for the Guild mob-programming "
                    "'before you start' prerequisites.")
    parser.add_argument("--repo-path", type=Path, default=None)
    parser.add_argument("--repo-url", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    ctx = DoctorContext(repo_path=args.repo_path, repo_url=args.repo_url)
    results = run_all(ctx)
    print(to_json(results) if args.json else to_text(results))
    return 1 if any(r.status == FAIL for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
