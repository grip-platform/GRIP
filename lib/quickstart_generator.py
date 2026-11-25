#!/usr/bin/env python3
"""GRIP Quickstart Generator -- Generate project-specific CLAUDE.md from templates.

Usage:
    python3 lib/quickstart_generator.py [--type TYPE] [--output PATH] [--dry-run] [--dir DIR] [--list-sections]

Pure Python, stdlib only.
"""

from __future__ import annotations

import argparse
import enum
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ProjectType(enum.Enum):
    """Detected or specified project type."""

    PYTHON = "python"
    NODE = "node"
    REACT = "react"
    DJANGO = "django"
    FASTAPI = "fastapi"
    RUST = "rust"
    GO = "go"
    GENERIC = "generic"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_project_type(project_dir: str) -> ProjectType:
    """Detect project type from filesystem markers.

    Detection order matters -- more specific types (Django, FastAPI, React)
    are checked before their parent types (Python, Node).
    """
    p = Path(project_dir)

    # Django: manage.py with django references, or settings.py in a subdir
    manage_py = p / "manage.py"
    if manage_py.is_file():
        try:
            content = manage_py.read_text(errors="replace")
            if "django" in content.lower():
                return ProjectType.DJANGO
        except OSError:
            pass
    # Also check requirements.txt for django
    req = p / "requirements.txt"
    if req.is_file():
        try:
            content = req.read_text(errors="replace").lower()
            if "django" in content:
                return ProjectType.DJANGO
        except OSError:
            pass

    # FastAPI: check requirements.txt or pyproject.toml for fastapi
    for marker_file in ["requirements.txt", "pyproject.toml"]:
        marker = p / marker_file
        if marker.is_file():
            try:
                content = marker.read_text(errors="replace").lower()
                if "fastapi" in content:
                    return ProjectType.FASTAPI
            except OSError:
                pass

    # React: package.json with react dependency
    pkg_json = p / "package.json"
    if pkg_json.is_file():
        try:
            content = pkg_json.read_text(errors="replace")
            if '"react"' in content:
                return ProjectType.REACT
        except OSError:
            pass

    # Rust: Cargo.toml
    if (p / "Cargo.toml").is_file():
        return ProjectType.RUST

    # Go: go.mod
    if (p / "go.mod").is_file():
        return ProjectType.GO

    # Node: package.json (non-React)
    if pkg_json.is_file():
        return ProjectType.NODE

    # Python: setup.py, setup.cfg, pyproject.toml, requirements.txt, or *.py in root
    python_markers = ["setup.py", "setup.cfg", "pyproject.toml", "requirements.txt"]
    for m in python_markers:
        if (p / m).is_file():
            return ProjectType.PYTHON
    # Check for any .py files in root
    if any(f.suffix == ".py" for f in p.iterdir() if f.is_file()):
        return ProjectType.PYTHON

    return ProjectType.GENERIC


# ---------------------------------------------------------------------------
# Template sections
# ---------------------------------------------------------------------------

@dataclass
class TemplateSection:
    """A reusable section of a CLAUDE.md template."""

    id: str
    title: str
    content: str
    required: bool = False
    project_types: List[ProjectType] = field(default_factory=list)

    def applies_to(self, project_type: ProjectType) -> bool:
        """Return True if this section applies to the given project type."""
        if self.required:
            return True
        if not self.project_types:
            return True  # universal section
        return project_type in self.project_types


