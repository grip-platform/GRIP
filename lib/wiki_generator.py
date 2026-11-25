"""
GRIP Wiki Auto-Generator — builds GitHub wiki pages from codebase.

Scans skills, modes, agents, commands, and docs to generate a complete
wiki for the GRIP GitHub repository. Pushes updates via git.

Usage:
    python3 lib/wiki_generator.py generate [--output-dir DIR]
    python3 lib/wiki_generator.py push [--output-dir DIR]
    python3 lib/wiki_generator.py preview             # Print page list
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")
WIKI_CLONE_DIR = Path(tempfile.gettempdir()) / "grip-wiki-clone"


@dataclass
class SkillInfo:
    """Parsed skill metadata."""
    name: str
    description: str = ""
    version: str = ""
    triggers: list = field(default_factory=list)
    status: str = "Active"
    path: str = ""


@dataclass
class ModeInfo:
    """Parsed mode metadata."""
    name: str
    description: str = ""
    version: str = ""
    triggers: list = field(default_factory=list)
    skills_always: list = field(default_factory=list)
    skills_optional: list = field(default_factory=list)


@dataclass
class AgentInfo:
    """Parsed agent metadata."""
    name: str
    description: str = ""
    model: str = ""
    tools: list = field(default_factory=list)
    triggers: list = field(default_factory=list)


@dataclass
class CommandInfo:
    """Parsed command metadata."""
    name: str
    description: str = ""
    title: str = ""

@dataclass
class LibraryInfo:
    """Parsed library metadata."""
    name: str
    description: str = ""
    functions: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    category: str = "Other"
    path: str = ""


def parse_yaml_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def scan_skills() -> list[SkillInfo]:
    """Scan skills/ directory for skill metadata."""
    skills_dir = CLAUDE_DIR / "skills"
    if not skills_dir.is_dir():
        return []

    skills = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.exists():
            skill_file = entry / "SKILL.grip"
            if not skill_file.exists():
                continue

        try:
            content = skill_file.read_text(errors="replace")
        except OSError:
            continue

        fm = parse_yaml_frontmatter(content)
        skills.append(SkillInfo(
            name=fm.get("name", entry.name),
            description=fm.get("description", ""),
            version=fm.get("version", ""),
            triggers=fm.get("triggers", []),
            status=fm.get("status", "Active"),
            path=str(entry.relative_to(CLAUDE_DIR)),
        ))

    return skills


def scan_modes() -> list[ModeInfo]:
    """Scan modes/definitions/ for mode YAML definitions."""
    modes_dir = CLAUDE_DIR / "modes" / "definitions"
    if not modes_dir.is_dir():
        return []

    modes = []
    for yaml_file in sorted(modes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(errors="replace"))
        except (yaml.YAMLError, OSError):
            continue
        if not data:
            continue

        skills_section = data.get("skills", {})
        modes.append(ModeInfo(
            name=data.get("name", yaml_file.stem.replace("-mode", "")),
            description=data.get("description", ""),
            version=data.get("version", ""),
            triggers=data.get("triggers", []),
            skills_always=skills_section.get("always", []) if isinstance(skills_section, dict) else [],
            skills_optional=skills_section.get("optional", []) if isinstance(skills_section, dict) else [],
        ))

    return modes


def scan_agents() -> list[AgentInfo]:
    """Scan agents/ directory for agent definitions."""
    agents_dir = CLAUDE_DIR / "agents"
    if not agents_dir.is_dir():
        return []

    agents = []
    for md_file in sorted(agents_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue

        try:
            content = md_file.read_text(errors="replace")
        except OSError:
            continue

        fm = parse_yaml_frontmatter(content)
        if not fm:
            continue

        agents.append(AgentInfo(
            name=fm.get("name", md_file.stem),
            description=fm.get("description", ""),
            model=fm.get("model", ""),
            tools=fm.get("tools", []),
            triggers=fm.get("triggers", []),
        ))

    return agents


def scan_commands() -> list[CommandInfo]:
    """Scan commands/ directory for command definitions."""
    commands_dir = CLAUDE_DIR / "commands"
    if not commands_dir.is_dir():
        return []

    commands = []
    for md_file in sorted(commands_dir.glob("*.md")):
        if md_file.name == "CLAUDE.md":
            continue

        try:
            content = md_file.read_text(errors="replace")
        except OSError:
            continue

        fm = parse_yaml_frontmatter(content)

        # Extract title from first # heading if no frontmatter
        title = ""
        for line in content.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        commands.append(CommandInfo(
            name=md_file.stem,
            description=fm.get("description", ""),
            title=title,
        ))

    return commands



# User-facing libraries to document (curated list)
USER_FACING_LIBRARIES = {
    "smart_commit": ("Git Workflow", "AI-powered commit message generation"),
    "review_checklist": ("Git Workflow", "PR review checklists"),
    "pr_review_generator": ("Git Workflow", "Interactive PR review"),
    "pr_impact_analyzer": ("Git Workflow", "PR risk scoring and blast radius"),
    "changelog_generator": ("Git Workflow", "Changelog generation from git history"),
    "release_notes": ("Git Workflow", "Release notes composer"),
    "deliverable_generator": ("Reporting", "Standup/weekly/sprint deliverable reports"),
    "session_achievements": ("Reporting", "Session productivity scoring"),
    "sprint_velocity": ("Reporting", "Sprint velocity tracking and trends"),
    "code_health_dashboard": ("Code Quality", "Code health scoring dashboard"),
    "complexity_profiler": ("Code Quality", "AST-based complexity analysis"),
    "doc_coverage": ("Code Quality", "Documentation coverage scanner"),
    "contributor_insights": ("Analytics", "Git contributor analytics and bus factor"),
    "tech_debt_mapper": ("Code Quality", "Tech debt detection and mapping"),
    "code_storyteller": ("Analytics", "Narrative git history"),
    "sprint_retro": ("Reporting", "Auto sprint retrospectives"),
    "routine_predictor": ("Analytics", "Developer routine pattern detection"),
    "architecture_explorer": ("Code Quality", "Architecture visualization"),
    "mcp_app_generator": ("Integration", "MCP app data generators (21 generators)"),
}


def scan_libraries() -> list[LibraryInfo]:
    """Scan lib/ directory for user-facing libraries."""
    lib_dir = CLAUDE_DIR / "lib"
    if not lib_dir.is_dir():
        return []

    import ast as _ast

    libraries = []
    for name, (category, fallback_desc) in sorted(USER_FACING_LIBRARIES.items()):
        lib_file = lib_dir / f"{name}.py"
        if not lib_file.exists():
            continue

        try:
            source = lib_file.read_text(errors="replace")
            tree = _ast.parse(source)
        except (OSError, SyntaxError):
            libraries.append(LibraryInfo(
                name=name, description=fallback_desc,
                category=category, path=f"lib/{name}.py",
            ))
            continue

        module_doc = _ast.get_docstring(tree) or fallback_desc
        # Take first sentence/line
        desc = module_doc.split("\n")[0].strip()
        if len(desc) > 120:
            desc = desc[:117] + "..."

        funcs = []
        classes = []
        for node in _ast.iter_child_nodes(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    fdoc = _ast.get_docstring(node) or ""
                    short = fdoc.split("\n")[0][:80] if fdoc else ""
                    funcs.append(f"{node.name}() - {short}" if short else f"{node.name}()")
            elif isinstance(node, _ast.ClassDef):
                cdoc = _ast.get_docstring(node) or ""
                short = cdoc.split("\n")[0][:80] if cdoc else ""
                classes.append(f"{node.name} - {short}" if short else node.name)

        libraries.append(LibraryInfo(
            name=name, description=desc,
            functions=funcs, classes=classes,
            category=category, path=f"lib/{name}.py",
        ))

    return libraries

def generate_home(skills: list, modes: list, agents: list, commands: list, libraries: list | None = None) -> str:
    """Generate the wiki Home page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# GRIP - Claude Code Enhancement Framework",
        "",
        "Session continuity, efficiency protocols, and safety gates for Claude Code.",
        "",
        f"> Auto-generated on {now} from the GRIP codebase.",
        "",
        "## Quick Stats",
        "",
        f"| Component | Count |",
        f"|-----------|-------|",
        f"| Skills | {len(skills)} |",
        f"| Modes | {len(modes)} |",
        f"| Agents | {len(agents)} |",
        f"| Commands | {len(commands)} |",
        f"| Libraries | {len(libraries or [])} |",
        "",
        "## Navigation",
        "",
        "- [Skills Catalog](Skills) - All available skills",
        "- [Operating Modes](Modes) - Context-specific mode definitions",
        "- [Agents](Agents) - Autonomous task agents",
        "- [Commands](Commands) - Slash commands reference",
        "- [Architecture](Architecture) - System design and key docs",
        "- [Getting Started](Getting-Started) - Setup and onboarding",
        "- [Safety Gates](Safety-Gates) - Deterministic safety enforcement",
        "- [Libraries](Libraries) - User-facing tools and report generators",
        "",
        "## Core Concepts",
        "",
        "### Skills",
        "Passive reference protocols loaded as context. Skills define *what* to do.",
        "",
        "### Modes",
        "Operating contexts that load domain-specific skill sets. "
        "Activate with `/mode <name>`.",
        "",
        "### Agents",
        "Active execution units with isolated context and tool access. "
        "Agents implement *how* to do it.",
        "",
        "### Commands",
        "Slash commands (`/command`) that invoke skills or workflows.",
        "",
        "### Safety Gates",
        "Deterministic pre-tool-call checks that enforce security, "
        "dependency, and operational constraints.",
        "",
        "## Design Principles",
        "",
        "GRIP follows: **SOLID**, **GRASP**, **DRY**, **KISS**, **YAGNI**, **YSH**",
        "",
        "- **YSH (You Should Have)**: When a pattern is proven 2x, abstract NOW (first build with a human in the loop).",
        "- Token budget: 1M per session (Opus 4.7 default since April 2026; Opus 4.6 / Sonnet 4.6 since March 2026).",
        "",
    ]
    return "\n".join(lines)


