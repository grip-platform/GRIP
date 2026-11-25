"""
Project Onboarding Guide Generator -- analyzes codebases for new team members.

Scans project structure, git history, config files, and conventions to produce
a comprehensive onboarding document. Supports markdown, Slack mrkdwn, JSON,
and MCP output formats.

Usage:
    python3 lib/onboarding_guide.py generate [--path DIR] [--format markdown|slack|json|checklist|concept_map]
    python3 lib/onboarding_guide.py detect [--path DIR]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ProjectInfo:
    """Core project metadata."""
    name: str
    path: str
    tech_stack: dict = field(default_factory=dict)
    languages: list = field(default_factory=list)
    frameworks: list = field(default_factory=list)


@dataclass
class SetupStep:
    """A single step in the setup process."""
    order: int
    command: str
    description: str
    platform_notes: str = ""


@dataclass
class TeamMember:
    """A contributor derived from git history."""
    name: str
    email: str = ""
    expertise_areas: list = field(default_factory=list)
    recent_files: list = field(default_factory=list)


@dataclass
class OnboardingGuide:
    """Complete onboarding guide output."""
    project_info: ProjectInfo = field(default_factory=lambda: ProjectInfo("", ""))
    setup_steps: list = field(default_factory=list)
    architecture_overview: str = ""
    team_contacts: list = field(default_factory=list)
    gotchas: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    testing_info: dict = field(default_factory=dict)
    env_setup: dict = field(default_factory=dict)
    git_workflow: dict = field(default_factory=dict)
    config_files: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tech stack detection
# ---------------------------------------------------------------------------

# Maps config filenames to tech stack signals
CONFIG_SIGNALS = {
    "package.json": {"lang": "JavaScript", "tool": "npm"},
    "package-lock.json": {"tool": "npm"},
    "yarn.lock": {"tool": "yarn"},
    "pnpm-lock.yaml": {"tool": "pnpm"},
    "tsconfig.json": {"lang": "TypeScript"},
    "pyproject.toml": {"lang": "Python", "tool": "pip/poetry"},
    "setup.py": {"lang": "Python", "tool": "pip"},
    "setup.cfg": {"lang": "Python", "tool": "pip"},
    "requirements.txt": {"lang": "Python", "tool": "pip"},
    "Pipfile": {"lang": "Python", "tool": "pipenv"},
    "poetry.lock": {"lang": "Python", "tool": "poetry"},
    "Cargo.toml": {"lang": "Rust", "tool": "cargo"},
    "Cargo.lock": {"lang": "Rust", "tool": "cargo"},
    "go.mod": {"lang": "Go", "tool": "go modules"},
    "go.sum": {"lang": "Go", "tool": "go modules"},
    "pom.xml": {"lang": "Java", "tool": "maven"},
    "build.gradle": {"lang": "Java", "tool": "gradle"},
    "build.gradle.kts": {"lang": "Kotlin", "tool": "gradle"},
    "Gemfile": {"lang": "Ruby", "tool": "bundler"},
    "Gemfile.lock": {"lang": "Ruby", "tool": "bundler"},
    "composer.json": {"lang": "PHP", "tool": "composer"},
    "mix.exs": {"lang": "Elixir", "tool": "mix"},
    "pubspec.yaml": {"lang": "Dart", "tool": "pub"},
    "CMakeLists.txt": {"lang": "C/C++", "tool": "cmake"},
    "Makefile": {"tool": "make"},
    "Dockerfile": {"tool": "docker"},
    "docker-compose.yml": {"tool": "docker-compose"},
    "docker-compose.yaml": {"tool": "docker-compose"},
    ".github/workflows": {"tool": "github-actions"},
    ".gitlab-ci.yml": {"tool": "gitlab-ci"},
    "Jenkinsfile": {"tool": "jenkins"},
    ".circleci": {"tool": "circleci"},
    ".travis.yml": {"tool": "travis-ci"},
    "vercel.json": {"tool": "vercel"},
    "netlify.toml": {"tool": "netlify"},
    "railway.json": {"tool": "railway"},
}

# Framework detection patterns in config files
FRAMEWORK_PATTERNS = {
    "package.json": {
        "react": "React",
        "next": "Next.js",
        "vue": "Vue.js",
        "nuxt": "Nuxt.js",
        "angular": "Angular",
        "svelte": "Svelte",
        "express": "Express",
        "fastify": "Fastify",
        "nestjs": "NestJS",
        "electron": "Electron",
        "tailwindcss": "Tailwind CSS",
        "jest": "Jest",
        "vitest": "Vitest",
        "mocha": "Mocha",
        "cypress": "Cypress",
        "playwright": "Playwright",
    },
    "pyproject.toml": {
        "django": "Django",
        "flask": "Flask",
        "fastapi": "FastAPI",
        "pytest": "pytest",
        "sqlalchemy": "SQLAlchemy",
        "celery": "Celery",
        "pydantic": "Pydantic",
    },
    "requirements.txt": {
        "django": "Django",
        "flask": "Flask",
        "fastapi": "FastAPI",
        "pytest": "pytest",
        "sqlalchemy": "SQLAlchemy",
        "celery": "Celery",
    },
    "Cargo.toml": {
        "actix": "Actix",
        "rocket": "Rocket",
        "tokio": "Tokio",
        "serde": "Serde",
        "axum": "Axum",
    },
    "go.mod": {
        "gin-gonic": "Gin",
        "gorilla/mux": "Gorilla Mux",
        "fiber": "Fiber",
        "echo": "Echo",
    },
    "Gemfile": {
        "rails": "Ruby on Rails",
        "sinatra": "Sinatra",
        "rspec": "RSpec",
    },
}

# Extension-to-language mapping
EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".swift": "Swift",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

# Directories to skip when scanning
SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", "__pycache__", "venv",
    ".venv", "target", "vendor", ".git", ".svn", ".hg", "Pods",
    ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".nyc_output",
    "egg-info", ".eggs", "htmlcov", ".gradle", ".idea", ".vscode",
    "bin", "obj",
}


def _safe_read(path: Path, max_bytes: int = 65536) -> str:
    """Read a file safely, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except (OSError, PermissionError):
        return ""