# The template library
TEMPLATE_SECTIONS: List[TemplateSection] = [
    # --- Required / universal ---
    TemplateSection(
        id="header",
        title="Project Overview",
        content=(
            "# {project_name} -- CLAUDE.md\n\n"
            "Project-specific rules. Global rules in `~/.claude/CLAUDE.md` and "
            "`~/.claude/rules/` are inherited automatically.\n"
        ),
        required=True,
    ),
    TemplateSection(
        id="design-principles",
        title="Design Principles",
        content=(
            "## Design Principles\n\n"
            "SOLID, GRASP, DRY, KISS, YAGNI\n\n"
            "- Prefer composition over inheritance\n"
            "- Lowest reasonable algorithmic complexity\n"
        ),
        required=True,
    ),
    TemplateSection(
        id="git-rules",
        title="Git Rules",
        content=(
            "## Git Rules\n\n"
            "- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`\n"
            "- Never force push without explicit approval\n"
            "- Never modify `.github/workflows/` on PR branches\n"
        ),
        required=True,
    ),
    TemplateSection(
        id="safety",
        title="Safety",
        content=(
            "## Safety\n\n"
            "- Never commit secrets (.env, API keys, credentials)\n"
            "- Never read dependency folders (node_modules, venv, dist, build, __pycache__)\n"
            "- When confidence < 100%: HALT and ask the user\n"
        ),
        required=True,
    ),
    # --- Universal but not required ---
    TemplateSection(
        id="testing",
        title="Testing",
        content=(
            "## Testing\n\n"
            "- Write tests for all new features and bug fixes\n"
            "- Tests must pass before committing\n"
            "- Use temporary directories for filesystem tests\n"
        ),
    ),
    TemplateSection(
        id="deployment",
        title="Deployment",
        content=(
            "## Deployment\n\n"
            "- Always commit and push before deploying\n"
            "- Verify deployment succeeds after pushing\n"
        ),
    ),
    TemplateSection(
        id="dependencies",
        title="Dependencies",
        content=(
            "## Dependencies\n\n"
            "- Keep dependencies minimal\n"
            "- Pin versions in lock files\n"
            "- Review security advisories before upgrading\n"
        ),
    ),
    TemplateSection(
        id="hooks",
        title="Hooks",
        content=(
            "## Hooks\n\n"
            "- Pre-commit hooks run automatically on commit\n"
            "- Never bypass hooks with --no-verify\n"
        ),
    ),
    TemplateSection(
        id="skills",
        title="Skills",
        content=(
            "## Skills\n\n"
            "- Check `skills/` directory for reusable patterns\n"
            "- Create new skills when a pattern is proven 2x, building the first with a human in the loop\n"
        ),
    ),
    TemplateSection(
        id="context-management",
        title="Context Management",
        content=(
            "## Context Management\n\n"
            "- Batch file reads in Phase 1, targeted edits in Phase 2+\n"
            "- At 85% context usage: HALT and ask user\n"
        ),
    ),
    # --- Python-family ---
    TemplateSection(
        id="python-style",
        title="Python Style",
        content=(
            "## Python Style\n\n"
            "- Type hints on all public functions\n"
            "- Docstrings on all public modules, classes, and functions\n"
            "- Use `pathlib.Path` over `os.path`\n"
            "- Prefer f-strings over `.format()` or `%`\n"
        ),
        project_types=[ProjectType.PYTHON, ProjectType.DJANGO, ProjectType.FASTAPI],
    ),
    TemplateSection(
        id="python-testing",
        title="Python Testing",
        content=(
            "## Python Testing\n\n"
            "- Use pytest as the test runner\n"
            "- Use `tmp_path` fixture for filesystem tests\n"
            "- Use `monkeypatch` for environment variables\n"
            "- Target: 80%+ code coverage\n"
        ),
        project_types=[ProjectType.PYTHON, ProjectType.DJANGO, ProjectType.FASTAPI],
    ),
    TemplateSection(
        id="async-safety",
        title="Async Safety",
        content=(
            "## Async Safety\n\n"
            "- Never use `subprocess.run()` in async code (blocks event loop)\n"
            "- Use `asyncio.create_subprocess_exec()` instead\n"
            "- `time.sleep()` -> `await asyncio.sleep()`\n"
            "- Always use `asyncio.wait_for()` for timeout control\n"
        ),
        project_types=[ProjectType.FASTAPI, ProjectType.PYTHON],
    ),
    # --- Django ---
    TemplateSection(
        id="django-rules",
        title="Django Rules",
        content=(
            "## Django Rules\n\n"
            "- Always create and run migrations for model changes\n"
            "- Use Django ORM; avoid raw SQL unless justified\n"
            "- Keep views thin, logic in models or services\n"
            "- Use `reverse()` for URL resolution, never hardcode paths\n"
        ),
        project_types=[ProjectType.DJANGO],
    ),
    # --- FastAPI ---
    TemplateSection(
        id="fastapi-rules",
        title="FastAPI Rules",
        content=(
            "## FastAPI Rules\n\n"
            "- Use Pydantic models for request/response validation\n"
            "- Use dependency injection for shared resources\n"
            "- Document endpoints with OpenAPI docstrings\n"
            "- Use `BackgroundTasks` for non-blocking operations\n"
        ),
        project_types=[ProjectType.FASTAPI],
    ),
    # --- Node / React ---
    TemplateSection(
        id="node-style",
        title="Node Style",
        content=(
            "## Node Style\n\n"
            "- Use ES modules (`import/export`) over CommonJS\n"
            "- Use `const` by default, `let` when reassignment needed\n"
            "- Prefer `async/await` over raw Promises\n"
            "- Use strict TypeScript if tsconfig is present\n"
        ),
        project_types=[ProjectType.NODE, ProjectType.REACT],
    ),
    TemplateSection(
        id="react-rules",
        title="React Rules",
        content=(
            "## React Rules\n\n"
            "- Functional components with hooks (no class components)\n"
            "- Colocate component, styles, and tests\n"
            "- Use React.memo/useMemo/useCallback only for measured perf issues\n"
            "- Prefer server components where supported (Next.js 13+)\n"
        ),
        project_types=[ProjectType.REACT],
    ),
    # --- Rust ---
    TemplateSection(
        id="rust-rules",
        title="Rust Rules",
        content=(
            "## Rust Rules\n\n"
            "- `cargo fmt` before committing\n"
            "- `cargo clippy` must pass with no warnings\n"
            "- Prefer `Result<T, E>` over `.unwrap()` in library code\n"
            "- Use `#[derive]` macros for standard traits\n"
        ),
        project_types=[ProjectType.RUST],
    ),
    # --- Go ---
    TemplateSection(
        id="go-rules",
        title="Go Rules",
        content=(
            "## Go Rules\n\n"
            "- `gofmt` / `goimports` before committing\n"
            "- `go vet` and `golangci-lint` must pass\n"
            "- Use `context.Context` for cancellation and deadlines\n"
            "- Error wrapping with `fmt.Errorf(\"...: %w\", err)`\n"
        ),
        project_types=[ProjectType.GO],
    ),
]