def generate_skills_page(skills: list[SkillInfo]) -> str:
    """Generate the Skills catalog page."""
    lines = [
        "# Skills Catalog",
        "",
        f"{len(skills)} skills available in GRIP.",
        "",
        "| Skill | Description | Status |",
        "|-------|-------------|--------|",
    ]
    for s in skills:
        desc = (s.description[:80] + "...") if len(s.description) > 80 else s.description
        lines.append(f"| `{s.name}` | {desc} | {s.status} |")

    # Group by category
    lines.extend([
        "",
        "## Skill Categories",
        "",
    ])

    categories = {
        "Development": [],
        "Modes": [],
        "Safety": [],
        "Learning": [],
        "Documentation": [],
        "Other": [],
    }

    mode_keywords = {"mode", "brainstorm", "strategy", "legal", "marketing",
                     "research", "architect", "analysis", "security", "review",
                     "writing", "synthesis", "decision", "planning", "teaching",
                     "testing", "business", "learning", "demo", "create",
                     "automation", "upskilling", "incognito", "pair", "flow"}
    safety_keywords = {"gate", "guard", "safety", "sanitiz", "security",
                       "deployment", "hardening"}
    learning_keywords = {"learn", "recursive", "improvement", "self-improvement",
                         "rsi", "insight", "forging"}
    doc_keywords = {"doc", "wiki", "markdown", "writing", "pdf", "article"}
    dev_keywords = {"code", "pr", "git", "test", "feature", "ui", "api",
                    "figma", "e2e", "agentic", "branch", "ci", "node",
                    "mcp", "remotion"}

    for s in skills:
        name_lower = s.name.lower()
        desc_lower = s.description.lower()
        combined = f"{name_lower} {desc_lower}"

        if any(k in combined for k in mode_keywords) and "mode" in name_lower:
            categories["Modes"].append(s)
        elif any(k in combined for k in safety_keywords):
            categories["Safety"].append(s)
        elif any(k in combined for k in learning_keywords):
            categories["Learning"].append(s)
        elif any(k in combined for k in doc_keywords):
            categories["Documentation"].append(s)
        elif any(k in combined for k in dev_keywords):
            categories["Development"].append(s)
        else:
            categories["Other"].append(s)

    for cat, cat_skills in categories.items():
        if cat_skills:
            lines.append(f"### {cat} ({len(cat_skills)})")
            lines.append("")
            for s in cat_skills:
                desc = s.description[:100] if s.description else "No description"
                lines.append(f"- **{s.name}**: {desc}")
            lines.append("")

    return "\n".join(lines)


