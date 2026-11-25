#!/usr/bin/env python3
"""
GRIP Context Miner - Mine context from multiple sources for fresh sessions.

When no serialized GRIP sessions exist, this module extracts context from:
- Raw JSONL session files (Claude's native format)
- Git context (recent commits, branch)
- Project structure (package.json, pyproject.toml, README)
- Project .claude/CLAUDE.md

Every file and git read names UTF-8. On Windows the default is cp1252, which
cannot decode most transcripts, and the broad excepts here turned that into
silently missing context.

VERSION: 1.0.0
DATE: 2025-12-29
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime


class ContextMiner:
    """Mine context from multiple sources for fresh sessions."""

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.encoded_path = self._encode_project_path(project_path)
        self.claude_projects_dir = Path.home() / ".claude" / "projects" / self.encoded_path

    def _encode_project_path(self, path: Path) -> str:
        """Encode project path to Claude's format: /a/b -> -a-b"""
        return str(path).replace("/", "-").replace(".", "-")

    def mine(self) -> Optional[str]:
        """Return compressed context from all available sources."""
        context_parts = []

        # 1. Mine raw JSONL session files
        jsonl_context = self._mine_jsonl_files()
        if jsonl_context:
            context_parts.append(("Session History", jsonl_context))

        # 2. Mine git context (recent commits, branch)
        git_context = self._mine_git_context()
        if git_context:
            context_parts.append(("Git Context", git_context))

        # 4. Analyze project structure (package.json, README)
        project_context = self._mine_project_structure()
        if project_context:
            context_parts.append(("Project Structure", project_context))

        # 5. Check project .claude/CLAUDE.md
        claude_md = self._mine_project_claude_md()
        if claude_md:
            context_parts.append(("Project Rules", claude_md))

        if not context_parts:
            return None

        return self._format_context(context_parts)

    def _mine_jsonl_files(self) -> Optional[str]:
        """Extract context from Claude's raw JSONL session files."""
        # Look in Claude's project directory
        if not self.claude_projects_dir.exists():
            return None

        # Find all JSONL files (exclude agent sessions)
        jsonl_files = []
        for f in self.claude_projects_dir.glob("*.jsonl"):
            if "agent" not in f.stem.lower():
                jsonl_files.append(f)

        if not jsonl_files:
            return None

        # Sort by modification time, newest first
        jsonl_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Extract key information from most recent session(s)
        context_items = []
        max_files = 3  # Process up to 3 most recent sessions

        for jsonl_file in jsonl_files[:max_files]:
            try:
                messages = self._parse_jsonl(jsonl_file)
                if messages:
                    summary = self._summarize_session(messages, jsonl_file)
                    if summary:
                        context_items.append(summary)
            except Exception:
                continue

        if not context_items:
            return None

        return "\n\n".join(context_items)

    def _parse_jsonl(self, jsonl_file: Path) -> List[Dict[str, Any]]:
        """Parse JSONL file into list of message dicts.

        errors='replace' turns a torn last line into one unparseable line,
        which the JSONDecodeError branch skips.
        """
        messages = []
        try:
            with open(jsonl_file, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return messages

    @staticmethod
    def _collect_assistant_items(content: Any, tool_calls: List[str],
                                 responses: List[str]) -> None:
        """Tool names and text openings from one assistant message's content."""
        if not isinstance(content, list):
            return
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                tool_calls.append(item.get("name", "unknown"))
            elif item.get("type") == "text":
                text = item.get("text", "")[:200]
                if text:
                    responses.append(text)

    def _summarize_session(self, messages: List[Dict], source_file: Path) -> Optional[str]:
        """Summarize a session's key information."""
        if not messages:
            return None

        # Extract key facts
        user_messages = []
        assistant_responses = []
        tool_calls = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user" and isinstance(content, str):
                # First 200 chars of user messages
                user_messages.append(content[:200])
            elif role == "assistant":
                self._collect_assistant_items(content, tool_calls, assistant_responses)

        # Build summary
        mtime = datetime.fromtimestamp(source_file.stat().st_mtime)
        parts = [f"Session from {mtime.strftime('%Y-%m-%d %H:%M')}:"]

        if user_messages:
            parts.append(f"- Topics: {user_messages[0][:100]}...")

        if tool_calls:
            unique_tools = list(set(tool_calls))[:5]
            parts.append(f"- Tools used: {', '.join(unique_tools)}")

        parts.append(f"- Message count: {len(messages)}")

        return "\n".join(parts)

    def _git(self, *args: str) -> Optional[str]:
        """Stripped stdout of one git command in the project, or None on failure.

        git writes UTF-8; decoding it with Windows' cp1252 default can raise on
        a commit message, so the encoding is named.
        """
        result = subprocess.run(
            ["git", *args],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _mine_git_context(self) -> Optional[str]:
        """Extract git context: branch, last 5 commits, up to 10 changed files."""
        try:
            if self._git("rev-parse", "--git-dir") is None:
                return None

            context_parts = []
            branch = self._git("branch", "--show-current")
            if branch is not None:
                context_parts.append(f"Branch: {branch}")

            log = self._git("log", "--oneline", "-5")
            if log:
                context_parts.append(f"Recent commits:\n{log}")

            status = self._git("status", "--short")
            if status:
                files = status.split('\n')[:10]  # Max 10 files
                context_parts.append("Modified files:\n" + "\n".join(files))

            return "\n".join(context_parts) if context_parts else None

        except Exception:
            return None

    def _mine_project_structure(self) -> Optional[str]:
        """Extract project structure context."""
        structure_parts = []

        # Check package.json
        package_json = self.project_path / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding='utf-8'))
                name = data.get("name", "unknown")
                desc = data.get("description", "")[:100]
                deps = list(data.get("dependencies", {}).keys())[:10]
                structure_parts.append(f"Node project: {name}")
                if desc:
                    structure_parts.append(f"Description: {desc}")
                if deps:
                    structure_parts.append(f"Key deps: {', '.join(deps)}")
            except Exception:
                pass

        # Check pyproject.toml
        pyproject = self.project_path / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding='utf-8')
                # Simple extraction without toml library
                if 'name = "' in content:
                    name = content.split('name = "')[1].split('"')[0]
                    structure_parts.append(f"Python project: {name}")
            except Exception:
                pass

        # Check README
        readme_files = ["README.md", "README.txt", "README"]
        for readme_name in readme_files:
            readme = self.project_path / readme_name
            if readme.exists():
                try:
                    content = readme.read_text(encoding='utf-8')[:500]
                    # Get first paragraph
                    first_para = content.split('\n\n')[0][:200]
                    structure_parts.append(f"README excerpt: {first_para}")
                    break
                except Exception:
                    pass

        return "\n".join(structure_parts) if structure_parts else None

    def _mine_project_claude_md(self) -> Optional[str]:
        """Extract project-specific Claude rules."""
        claude_md = self.project_path / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            try:
                content = claude_md.read_text(encoding='utf-8')[:1500]  # Max 1.5k chars
                return f"Project rules loaded from .claude/CLAUDE.md"
            except Exception:
                pass
        return None

    def _format_context(self, parts: List[tuple]) -> str:
        """Format all context parts into a single document."""
        lines = [
            "# GRIP Context (Mined)",
            "",
            "Context reconstructed from available sources.",
            "",
        ]

        for section_name, content in parts:
            lines.append(f"## {section_name}")
            lines.append("")
            lines.append(content)
            lines.append("")

        lines.append("---")
        lines.append("Context mined by GRIP.")

        return "\n".join(lines)


def main():
    """CLI entry point."""
    import argparse
    from _compat import ensure_utf8_stdio

    # bin/grip redirects this output into resurrection.md. A Windows console
    # gives Python a cp1252 stdout, which cannot print most transcript text.
    ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="GRIP Context Miner")
    parser.add_argument("command", choices=["mine"], help="Command to run")
    parser.add_argument("--project", type=str, default=".", help="Project path")

    args = parser.parse_args()

    project_path = Path(args.project).resolve()
    miner = ContextMiner(project_path)

    if args.command == "mine":
        context = miner.mine()
        if context:
            print(context)
            sys.exit(0)
        else:
            print("No context sources found", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
