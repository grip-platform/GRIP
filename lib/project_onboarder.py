#!/usr/bin/env python3
"""Project Onboarder — core automation for GRIP project onboarding.

Implements the 5-phase pipeline: EXPLORE -> GENERATE -> SCORE -> DELIVER -> EVOLVE.
Used by the deploying-grip-to-projects skill and /grip-onboard command.

Usage:
    python3 -m lib.project_onboarder explore /path/to/repo
    python3 -m lib.project_onboarder generate /path/to/repo --lead K>>
    python3 -m lib.project_onboarder score /path/to/analysis.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class StackProfile:
    """Detected technology stack."""
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    build_tools: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    platform: str = "unknown"  # web, desktop, mobile, backend, cli


@dataclass
class QualityGap:
    """A detected quality gap in the project."""
    category: str   # tests, types, logging, security, ci, docs
    severity: str   # critical, high, medium, low
    description: str
    recommendation: str


@dataclass
class APIEndpoint:
    """A detected API endpoint or IPC handler."""
    method: str     # GET, POST, ipcMain.handle, etc.
    path: str       # /api/users, channel-name, etc.
    file: str       # Source file
    line: int = 0   # Line number


@dataclass
class RepoAnalysis:
    """Complete analysis of a repository."""
    name: str = ""
    repo_url: str = ""
    stack: StackProfile = field(default_factory=StackProfile)
    loc: dict[str, int] = field(default_factory=dict)
    file_count: int = 0
    quality_gaps: list[QualityGap] = field(default_factory=list)
    api_surface: list[APIEndpoint] = field(default_factory=list)
    ci_workflows: list[str] = field(default_factory=list)
    existing_claude_md: Optional[str] = None
    prs_open: list[dict] = field(default_factory=list)
    issues_open: list[dict] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    readme_exists: bool = False
    symbol_summary: Optional[dict] = None
    django_components: Optional[dict] = None

    @property
    def framework_components(self) -> Optional[dict]:
        """Alias for django_components."""
        return self.django_components

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OnboardScorecard:
    """Quality scoring for onboarding deliverables."""
    accuracy: float = 0.0       # 0-100, weight 30%
    completeness: float = 0.0   # 0-100, weight 30%
    actionability: float = 0.0  # 0-100, weight 20%
    delight: float = 0.0        # 0-100, weight 20%

    @property
    def composite(self) -> float:
        return (
            self.accuracy * 0.30
            + self.completeness * 0.30
            + self.actionability * 0.20
            + self.delight * 0.20
        )

    @property
    def spread(self) -> float:
        scores = [self.accuracy, self.completeness, self.actionability, self.delight]
        return max(scores) - min(scores)

    @property
    def anti_attractor_ok(self) -> bool:
        """Require at least 10-point spread to avoid suspect uniformity."""
        return self.spread >= 10.0


# ---------------------------------------------------------------------------
# Phase 1: EXPLORE
# ---------------------------------------------------------------------------

# Stack detection markers
STACK_MARKERS = {
    "package.json": ("Node.js", "npm"),
    "yarn.lock": ("Node.js", "yarn"),
    "pnpm-lock.yaml": ("Node.js", "pnpm"),
    "bun.lockb": ("Node.js", "bun"),
    "requirements.txt": ("Python", "pip"),
    "pyproject.toml": ("Python", "pip"),
    "Pipfile": ("Python", "pipenv"),
    "Cargo.toml": ("Rust", "cargo"),
    "go.mod": ("Go", "go"),
    "Gemfile": ("Ruby", "bundler"),
    "build.gradle": ("Kotlin", "gradle"),
    "build.gradle.kts": ("Kotlin", "gradle"),
    "Package.swift": ("Swift", "spm"),
    "Podfile": ("Swift", "cocoapods"),
    "composer.json": ("PHP", "composer"),
    "pom.xml": ("Java", "maven"),
}

FRAMEWORK_MARKERS = {
    "next": "Next.js",
    "nuxt": "Nuxt",
    "vite": "Vite",
    "webpack": "Webpack",
    "electron-builder": "Electron",
    "react": "React",
    "vue": "Vue",
    "angular": "Angular",
    "express": "Express",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
}

# File extensions for LOC counting
LOC_EXTENSIONS = {
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".py": "Python",
    ".rs": "Rust",
    ".go": "Go",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".php": "PHP",
    ".css": "CSS",
    ".scss": "SCSS",
    ".html": "HTML",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".sh": "Shell",
    ".sql": "SQL",
}

# Directories to skip during analysis
SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "target", "vendor", "Pods", ".git",
    ".gradle", ".idea", ".vscode", "coverage", ".nyc_output",
}

# Test file patterns
TEST_PATTERNS = [
    "test_", "_test.", ".test.", ".spec.", "_spec.",
    "tests/", "test/", "__tests__/", "spec/",
]


def detect_stack(repo_path: Path) -> StackProfile:
    """Detect technology stack from config files and dependencies."""
    profile = StackProfile()
    languages_seen = set()
    managers_seen = set()

    for marker_file, (lang, manager) in STACK_MARKERS.items():
        # Check root and one level deep
        candidates = [repo_path / marker_file]
        for subdir in repo_path.iterdir():
            if subdir.is_dir() and subdir.name not in SKIP_DIRS:
                candidates.append(subdir / marker_file)

        for candidate in candidates:
            if candidate.exists():
                languages_seen.add(lang)
                managers_seen.add(manager)

    profile.languages = sorted(languages_seen)
    profile.package_managers = sorted(managers_seen)

    # Detect frameworks from package.json dependencies
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            for dep_name, framework_name in FRAMEWORK_MARKERS.items():
                if dep_name in all_deps:
                    profile.frameworks.append(framework_name)
        except (json.JSONDecodeError, OSError):
            pass

    # Detect frameworks from Python requirements
    req_txt = repo_path / "requirements.txt"
    if req_txt.exists():
        try:
            content = req_txt.read_text().lower()
            for marker, framework in FRAMEWORK_MARKERS.items():
                if marker.lower() in content:
                    profile.frameworks.append(framework)
        except OSError:
            pass

    profile.frameworks = sorted(set(profile.frameworks))

    # Detect platform
    if "Electron" in profile.frameworks:
        profile.platform = "desktop"
    elif any(f in profile.frameworks for f in ["React", "Vue", "Angular", "Next.js"]):
        profile.platform = "web"
    elif "Swift" in profile.languages or "Kotlin" in profile.languages:
        profile.platform = "mobile"
    elif any(f in profile.frameworks for f in ["Express", "FastAPI", "Flask", "Django"]):
        profile.platform = "backend"
    elif profile.languages:
        profile.platform = "backend"

    return profile


def count_loc(repo_path: Path) -> dict[str, int]:
    """Count lines of code per language, excluding dependency directories."""
    loc = {}
    for path in repo_path.rglob("*"):
        if path.is_file() and not any(skip in path.parts for skip in SKIP_DIRS):
            ext = path.suffix.lower()
            lang = LOC_EXTENSIONS.get(ext)
            if lang:
                try:
                    line_count = len(path.read_text(errors="replace").splitlines())
                    loc[lang] = loc.get(lang, 0) + line_count
                except OSError:
                    pass
    return loc


def count_files(repo_path: Path) -> int:
    """Count source files excluding dependency directories."""
    count = 0
    for path in repo_path.rglob("*"):
        if path.is_file() and not any(skip in path.parts for skip in SKIP_DIRS):
            if path.suffix.lower() in LOC_EXTENSIONS:
                count += 1
    return count


def find_test_files(repo_path: Path) -> list[str]:
    """Find all test files in the repo."""
    test_files = []
    for path in repo_path.rglob("*"):
        if path.is_file() and not any(skip in path.parts for skip in SKIP_DIRS):
            rel = str(path.relative_to(repo_path))
            if any(pattern in rel.lower() for pattern in TEST_PATTERNS):
                test_files.append(rel)
    return test_files


def find_ci_workflows(repo_path: Path) -> list[str]:
    """Find CI/CD workflow files."""
    workflows = []
    gh_dir = repo_path / ".github" / "workflows"
    if gh_dir.exists():
        for f in gh_dir.iterdir():
            if f.suffix in (".yml", ".yaml"):
                workflows.append(f.name)
    # Also check for other CI
    for ci_file in [".travis.yml", "Jenkinsfile", ".circleci/config.yml",
                    "azure-pipelines.yml", ".gitlab-ci.yml"]:
        if (repo_path / ci_file).exists():
            workflows.append(ci_file)
    return workflows


def map_quality_gaps(
    repo_path: Path,
    stack: StackProfile,
    test_files: list[str],
    ci_workflows: list[str],
) -> list[QualityGap]:
    """Identify quality gaps based on repo analysis."""
    gaps = []

    # Tests
    if not test_files:
        gaps.append(QualityGap(
            category="tests",
            severity="critical",
            description="No test files found in the repository",
            recommendation="Add test framework and write unit tests for core logic",
        ))
    elif len(test_files) < 5:
        gaps.append(QualityGap(
            category="tests",
            severity="high",
            description=f"Only {len(test_files)} test files found — coverage likely insufficient",
            recommendation="Increase test coverage to at least 60%",
        ))

    # CI/CD
    if not ci_workflows:
        gaps.append(QualityGap(
            category="ci",
            severity="high",
            description="No CI/CD workflows found",
            recommendation="Add GitHub Actions workflow for lint + test on PR",
        ))

    # TypeScript (for JS projects)
    if "Node.js" in stack.languages:
        ts_config = repo_path / "tsconfig.json"
        if not ts_config.exists():
            # Check if any .ts files exist
            has_ts = any((repo_path / f).exists() for f in ["tsconfig.json"])
            if not has_ts:
                gaps.append(QualityGap(
                    category="types",
                    severity="medium",
                    description="JavaScript project without TypeScript",
                    recommendation="Consider gradual TypeScript migration for type safety",
                ))

    # Logging
    has_logging = False
    for log_dep in ["winston", "pino", "bunyan", "electron-log", "logging", "loguru"]:
        pkg_json = repo_path / "package.json"
        req_txt = repo_path / "requirements.txt"
        if pkg_json.exists():
            try:
                content = pkg_json.read_text()
                if log_dep in content:
                    has_logging = True
                    break
            except OSError:
                pass
        if req_txt.exists():
            try:
                content = req_txt.read_text()
                if log_dep in content:
                    has_logging = True
                    break
            except OSError:
                pass
    if not has_logging:
        gaps.append(QualityGap(
            category="logging",
            severity="medium",
            description="No structured logging framework detected",
            recommendation="Add structured logging (winston/pino for Node, loguru for Python)",
        ))

    # Documentation
    readme = repo_path / "README.md"
    if not readme.exists():
        gaps.append(QualityGap(
            category="docs",
            severity="medium",
            description="No README.md found",
            recommendation="Add README with project overview, setup, and usage instructions",
        ))

    # Security: check for .env in git
    env_file = repo_path / ".env"
    gitignore = repo_path / ".gitignore"
    if env_file.exists():
        env_in_gitignore = False
        if gitignore.exists():
            try:
                content = gitignore.read_text()
                env_in_gitignore = ".env" in content
            except OSError:
                pass
        if not env_in_gitignore:
            gaps.append(QualityGap(
                category="security",
                severity="critical",
                description=".env file exists but may not be in .gitignore",
                recommendation="Ensure .env is in .gitignore and remove from git history if committed",
            ))

    return gaps


def explore_repo(repo_path: Path, use_code_index: bool = True) -> RepoAnalysis:
    """Phase 1: Full exploration of a repository.

    Returns a RepoAnalysis with stack, LOC, quality gaps, and more.
    When use_code_index is True, also populates api_surface, symbol_summary,
    and django_components via AST-based symbol indexing.
    """
    repo_path = Path(repo_path)
    if not repo_path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")

    analysis = RepoAnalysis()
    analysis.name = repo_path.name

    # Detect stack
    analysis.stack = detect_stack(repo_path)

    # Count LOC and files
    analysis.loc = count_loc(repo_path)
    analysis.file_count = count_files(repo_path)

    # Find tests and CI
    analysis.test_files = find_test_files(repo_path)
    analysis.ci_workflows = find_ci_workflows(repo_path)

    # Check for existing CLAUDE.md
    claude_md = repo_path / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        try:
            analysis.existing_claude_md = claude_md.read_text()
        except OSError:
            pass

    # Check README
    analysis.readme_exists = (repo_path / "README.md").exists()

    # Quality gaps
    analysis.quality_gaps = map_quality_gaps(
        repo_path, analysis.stack, analysis.test_files, analysis.ci_workflows
    )

    # Phase 1.7: AST-based symbol indexing (code-index integration)
    if use_code_index and "Python" in analysis.stack.languages:
        try:
            from lib.code_index.index import CodeIndex
            idx = CodeIndex(db_path=":memory:")
            idx.index_project(str(repo_path))
            # Populate api_surface from AST-detected endpoints
            api_data = idx.get_api_endpoints()
            analysis.api_surface = [
                APIEndpoint(
                    method=ep.get("method", ""),
                    path=ep.get("path", ""),
                    file=ep.get("file", ""),
                    line=ep.get("line", 0),
                )
                for ep in api_data
            ]
            analysis.symbol_summary = idx.get_overview()
            analysis.django_components = idx.get_django_components()
            idx.close()
        except Exception:
            pass  # Graceful degradation — onboarding works without code-index

    return analysis


# ---------------------------------------------------------------------------
# Phase 2: GENERATE (templates)
# ---------------------------------------------------------------------------

# Interaction mode templates
INTERACTION_MODES = {
    "concise": {
        "style": "Dense, no fluff. Bullet points over paragraphs.",
        "prompt_prefix": "Execute directly:",
        "detail_level": "minimal",
    },
    "explanatory": {
        "style": "Thorough, educational. Explain why and how.",
        "prompt_prefix": "Here is the context and approach:",
        "detail_level": "high",
    },
    "direct": {
        "style": "Action-first. No preamble.",
        "prompt_prefix": "",
        "detail_level": "medium",
    },
    "confirm-first": {
        "style": "Include decision points. Halt before destructive actions.",
        "prompt_prefix": "Review and confirm:",
        "detail_level": "high",
    },
    "supportive": {
        "style": "Encouraging, detailed feedback.",
        "prompt_prefix": "Let me walk you through this:",
        "detail_level": "high",
    },
}


def generate_claude_md(analysis: RepoAnalysis, lead: str = "V>>",
                       interaction_mode: str = "direct") -> str:
    """Generate a CLAUDE.md for the project based on analysis."""
    mode = INTERACTION_MODES.get(interaction_mode, INTERACTION_MODES["direct"])

    sections = []

    # ultrathink++ directive (ALWAYS at top)
    sections.append("## Reasoning (ALWAYS ACTIVE)\n")
    sections.append("ultrathink++ is ALWAYS enabled. Every response uses extended reasoning.\n")

    # Project identity
    sections.append(f"# {analysis.name}\n")
    sections.append(f"**Stack**: {', '.join(analysis.stack.languages)} | "
                    f"**Frameworks**: {', '.join(analysis.stack.frameworks)} | "
                    f"**Platform**: {analysis.stack.platform}\n")

    # LOC summary
    if analysis.loc:
        loc_lines = []
        for lang, count in sorted(analysis.loc.items(), key=lambda x: -x[1]):
            loc_lines.append(f"| {lang} | {count:,} |")
        sections.append("## Lines of Code\n")
        sections.append("| Language | Lines |")
        sections.append("|----------|-------|")
        sections.extend(loc_lines)
        sections.append("")

    # Quality roadmap
    if analysis.quality_gaps:
        sections.append("## Quality Roadmap\n")
        for i, gap in enumerate(analysis.quality_gaps, 1):
            severity_badge = {"critical": "CRITICAL", "high": "HIGH",
                              "medium": "MEDIUM", "low": "LOW"}.get(gap.severity, "?")
            sections.append(f"### {i}. [{severity_badge}] {gap.category.title()}\n")
            sections.append(f"{gap.description}\n")
            sections.append(f"**Recommendation**: {gap.recommendation}\n")

    # API Surface (from code-index AST analysis)
    if analysis.api_surface:
        sections.append("## API Surface\n")
        sections.append("| Handler | Type | File |")
        sections.append("|---------|------|------|")
        for ep in analysis.api_surface[:20]:  # Cap at 20 for readability
            sections.append(f"| {ep.path} | {ep.method} | {ep.file}:{ep.line} |")
        if len(analysis.api_surface) > 20:
            sections.append(f"\n*... and {len(analysis.api_surface) - 20} more endpoints*\n")
        sections.append("")

    # Symbol Overview (from code-index)
    if analysis.symbol_summary:
        ss = analysis.symbol_summary
        sections.append("## Codebase Structure\n")
        sections.append(f"- **Classes**: {ss.get('classes', 0):,}")
        sections.append(f"- **Functions**: {ss.get('functions', 0):,}")
        sections.append(f"- **Methods**: {ss.get('methods', 0):,}")
        sections.append(f"- **Files indexed**: {ss.get('files_indexed', 0):,}")
        sections.append("")

    # Framework Components (from code-index)
    if analysis.django_components:
        sections.append("## Framework Components\n")
        sections.append("| Type | Count |")
        sections.append("|------|-------|")
        for dtype, count in sorted(analysis.django_components.items()):
            sections.append(f"| {dtype.title()} | {count:,} |")
        sections.append("")

    # GRIP commands cheat sheet
    sections.append("## GRIP Commands\n")
    sections.append("| Command | Purpose |")
    sections.append("|---------|---------|")
    sections.append("| `/refresh-context` | Rebuild mental model |")
    sections.append("| `/create-pr` | Branch, commit, push, PR |")
    sections.append("| `/converge` | Recursive convergence loop |")
    sections.append("| `/audit-efficiency` | Score 0-100 |")
    sections.append("| `/save` | Persist all memory subsystems |")
    sections.append("")

    return "\n".join(sections)


def generate_prompts(analysis: RepoAnalysis, lead: str = "V>>",
                     interaction_mode: str = "direct") -> str:
    """Generate GRIP-PROMPTS.md with copy-paste prompts for the lead."""
    mode = INTERACTION_MODES.get(interaction_mode, INTERACTION_MODES["direct"])
    sections = []

    sections.append(f"# GRIP Prompts for {lead}\n")
    sections.append(f"Interaction mode: **{interaction_mode}**\n")
    sections.append("Copy-paste these prompts to start GRIP sessions.\n")

    # Session start
    sections.append("## Session Start\n")
    sections.append("```")
    sections.append(f"ultrathink++ ALWAYS ON. I am {lead} working on {analysis.name}.")
    sections.append(f"Stack: {', '.join(analysis.stack.languages + analysis.stack.frameworks)}.")
    sections.append("/refresh-context")
    sections.append("```\n")

    # Quality fix prompts
    if analysis.quality_gaps:
        for gap in analysis.quality_gaps:
            sections.append(f"## Fix: {gap.category.title()} ({gap.severity})\n")
            sections.append("```")
            sections.append(f"converge(")
            sections.append(f'  task="Fix {gap.category} gap in {analysis.name}",')
            sections.append(f'  criterion="{gap.recommendation}",')
            sections.append(f"  max_depth=2")
            sections.append(f")")
            sections.append("```\n")

    return "\n".join(sections)


def generate_architecture_doc(analysis: RepoAnalysis) -> str:
    """Generate ARCHITECTURE-ANALYSIS.md."""
    sections = []
    sections.append(f"# Architecture Analysis: {analysis.name}\n")
    sections.append(f"Generated by GRIP project onboarding pipeline.\n")

    # Stack overview
    sections.append("## Technology Stack\n")
    sections.append(f"- **Languages**: {', '.join(analysis.stack.languages) or 'Unknown'}")
    sections.append(f"- **Frameworks**: {', '.join(analysis.stack.frameworks) or 'None detected'}")
    sections.append(f"- **Build Tools**: {', '.join(analysis.stack.build_tools) or 'None detected'}")
    sections.append(f"- **Platform**: {analysis.stack.platform}")
    sections.append(f"- **Files**: {analysis.file_count}")
    sections.append(f"- **Total LOC**: {sum(analysis.loc.values()):,}\n")

    # Quality gap matrix
    if analysis.quality_gaps:
        sections.append("## Quality Gap Matrix\n")
        sections.append("| Category | Severity | Description |")
        sections.append("|----------|----------|-------------|")
        for gap in analysis.quality_gaps:
            sections.append(f"| {gap.category} | {gap.severity} | {gap.description} |")
        sections.append("")

    # CI/CD
    sections.append("## CI/CD\n")
    if analysis.ci_workflows:
        for wf in analysis.ci_workflows:
            sections.append(f"- {wf}")
    else:
        sections.append("No CI/CD workflows detected.")
    sections.append("")

    return "\n".join(sections)


def generate_novel_features(analysis: RepoAnalysis) -> str:
    """Generate NOVEL-FEATURES.md with feature proposals."""
    sections = []
    sections.append(f"# Novel Feature Proposals: {analysis.name}\n")
    sections.append("Feature proposals based on architecture analysis.\n")
    sections.append("| # | Feature | Effort | Impact | Priority |")
    sections.append("|---|---------|--------|--------|----------|")
    sections.append("| 1 | Comprehensive test suite | M | High | P1 |")
    sections.append("| 2 | CI/CD pipeline | S | High | P1 |")
    sections.append("| 3 | Structured logging | S | Medium | P2 |")
    sections.append("| 4 | Error handling improvements | M | High | P2 |")
    sections.append("| 5 | API documentation | S | Medium | P3 |")
    sections.append("| 6 | Performance monitoring | M | Medium | P3 |")
    sections.append("| 7 | Security audit fixes | L | High | P1 |")
    sections.append("| 8 | Developer onboarding guide | S | Low | P4 |")
    sections.append("")
    sections.append("**Effort**: S = <1 day, M = 1-3 days, L = 1 week+")
    sections.append("")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Phase 3: SCORE
# ---------------------------------------------------------------------------

def score_deliverables(analysis: RepoAnalysis, claude_md: str,
                       prompts: str, arch_doc: str) -> OnboardScorecard:
    """Score the generated deliverables.

    Uses heuristic scoring (not LLM-as-judge) for deterministic testing.
    LLM-as-judge scoring happens at the caller level.
    """
    scorecard = OnboardScorecard()

    # Accuracy: does CLAUDE.md reference actual languages/frameworks?
    accuracy_hits = 0
    accuracy_total = max(len(analysis.stack.languages) + len(analysis.stack.frameworks), 1)
    for lang in analysis.stack.languages:
        if lang.lower() in claude_md.lower():
            accuracy_hits += 1
    for fw in analysis.stack.frameworks:
        if fw.lower() in claude_md.lower():
            accuracy_hits += 1
    # Bonus for AST-derived data (symbol counts, API surface)
    if analysis.symbol_summary and "Codebase Structure" in claude_md:
        accuracy_hits += 1
        accuracy_total += 1
    if analysis.api_surface and "API Surface" in claude_md:
        accuracy_hits += 1
        accuracy_total += 1
    scorecard.accuracy = min(100, (accuracy_hits / accuracy_total) * 100)

    # Completeness: are all quality gaps AND expected sections present?
    completeness_hits = 0
    completeness_total = 0
    # Quality gap categories
    for gap in analysis.quality_gaps:
        completeness_total += 1
        if gap.category.lower() in claude_md.lower():
            completeness_hits += 1
    # Expected enrichment sections (code-index makes these possible)
    for section in ["API Surface", "Codebase Structure", "Framework Components"]:
        completeness_total += 1
        if section in claude_md:
            completeness_hits += 1
    completeness_total = max(completeness_total, 1)
    scorecard.completeness = min(100, (completeness_hits / completeness_total) * 100)

    # Actionability: do prompts contain converge() calls?
    converge_count = prompts.count("converge(")
    scorecard.actionability = min(100, converge_count * 25)  # 4 converges = 100

    # Delight: check for ultrathink++, GRIP commands, quality roadmap
    delight_hits = 0
    if "ultrathink++" in claude_md:
        delight_hits += 1
    if "GRIP Commands" in claude_md:
        delight_hits += 1
    if "Quality Roadmap" in claude_md:
        delight_hits += 1
    if "Architecture" in arch_doc:
        delight_hits += 1
    scorecard.delight = min(100, delight_hits * 25)

    return scorecard


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 -m lib.project_onboarder <command> <repo_path>")
        print("Commands: explore, generate, score")
        sys.exit(1)

    command = sys.argv[1]
    repo_path = Path(sys.argv[2])

    use_ci = "--use-code-index" in sys.argv

    if command == "explore":
        analysis = explore_repo(repo_path, use_code_index=use_ci)
        print(json.dumps(analysis.to_dict(), indent=2, default=str))
    elif command == "generate":
        analysis = explore_repo(repo_path, use_code_index=use_ci)
        lead = sys.argv[4] if len(sys.argv) > 4 else "V>>"
        mode = "direct"
        claude_md = generate_claude_md(analysis, lead, mode)
        print(claude_md)
    elif command == "score":
        analysis = explore_repo(repo_path, use_code_index=use_ci)
        claude_md = generate_claude_md(analysis)
        prompts = generate_prompts(analysis)
        arch_doc = generate_architecture_doc(analysis)
        scorecard = score_deliverables(analysis, claude_md, prompts, arch_doc)
        print(f"Composite: {scorecard.composite:.1f}")
        print(f"  Accuracy: {scorecard.accuracy:.1f}")
        print(f"  Completeness: {scorecard.completeness:.1f}")
        print(f"  Actionability: {scorecard.actionability:.1f}")
        print(f"  Delight: {scorecard.delight:.1f}")
        print(f"  Anti-attractor OK: {scorecard.anti_attractor_ok}")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