def generate_modes_page(modes: list[ModeInfo]) -> str:
    """Generate the Modes page."""
    lines = [
        "# Operating Modes",
        "",
        f"{len(modes)} modes available. Activate with `/mode <name>`.",
        "",
        "| Mode | Description |",
        "|------|-------------|",
    ]
    for m in modes:
        desc = (m.description[:80] + "...") if len(m.description) > 80 else m.description
        lines.append(f"| `{m.name}` | {desc} |")

    lines.extend(["", "## Mode Details", ""])

    for m in modes:
        lines.append(f"### {m.name}")
        lines.append("")
        if m.description:
            lines.append(m.description)
            lines.append("")
        if m.skills_always:
            lines.append("**Always-loaded skills:**")
            for s in m.skills_always:
                lines.append(f"- `{s}`")
            lines.append("")
        if m.skills_optional:
            lines.append("**Optional skills:**")
            for s in m.skills_optional:
                lines.append(f"- `{s}`")
            lines.append("")
        if m.triggers:
            triggers_str = ", ".join(f"`{t}`" for t in m.triggers[:5])
            lines.append(f"**Triggers:** {triggers_str}")
            lines.append("")

    return "\n".join(lines)


def generate_agents_page(agents: list[AgentInfo]) -> str:
    """Generate the Agents page."""
    lines = [
        "# Agents",
        "",
        f"{len(agents)} specialized agents for autonomous task execution.",
        "",
        "| Agent | Description | Model |",
        "|-------|-------------|-------|",
    ]
    for a in agents:
        desc = (a.description[:70] + "...") if len(a.description) > 70 else a.description
        lines.append(f"| `{a.name}` | {desc} | {a.model or 'default'} |")

    lines.extend(["", "## Agent Details", ""])

    for a in agents:
        lines.append(f"### {a.name}")
        lines.append("")
        if a.description:
            lines.append(a.description)
            lines.append("")
        if a.tools:
            tools_str = ", ".join(f"`{t}`" for t in a.tools)
            lines.append(f"**Tools:** {tools_str}")
            lines.append("")
        if a.triggers:
            triggers_str = ", ".join(f"`{t}`" for t in a.triggers[:5])
            lines.append(f"**Triggers:** {triggers_str}")
            lines.append("")

    return "\n".join(lines)


