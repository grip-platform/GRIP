"""The grip-browser launch surface: which upstream tool sections a session gets.

GRIP attaches stealth-browser-mcp (pinned sha, own python3.13 venv, outside every
repository; see ``lib/grip_tools_dir.py``) opt-in per launch as a stdio MCP
server named ``grip-browser``. The server ships 101 tools in 11 sections and a
``--disable-<section>`` flag per section. This module is the ONE place that
decides which flags the launcher passes, as a pure function of the launch
environment, so the launcher, the dry run and the tests cannot disagree.

Two layers hold the surface, and they take one decision together:

- the LAUNCHER removes whole sections whose tools are denied by default, so
  the denied tools are absent from the session, not merely refused, and their
  schemas cost no context;
- the GATE (``hooks/grip-browser-tool-policy-gate.py`` over
  ``lib/grip_browser_tool_policy.py``) classifies every call by effect and
  denies the credential, code-exec, host-file and server-admin classes unless
  the operator's switch for that class is set.

An operator switch (``GRIP_BROWSER_ALLOW_COOKIES=1``, ``GRIP_BROWSER_ALLOW_CODE=1``)
therefore opens its section HERE and its class at the gate, one decision per
class, never two. Server-admin has no switch in v1, so ``debugging`` and
``dynamic-hooks`` stay off outside the ``full`` measurement profile.

Profiles (``GRIP_BROWSER_SURFACE``):

- ``minimal`` (the default until the schema-cost measurement picks one):
  upstream's ``--minimal`` keeps browser-management (8) and element-interaction
  (13), 21 tools. Upstream has no add-back flag for it, so an explicit
  ``GRIP_BROWSER_SECTIONS`` under this profile is an error, not a silent no-op.
- ``xpool-safe``: no ``--minimal``; the four gated sections are disabled
  (``--xpool-safe`` is upstream's own flag for dropping the 17 raw-CDP tools)
  and a switch or ``GRIP_BROWSER_SECTIONS=a,b`` adds one back. 64 tools.
- ``full``: every section, 101 tools. For the schema-cost measurement only; the
  gate still denies what it denies.

Upstream reads ``XPOOL_SAFE_MODE`` at import and disables cdp-functions when it
is set, so the child environment carries that variable exactly when the surface
disables cdp-functions and drops it when the operator opened the section. The
transport is upstream's default, stdio; this module never names another.

Stdlib only, no I/O outside ``main`` and ``resolve_launch``: the server's own
venv interpreter runs it as a script from ``mcp-servers/grip-browser/start.sh``.
"""
from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import grip_tools_dir  # noqa: E402

TOOL = "stealth-browser-mcp"
SERVER_KEY = "grip-browser"

# The 11 sections and their tool counts at the pinned sha (ba95cca9), read from
# `src/server.py --list-sections` on 2026-10-18. The sum is 101.
SECTION_TOOL_COUNTS = {
    "browser-management": 8,
    "element-interaction": 13,
    "element-extraction": 9,
    "file-extraction": 9,
    "network-debugging": 10,
    "cdp-functions": 17,
    "progressive-cloning": 10,
    "cookies-storage": 3,
    "tabs": 5,
    "debugging": 7,
    "dynamic-hooks": 10,
}
SECTIONS = tuple(SECTION_TOOL_COUNTS)
TOOL_TOTAL = sum(SECTION_TOOL_COUNTS.values())
MINIMAL_SECTIONS = ("browser-management", "element-interaction")

# Disabled unless a switch or GRIP_BROWSER_SECTIONS opens them. Order is the
# order the flags are rendered in.
GATED_SECTIONS = ("cookies-storage", "cdp-functions", "debugging", "dynamic-hooks")
XPOOL_FLAG = "--xpool-safe"
XPOOL_ENV = "XPOOL_SAFE_MODE"

PROFILE_ENV = "GRIP_BROWSER_SURFACE"
PROFILES = ("minimal", "xpool-safe", "full")
GATED_DEFAULT = "minimal"
SECTIONS_ENV = "GRIP_BROWSER_SECTIONS"
DIR_ENV = "GRIP_BROWSER_DIR"
SCRATCH_ENV = "GRIP_BROWSER_SCRATCH"
DRY_RUN_ENV = "GRIP_BROWSER_DRY_RUN"