def get_section_by_id(section_id: str) -> Optional[TemplateSection]:
    """Look up a template section by its ID."""
    for s in TEMPLATE_SECTIONS:
        if s.id == section_id:
            return s
    return None


def list_section_ids() -> List[str]:
    """Return all available section IDs."""
    return [s.id for s in TEMPLATE_SECTIONS]


# ---------------------------------------------------------------------------
# Selection and generation
# ---------------------------------------------------------------------------

def select_sections(
    project_type: ProjectType,
    custom_excludes: Optional[List[str]] = None,
) -> List[TemplateSection]:
    """Select applicable sections for a project type, minus exclusions."""
    excludes = set(custom_excludes or [])
    return [
        s
        for s in TEMPLATE_SECTIONS
        if s.applies_to(project_type) and s.id not in excludes
    ]


def generate_claude_md(
    project_dir: str,
    project_type: Optional[ProjectType] = None,
    sections: Optional[List[TemplateSection]] = None,
) -> str:
    """Generate a CLAUDE.md string for a project directory.

    Args:
        project_dir: Path to the project root.
        project_type: Override detection. If None, auto-detect.
        sections: Override section selection. If None, auto-select.

    Returns:
        The generated CLAUDE.md content as a string.
    """
    p = Path(project_dir)
    project_name = p.resolve().name

    if project_type is None:
        project_type = detect_project_type(project_dir)

    if sections is None:
        sections = select_sections(project_type)

    parts: List[str] = []
    for section in sections:
        content = section.content.replace("{project_name}", project_name)
        content = content.replace("{project_type}", project_type.value)
        parts.append(content)

    # Join with blank line between sections
    result = "\n".join(parts)

    # Footer
    result += (
        "\n---\n\n"
        f"*Generated by GRIP Quickstart Generator | "
        f"Type: {project_type.value}*\n"
    )

    return result


def format_preview(content: str) -> str:
    """Format content with line numbers for dry-run preview."""
    lines = content.splitlines() if content else [""]
    width = max(len(str(len(lines))), 1)
    numbered = []
    for i, line in enumerate(lines, 1):
        numbered.append(f"{i:>{width}} | {line}")
    return "\n".join(numbered)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="GRIP Quickstart Generator -- project CLAUDE.md from templates",
    )
    parser.add_argument(
        "--type",
        dest="project_type",
        choices=[t.value for t in ProjectType],
        default=None,
        help="Override auto-detected project type",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: <dir>/.claude/CLAUDE.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview output with line numbers, do not write",
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Project directory to analyze (default: current dir)",
    )
    parser.add_argument(
        "--list-sections",
        action="store_true",
        help="List all available template sections and exit",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help="Section IDs to exclude",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # List sections mode
    if args.list_sections:
        print("Available template sections:\n")
        for s in TEMPLATE_SECTIONS:
            types_str = ", ".join(t.value for t in s.project_types) if s.project_types else "all"
            req_str = " [REQUIRED]" if s.required else ""
            print(f"  {s.id:<25} {s.title:<25} types: {types_str}{req_str}")
        return 0

    project_dir = os.path.abspath(args.dir)
    if not os.path.isdir(project_dir):
        print(f"Error: directory not found: {project_dir}", file=sys.stderr)
        return 1

    # Determine project type
    pt = None
    if args.project_type:
        pt = ProjectType(args.project_type)

    # Select sections
    if pt is None:
        pt = detect_project_type(project_dir)

    sections = select_sections(pt, custom_excludes=args.exclude)
    content = generate_claude_md(project_dir, project_type=pt, sections=sections)

    if args.dry_run:
        print(format_preview(content))
        return 0

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(project_dir) / ".claude" / "CLAUDE.md"

    # Create parent dirs
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    print(f"Generated: {output_path}")
    print(f"Type: {pt.value} | Sections: {len(sections)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