def generate_commands_page(commands: list[CommandInfo]) -> str:
    """Generate the Commands page."""
    lines = [
        "# Commands Reference",
        "",
        f"{len(commands)} slash commands available.",
        "",
        "| Command | Description |",
        "|---------|-------------|",
    ]
    for c in commands:
        desc = c.description or c.title or "No description"
        if len(desc) > 80:
            desc = desc[:80] + "..."
        lines.append(f"| `/{c.name}` | {desc} |")

    return "\n".join(lines)


def generate_architecture_page() -> str:
    """Generate the Architecture page from docs/."""
    docs_dir = CLAUDE_DIR / "docs"
    if not docs_dir.is_dir():
        return "# Architecture\n\nNo documentation files found."

    lines = [
        "# Architecture",
        "",
        "Key architecture and design documents.",
        "",
    ]

    # Categorize docs
    key_docs = {
        "Core": ["HOOKS-AND-GATES-GUIDE.md", "HOOKS-ARCHITECTURE.md",
                 "SAFETY-GATES.md", "INFORMATION-ARCHITECTURE.md"],
        "Memory": ["MEMORY-ARCHITECTURE.md", "MEMORY-GUIDE.md",
                   "KONO-USAGE-GUIDE.md", "EMBEDDINGS.md"],
        "Agents": ["BROLY-ARCHITECTURE.md", "BROLY-GUIDE.md"],
        "Copilot": ["COPILOT-ARCHITECTURE.md"],
        "Context": ["ACE-ARCHITECTURE.md"],
        "Guides": ["MODES-AND-SKILLS-GUIDE.md", "LAUNCH-METHODS.md",
                   "MCP_SETUP.md", "MOBILE-GRIP-SETUP.md"],
    }

    for category, doc_names in key_docs.items():
        existing = [d for d in doc_names if (docs_dir / d).exists()]
        if existing:
            lines.append(f"## {category}")
            lines.append("")
            for doc_name in existing:
                title = doc_name.replace(".md", "").replace("-", " ").title()
                lines.append(f"- [{title}](https://github.com/{REPO_SLUG}/blob/main/docs/{doc_name})")
            lines.append("")

    # List remaining docs
    categorized = set()
    for names in key_docs.values():
        categorized.update(names)

    remaining = []
    for f in sorted(docs_dir.iterdir()):
        if f.suffix == ".md" and f.name not in categorized and not f.name.startswith("."):
            remaining.append(f.name)

    if remaining:
        lines.append("## Other Documentation")
        lines.append("")
        for doc_name in remaining:
            title = doc_name.replace(".md", "").replace("-", " ").title()
            lines.append(f"- [{title}](https://github.com/{REPO_SLUG}/blob/main/docs/{doc_name})")
        lines.append("")

    return "\n".join(lines)


