"""GRIP activation heal engine — the DRY core behind the /activate-grip Install
phase AND the standalone /grip-doctor --fix.

Flow (the "GRIP heals itself" mechanic):
    detect (per-server handshake, before) -> heal (idempotent grip-deps-install.sh)
    -> verify (handshake, after) -> honest per-server FAIL->OK / still-RED narrative.

Why a live handshake, not a filesystem check: the #3896 bug is "node_modules
present but the server won't boot" — a file check passes it, a handshake catches
it (lib/mcp_health.py is the floor).

Every external seam (handshake, installer, filesystem) is injectable so the whole
engine is unit-testable with zero network, zero `claude`, zero install side-effects.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import ensure_utf8_stdio  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
from mcp_health import CONNECTED, UNKNOWN, ServerHealth, check_health  # noqa: E402

# Server kinds that carry local deps an install can repair (http is remote).
_HEALABLE_KINDS = ("node", "python")


@dataclass(frozen=True)
class ServerSpec:
    """One MCP server as declared in .mcp.json, normalised for healing."""

    name: str
    kind: str  # "node" | "python" | "http" | "other"
    subdir: str  # e.g. "mcp-servers/grip-apps", "" for http/other


@dataclass
class ServerState:
    """Post-activation state of one server, for the narrative + receipt."""

    name: str
    status: str  # connected | failed | unknown
    healed: bool = False  # was down before, connected after
    detail: str = ""  # honest diagnosis when not connected


@dataclass
class ActivationResult:
    servers: list[ServerState] = field(default_factory=list)
    installed: bool = False  # the heal/install pass ran and succeeded
    all_green: bool = False

    @property
    def offline(self) -> list[ServerState]:
        return [s for s in self.servers if s.status != CONNECTED]


def _classify_kind(cfg: dict) -> tuple[str, str]:
    """Return (kind, subdir) for one .mcp.json server config."""
    if cfg.get("type") == "http" or cfg.get("url"):
        return "http", ""
    args = cfg.get("args", []) or []
    argpath = next((a for a in args if "mcp-servers/" in a), "")
    subdir = ""
    if "mcp-servers/" in argpath:
        parts = argpath.split("/")
        i = parts.index("mcp-servers")
        subdir = "/".join(parts[i : i + 2])
    command = cfg.get("command", "")
    if command == "node":
        return "node", subdir
    if command in ("bash", "sh") and argpath.endswith(".sh"):
        # A start.sh launcher fronts a FastMCP python server (needs the mcp pip pkg).
        return "python", subdir
    return "other", subdir


def load_specs(grip_home: Path) -> dict[str, ServerSpec]:
    """Parse .mcp.json into normalised ServerSpecs. Empty dict if absent/corrupt."""
    mcp_path = grip_home / ".mcp.json"
    try:
        data = json.loads(mcp_path.read_text())
    except (OSError, ValueError):
        return {}
    specs: dict[str, ServerSpec] = {}
    for name, cfg in (data.get("mcpServers") or {}).items():
        kind, subdir = _classify_kind(cfg or {})
        specs[name] = ServerSpec(name, kind, subdir)
    return specs


def _venv_has_mcp(grip_home: Path) -> bool:
    """Filesystem proxy for 'the GRIP venv can import mcp' — deterministic and
    cheap (no subprocess). The authoritative signal is the handshake; this only
    supplies the human reason for a RED python server.

    Both venv layouts are checked. POSIX nests site-packages under a version
    directory (lib/python3.13/site-packages); Windows does not (Lib/site-packages),
    so the POSIX glob needs a 'python*' level that never exists there. Globbing
    only the POSIX shape made this return False on Windows no matter what was
    installed, so every python server was permanently reported as missing 'mcp'
    and no amount of installing could clear it.
    """
    venv = grip_home / "venv"
    if any(venv.glob("lib/python*/site-packages/mcp")):
        return True
    return (venv / "Lib" / "site-packages" / "mcp").exists()


def diagnose(spec: ServerSpec, grip_home: Path) -> str:
    """Honest plain-language reason a server is not connected (for the RED card)."""
    if spec.kind not in _HEALABLE_KINDS:
        # Nothing a local install can repair. Naming npm ci or a pip package here
        # would send the operator after a fix that cannot apply to someone else's
        # endpoint, so name what they can actually act on instead.
        if spec.kind == "http":
            return "the remote endpoint did not answer (check the URL, the network, and its auth token)"
        return "unrecognised server kind"
    if spec.kind == "node":
        # A server with no package.json declares no dependencies and runs on Node
        # built-ins alone, so there is nothing for `npm ci` to install and a
        # missing node_modules/ says nothing about its health. Naming npm ci
        # there sends the operator after a fix that cannot apply: grip-run is
        # exactly this shape (server.js requires only fs/os/path/readline/
        # child_process, and the directory ships no manifest at all).
        if not (grip_home / spec.subdir / "package.json").is_file():
            return "the server failed to boot (no node dependencies required)"
        if not (grip_home / spec.subdir / "node_modules").is_dir():
            return "node dependencies not installed (needs npm ci)"
        return "node deps present but the server failed to boot"
    if spec.kind == "python":
        # _classify_kind calls every `bash .../start.sh` launcher "python", but
        # some launchers front an EXTERNAL binary instead (rea -> `rea mcp`,
        # antepaste -> a sibling checkout). Those directories hold no Python
        # and no requirements.txt, so the venv's `mcp` package says nothing
        # about them; the launcher itself prints the install hint when run.
        if not _launcher_fronts_python(grip_home / spec.subdir):
            return ("the launcher's prerequisite binary is missing -- run "
                    f"`bash {spec.subdir}/start.sh` for its install hint")
        if not _venv_has_mcp(grip_home):
            return "the 'mcp' python package is missing from the GRIP venv"
        return "python deps present but the server failed to boot"
    return "unrecognised server kind"


def _launcher_fronts_python(subdir: Path) -> bool:
    """True when a start.sh launcher's directory carries Python it runs."""
    if not subdir.is_dir():
        return True  # unknown layout: keep the historical python diagnosis
    return (subdir / "requirements.txt").is_file() or any(subdir.glob("*.py"))