# A class switch that opens a section at the launcher as well as at the gate.
CLASS_SWITCH_SECTIONS = {
    "GRIP_BROWSER_ALLOW_COOKIES": ("cookies-storage",),
    "GRIP_BROWSER_ALLOW_CODE": ("cdp-functions",),
}

# Upstream variables the launcher never passes through: the port and the auth
# token belong to a transport GRIP does not run; the debug flags write logs
# the session did not ask for; the idle timeout keeps upstream's 600 s reaper.
UNSET_IN_CHILD = (
    "PORT",
    "STEALTH_BROWSER_MCP_AUTH_TOKEN",
    "MCP_AUTH_TOKEN",
    "STEALTH_BROWSER_DEBUG",
    "DEBUG",
    "BROWSER_IDLE_TIMEOUT",
)
UPLOAD_DIRS_ENV = "BROWSER_FILE_UPLOAD_ALLOWED_DIRS"
INSTALL_HINT = f"grip-harness-install {SERVER_KEY}"


class SurfaceError(ValueError):
    """The launch environment asks for a surface this module cannot build."""


def _disable_flag(section: str) -> str:
    return f"--disable-{section}"


def explicit_sections(env: dict) -> frozenset:
    """Sections named in ``GRIP_BROWSER_SECTIONS``; an unknown name raises."""
    raw = (env.get(SECTIONS_ENV) or "").strip()
    if not raw:
        return frozenset()
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(names) - set(SECTIONS))
    if unknown:
        raise SurfaceError(
            f"unknown section {', '.join(unknown)} in {SECTIONS_ENV}; "
            f"the sections are: {', '.join(SECTIONS)}")
    return frozenset(names)


def switch_sections(env: dict) -> frozenset:
    """Sections opened by a class switch set to exactly the string ``1``."""
    opened = set()
    for switch, sections in CLASS_SWITCH_SECTIONS.items():
        if env.get(switch) == "1":
            opened.update(sections)
    return frozenset(opened)


def profile_for(env: dict) -> str:
    name = (env.get(PROFILE_ENV) or GATED_DEFAULT).strip()
    if name not in PROFILES:
        raise SurfaceError(
            f"unknown profile {name!r} in {PROFILE_ENV}; one of {', '.join(PROFILES)}")
    return name


def flags_for(env: dict) -> list[str]:
    """The server flags for this launch environment. Pure; raises SurfaceError."""
    profile = profile_for(env)
    explicit = explicit_sections(env)
    opened = explicit | switch_sections(env)
    if profile == "full":
        return []
    if profile == "minimal":
        if explicit:
            raise SurfaceError(
                f"the minimal profile has no add-back (upstream --minimal); set "
                f"{PROFILE_ENV}=xpool-safe to open {', '.join(sorted(explicit))}")
        flags = ["--minimal"]
        opened = frozenset()
    else:
        flags = []
    for section in GATED_SECTIONS:
        if section in opened:
            continue
        flags.append(_disable_flag(section))
        if section == "cdp-functions":
            flags.append(XPOOL_FLAG)
    return flags


def enabled_sections(flags: list[str]) -> tuple:
    """The sections a server started with ``flags`` advertises."""
    if "--minimal" in flags:
        return MINIMAL_SECTIONS
    disabled = {f[len("--disable-"):] for f in flags if f.startswith("--disable-")}
    if XPOOL_FLAG in flags:
        disabled.add("cdp-functions")
    return tuple(s for s in SECTIONS if s not in disabled)


def expected_tool_count(flags: list[str]) -> int:
    return sum(SECTION_TOOL_COUNTS[s] for s in enabled_sections(flags))


