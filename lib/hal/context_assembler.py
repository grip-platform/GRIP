"""HAL Context Assembler — builds the full context for any LLM runtime.

Assembles: CLAUDE.md chain -> mode prompt injection -> skill prompts ->
git status -> memory -> ContextBundle. Runtime-agnostic.

Usage:
    from lib.hal.context_assembler import ContextAssembler

    assembler = ContextAssembler()
    bundle = assembler.assemble(mode="code")
    print(bundle.system_prompt[:500])
"""
import os
import subprocess
from typing import Optional

from lib.hal.memory_engine import MemoryEngine
from lib.hal.skill_loader import SkillLoader

from lib.hal.home import HAL_HOME as _HAL_HOME
_MAX_GIT_STATUS_CHARS = 2000


class ContextBundle:
    """Assembled context ready for LLM injection."""

    def __init__(
        self,
        system_prompt: str = "",
        claude_md_chain: Optional[list[str]] = None,
        mode_name: str = "",
        mode_prompt: str = "",
        skill_names: Optional[list[str]] = None,
        git_branch: str = "",
        git_status: str = "",
        git_recent_commits: str = "",
        memory_entrypoint: str = "",
    ):
        self.system_prompt = system_prompt
        self.claude_md_chain = claude_md_chain or []
        self.mode_name = mode_name
        self.mode_prompt = mode_prompt
        self.skill_names = skill_names or []
        self.git_branch = git_branch
        self.git_status = git_status
        self.git_recent_commits = git_recent_commits
        self.memory_entrypoint = memory_entrypoint


class ContextAssembler:
    """Assembles full context from all GRIP sources."""

    def __init__(
        self,
        skill_loader: Optional[SkillLoader] = None,
        memory_engine: Optional[MemoryEngine] = None,
        project_dir: Optional[str] = None,
    ):
        self._skills = skill_loader or SkillLoader()
        self._memory = memory_engine or MemoryEngine()
        self._project_dir = project_dir or os.getcwd()

    def assemble(self, mode: str = "") -> ContextBundle:
        """Assemble the full context bundle for a given mode."""
        claude_md_chain = self._load_claude_md_chain()
        mode_def = self._skills.get_mode(mode) if mode else None
        skill_names = self._skills.resolve_for_mode(mode) if mode else []
        memory_state = self._memory.read_memory()
        git_info = _get_git_info(self._project_dir)

        mode_prompt = ""
        if mode_def and hasattr(mode_def, "prompt_injection"):
            mode_prompt = getattr(mode_def, "prompt_injection", "")

        system_prompt = _build_system_prompt(
            claude_md_chain, mode, mode_prompt,
            skill_names, memory_state.entrypoint, git_info,
        )

        return ContextBundle(
            system_prompt=system_prompt,
            claude_md_chain=claude_md_chain,
            mode_name=mode,
            mode_prompt=mode_prompt,
            skill_names=skill_names,
            git_branch=git_info.get("branch", ""),
            git_status=git_info.get("status", ""),
            git_recent_commits=git_info.get("commits", ""),
            memory_entrypoint=memory_state.entrypoint,
        )

    def _load_claude_md_chain(self) -> list[str]:
        """Load CLAUDE.md from project -> user -> global (CC convention)."""
        chain = []
        paths = [
            os.path.join(self._project_dir, ".claude", "CLAUDE.md"),
            os.path.join(self._project_dir, "CLAUDE.md"),
            os.path.join(_HAL_HOME, "CLAUDE.md"),
        ]
        for path in paths:
            content = _read_file(path)
            if content:
                chain.append(content)
        return chain


def _build_system_prompt(
    claude_md_chain: list[str],
    mode: str,
    mode_prompt: str,
    skill_names: list[str],
    memory: str,
    git_info: dict,
) -> str:
    """Build the assembled system prompt from all sources."""
    sections = []

    for content in claude_md_chain:
        sections.append(content)

    if mode and mode_prompt:
        sections.append(f"[MODE: {mode.upper()}]\n{mode_prompt}")

    if skill_names:
        sections.append(f"Active skills: {', '.join(skill_names)}")

    branch = git_info.get("branch", "")
    status = git_info.get("status", "")
    commits = git_info.get("commits", "")
    if branch:
        git_section = f"Git: {branch}"
        if status:
            git_section += f"\n{status}"
        if commits:
            git_section += f"\nRecent: {commits}"
        sections.append(git_section)

    if memory:
        sections.append(f"Memory:\n{memory[:2000]}")

    return "\n\n---\n\n".join(sections)


def _get_git_info(project_dir: str) -> dict:
    """Collect git branch, status, and recent commits."""
    result = {}

    result["branch"] = _run_git(project_dir, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    raw_status = _run_git(project_dir, ["git", "status", "--short"])
    result["status"] = raw_status[:_MAX_GIT_STATUS_CHARS] if raw_status else ""
    result["commits"] = _run_git(project_dir, ["git", "log", "--oneline", "-5"])

    return result


def _run_git(cwd: str, cmd: list[str]) -> str:
    """Run a git command and return stdout, empty string on failure."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=cwd,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""