def _run_installer(grip_home: Path, timeout: float = 600.0) -> bool:
    """Run the idempotent, comprehensive dependency installer. Returns True on
    exit 0. This IS the heal action — grip-deps-install.sh already diagnoses uv
    venvs, installs the mcp pip pkg, and npm-ci's node servers (PR #3902)."""
    script = grip_home / "scripts" / "grip-deps-install.sh"
    if not script.is_file():
        return False
    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode == 0


def run_activation(
    grip_home: Path | None = None,
    *,
    health_fn: Callable[..., dict[str, ServerHealth]] = check_health,
    heal_fn: Callable[[Path], bool] = _run_installer,
    diagnose_fn: Callable[[ServerSpec, Path], str] = diagnose,
    do_heal: bool = True,
) -> ActivationResult:
    """The Install phase / grip-doctor --fix coordinator.

    detect (before) -> heal (idempotent) -> verify (after) -> per-server state.
    ``do_heal=False`` runs a pure verify (no install) for a dry preview.
    """
    home = grip_home or get_claude_dir()
    specs = load_specs(home)
    # VERIFY every declared server; HEAL only the kinds a local install can repair.
    # These are two different questions and conflating them produced a false green:
    # scoping the probe to _HEALABLE_KINDS meant a remote http server (grip-channel)
    # was never asked, yet all_green was computed over the survivors and the banner
    # still announced "every tool is live". Unhealable must not mean unasserted --
    # a remote endpoint is precisely the kind that goes down on its own.
    verifiable = list(specs.values())
    names = [s.name for s in verifiable]

    before = health_fn(names)
    installed = heal_fn(home) if do_heal else False
    after = health_fn(names)

    states: list[ServerState] = []
    for spec in verifiable:
        h_after = after.get(spec.name)
        status = h_after.status if h_after else UNKNOWN
        h_before = before.get(spec.name)
        was_down = h_before is None or h_before.status != CONNECTED
        # An UNKNOWN server is one the probe could not ask about - not one that
        # answered badly. Running diagnose() on it INVENTS a reason ("failed to
        # boot") for a server that may be perfectly healthy, which is exactly what
        # happened when the probe timed out: five connected servers were each
        # given a fabricated boot failure. Say what is true instead.
        if status == CONNECTED:
            detail = ""
        elif status == UNKNOWN:
            detail = "could not verify - the MCP health probe did not return a status"
        else:
            detail = diagnose_fn(spec, home)
        states.append(
            ServerState(
                name=spec.name,
                status=status,
                healed=was_down and status == CONNECTED,
                detail=detail,
            )
        )

    all_green = bool(states) and all(s.status == CONNECTED for s in states)
    return ActivationResult(servers=states, installed=installed, all_green=all_green)


def _read_signature(home: Path) -> str:
    """Best-effort operator signature (e.g. 'V>>') for the all-green seal."""
    try:
        for line in (home / "facts" / "identity.md").read_text().splitlines():
            if line.startswith("signature:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _try_chime() -> None:
    """Best-effort all-green celebration — audio never gates activation."""
    try:
        import handoff_chime

        handoff_chime.play_chime()
    except Exception:
        pass


def main(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    run_fn: Callable[..., ActivationResult] = run_activation,
    chime: Callable[[], None] | None = _try_chime,
) -> int:
    """CLI surface shared by the /activate-grip Install phase and /grip-doctor --fix.

    Heals, renders the 'GRIP heals itself' narrative, writes a signed receipt, and
    (on all-green) plays the chime. Exit 0 iff every healable server is live.
    """
    import argparse
    import platform

    from grip_activation_receipt import build_receipt, sign_receipt, write_receipt
    from grip_activation_render import render_heal, render_json_receipt

    parser = argparse.ArgumentParser(
        prog="grip-activation-health",
        description="Detect, heal, and verify GRIP's MCP-server dependencies.",
    )
    parser.add_argument("--check", action="store_true", help="dry preview — verify only, no install")
    parser.add_argument("--json", action="store_true", help="emit the signed receipt as JSON (non-tty/CI)")
    args = parser.parse_args(argv)

    # Before any render: this module prints U+2714/U+2588 via render_heal, and
    # Windows defaults stdout to the ANSI code page, which cannot encode them.
    # Without this the command dies with UnicodeEncodeError instead of
    # reporting -- the health check failing in the way it exists to detect.
    ensure_utf8_stdio()

    home = home or get_claude_dir()
    result = run_fn(home, do_heal=not args.check)
    signed = sign_receipt(build_receipt(result, host=platform.node()))

    if args.json:
        print(render_json_receipt(signed))
    else:
        print(render_heal(result, signature=_read_signature(home)))
        if not args.check:
            write_receipt(signed, home)

    if result.all_green and not args.check and chime is not None:
        chime()
    return 0 if result.all_green else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
