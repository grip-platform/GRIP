"""HAL Native Tool Dispatch — execute tools without any external harness.

Absorbed from TF++ src/tools/. HAL runs Bash, Read, Write, Edit, Glob, Grep
natively with sandbox enforcement via the hook dispatch engine.

Usage:
    from lib.hal.tool_dispatch import ToolDispatcher

    dispatcher = ToolDispatcher()
    result = dispatcher.execute("Read", {"file_path": "/tmp/test.py"})
    result = dispatcher.execute("Bash", {"command": "ls -la"})
"""
import glob as glob_mod
import os
import re
import subprocess
from typing import Optional

from lib.hal.hook_dispatch import HookDispatcher

_TOOL_REGISTRY: dict[str, "callable"] = {}


class ToolResult:
    """Result of a tool execution."""

    def __init__(self, output: str = "", error: str = "", blocked: bool = False, reason: str = ""):
        self.output = output
        self.error = error
        self.blocked = blocked
        self.reason = reason


class ToolDispatcher:
    """Dispatches tool calls with hook enforcement."""

    def __init__(self, hooks: Optional[HookDispatcher] = None):
        self._hooks = hooks or HookDispatcher()

    def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute a tool with pre/post hook enforcement."""
        pre = self._hooks.dispatch("PreToolUse", {
            "tool_name": tool_name, "tool_input": tool_input,
        })
        if pre.blocked:
            return ToolResult(blocked=True, reason=pre.reason)

        handler = _TOOL_REGISTRY.get(tool_name)
        if not handler:
            return ToolResult(error=f"Unknown tool: {tool_name}")

        result = handler(tool_input)

        self._hooks.dispatch("PostToolUse", {
            "tool_name": tool_name, "tool_input": tool_input,
            "tool_output": result.output[:1000],
        })

        return result

    def available_tools(self) -> list[str]:
        """List registered tool names."""
        return sorted(_TOOL_REGISTRY.keys())


# --- Tool Implementations (absorbed from TF++ src/tools/) ---

def _tool_bash(args: dict) -> ToolResult:
    """Execute a shell command. Absorbed from TF++ BashTool."""
    command = args.get("command", "")
    timeout = min(args.get("timeout", 120000), 600000) / 1000

    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=args.get("cwd"),
        )
        output = proc.stdout
        if proc.stderr:
            output += f"\n{proc.stderr}" if output else proc.stderr
        if proc.returncode != 0:
            return ToolResult(output=output, error=f"Exit code {proc.returncode}")
        return ToolResult(output=output)
    except subprocess.TimeoutExpired:
        return ToolResult(error=f"Timeout after {timeout}s")
    except OSError as e:
        return ToolResult(error=str(e))


def _tool_read(args: dict) -> ToolResult:
    """Read a file. Absorbed from TF++ FileReadTool."""
    path = args.get("file_path", "")
    offset = args.get("offset", 0)
    limit = args.get("limit", 2000)

    if not os.path.isfile(path):
        return ToolResult(error=f"File not found: {path}")

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        selected = lines[offset:offset + limit]
        numbered = "".join(
            f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)
        )
        return ToolResult(output=numbered)
    except OSError as e:
        return ToolResult(error=str(e))


def _tool_write(args: dict) -> ToolResult:
    """Write a file. Absorbed from TF++ FileWriteTool."""
    path = args.get("file_path", "")
    content = args.get("content", "")

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(output=f"Written to {path}")
    except OSError as e:
        return ToolResult(error=str(e))


def _tool_edit(args: dict) -> ToolResult:
    """Edit a file via string replacement. Absorbed from TF++ FileEditTool."""
    path = args.get("file_path", "")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    replace_all = args.get("replace_all", False)

    if not os.path.isfile(path):
        return ToolResult(error=f"File not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if old not in content:
            return ToolResult(error="old_string not found in file")

        if replace_all:
            updated = content.replace(old, new)
        else:
            updated = content.replace(old, new, 1)

        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        return ToolResult(output=f"Updated {path}")
    except OSError as e:
        return ToolResult(error=str(e))


def _tool_glob(args: dict) -> ToolResult:
    """Find files by pattern. Absorbed from TF++ GlobTool."""
    pattern = args.get("pattern", "")
    path = args.get("path", ".")

    full_pattern = os.path.join(path, pattern)
    matches = sorted(glob_mod.glob(full_pattern, recursive=True))
    return ToolResult(output="\n".join(matches))


def _tool_grep(args: dict) -> ToolResult:
    """Search file contents. Absorbed from TF++ GrepTool."""
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    file_type = args.get("type", "")

    cmd = ["rg", "--no-heading", pattern, path]
    if file_type:
        cmd.extend(["--type", file_type])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return ToolResult(output=proc.stdout[:10000])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fallback to Python regex if rg not available
        return _grep_fallback(pattern, path)


def _grep_fallback(pattern: str, path: str) -> ToolResult:
    """Pure Python grep fallback when rg is not installed."""
    # Collect files first (O(n)), then search each (O(m)) — total O(n+m)
    files = _collect_files(path, max_files=5000)
    regex = re.compile(pattern)
    matches = []
    for fpath in files:
        _grep_single_file(fpath, regex, matches)
        if len(matches) >= 250:
            break
    return ToolResult(output="\n".join(matches))


def _collect_files(path: str, max_files: int) -> list[str]:
    """Collect file paths up to a maximum. O(n) single pass."""
    result = []
    for root, _, names in os.walk(path):
        result.extend(os.path.join(root, n) for n in names)
        if len(result) >= max_files:
            return result[:max_files]
    return result


def _grep_single_file(fpath: str, regex: "re.Pattern", matches: list) -> None:
    """Search one file for regex matches. Appends to matches list."""
    try:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if regex.search(line):
                    matches.append(f"{fpath}:{i}:{line.rstrip()}")
                    if len(matches) >= 250:
                        return
    except OSError:
        pass


# Register all tools
_TOOL_REGISTRY.update({
    "Bash": _tool_bash,
    "Read": _tool_read,
    "Write": _tool_write,
    "Edit": _tool_edit,
    "Glob": _tool_glob,
    "Grep": _tool_grep,
})
