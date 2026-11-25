#!/usr/bin/env python3
"""
grip_complete.py — Shell completion generator for GRIP commands.

Generates zsh or bash completion scripts from live GRIP infrastructure:
  - Skills (from skills/ directories)
  - Modes (from modes/definitions/*.yaml)
  - Commands (from settings.json slash commands)

Usage:
    python3 lib/grip_complete.py zsh     # Output zsh completion script
    python3 lib/grip_complete.py bash    # Output bash completion script
    python3 lib/grip_complete.py list    # List all completable commands

Install (zsh):
    eval "$(python3 ~/.claude/lib/grip_complete.py zsh)"

Install (bash):
    eval "$(python3 ~/.claude/lib/grip_complete.py bash)"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


def get_modes() -> list[str]:
    """Get available mode names from definitions."""
    modes_dir = CLAUDE_DIR / "modes" / "definitions"
    if not modes_dir.exists():
        return []
    return sorted(p.stem for p in modes_dir.glob("*.yaml"))


def get_skills() -> list[str]:
    """Get available skill names from skill directories."""
    skills_dir = CLAUDE_DIR / "skills"
    if not skills_dir.exists():
        return []
    return sorted(
        p.name for p in skills_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
        and (p / "SKILL.md").exists()
    )


def get_slash_commands() -> list[str]:
    """Get slash commands from settings.json."""
    settings_file = CLAUDE_DIR / "settings.json"
    if not settings_file.exists():
        return []
    try:
        data = json.loads(settings_file.read_text())
        commands = data.get("slashCommands", {})
        return sorted(commands.keys())
    except (json.JSONDecodeError, OSError):
        return []


def get_grip_subcommands() -> list[str]:
    """Get grip CLI subcommands."""
    return ["resume", "list", "fresh", "inspect", "doctor", "config"]


def generate_zsh() -> str:
    """Generate zsh completion script."""
    modes = get_modes()
    skills = get_skills()
    commands = get_slash_commands()
    grip_subs = get_grip_subcommands()

    mode_names = " ".join(m.replace("-mode", "") for m in modes)
    skill_names = " ".join(skills)
    command_names = " ".join(commands)
    grip_sub_names = " ".join(grip_subs)

    return f'''# GRIP zsh completions — auto-generated
# eval "$(python3 ~/.claude/lib/grip_complete.py zsh)"

_grip_modes=({mode_names})
_grip_skills=({skill_names})
_grip_commands=({command_names})
_grip_subcommands=({grip_sub_names})

_grip_complete_mode() {{
    compadd -a _grip_modes
}}

_grip_complete_skill() {{
    compadd -a _grip_skills
}}

_grip_complete_command() {{
    compadd -a _grip_commands
}}

_grip_complete() {{
    local -a subcmds
    subcmds=($_grip_subcommands)
    if (( CURRENT == 2 )); then
        compadd -a subcmds
    elif [[ "${{words[2]}}" == "resume" ]] && (( CURRENT == 3 )); then
        compadd latest gen
    fi
}}

compdef _grip_complete grip
compdef _grip_complete_mode mode
compdef _grip_complete_command converge
'''


def generate_bash() -> str:
    """Generate bash completion script."""
    modes = get_modes()
    skills = get_skills()
    commands = get_slash_commands()
    grip_subs = get_grip_subcommands()

    mode_names = " ".join(m.replace("-mode", "") for m in modes)
    grip_sub_names = " ".join(grip_subs)

    return f'''# GRIP bash completions — auto-generated
# eval "$(python3 ~/.claude/lib/grip_complete.py bash)"

_grip_complete() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local subcmds="{grip_sub_names}"
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$subcmds" -- "$cur"))
    elif [[ "${{COMP_WORDS[1]}}" == "resume" ]] && [[ $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=($(compgen -W "latest gen" -- "$cur"))
    fi
}}

_grip_mode_complete() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local modes="{mode_names}"
    COMPREPLY=($(compgen -W "$modes" -- "$cur"))
}}

complete -F _grip_complete grip
complete -F _grip_mode_complete mode
'''


def list_all() -> None:
    """Print all completable commands."""
    modes = get_modes()
    skills = get_skills()
    commands = get_slash_commands()

    print(f"Modes ({len(modes)}):")
    for m in modes:
        print(f"  {m.replace('-mode', '')}")

    print(f"\nSkills ({len(skills)}):")
    for s in skills:
        print(f"  {s}")

    print(f"\nSlash commands ({len(commands)}):")
    for c in commands:
        print(f"  /{c}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: grip_complete.py <zsh|bash|list>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "zsh":
        print(generate_zsh())
    elif cmd == "bash":
        print(generate_bash())
    elif cmd == "list":
        list_all()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