def _run_git(args: list, cwd: str, timeout: int = 30) -> str:
    """Run a git command and return stdout, empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _is_git_repo(path: str) -> bool:
    """Check if path is inside a git repository."""
    return bool(_run_git(["rev-parse", "--is-inside-work-tree"], cwd=path))


def detect_tech_stack(path: str) -> dict:
    """Detect the tech stack of a project at the given path.

    Returns a dict with keys: languages, frameworks, build_tools, ci_cd, deployment.
    Each value is a sorted list of unique detected items.
    """
    project = Path(path)
    if not project.is_dir():
        return {"languages": [], "frameworks": [], "build_tools": [], "ci_cd": [], "deployment": []}

    languages = set()
    frameworks = set()
    build_tools = set()
    ci_cd = set()
    deployment = set()

    ci_tools = {"github-actions", "gitlab-ci", "jenkins", "circleci", "travis-ci"}
    deploy_tools = {"vercel", "netlify", "railway", "docker", "docker-compose"}

    # Check config files
    for config_name, signals in CONFIG_SIGNALS.items():
        config_path = project / config_name
        if config_path.exists() or (config_path.is_dir()):
            if "lang" in signals:
                languages.add(signals["lang"])
            tool = signals.get("tool", "")
            if tool:
                if tool in ci_tools:
                    ci_cd.add(tool)
                elif tool in deploy_tools:
                    deployment.add(tool)
                else:
                    build_tools.add(tool)

    # Detect frameworks from config file contents
    for config_name, fw_patterns in FRAMEWORK_PATTERNS.items():
        config_path = project / config_name
        if config_path.is_file():
            content = _safe_read(config_path).lower()
            for pattern, framework_name in fw_patterns.items():
                if pattern in content:
                    frameworks.add(framework_name)

    # Scan file extensions for languages (shallow scan -- top 3 levels)
    ext_counts = Counter()
    for depth, (dirpath, dirnames, filenames) in enumerate(_walk_limited(project, max_depth=3)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext in EXTENSION_LANGUAGES:
                ext_counts[EXTENSION_LANGUAGES[ext]] += 1

    # Add languages found by extension scan
    for lang, _count in ext_counts.most_common():
        languages.add(lang)

    return {
        "languages": sorted(languages),
        "frameworks": sorted(frameworks),
        "build_tools": sorted(build_tools),
        "ci_cd": sorted(ci_cd),
        "deployment": sorted(deployment),
    }


def _walk_limited(root: Path, max_depth: int = 3):
    """Walk a directory tree with a depth limit. Yields (dirpath, dirnames, filenames)."""
    root_str = str(root)
    root_depth = root_str.count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root_str):
        current_depth = dirpath.count(os.sep) - root_depth
        if current_depth >= max_depth:
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        yield dirpath, dirnames, filenames


# ---------------------------------------------------------------------------
# Setup step extraction
# ---------------------------------------------------------------------------

def _extract_from_package_json(path: Path) -> list:
    """Extract setup steps from package.json."""
    steps = []
    pkg_path = path / "package.json"
    if not pkg_path.is_file():
        return steps

    content = _safe_read(pkg_path)
    try:
        pkg = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return steps

    # Detect package manager
    lock_files = {
        "yarn.lock": "yarn",
        "pnpm-lock.yaml": "pnpm",
        "package-lock.json": "npm",
    }
    pkg_mgr = "npm"
    for lock_name, mgr in lock_files.items():
        if (path / lock_name).exists():
            pkg_mgr = mgr
            break

    install_cmd = {"npm": "npm install", "yarn": "yarn", "pnpm": "pnpm install"}
    steps.append(SetupStep(
        order=1,
        command=install_cmd.get(pkg_mgr, "npm install"),
        description="Install project dependencies",
    ))

    scripts = pkg.get("scripts", {})
    script_priorities = [
        ("dev", "Start development server"),
        ("start", "Start the application"),
        ("build", "Build for production"),
        ("test", "Run test suite"),
        ("lint", "Run linter"),
    ]
    order = 2
    for script_name, desc in script_priorities:
        if script_name in scripts:
            run_prefix = f"{pkg_mgr} run " if pkg_mgr != "npm" else "npm run "
            if pkg_mgr == "npm" and script_name in ("start", "test"):
                run_prefix = "npm "
            steps.append(SetupStep(
                order=order,
                command=f"{run_prefix}{script_name}",
                description=desc,
            ))
            order += 1

    return steps


def _extract_from_makefile(path: Path) -> list:
    """Extract setup steps from Makefile."""
    steps = []
    mk_path = path / "Makefile"
    if not mk_path.is_file():
        return steps

    content = _safe_read(mk_path)
    # Find targets with optional comments
    target_pattern = re.compile(r'^([a-zA-Z_][\w-]*):\s*(?:.*?)(?:#\s*(.+))?$', re.MULTILINE)
    priority_targets = {
        "install": "Install dependencies",
        "setup": "Set up the project",
        "init": "Initialize the project",
        "build": "Build the project",
        "run": "Run the application",
        "dev": "Start development mode",
        "test": "Run tests",
        "lint": "Run linter",
        "clean": "Clean build artifacts",
    }

    found = {}
    for match in target_pattern.finditer(content):
        target = match.group(1)
        comment = (match.group(2) or "").strip()
        if target in priority_targets or comment:
            desc = comment if comment else priority_targets.get(target, f"Run make {target}")
            found[target] = desc

    order = 1
    for target_name in priority_targets:
        if target_name in found:
            steps.append(SetupStep(
                order=order,
                command=f"make {target_name}",
                description=found[target_name],
            ))
            order += 1

    return steps


def _extract_from_pyproject(path: Path) -> list:
    """Extract setup steps from pyproject.toml."""
    steps = []
    toml_path = path / "pyproject.toml"
    if not toml_path.is_file():
        return steps

    content = _safe_read(toml_path)

    # Detect if poetry or pip
    if "[tool.poetry]" in content:
        steps.append(SetupStep(order=1, command="poetry install", description="Install dependencies with Poetry"))
        steps.append(SetupStep(order=2, command="poetry run python -m pytest", description="Run tests"))
    elif "[build-system]" in content:
        steps.append(SetupStep(order=1, command="pip install -e '.[dev]'", description="Install package in development mode"))

    # Check for scripts
    if "[project.scripts]" in content or "[tool.poetry.scripts]" in content:
        script_match = re.search(r'\[(?:project|tool\.poetry)\.scripts\]\s*\n((?:\w+\s*=\s*.+\n?)+)', content)
        if script_match:
            for line in script_match.group(1).strip().split("\n"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    script_name = parts[0].strip()
                    steps.append(SetupStep(
                        order=len(steps) + 1,
                        command=script_name,
                        description=f"Run {script_name} CLI entry point",
                    ))

    return steps


def _extract_from_requirements(path: Path) -> list:
    """Extract setup steps from requirements.txt."""
    req_path = path / "requirements.txt"
    if not req_path.is_file():
        return []

    steps = [
        SetupStep(order=1, command="python3 -m venv venv", description="Create virtual environment"),
        SetupStep(order=2, command="source venv/bin/activate",
                  description="Activate virtual environment",
                  platform_notes="On Windows: venv\\Scripts\\activate"),
        SetupStep(order=3, command="pip install -r requirements.txt", description="Install dependencies"),
    ]

    # Check for dev requirements
    dev_req = path / "requirements-dev.txt"
    if dev_req.is_file():
        steps.append(SetupStep(order=4, command="pip install -r requirements-dev.txt",
                               description="Install development dependencies"))

    return steps


def _extract_from_cargo(path: Path) -> list:
    """Extract setup steps from Cargo.toml."""
    if not (path / "Cargo.toml").is_file():
        return []
    return [
        SetupStep(order=1, command="cargo build", description="Build the project"),
        SetupStep(order=2, command="cargo test", description="Run tests"),
        SetupStep(order=3, command="cargo run", description="Run the application"),
    ]


def _extract_from_go_mod(path: Path) -> list:
    """Extract setup steps from go.mod."""
    if not (path / "go.mod").is_file():
        return []
    return [
        SetupStep(order=1, command="go mod download", description="Download dependencies"),
        SetupStep(order=2, command="go build ./...", description="Build the project"),
        SetupStep(order=3, command="go test ./...", description="Run tests"),
    ]


def _extract_from_docker(path: Path) -> list:
    """Extract setup steps from Docker files."""
    steps = []
    if (path / "docker-compose.yml").is_file() or (path / "docker-compose.yaml").is_file():
        steps.append(SetupStep(order=1, command="docker-compose up -d", description="Start all services"))
        steps.append(SetupStep(order=2, command="docker-compose logs -f", description="View service logs"))
    elif (path / "Dockerfile").is_file():
        name = Path(path).name.lower().replace(" ", "-")
        steps.append(SetupStep(order=1, command=f"docker build -t {name} .", description="Build Docker image"))
        steps.append(SetupStep(order=2, command=f"docker run -p 3000:3000 {name}", description="Run container"))
    return steps


def extract_setup_steps(path: str) -> list:
    """Extract setup steps from all detected config files.

    Merges steps from package.json, Makefile, pyproject.toml, requirements.txt,
    Cargo.toml, go.mod, and Docker files. Deduplicates by command.

    Returns a list of SetupStep, ordered by priority.
    """
    project = Path(path)
    if not project.is_dir():
        return []

    all_steps = []
    extractors = [
        _extract_from_docker,
        _extract_from_package_json,
        _extract_from_pyproject,
        _extract_from_requirements,
        _extract_from_makefile,
        _extract_from_cargo,
        _extract_from_go_mod,
    ]

    seen_commands = set()
    for extractor in extractors:
        for step in extractor(project):
            if step.command not in seen_commands:
                seen_commands.add(step.command)
                all_steps.append(step)

    # Re-number
    for i, step in enumerate(all_steps, 1):
        step.order = i

    return all_steps


# ---------------------------------------------------------------------------
# Architecture analysis
# ---------------------------------------------------------------------------

# Key directories and their typical purposes
DIR_PURPOSES = {
    "src": "Main source code",
    "lib": "Library/utility code",
    "app": "Application entry points",
    "apps": "Multi-app workspace",
    "packages": "Monorepo packages",
    "api": "API layer",
    "server": "Server-side code",
    "client": "Client-side code",
    "frontend": "Frontend application",
    "backend": "Backend application",
    "web": "Web application",
    "mobile": "Mobile application",
    "components": "UI components",
    "pages": "Page components/routes",
    "routes": "Route definitions",
    "controllers": "Request handlers",
    "models": "Data models",
    "services": "Business logic services",
    "utils": "Utility functions",
    "helpers": "Helper functions",
    "middleware": "Middleware/interceptors",
    "hooks": "React hooks / lifecycle hooks",
    "store": "State management",
    "config": "Configuration files",
    "scripts": "Automation scripts",
    "tests": "Test files",
    "test": "Test files",
    "__tests__": "Test files",
    "spec": "Test specifications",
    "docs": "Documentation",
    "public": "Static assets",
    "static": "Static files",
    "assets": "Media/design assets",
    "migrations": "Database migrations",
    "fixtures": "Test fixtures/seed data",
    "templates": "Template files",
    "views": "View/template files",
    "types": "Type definitions",
    "interfaces": "Interface definitions",
    "proto": "Protocol buffer definitions",
    "cmd": "Go command entry points",
    "internal": "Internal packages (Go)",
    "pkg": "Public packages (Go)",
    "crates": "Rust workspace crates",
}

# Common entry point filenames
ENTRY_POINTS = [
    "main.py", "app.py", "server.py", "index.py", "manage.py", "wsgi.py", "asgi.py",
    "main.ts", "index.ts", "server.ts", "app.ts",
    "main.js", "index.js", "server.js", "app.js",
    "main.go", "cmd/main.go",
    "main.rs", "lib.rs",
    "Main.java", "Application.java",
    "Program.cs",
    "main.rb", "config.ru",
]


def analyze_architecture(path: str) -> str:
    """Analyze project architecture and return a human-readable overview.

    Identifies key directories, entry points, and structural patterns.
    Returns a string suitable for inclusion in onboarding docs.
    """
    project = Path(path)
    if not project.is_dir():
        return "Directory not found."

    lines = []

    # Detect key directories
    key_dirs = []
    for dirpath, dirnames, _filenames in _walk_limited(project, max_depth=2):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, path)
        if rel == ".":
            continue
        dir_name = Path(rel).parts[0] if Path(rel).parts else rel
        if dir_name in DIR_PURPOSES and dir_name not in [d[0] for d in key_dirs]:
            key_dirs.append((dir_name, DIR_PURPOSES[dir_name]))

    if key_dirs:
        lines.append("Key directories:")
        for dname, purpose in sorted(key_dirs):
            lines.append(f"  {dname}/ -- {purpose}")

    # Detect entry points
    found_entries = []
    for entry in ENTRY_POINTS:
        entry_path = project / entry
        if entry_path.is_file():
            found_entries.append(entry)
        # Also check src/ prefix
        src_entry = project / "src" / entry
        if src_entry.is_file():
            found_entries.append(f"src/{entry}")

    if found_entries:
        lines.append("")
        lines.append("Entry points:")
        for entry in found_entries:
            lines.append(f"  {entry}")

    # Detect monorepo
    if (project / "packages").is_dir() or (project / "apps").is_dir():
        workspace_dir = "packages" if (project / "packages").is_dir() else "apps"
        workspace_path = project / workspace_dir
        try:
            packages = sorted([
                d.name for d in workspace_path.iterdir()
                if d.is_dir() and d.name not in SKIP_DIRS
            ])
        except OSError:
            packages = []
        if packages:
            lines.append("")
            lines.append(f"Monorepo ({workspace_dir}/):")
            for pkg in packages:
                lines.append(f"  {pkg}/")

    if not lines:
        lines.append("Flat project structure (no conventional directories detected).")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Team contacts from git
# ---------------------------------------------------------------------------

def find_team_contacts(path: str, limit: int = 20) -> list:
    """Extract team members from git history.

    Returns a list of TeamMember with name, email, and expertise areas
    derived from recently modified files.
    """
    if not _is_git_repo(path):
        return []

    # Get contributors with commit counts
    log = _run_git(
        ["shortlog", "-sne", "--no-merges", "HEAD"],
        cwd=path,
    )
    if not log:
        return []

    members = []
    for line in log.strip().split("\n")[:limit]:
        line = line.strip()
        if not line:
            continue
        # Format: "  123\tName <email>"
        match = re.match(r'\s*(\d+)\t(.+?)\s*<(.+?)>', line)
        if match:
            _count, name, email = match.groups()
            # Get recent files for this author
            recent = _run_git(
                ["log", "--author=" + email, "--pretty=format:", "--name-only",
                 "-20", "--no-merges"],
                cwd=path,
            )
            recent_files = []
            if recent:
                file_counts = Counter(f for f in recent.split("\n") if f.strip())
                recent_files = [f for f, _ in file_counts.most_common(5)]

            # Derive expertise from file paths
            expertise = _derive_expertise(recent_files)

            members.append(TeamMember(
                name=name.strip(),
                email=email.strip(),
                expertise_areas=expertise,
                recent_files=recent_files,
            ))

    return members


def _derive_expertise(files: list) -> list:
    """Derive expertise areas from a list of file paths."""
    expertise = set()
    area_patterns = {
        "frontend": re.compile(r'(components?|pages?|views?|ui|frontend|\.tsx|\.jsx|\.vue|\.svelte)', re.I),
        "backend": re.compile(r'(server|api|routes?|controllers?|backend|\.go$|\.rs$)', re.I),
        "testing": re.compile(r'(test|spec|__tests__|\.test\.|\.spec\.)', re.I),
        "devops": re.compile(r'(docker|\.yml$|\.yaml$|ci|deploy|infra|terraform|k8s)', re.I),
        "database": re.compile(r'(migration|model|schema|db|database|sql)', re.I),
        "documentation": re.compile(r'(docs?|readme|\.md$|wiki)', re.I),
        "configuration": re.compile(r'(config|settings|\.env|\.toml|\.ini)', re.I),
    }

    for f in files:
        for area, pattern in area_patterns.items():
            if pattern.search(f):
                expertise.add(area)

    return sorted(expertise)


# ---------------------------------------------------------------------------
# Gotcha detection
# ---------------------------------------------------------------------------

# Common pitfalls to check for
GOTCHA_CHECKS = [
    {
        "files": [".env.example", ".env.template", ".env.sample"],
        "message": "Environment variables required. Copy .env.example to .env and fill in values before running.",
    },
    {
        "files": [".nvmrc", ".node-version", ".tool-versions"],
        "message": "Specific runtime version required. Check version file and use a version manager (nvm, asdf, rtx).",
    },
    {
        "files": ["docker-compose.yml", "docker-compose.yaml"],
        "message": "Docker services required. Run docker-compose up before starting development.",
    },
    {
        "files": [".husky", ".husky/_"],
        "message": "Git hooks managed by Husky. Run install step to set up commit hooks.",
    },
    {
        "files": [".pre-commit-config.yaml"],
        "message": "Pre-commit hooks configured. Run 'pre-commit install' after cloning.",
    },
    {
        "files": ["lerna.json", "pnpm-workspace.yaml", "nx.json", "turbo.json"],
        "message": "Monorepo workspace. Dependencies may need to be installed from the root directory.",
    },
]


def detect_gotchas(path: str) -> list:
    """Detect common pitfalls and gotchas in a project.

    Returns a list of warning strings for things new developers often miss.
    """
    project = Path(path)
    if not project.is_dir():
        return []

    gotchas = []

    # Check file-based gotchas
    for check in GOTCHA_CHECKS:
        for fname in check["files"]:
            check_path = project / fname
            if check_path.exists():
                gotchas.append(check["message"])
                break  # Only add each gotcha once

    # Check for large repo
    if _is_git_repo(path):
        commit_count = _run_git(["rev-list", "--count", "HEAD"], cwd=path)
        if commit_count and int(commit_count) > 5000:
            gotchas.append(
                f"Large repository ({commit_count} commits). Consider shallow clone: "
                "git clone --depth 100."
            )

    # Check for git submodules
    if (project / ".gitmodules").is_file():
        gotchas.append("Git submodules detected. Run 'git submodule update --init --recursive' after cloning.")

    # Check for multiple package managers
    lock_files_found = []
    for lock_name in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]:
        if (project / lock_name).is_file():
            lock_files_found.append(lock_name)
    if len(lock_files_found) > 1:
        gotchas.append(
            f"Multiple lock files detected ({', '.join(lock_files_found)}). "
            "Use the package manager matching the primary lock file."
        )

    return gotchas


# ---------------------------------------------------------------------------
# Testing info
# ---------------------------------------------------------------------------

def _detect_testing(path: str) -> dict:
    """Detect testing setup and commands."""
    project = Path(path)
    info = {"framework": "", "command": "", "config_file": "", "coverage": ""}

    test_configs = {
        "jest.config.js": ("Jest", "npx jest"),
        "jest.config.ts": ("Jest", "npx jest"),
        "vitest.config.ts": ("Vitest", "npx vitest"),
        "vitest.config.js": ("Vitest", "npx vitest"),
        "pytest.ini": ("pytest", "python -m pytest"),
        "setup.cfg": ("pytest", "python -m pytest"),
        "conftest.py": ("pytest", "python -m pytest"),
        ".rspec": ("RSpec", "bundle exec rspec"),
        "phpunit.xml": ("PHPUnit", "vendor/bin/phpunit"),
    }

    for config_name, (framework, cmd) in test_configs.items():
        if (project / config_name).is_file():
            info["framework"] = framework
            info["command"] = cmd
            info["config_file"] = config_name
            break

    # Check pyproject.toml for pytest
    if not info["framework"]:
        toml_path = project / "pyproject.toml"
        if toml_path.is_file():
            content = _safe_read(toml_path)
            if "[tool.pytest" in content:
                info["framework"] = "pytest"
                info["command"] = "python -m pytest"
                info["config_file"] = "pyproject.toml"

    # Check package.json for test script
    if not info["framework"]:
        pkg_path = project / "package.json"
        if pkg_path.is_file():
            content = _safe_read(pkg_path)
            try:
                pkg = json.loads(content)
                if "test" in pkg.get("scripts", {}):
                    info["command"] = "npm test"
                    test_script = pkg["scripts"]["test"]
                    if "jest" in test_script:
                        info["framework"] = "Jest"
                    elif "vitest" in test_script:
                        info["framework"] = "Vitest"
                    elif "mocha" in test_script:
                        info["framework"] = "Mocha"
            except (json.JSONDecodeError, ValueError):
                pass

    # Check for coverage config
    coverage_indicators = [".nycrc", ".nycrc.json", ".coveragerc", "codecov.yml", ".codecov.yml"]
    for indicator in coverage_indicators:
        if (project / indicator).is_file():
            info["coverage"] = indicator
            break

    return info


# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

def _detect_env_setup(path: str) -> dict:
    """Detect environment setup requirements."""
    project = Path(path)
    env_info = {"env_file": "", "env_vars": [], "docker": False, "databases": []}

    # Check .env.example
    for env_name in [".env.example", ".env.template", ".env.sample"]:
        env_path = project / env_name
        if env_path.is_file():
            env_info["env_file"] = env_name
            content = _safe_read(env_path)
            # Extract variable names
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    var_name = line.split("=", 1)[0].strip()
                    if var_name:
                        env_info["env_vars"].append(var_name)
            break

    # Check docker
    if (project / "docker-compose.yml").is_file() or (project / "docker-compose.yaml").is_file():
        env_info["docker"] = True
        # Try to detect database services
        compose_name = "docker-compose.yml" if (project / "docker-compose.yml").is_file() else "docker-compose.yaml"
        content = _safe_read(project / compose_name)
        db_patterns = {
            "postgres": "PostgreSQL",
            "mysql": "MySQL",
            "mongo": "MongoDB",
            "redis": "Redis",
            "elasticsearch": "Elasticsearch",
            "rabbitmq": "RabbitMQ",
            "kafka": "Kafka",
        }
        for pattern, db_name in db_patterns.items():
            if pattern in content.lower():
                env_info["databases"].append(db_name)

    return env_info


# ---------------------------------------------------------------------------
# Git workflow
# ---------------------------------------------------------------------------

def _detect_git_workflow(path: str) -> dict:
    """Detect git workflow patterns from history."""
    if not _is_git_repo(path):
        return {}

    workflow = {"default_branch": "", "branch_patterns": [], "pr_conventions": [], "protected_branches": []}

    # Default branch
    default_branch = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=path)
    if not default_branch:
        default_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    workflow["default_branch"] = default_branch or "main"

    # Branch naming patterns from recent branches
    branches_raw = _run_git(["branch", "-r", "--format=%(refname:short)"], cwd=path)
    if branches_raw:
        branch_names = [b.strip() for b in branches_raw.split("\n") if b.strip()]
        patterns = set()
        prefix_pattern = re.compile(r'^(?:origin/)?([a-z]+)/')
        for branch in branch_names:
            match = prefix_pattern.match(branch)
            if match:
                patterns.add(match.group(1))
        if patterns:
            workflow["branch_patterns"] = sorted(patterns)

    # Commit message conventions
    recent_commits = _run_git(
        ["log", "--oneline", "-50", "--no-merges", "--format=%s"],
        cwd=path,
    )
    if recent_commits:
        conventional = 0
        total = 0
        for msg in recent_commits.split("\n"):
            if msg.strip():
                total += 1
                if re.match(r'^(feat|fix|chore|docs|test|refactor|perf|style|ci|build)(\(.+?\))?:', msg):
                    conventional += 1
        if total > 0 and conventional / total > 0.5:
            workflow["pr_conventions"].append("conventional-commits")

    return workflow


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------

def _detect_config_files(path: str) -> list:
    """Detect and describe key configuration files."""
    project = Path(path)
    configs = []

    config_descriptions = {
        "package.json": "Node.js project manifest (dependencies, scripts)",
        "tsconfig.json": "TypeScript compiler configuration",
        "pyproject.toml": "Python project configuration",
        "setup.py": "Python package setup script",
        "setup.cfg": "Python package configuration",
        "Cargo.toml": "Rust project manifest",
        "go.mod": "Go module definition",
        "Makefile": "Build automation targets",
        "Dockerfile": "Container image definition",
        "docker-compose.yml": "Multi-container Docker setup",
        "docker-compose.yaml": "Multi-container Docker setup",
        ".eslintrc.js": "ESLint configuration",
        ".eslintrc.json": "ESLint configuration",
        ".prettierrc": "Prettier code formatter configuration",
        ".prettierrc.json": "Prettier code formatter configuration",
        "tailwind.config.js": "Tailwind CSS configuration",
        "tailwind.config.ts": "Tailwind CSS configuration",
        "vite.config.ts": "Vite build tool configuration",
        "vite.config.js": "Vite build tool configuration",
        "webpack.config.js": "Webpack bundler configuration",
        "next.config.js": "Next.js framework configuration",
        "next.config.mjs": "Next.js framework configuration",
        "nuxt.config.ts": "Nuxt.js framework configuration",
        ".env.example": "Environment variable template",
        ".env.template": "Environment variable template",
        ".gitignore": "Git ignore patterns",
        ".editorconfig": "Editor formatting rules",
        "jest.config.js": "Jest test configuration",
        "jest.config.ts": "Jest test configuration",
        "vitest.config.ts": "Vitest test configuration",
        "pytest.ini": "pytest configuration",
        "tox.ini": "tox testing configuration",
        ".github/workflows": "GitHub Actions CI/CD workflows",
        "vercel.json": "Vercel deployment configuration",
        "netlify.toml": "Netlify deployment configuration",
        "renovate.json": "Renovate dependency update configuration",
        ".dependabot": "Dependabot dependency update configuration",
    }

    for fname, desc in sorted(config_descriptions.items()):
        if (project / fname).exists():
            configs.append({"file": fname, "description": desc})

    return configs


# ---------------------------------------------------------------------------
# Guide generation
# ---------------------------------------------------------------------------

def generate_onboarding_guide(path: str) -> OnboardingGuide:
    """Generate a complete onboarding guide for the project at the given path.

    Combines all analysis functions into a single comprehensive guide.
    """
    path = str(Path(path).resolve())
    project_name = Path(path).name

    tech_stack = detect_tech_stack(path)
    setup_steps = extract_setup_steps(path)
    architecture = analyze_architecture(path)
    team = find_team_contacts(path)
    gotchas = detect_gotchas(path)
    testing = _detect_testing(path)
    env_setup = _detect_env_setup(path)
    git_workflow = _detect_git_workflow(path)
    config_files = _detect_config_files(path)

    # Build resources list
    resources = []
    for doc_name in ["README.md", "CONTRIBUTING.md", "CHANGELOG.md", "docs/", "wiki/"]:
        if (Path(path) / doc_name).exists():
            resources.append(doc_name)

    return OnboardingGuide(
        project_info=ProjectInfo(
            name=project_name,
            path=path,
            tech_stack=tech_stack,
            languages=tech_stack.get("languages", []),
            frameworks=tech_stack.get("frameworks", []),
        ),
        setup_steps=setup_steps,
        architecture_overview=architecture,
        team_contacts=team,
        gotchas=gotchas,
        resources=resources,
        testing_info=testing,
        env_setup=env_setup,
        git_workflow=git_workflow,
        config_files=config_files,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(guide: OnboardingGuide) -> str:
    """Format an OnboardingGuide as a full markdown document."""
    lines = []
    info = guide.project_info

    lines.append(f"# {info.name} -- Onboarding Guide")
    lines.append("")
    lines.append(f"Welcome to *{info.name}*. This guide will help you get up and running.")
    lines.append("")

    # Tech stack
    lines.append("## Tech Stack")
    lines.append("")
    if info.languages:
        lines.append(f"**Languages**: {', '.join(info.languages)}")
    if info.frameworks:
        lines.append(f"**Frameworks**: {', '.join(info.frameworks)}")
    if info.tech_stack.get("build_tools"):
        lines.append(f"**Build Tools**: {', '.join(info.tech_stack['build_tools'])}")
    if info.tech_stack.get("ci_cd"):
        lines.append(f"**CI/CD**: {', '.join(info.tech_stack['ci_cd'])}")
    if info.tech_stack.get("deployment"):
        lines.append(f"**Deployment**: {', '.join(info.tech_stack['deployment'])}")
    lines.append("")

    # Setup
    if guide.setup_steps:
        lines.append("## Getting Started")
        lines.append("")
        for step in guide.setup_steps:
            lines.append(f"{step.order}. **{step.description}**")
            lines.append(f"   ```")
            lines.append(f"   {step.command}")
            lines.append(f"   ```")
            if step.platform_notes:
                lines.append(f"   > {step.platform_notes}")
            lines.append("")

    # Environment
    if guide.env_setup and (guide.env_setup.get("env_file") or guide.env_setup.get("docker")):
        lines.append("## Environment Setup")
        lines.append("")
        if guide.env_setup.get("env_file"):
            lines.append(f"Copy `{guide.env_setup['env_file']}` to `.env` and configure:")
            lines.append("")
            for var in guide.env_setup.get("env_vars", [])[:15]:
                lines.append(f"- `{var}`")
            lines.append("")
        if guide.env_setup.get("docker"):
            lines.append("Docker services are used for local development.")
            if guide.env_setup.get("databases"):
                lines.append(f"Databases: {', '.join(guide.env_setup['databases'])}")
            lines.append("")

    # Architecture
    if guide.architecture_overview:
        lines.append("## Architecture")
        lines.append("")
        lines.append(guide.architecture_overview)
        lines.append("")

    # Testing
    if guide.testing_info and guide.testing_info.get("framework"):
        lines.append("## Testing")
        lines.append("")
        lines.append(f"**Framework**: {guide.testing_info['framework']}")
        lines.append(f"**Run tests**: `{guide.testing_info['command']}`")
        if guide.testing_info.get("config_file"):
            lines.append(f"**Config**: `{guide.testing_info['config_file']}`")
        lines.append("")

    # Git workflow
    if guide.git_workflow:
        lines.append("## Git Workflow")
        lines.append("")
        if guide.git_workflow.get("default_branch"):
            lines.append(f"**Default branch**: `{guide.git_workflow['default_branch']}`")
        if guide.git_workflow.get("branch_patterns"):
            lines.append(f"**Branch prefixes**: {', '.join(guide.git_workflow['branch_patterns'])}")
        if guide.git_workflow.get("pr_conventions"):
            lines.append(f"**Conventions**: {', '.join(guide.git_workflow['pr_conventions'])}")
        lines.append("")

    # Config files
    if guide.config_files:
        lines.append("## Key Configuration Files")
        lines.append("")
        lines.append("| File | Purpose |")
        lines.append("|------|---------|")
        for cfg in guide.config_files:
            lines.append(f"| `{cfg['file']}` | {cfg['description']} |")
        lines.append("")

    # Team
    if guide.team_contacts:
        lines.append("## Team")
        lines.append("")
        lines.append("| Name | Email | Areas |")
        lines.append("|------|-------|-------|")
        for member in guide.team_contacts[:10]:
            areas = ", ".join(member.expertise_areas) if member.expertise_areas else "-"
            lines.append(f"| {member.name} | {member.email} | {areas} |")
        lines.append("")

    # Gotchas
    if guide.gotchas:
        lines.append("## Common Pitfalls")
        lines.append("")
        for gotcha in guide.gotchas:
            lines.append(f"- {gotcha}")
        lines.append("")

    # Resources
    if guide.resources:
        lines.append("## Resources")
        lines.append("")
        for res in guide.resources:
            lines.append(f"- `{res}`")
        lines.append("")

    return "\n".join(lines)


def format_slack(guide: OnboardingGuide) -> str:
    """Format an OnboardingGuide as Slack mrkdwn.

    Uses *bold* (single asterisk), not **bold** (double asterisk).
    Uses bullet lists and code formatting compatible with Slack.
    """
    lines = []
    info = guide.project_info

    lines.append(f"*{info.name} -- Onboarding Guide*")
    lines.append("")

    # Tech stack summary
    stack_parts = []
    if info.languages:
        stack_parts.append(f"Languages: {', '.join(info.languages)}")
    if info.frameworks:
        stack_parts.append(f"Frameworks: {', '.join(info.frameworks)}")
    if stack_parts:
        lines.append("*Tech Stack*")
        for part in stack_parts:
            lines.append(f"  {part}")
        lines.append("")

    # Quick start
    if guide.setup_steps:
        lines.append("*Getting Started*")
        for step in guide.setup_steps[:5]:
            lines.append(f"{step.order}. {step.description}: `{step.command}`")
        lines.append("")

    # Gotchas
    if guide.gotchas:
        lines.append("*Watch Out For*")
        for gotcha in guide.gotchas[:5]:
            lines.append(f"  - {gotcha}")
        lines.append("")

    # Testing
    if guide.testing_info and guide.testing_info.get("command"):
        lines.append(f"*Tests*: `{guide.testing_info['command']}`")
        lines.append("")

    # Team contacts (top 5)
    if guide.team_contacts:
        lines.append("*Key Contacts*")
        for member in guide.team_contacts[:5]:
            areas = f" ({', '.join(member.expertise_areas)})" if member.expertise_areas else ""
            lines.append(f"  - {member.name}{areas}")
        lines.append("")

    return "\n".join(lines)


def format_json(guide: OnboardingGuide) -> str:
    """Format an OnboardingGuide as JSON.

    Uses dataclass asdict for complete serialization.
    """
    data = {
        "project_info": asdict(guide.project_info),
        "setup_steps": [asdict(s) for s in guide.setup_steps],
        "architecture_overview": guide.architecture_overview,
        "team_contacts": [asdict(m) for m in guide.team_contacts],
        "gotchas": guide.gotchas,
        "resources": guide.resources,
        "testing_info": guide.testing_info,
        "env_setup": guide.env_setup,
        "git_workflow": guide.git_workflow,
        "config_files": guide.config_files,
    }
    return json.dumps(data, indent=2)


def generate_mcp_checklist(guide: OnboardingGuide) -> dict:
    """Generate an MCP-compatible checklist from the onboarding guide.

    Returns a dict suitable for MCP checklist rendering with setup steps
    as checklist items plus environment and gotcha items.
    """
    items = []

    # Setup steps
    for step in guide.setup_steps:
        items.append({
            "id": f"setup-{step.order}",
            "label": step.description,
            "detail": step.command,
            "checked": False,
            "category": "setup",
        })

    # Environment setup
    if guide.env_setup.get("env_file"):
        items.append({
            "id": "env-config",
            "label": f"Configure environment from {guide.env_setup['env_file']}",
            "detail": f"Copy {guide.env_setup['env_file']} to .env and fill in values",
            "checked": False,
            "category": "environment",
        })

    for var in guide.env_setup.get("env_vars", [])[:10]:
        items.append({
            "id": f"env-{var.lower()}",
            "label": f"Set {var}",
            "detail": f"Configure {var} in .env",
            "checked": False,
            "category": "environment",
        })

    # Gotchas as checklist items
    for i, gotcha in enumerate(guide.gotchas):
        items.append({
            "id": f"gotcha-{i}",
            "label": gotcha,
            "detail": "",
            "checked": False,
            "category": "gotchas",
        })

    return {
        "type": "checklist",
        "title": f"{guide.project_info.name} -- Onboarding Checklist",
        "items": items,
        "categories": ["setup", "environment", "gotchas"],
    }


def generate_mcp_concept_map(guide: OnboardingGuide) -> dict:
    """Generate an MCP-compatible concept map of the project architecture.

    Returns a dict with nodes (directories/components) and edges (relationships).
    """
    nodes = []
    edges = []

    # Project root node
    nodes.append({
        "id": "root",
        "label": guide.project_info.name,
        "type": "project",
        "metadata": {
            "languages": guide.project_info.languages,
            "frameworks": guide.project_info.frameworks,
        },
    })

    # Parse architecture overview for directory nodes
    for line in guide.architecture_overview.split("\n"):
        line = line.strip()
        # Match "dirname/ -- purpose" from analyze_architecture output
        dir_match = re.match(r'^(\S+)/\s+--\s+(.+)$', line)
        if dir_match:
            dir_name = dir_match.group(1)
            purpose = dir_match.group(2)
            node_id = f"dir-{dir_name}"
            nodes.append({
                "id": node_id,
                "label": f"{dir_name}/",
                "type": "directory",
                "metadata": {"purpose": purpose},
            })
            edges.append({
                "source": "root",
                "target": node_id,
                "relationship": "contains",
            })

    # Add tech stack nodes
    for framework in guide.project_info.frameworks:
        fw_id = f"fw-{framework.lower().replace(' ', '-').replace('.', '-')}"
        nodes.append({
            "id": fw_id,
            "label": framework,
            "type": "framework",
        })
        edges.append({
            "source": "root",
            "target": fw_id,
            "relationship": "uses",
        })

    # Add testing node if present
    if guide.testing_info and guide.testing_info.get("framework"):
        test_id = f"test-{guide.testing_info['framework'].lower()}"
        nodes.append({
            "id": test_id,
            "label": f"Tests ({guide.testing_info['framework']})",
            "type": "testing",
            "metadata": {"command": guide.testing_info.get("command", "")},
        })
        edges.append({
            "source": "root",
            "target": test_id,
            "relationship": "tested-by",
        })

    return {
        "type": "concept_map",
        "title": f"{guide.project_info.name} -- Architecture Map",
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate project onboarding guides",
        prog="onboarding_guide",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # generate command
    gen_parser = subparsers.add_parser("generate", help="Generate full onboarding guide")
    gen_parser.add_argument("--path", default=".", help="Project directory path (default: current dir)")
    gen_parser.add_argument(
        "--format",
        choices=["markdown", "slack", "json", "checklist", "concept_map"],
        default="markdown",
        help="Output format (default: markdown)",
    )

    # detect command
    det_parser = subparsers.add_parser("detect", help="Detect tech stack only")
    det_parser.add_argument("--path", default=".", help="Project directory path (default: current dir)")

    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "detect":
        path = str(Path(args.path).resolve())
        stack = detect_tech_stack(path)
        print(json.dumps(stack, indent=2))
        return 0

    if args.command == "generate":
        path = str(Path(args.path).resolve())
        guide = generate_onboarding_guide(path)

        formatters = {
            "markdown": format_markdown,
            "slack": format_slack,
            "json": format_json,
            "checklist": lambda g: json.dumps(generate_mcp_checklist(g), indent=2),
            "concept_map": lambda g: json.dumps(generate_mcp_concept_map(g), indent=2),
        }

        output = formatters[args.format](guide)
        print(output)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