def scratch_dir(env: dict) -> Path:
    """The artefact root: every screenshot, export and clone file lands here, and
    ``file_upload`` may read only from here. Outside every repository."""
    override = (env.get(SCRATCH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return grip_tools_dir.tool_root(TOOL, env) / "scratch"


def install_dir(env: dict) -> Path:
    """``GRIP_BROWSER_DIR`` (a re-pin trial or a rollback) or ``<tool>/current``."""
    override = (env.get(DIR_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return grip_tools_dir.current_dir(TOOL, env)


def child_env(env: dict, flags: list[str], scratch: Path) -> dict:
    """The server's environment: the launch environment minus the variables
    GRIP never passes, plus the upload root and the xpool mode that matches
    the flags."""
    child = {k: v for k, v in env.items() if k not in UNSET_IN_CHILD}
    child[UPLOAD_DIRS_ENV] = str(scratch)
    if "cdp-functions" in enabled_sections(flags):
        child.pop(XPOOL_ENV, None)
    else:
        child[XPOOL_ENV] = "1"
    return child


@dataclass(frozen=True)
class Launch:
    python: Path
    server: Path
    flags: tuple
    cwd: Path
    env: dict

    @property
    def argv(self) -> list[str]:
        return [str(self.python), str(self.server), *self.flags]


def resolve_launch(env: dict, directory: Path | None = None) -> Launch:
    """Everything the launcher needs, or SurfaceError naming what is missing."""
    flags = flags_for(env)
    where = directory or install_dir(env)
    python = grip_tools_dir.venv_python(where)
    server = where / "repo" / "src" / "server.py"
    if python is None or not server.is_file():
        raise SurfaceError(
            f"{SERVER_KEY} is not installed at {where} (no venv interpreter or no "
            f"repo/src/server.py); install the pinned sha with: {INSTALL_HINT}")
    scratch = scratch_dir(env)
    return Launch(python=python, server=server, flags=tuple(flags), cwd=scratch,
                  env=child_env(env, flags, scratch))


def describe(env: dict) -> str:
    """The dry run: flags, the install location and the environment deltas,
    without needing the install to exist."""
    flags = flags_for(env)
    where = install_dir(env)
    scratch = scratch_dir(env)
    python = grip_tools_dir.venv_python(where)
    lines = [
        f"profile: {profile_for(env)}",
        f"flags: {' '.join(flags) or '(none)'}",
        f"sections: {', '.join(enabled_sections(flags))} "
        f"({expected_tool_count(flags)} tools)",
        f"install: {where} ({'present' if python else 'absent -- ' + INSTALL_HINT})",
        f"cwd: {scratch}",
        f"env set: {UPLOAD_DIRS_ENV}={shlex.quote(str(scratch))}"
        + (f" {XPOOL_ENV}=1" if "cdp-functions" not in enabled_sections(flags) else ""),
        f"env unset: {' '.join(UNSET_IN_CHILD)}",
    ]
    return "\n".join(lines)


def main(argv: list[str], env: dict | None = None) -> int:
    """``--flags`` prints one flag per line; ``--dry-run`` prints the plan;
    ``--launch <dir>`` execs the server (the launcher's path). Exit 2 on a
    surface error, 1 when the install is missing."""
    environ = dict(os.environ if env is None else env)
    try:
        if argv[:1] == ["--flags"]:
            print("\n".join(flags_for(environ)))
            return 0
        if argv[:1] == ["--launch"]:
            directory = Path(argv[1]) if len(argv) > 1 else None
            launch = resolve_launch(environ, directory)
            if environ.get(DRY_RUN_ENV) == "1":
                print(describe(environ))
                print("argv: " + " ".join(shlex.quote(a) for a in launch.argv))
                return 0
            launch.cwd.mkdir(parents=True, exist_ok=True)
            os.chdir(launch.cwd)
            os.execve(str(launch.python), launch.argv, launch.env)
        if argv[:1] == ["--dry-run"] or (not argv and environ.get(DRY_RUN_ENV) == "1"):
            print(describe(environ))
            return 0
        print(f"usage: {Path(__file__).name} --flags | --dry-run | --launch [DIR]",
              file=sys.stderr)
        return 2
    except SurfaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1 if "not installed" in str(exc) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