def generate_getting_started() -> str:
    """Generate Getting Started page."""
    lines = [
        "# Getting Started with GRIP",
        "",
        "## Prerequisites",
        "",
        "- [Claude Code](https://claude.com/claude-code) CLI installed",
        "- GitHub CLI (`gh`) authenticated",
        "- Python 3.10+",
        "",
        "## Installation",
        "",
        "```bash",
        "# Clone GRIP into your Claude config",
        "git clone https://github.com/grip-platform/GRIP.git ~/.claude",
        "",
        "# Run the activation wizard",
        "/activate-grip",
        "```",
        "",
        "## First Steps",
        "",
        "1. **Activate**: Run `/activate-grip` to configure identity and preferences",
        "2. **Mode**: Try `/mode code` to load development skills",
        "3. **Context**: Use `/refresh-context` to build mental model of your project",
        "4. **PR**: Use `/create-pr` for automated pull request creation",
        "",
        "## Key Concepts",
        "",
        "### Session Continuity",
        "GRIP preserves context across sessions via instance serialization. "
        "Use `grip resume latest` to pick up where you left off.",
        "",
        "### Safety Gates",
        "10 deterministic gates run before every tool call:",
        "- Dependency folder blocking",
        "- File read caching",
        "- Git operation safety",
        "- Secret detection",
        "- MCP surface detection",
        "",
        "### Efficiency",
        "GRIP saves 60-70K tokens per session (30-35% of budget) through:",
        "- Batch file reads",
        "- Dependency guardian",
        "- Context-aware caching",
        "- Compact hook output",
        "",
        "## Further Reading",
        "",
        "- [Skills Catalog](Skills) - Browse all available skills",
        "- [Operating Modes](Modes) - Domain-specific modes",
        "- [Commands](Commands) - Slash command reference",
        "- [Architecture](Architecture) - System design docs",
        "",
    ]
    return "\n".join(lines)



