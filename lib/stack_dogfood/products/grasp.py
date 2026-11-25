"""GRASP product adapter.

GRASP ships the ``grasp-mcp`` MCP server. Installing GRASP into a harness means
registering that server with the harness: an ``mcp add`` CLI for the seven CLI
harnesses, a plugin for Claude Code, and a JSON config entry for Cursor and the
Claude desktop app.
"""
from __future__ import annotations

import shutil

from ..installers import Target, cli_target, json_target
from ..model import Check
from ..run import run

NAME = "grasp"
_SERVER = "grasp-mcp"  # the GRASP MCP server binary


def _mcp(harness, list_argv, add_argv, add_input="", disabled_token="",
         config_path="", config_keys=()) -> Target:
    return cli_target(
        harness,
        list_argv=list_argv,
        present_token="grasp",
        add_argv=add_argv,
        add_input=add_input,
        disabled_token=disabled_token,
        config_path=config_path,
        config_keys=config_keys,
    )


def targets() -> list[Target]:
    abs_server = shutil.which(_SERVER) or _SERVER
    return [
        _mcp("codex", ["codex", "mcp", "list"],
             ["codex", "mcp", "add", "grasp", "--", _SERVER]),
        # gemini rejects the -- separator, and its default scope is the project
        _mcp("gemini", ["gemini", "mcp", "list"],
             ["gemini", "mcp", "add", "--scope", "user", "grasp", _SERVER]),
        # opencode has listed no servers while its config named grasp (GRIP#9514)
        _mcp("opencode", ["opencode", "mcp", "list"],
             ["opencode", "mcp", "add", "--global", "grasp", "--", _SERVER],
             config_path="~/.config/opencode/opencode.json",
             config_keys=("mcp.grasp", "mcp.servers.grasp")),
        # hermes connects, then asks "Enable all N tools? [Y/n/select]". When the
        # connection fails it asks "Save config anyway?" instead, and a yes saves
        # the server disabled, so a listed row that says disabled is not installed.
        _mcp("hermes", ["hermes", "mcp", "list"],
             ["hermes", "mcp", "add", "grasp", "--command", _SERVER],
             add_input="Y\n", disabled_token="disabled"),
        _mcp("amp", ["amp", "mcp", "list"],
             ["amp", "mcp", "add", "grasp", "--", _SERVER]),
        _mcp("grok", ["grok", "mcp", "list"],
             ["grok", "mcp", "add", "grasp", _SERVER]),
        _mcp("qwen", ["qwen", "mcp", "list"],
             ["qwen", "mcp", "add", "grasp", _SERVER]),
        cli_target("claude-code",
                   list_argv=["claude", "plugin", "list"],
                   present_token="grasp",
                   add_argv=["claude", "plugin", "install", "grasp@grasp"]),
        json_target("cursor",
                    config_path="~/.cursor/mcp.json",
                    dotted_key="mcpServers.grasp",
                    value={"command": _SERVER}),
        json_target("claude-desktop",
                    config_path="~/Library/Application Support/Claude/"
                                "claude_desktop_config.json",
                    dotted_key="mcpServers.grasp",
                    value={"command": abs_server}),
    ]


def checks() -> list[Check]:
    path = shutil.which(_SERVER)
    on_path = Check(f"{NAME}::server-on-path", path is not None,
                    path or f"{_SERVER} not on PATH")
    # grasp has no --version flag: its CLI requires a subcommand, so `grasp
    # --version` exits non-zero ("the following arguments are required:
    # command") and the check failed on every healthy install. `grasp --help`
    # exits 0 and carries the version line ("GRASP grasp X.Y.Z -- ..."), so
    # probe that and surface the version rather than the usage banner.
    ver = run(["grasp", "--help"], timeout=15)
    version_line = next(
        (ln.strip() for ln in ver.out.splitlines()
         if "grasp" in ln.lower() and any(ch.isdigit() for ch in ln)),
        "",
    )
    detail = (version_line or ver.out.strip() or ver.err.strip() or "no output")[:120]
    cli = Check(f"{NAME}::cli-version", ver.ok, detail)
    return [on_path, cli]