def generate_libraries_page(libraries: list[LibraryInfo]) -> str:
    """Generate the Libraries documentation page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# User-Facing Libraries",
        "",
        f"{len(libraries)} libraries providing CLI tools, reports, and MCP app integrations.",
        "",
        f"> Auto-generated on {now}",
        "",
        "## Quick Reference",
        "",
        "| Library | Category | Description |",
        "|---------|----------|-------------|",
    ]
    for lib in libraries:
        desc = (lib.description[:70] + "...") if len(lib.description) > 70 else lib.description
        lines.append(f"| [`{lib.name}`](#{lib.name.replace('_', '-')}) | {lib.category} | {desc} |")

    # Group by category
    categories = {}
    for lib in libraries:
        categories.setdefault(lib.category, []).append(lib)

    cat_order = ["Git Workflow", "Code Quality", "Reporting", "Analytics", "Integration"]
    for cat in cat_order:
        cat_libs = categories.get(cat, [])
        if not cat_libs:
            continue

        lines.extend(["", f"## {cat}", ""])
        for lib in cat_libs:
            anchor = lib.name.replace("_", "-")
            lines.extend([
                f"### {lib.name}",
                "",
                lib.description,
                "",
                f"**Path:** `{lib.path}`",
                "",
            ])

            if lib.classes:
                lines.append("**Data classes:**")
                for cls in lib.classes[:5]:
                    lines.append(f"- `{cls}`")
                lines.append("")

            if lib.functions:
                lines.append("**Public API:**")
                for fn in lib.functions[:10]:
                    lines.append(f"- `{fn}`")
                if len(lib.functions) > 10:
                    lines.append(f"- ... and {len(lib.functions) - 10} more")
                lines.append("")

            lines.extend([
                "**Usage:**",
                "```bash",
                f"python3 lib/{lib.name}.py --help",
                "```",
                "",
                "---",
                "",
            ])

    # Output formats section
    lines.extend([
        "## Output Formats",
        "",
        "All libraries support multiple output formats:",
        "",
        "| Format | Flag | Use Case |",
        "|--------|------|----------|",
        "| Text | `--format text` | Terminal display |",
        "| Slack | `--format slack` | Slack mrkdwn messages |",
        "| JSON | `--format json` | Programmatic consumption |",
        "| Markdown | `--format markdown` | GitHub/docs |",
        "| MCP App | `--format mcp` | MCP App components |",
        "",
        "## MCP App Integration",
        "",
        "Libraries that produce MCP App data can be rendered in Claude Desktop",
        "or any MCP-compatible client. The `mcp_app_generator.py` library",
        "provides 21 generators that bridge all libraries to MCP App components.",
        "",
    ])

    return "\n".join(lines)

def generate_safety_gates() -> str:
    """Generate Safety Gates page."""
    lines = [
        "# Safety Gates",
        "",
        "Deterministic gates that run before every tool call. "
        "Denied actions are blocked mechanically -- no override possible.",
        "",
        "## Gate Summary",
        "",
        "| Gate | Name | Purpose |",
        "|------|------|---------|",
        "| A | Dependency Guardian | Block reads from node_modules, venv, etc. |",
        "| B | File Read Cache | Prevent redundant file reads |",
        "| C | Bash Tool Substitution | Enforce rg/fd over grep/find |",
        "| D | Sequential Rendering | Order UI component rendering |",
        "| E | Stash Safety | Block cross-branch stash pop |",
        "| F | Secret Detection | Block committing secrets |",
        "| G | Git Push Safety | Require approval for protected branches |",
        "| H | Plan Mode Guard | Prevent plan-mode loops |",
        "| I | Git Index Lock | Detect stale index.lock files |",
        "| J | MCP Surface | Block MCP Apps in CLI (no render surface) |",
        "",
        "## How Gates Work",
        "",
        "```",
        "User action -> PreToolUse hook -> Gate evaluation -> Allow/Deny",
        "```",
        "",
        "Gates are defined in `hooks/context-gate-hook.py` and evaluate "
        "deterministically based on tool name, input, and environment state.",
        "",
        "## Customization",
        "",
        "User-defined gates can be added via `~/.grip/gates.yaml`.",
        "",
    ]
    return "\n".join(lines)


def generate_sidebar(skills: list, modes: list, agents: list, commands: list, libraries: list = None) -> str:
    """Generate the wiki sidebar for navigation."""
    lines = [
        "**GRIP Wiki**",
        "",
        "- [Home](Home)",
        "- [Getting Started](Getting-Started)",
        "- [Skills](Skills) ({})".format(len(skills)),
        "- [Modes](Modes) ({})".format(len(modes)),
        "- [Agents](Agents) ({})".format(len(agents)),
        "- [Commands](Commands) ({})".format(len(commands)),
        "- [Architecture](Architecture)",
        "- [Safety Gates](Safety-Gates)",
    ]
    if libraries:
        lines.append("- [Libraries](Libraries) ({})".format(len(libraries)))
    return "\n".join(lines)


def generate_all_pages() -> dict[str, str]:
    """Generate all wiki pages. Returns {filename: content}."""
    skills = scan_skills()
    modes = scan_modes()
    agents = scan_agents()
    commands = scan_commands()

    libraries = scan_libraries()
    return {
        "Home.md": generate_home(skills, modes, agents, commands, libraries),
        "Skills.md": generate_skills_page(skills),
        "Modes.md": generate_modes_page(modes),
        "Agents.md": generate_agents_page(agents),
        "Commands.md": generate_commands_page(commands),
        "Architecture.md": generate_architecture_page(),
        "Getting-Started.md": generate_getting_started(),
        "Safety-Gates.md": generate_safety_gates(),
        "Libraries.md": generate_libraries_page(libraries),
        "_Sidebar.md": generate_sidebar(skills, modes, agents, commands, libraries),
    }


def write_pages(pages: dict[str, str], output_dir: Path) -> list[str]:
    """Write generated pages to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in pages.items():
        filepath = output_dir / filename
        filepath.write_text(content)
        written.append(filename)
    return written


def clone_wiki(output_dir: Path) -> bool:
    """Clone the wiki repo into output_dir."""
    if output_dir.exists():
        # Pull latest
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=str(output_dir),
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0

    try:
        result = subprocess.run(
            ["gh", "repo", "clone", f"{REPO_SLUG}.wiki", str(output_dir)],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def push_wiki(wiki_dir: Path) -> dict:
    """Commit and push wiki changes."""
    try:
        # Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(wiki_dir), capture_output=True, timeout=10,
        )

        # Check for changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wiki_dir), capture_output=True, text=True, timeout=10,
        )
        if not status.stdout.strip():
            return {"status": "no_changes", "message": "Wiki already up to date"}

        # Commit
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        subprocess.run(
            ["git", "commit", "-m", f"docs: auto-update wiki ({now})"],
            cwd=str(wiki_dir), capture_output=True, timeout=10,
        )

        # Push
        result = subprocess.run(
            ["git", "push"],
            cwd=str(wiki_dir), capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {"status": "pushed", "message": f"Wiki updated at {now}"}
        return {"status": "error", "message": result.stderr}

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Git operation timed out"}
    except FileNotFoundError:
        return {"status": "error", "message": "Git not found"}


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: wiki_generator.py <generate|push|preview>")
        sys.exit(1)

    cmd = sys.argv[1]
    output_dir = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--output-dir" else WIKI_CLONE_DIR

    if cmd == "preview":
        pages = generate_all_pages()
        for name, content in pages.items():
            lines = content.count("\n")
            print(f"  {name}: {lines} lines")
        print(f"\nTotal: {len(pages)} pages")

    elif cmd == "generate":
        pages = generate_all_pages()
        written = write_pages(pages, output_dir)
        print(f"Generated {len(written)} wiki pages to {output_dir}")
        for name in written:
            print(f"  {name}")

    elif cmd == "push":
        print(f"Cloning wiki to {output_dir}...")
        if not clone_wiki(output_dir):
            print("Failed to clone wiki. Ensure wiki is initialized on GitHub.")
            sys.exit(1)

        pages = generate_all_pages()
        written = write_pages(pages, output_dir)
        print(f"Generated {len(written)} pages")

        result = push_wiki(output_dir)
        print(f"Push result: {result['status']} - {result['message']}")
        if result["status"] == "error":
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
