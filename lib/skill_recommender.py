#!/usr/bin/env python3
"""Context-aware skill recommendation engine for GRIP.

Suggests relevant skills based on working context by matching against
skill metadata dynamically extracted from skills/*/SKILL.md files.
Integrates git diff, branch name, file types, and recent commits as
context signals. Uses TF-IDF cosine similarity for ranking.

Usage:
    python3 lib/skill_recommender.py                      # auto-detect context
    python3 lib/skill_recommender.py --context "fix auth"  # manual context
    python3 lib/skill_recommender.py --top 10              # more results
    python3 lib/skill_recommender.py --json                # JSON output
    python3 lib/skill_recommender.py --explain             # verbose reasoning

Version: 3.0.0
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "them", "this", "that", "these", "those", "what", "which",
    "who", "whom", "how", "when", "where", "why", "if", "then", "else",
    "so", "but", "and", "or", "not", "no", "nor", "for", "with",
    "about", "of", "to", "from", "in", "on", "at", "by", "as", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "up", "down", "out", "off", "over", "under", "again", "further",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "than", "too", "very",
    "just", "also", "use", "using", "used", "see", "file", "files",
})

# File extension to domain keyword mapping
EXT_DOMAIN_MAP: dict[str, list[str]] = {
    ".py": ["python", "code", "development"],
    ".js": ["javascript", "code", "frontend", "development"],
    ".ts": ["typescript", "code", "frontend", "development"],
    ".tsx": ["typescript", "react", "frontend", "code", "development"],
    ".jsx": ["javascript", "react", "frontend", "code", "development"],
    ".css": ["styling", "frontend", "design", "code"],
    ".scss": ["styling", "frontend", "design", "code"],
    ".html": ["frontend", "markup", "code"],
    ".md": ["documentation", "writing", "markdown"],
    ".yml": ["configuration", "deployment", "devops"],
    ".yaml": ["configuration", "deployment", "devops"],
    ".json": ["configuration", "data"],
    ".sh": ["bash", "scripting", "automation", "shell"],
    ".bash": ["bash", "scripting", "automation", "shell"],
    ".zsh": ["shell", "scripting", "automation"],
    ".rs": ["rust", "code", "development"],
    ".go": ["golang", "code", "development"],
    ".java": ["java", "code", "development"],
    ".rb": ["ruby", "code", "development"],
    ".sql": ["database", "sql", "data"],
    ".dockerfile": ["docker", "deployment", "devops", "containers"],
    ".tf": ["terraform", "infrastructure", "devops"],
    ".test.py": ["testing", "python", "code"],
    ".test.js": ["testing", "javascript", "code"],
    ".test.ts": ["testing", "typescript", "code"],
    ".spec.js": ["testing", "javascript", "code"],
    ".spec.ts": ["testing", "typescript", "code"],
}

# Branch name pattern to keyword mapping
BRANCH_PATTERN_MAP: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"feat/|feature/"), ["feature", "development", "code"]),
    (re.compile(r"fix/|bugfix/|hotfix/"), ["fix", "bug", "debugging", "code"]),
    (re.compile(r"refactor/"), ["refactor", "code", "design", "cleanup"]),
    (re.compile(r"test/"), ["testing", "test", "quality"]),
    (re.compile(r"docs/|doc/"), ["documentation", "writing"]),
    (re.compile(r"chore/"), ["maintenance", "cleanup", "chore"]),
    (re.compile(r"ci/|devops/|infra/"), ["devops", "deployment", "infrastructure"]),
    (re.compile(r"security/|sec/"), ["security", "vulnerability", "audit"]),
    (re.compile(r"perf/|performance/"), ["performance", "optimization", "profiling"]),
    (re.compile(r"release/"), ["release", "deployment", "changelog"]),
]

# Sections in SKILL.md that carry high keyword signal
_KEYWORD_SECTION_PATTERNS = [
    re.compile(r"^#+\s*(?:when\s+to\s+use|use\s+when|triggers?)", re.IGNORECASE),
    re.compile(r"^#+\s*(?:purpose|description|overview)", re.IGNORECASE),
]

# YAML frontmatter
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_YAML_FIELD_RE = re.compile(r"^(\w[\w_-]*):\s*(.+)$", re.MULTILINE)
_YAML_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.+)$", re.MULTILINE)

# Context signal weight multipliers
WEIGHT_DESCRIPTION = 2.0     # Skill description terms
WEIGHT_TRIGGERS = 3.0        # Skill trigger terms
WEIGHT_KEYWORDS = 1.5        # General extracted keywords
WEIGHT_GIT_DIFF = 1.5        # Git diff file signals
WEIGHT_BRANCH = 2.0          # Branch name signals
WEIGHT_COMMIT_MSG = 1.2      # Recent commit message signals
WEIGHT_FILE_TYPES = 1.0      # Working directory file types


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SkillInfo:
    """Metadata for a single GRIP skill."""

    name: str
    path: str
    description: str
    keywords: list[str] = field(default_factory=list)
    usage_count: int = 0
    last_used: str | None = None
    triggers: list[str] = field(default_factory=list)

    # Weighted keyword groups for TF-IDF
    description_tokens: list[str] = field(default_factory=list)
    trigger_tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "keywords": self.keywords,
            "usage_count": self.usage_count,
            "last_used": self.last_used,
            "triggers": self.triggers,
        }


@dataclass
class ContextSignals:
    """Working context signals extracted from the environment."""

    manual_context: str = ""
    git_diff_files: list[str] = field(default_factory=list)
    git_diff_extensions: list[str] = field(default_factory=list)
    branch_name: str = ""
    recent_commits: list[str] = field(default_factory=list)
    working_dir_extensions: list[str] = field(default_factory=list)

    def to_tokens(self) -> list[str]:
        """Convert all signals to weighted token list."""
        tokens: list[str] = []

        # Manual context (highest signal)
        if self.manual_context:
            tokens.extend(_tokenize(self.manual_context))

        # Git diff file names and extensions
        for f in self.git_diff_files:
            tokens.extend(_tokenize(Path(f).stem))
        for ext in self.git_diff_extensions:
            tokens.extend(EXT_DOMAIN_MAP.get(ext, []))

        # Branch name
        if self.branch_name:
            tokens.extend(_tokenize(self.branch_name.replace("/", " ").replace("-", " ")))
            for pattern, kws in BRANCH_PATTERN_MAP:
                if pattern.search(self.branch_name):
                    tokens.extend(kws)

        # Recent commit messages
        for msg in self.recent_commits:
            tokens.extend(_tokenize(msg))

        # Working directory file types
        for ext in self.working_dir_extensions:
            tokens.extend(EXT_DOMAIN_MAP.get(ext, []))

        # Filter stop words and deduplicate
        return [t for t in tokens if t not in STOP_WORDS and len(t) > 2]

    def summary(self) -> dict[str, Any]:
        """Return a summary of detected signals for explanation."""
        signals: dict[str, Any] = {}
        if self.manual_context:
            signals["manual_context"] = self.manual_context
        if self.git_diff_files:
            signals["changed_files"] = self.git_diff_files[:10]
        if self.git_diff_extensions:
            signals["changed_file_types"] = list(set(self.git_diff_extensions))
        if self.branch_name:
            signals["branch"] = self.branch_name
        if self.recent_commits:
            signals["recent_commits"] = self.recent_commits[:5]
        if self.working_dir_extensions:
            signals["working_dir_types"] = list(set(self.working_dir_extensions))[:10]
        return signals


@dataclass
class Recommendation:
    """A skill recommendation with relevance scoring."""

    skill_name: str
    relevance_score: float  # 0.0 - 1.0
    reason: str
    skill_info: SkillInfo | None = None
    matched_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "skill_name": self.skill_name,
            "relevance_score": round(self.relevance_score, 4),
            "reason": self.reason,
        }
        if self.skill_info:
            d["path"] = self.skill_info.path
            d["description"] = self.skill_info.description
            d["usage_count"] = self.skill_info.usage_count
        if self.matched_signals:
            d["matched_signals"] = self.matched_signals
        return d


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens, filtering short/stop words."""
    return [w for w in re.findall(r"[a-z][a-z0-9_-]+", text.lower()) if len(w) > 2]


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter fields from SKILL.md content."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}

    fm_text = m.group(1)
    fields: dict[str, Any] = {}

    for field_match in _YAML_FIELD_RE.finditer(fm_text):
        key = field_match.group(1)
        val = field_match.group(2).strip().strip('"').strip("'")
        fields[key] = val

    # Extract list items for triggers, integrates, see_also, tags
    for list_key in ("triggers", "integrates", "see_also", "tags"):
        # Try inline list first: key: [item1, item2]
        inline_pattern = re.compile(
            rf"^{list_key}:\s*\[([^\]]*)\]", re.MULTILINE
        )
        inline_match = inline_pattern.search(fm_text)
        if inline_match:
            items = [
                i.strip().strip('"').strip("'")
                for i in inline_match.group(1).split(",")
            ]
            fields[list_key] = [i for i in items if i]
            continue

        # Try YAML list: key:\n  - item1\n  - item2
        yaml_list_pattern = re.compile(
            rf"^{list_key}:\s*\n((?:\s+-\s+.+\n?)+)", re.MULTILINE
        )
        yaml_match = yaml_list_pattern.search(fm_text)
        if yaml_match:
            items = _YAML_LIST_ITEM_RE.findall(yaml_match.group(1))
            fields[list_key] = [i.strip().strip('"').strip("'") for i in items]

    return fields


def _extract_frontmatter_keywords(fm: dict) -> list[str]:
    """Extract keywords from parsed YAML frontmatter fields."""
    keywords: list[str] = []
    if fm.get("name"):
        keywords.extend(_tokenize(fm["name"]))
    if fm.get("description"):
        keywords.extend(_tokenize(fm["description"]))
    for list_key in ("triggers", "integrates", "tags"):
        if isinstance(fm.get(list_key), list):
            for item in fm[list_key]:
                keywords.extend(_tokenize(item))
    return keywords


def _extract_first_paragraph(lines: list[str], heading_match) -> list[str]:
    """Extract the first prose paragraph after the heading."""
    found_heading = False
    in_first_para = False
    first_para_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not found_heading and not in_first_para:
            if heading_match and stripped == heading_match.group(0).strip():
                found_heading = True
                continue
            if not heading_match and stripped:
                in_first_para = True
                first_para_lines.append(stripped)
                continue
        elif found_heading and not in_first_para:
            if not stripped:
                continue
            if stripped.startswith("#"):
                break
            in_first_para = True
            first_para_lines.append(stripped)
        else:
            if not stripped or stripped.startswith("#"):
                break
            first_para_lines.append(stripped)
    return first_para_lines


def _extract_section_keywords(lines: list[str]) -> list[str]:
    """Extract keywords from high-signal sections (When to Use, Triggers, etc)."""
    keywords: list[str] = []
    in_keyword_section = False
    section_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(p.match(stripped) for p in _KEYWORD_SECTION_PATTERNS):
            in_keyword_section = True
            continue
        if in_keyword_section:
            if stripped.startswith("#"):
                keywords.extend(_tokenize(" ".join(section_lines)))
                section_lines = []
                in_keyword_section = any(
                    p.match(stripped) for p in _KEYWORD_SECTION_PATTERNS
                )
                continue
            if stripped:
                section_lines.append(stripped)
    if section_lines:
        keywords.extend(_tokenize(" ".join(section_lines)))
    return keywords


def extract_keywords(skill_md: str) -> list[str]:
    """Extract keywords from SKILL.md content.

    Pulls words from:
    - YAML frontmatter name, description, triggers, tags
    - First markdown heading (title)
    - First paragraph after heading
    - "When to Use" / "Use when" / "Triggers" sections
    - Integrated skill references
    """
    fm = _parse_frontmatter(skill_md)
    keywords = _extract_frontmatter_keywords(fm)

    body = _FRONTMATTER_RE.sub("", skill_md).strip()
    heading_match = re.match(r"^#+\s+(.+)$", body, re.MULTILINE)
    if heading_match:
        keywords.extend(_tokenize(heading_match.group(1)))

    lines = body.split("\n")
    first_para = _extract_first_paragraph(lines, heading_match)
    if first_para:
        keywords.extend(_tokenize(" ".join(first_para)))

    keywords.extend(_extract_section_keywords(lines))

    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen and kw not in STOP_WORDS:
            seen.add(kw)
            unique.append(kw)
    return unique


def _extract_description_tokens(skill_md: str) -> list[str]:
    """Extract tokens specifically from the description field (higher weight)."""
    fm = _parse_frontmatter(skill_md)
    desc = fm.get("description", "")
    if desc:
        return [t for t in _tokenize(desc) if t not in STOP_WORDS]
    return []


def _extract_trigger_tokens(skill_md: str) -> list[str]:
    """Extract tokens specifically from triggers (highest weight)."""
    fm = _parse_frontmatter(skill_md)
    tokens: list[str] = []
    if isinstance(fm.get("triggers"), list):
        for trigger in fm["triggers"]:
            tokens.extend([t for t in _tokenize(trigger) if t not in STOP_WORDS])
    return tokens


# ---------------------------------------------------------------------------
# Context signal gathering
# ---------------------------------------------------------------------------

def _run_git_command(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout, empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def gather_context_signals(
    manual_context: str = "",
    cwd: str | None = None,
) -> ContextSignals:
    """Gather working context signals from the environment.

    Analyses:
    - Git diff (changed files, file types)
    - Current branch name
    - Recent git log messages
    - Working directory file types
    """
    signals = ContextSignals(manual_context=manual_context)
    work_dir = cwd or os.getcwd()

    # Git diff: changed files
    diff_output = _run_git_command(["diff", "--name-only", "HEAD"], cwd=work_dir)
    if not diff_output:
        # Also check staged changes
        diff_output = _run_git_command(["diff", "--name-only", "--cached"], cwd=work_dir)
    if not diff_output:
        # Also check untracked
        diff_output = _run_git_command(
            ["ls-files", "--others", "--exclude-standard"], cwd=work_dir
        )

    if diff_output:
        changed = [f for f in diff_output.split("\n") if f.strip()]
        signals.git_diff_files = changed
        signals.git_diff_extensions = [
            Path(f).suffix.lower() for f in changed if Path(f).suffix
        ]

    # Branch name
    branch = _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=work_dir)
    signals.branch_name = branch

    # Recent commits (last 5)
    log_output = _run_git_command(
        ["log", "--oneline", "-5", "--format=%s"], cwd=work_dir
    )
    if log_output:
        signals.recent_commits = [
            msg for msg in log_output.split("\n") if msg.strip()
        ]

    # Working directory file extensions
    try:
        extensions: list[str] = []
        for entry in Path(work_dir).iterdir():
            if entry.is_file() and entry.suffix:
                extensions.append(entry.suffix.lower())
        signals.working_dir_extensions = extensions
    except OSError:
        pass

    return signals


# ---------------------------------------------------------------------------
# Heatmap integration
# ---------------------------------------------------------------------------

def _get_heatmap_data(skills_dir: str | None = None) -> dict[str, Any]:
    """Load usage and co-occurrence data from skill_heatmap if available."""
    try:
        lib_dir = str(Path(__file__).parent)
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        from skill_heatmap import build_heatmap, co_occurrence, scan_sessions

        project_dir = None
        if skills_dir:
            proj_path = Path(skills_dir).parent / "projects"
            if proj_path.exists():
                project_dir = str(proj_path)

        invocations = scan_sessions(project_dir=project_dir, limit=30)
        heatmap = build_heatmap(invocations)
        pairs = co_occurrence(invocations)

        usage_counts: dict[str, int] = {}
        last_used: dict[str, str | None] = {}
        for name, usage in heatmap.items():
            usage_counts[name] = usage.count
            last_used[name] = usage.last_used.isoformat() if usage.last_used else None

        pair_data: dict[str, list[str]] = {}
        for (a, b), _count in pairs.items():
            pair_data.setdefault(a, []).append(b)
            pair_data.setdefault(b, []).append(a)

        return {
            "usage_counts": usage_counts,
            "last_used": last_used,
            "co_occurrence": pair_data,
        }
    except Exception:
        return {"usage_counts": {}, "last_used": {}, "co_occurrence": {}}


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def _get_skills_dir(base_dir: str | None = None) -> Path:
    """Resolve the skills directory."""
    if base_dir:
        return Path(base_dir)
    return Path(
        os.environ.get("GRIP_DIR", os.environ.get("HOME", "~") + "/.claude")
    ) / "skills"


def load_skill_catalog(
    skills_dir: str | None = None,
    include_heatmap: bool = True,
) -> list[SkillInfo]:
    """Scan skills/*/SKILL.md and build a catalog of skill metadata.

    Args:
        skills_dir: Path to skills directory. Defaults to ~/.claude/skills.
        include_heatmap: Whether to enrich with heatmap usage data.

    Returns:
        List of SkillInfo objects sorted by name.
    """
    base = _get_skills_dir(skills_dir)
    if not base.exists():
        return []

    heatmap_data = (
        _get_heatmap_data(skills_dir)
        if include_heatmap
        else {"usage_counts": {}, "last_used": {}, "co_occurrence": {}}
    )

    catalog: list[SkillInfo] = []

    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith("_"):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if not content.strip():
            continue

        fm = _parse_frontmatter(content)
        name = fm.get("name", skill_dir.name)
        description = fm.get("description", "")

        if not description:
            body = _FRONTMATTER_RE.sub("", content).strip()
            for line in body.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    description = stripped
                    break

        triggers: list[str] = []
        if isinstance(fm.get("triggers"), list):
            triggers = fm["triggers"]

        keywords = extract_keywords(content)
        desc_tokens = _extract_description_tokens(content)
        trig_tokens = _extract_trigger_tokens(content)
        usage_count = heatmap_data["usage_counts"].get(name, 0)
        last_used = heatmap_data["last_used"].get(name)

        catalog.append(
            SkillInfo(
                name=name,
                path=str(skill_md),
                description=description,
                keywords=keywords,
                usage_count=usage_count,
                last_used=last_used,
                triggers=triggers,
                description_tokens=desc_tokens,
                trigger_tokens=trig_tokens,
            )
        )

    return catalog


# ---------------------------------------------------------------------------
# TF-IDF scoring engine
# ---------------------------------------------------------------------------

def _build_idf(catalog: list[SkillInfo]) -> dict[str, float]:
    """Compute inverse document frequency for keywords across all skills."""
    n_docs = len(catalog)
    if n_docs == 0:
        return {}

    doc_freq: dict[str, int] = {}
    for skill in catalog:
        for kw in set(skill.keywords):
            doc_freq[kw] = doc_freq.get(kw, 0) + 1

    return {
        term: math.log((n_docs + 1) / (df + 1)) + 1.0
        for term, df in doc_freq.items()
    }


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Compute normalized term frequency for a token list."""
    if not tokens:
        return {}

    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1

    max_count = max(counts.values())
    return {t: c / max_count for t, c in counts.items()}


def _score_skill(
    context_tokens: list[str],
    context_tf: dict[str, float],
    skill: SkillInfo,
    idf: dict[str, float],
    max_usage: int,
) -> tuple[float, list[str]]:
    """Score a skill against context via weighted TF-IDF cosine similarity.

    Returns:
        Tuple of (score, list of matched signal descriptions).
    """
    if not context_tokens or not skill.keywords:
        return 0.0, []

    context_set = set(context_tokens)
    matched_signals: list[str] = []

    # Build weighted skill term vector
    # Base keywords get weight 1.0
    skill_weighted: dict[str, float] = {}
    for kw in skill.keywords:
        skill_weighted[kw] = skill_weighted.get(kw, 0) + WEIGHT_KEYWORDS

    # Description tokens get higher weight
    for t in skill.description_tokens:
        skill_weighted[t] = skill_weighted.get(t, 0) + WEIGHT_DESCRIPTION
        if t in context_set:
            matched_signals.append(f"description: {t}")

    # Trigger tokens get highest weight
    for t in skill.trigger_tokens:
        skill_weighted[t] = skill_weighted.get(t, 0) + WEIGHT_TRIGGERS
        if t in context_set:
            matched_signals.append(f"trigger: {t}")

    # General keyword matches
    for kw in skill.keywords:
        if kw in context_set and f"description: {kw}" not in matched_signals and f"trigger: {kw}" not in matched_signals:
            matched_signals.append(f"keyword: {kw}")

    # TF-IDF cosine similarity with weighted skill terms
    skill_tf = _compute_tf(list(skill_weighted.keys()))

    dot = 0.0
    context_mag = 0.0
    skill_mag = 0.0

    all_terms = set(context_tf.keys()) | set(skill_tf.keys())
    for term in all_terms:
        c_val = context_tf.get(term, 0.0) * idf.get(term, 1.0)
        s_weight = skill_weighted.get(term, 0.0)
        s_val = skill_tf.get(term, 0.0) * idf.get(term, 1.0) * (s_weight / WEIGHT_KEYWORDS if s_weight > 0 else 1.0)
        dot += c_val * s_val
        context_mag += c_val ** 2
        skill_mag += s_val ** 2

    if context_mag == 0 or skill_mag == 0:
        return 0.0, []

    cosine = dot / (math.sqrt(context_mag) * math.sqrt(skill_mag))

    # Usage boost (0-0.15)
    usage_boost = 0.0
    if max_usage > 0 and skill.usage_count > 0:
        usage_boost = 0.15 * (skill.usage_count / max_usage)
        matched_signals.append(f"usage: {skill.usage_count}x")

    # Staleness penalty
    staleness_penalty = 0.05 if skill.usage_count == 0 and max_usage > 0 else 0.0

    score = min(1.0, max(0.0, cosine + usage_boost - staleness_penalty))
    return score, matched_signals


def _build_reason(context_tokens: list[str], skill: SkillInfo) -> str:
    """Build a human-readable explanation for a match."""
    task_set = set(context_tokens)
    matching = [kw for kw in skill.keywords if kw in task_set]

    if matching:
        reason = f"Matches on: {', '.join(matching[:5])}"
    else:
        reason = f"Related to: {', '.join(skill.keywords[:3])}"

    if skill.usage_count > 0:
        reason += f" (used {skill.usage_count}x)"

    return reason


# ---------------------------------------------------------------------------
# Exclusion logic
# ---------------------------------------------------------------------------

def _detect_active_skills(context: ContextSignals) -> set[str]:
    """Detect skills that appear to already be active from context signals.

    Heuristic: if a commit message or branch name references a specific skill
    name pattern, that skill is likely already in use.
    """
    active: set[str] = set()

    all_text = " ".join(context.recent_commits) + " " + context.branch_name
    all_text_lower = all_text.lower()

    # Common patterns suggesting a skill is already active
    active_patterns = [
        r"/mode\s+(\w+)",
        r"skill[:\s]+(\w[\w-]+)",
        r"using\s+(\w[\w-]+)\s+skill",
    ]
    for pat in active_patterns:
        for match in re.finditer(pat, all_text_lower):
            active.add(match.group(1))

    return active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recommend(
    task: str = "",
    catalog: list[SkillInfo] | None = None,
    top_n: int = 5,
    skills_dir: str | None = None,
    context: ContextSignals | None = None,
    exclude_active: bool = True,
) -> list[Recommendation]:
    """Recommend skills based on context signals and/or task description.

    Args:
        task: Natural language task description (legacy, also in context.manual_context).
        catalog: Pre-loaded skill catalog (loaded if None).
        top_n: Number of recommendations to return.
        skills_dir: Path to skills directory.
        context: Pre-gathered context signals. Auto-gathered if None when task is empty.
        exclude_active: Whether to exclude skills detected as already active.

    Returns:
        Recommendations sorted by relevance descending.
    """
    if catalog is None:
        catalog = load_skill_catalog(skills_dir)

    if not catalog:
        return []

    # Build context
    if context is None:
        if task:
            context = ContextSignals(manual_context=task)
        else:
            context = gather_context_signals()

    # If task was provided but context was also provided without manual_context, merge
    if task and not context.manual_context:
        context.manual_context = task

    context_tokens = context.to_tokens()
    if not context_tokens:
        # Fallback: tokenize the task directly
        context_tokens = [t for t in _tokenize(task) if t not in STOP_WORDS]

    if not context_tokens:
        return []

    # Detect active skills for exclusion
    active_skills = _detect_active_skills(context) if exclude_active else set()

    context_tf = _compute_tf(context_tokens)
    idf = _build_idf(catalog)
    max_usage = max((s.usage_count for s in catalog), default=0)

    scored: list[tuple[float, SkillInfo, list[str]]] = []
    for skill in catalog:
        if exclude_active and skill.name in active_skills:
            continue

        score, signals = _score_skill(context_tokens, context_tf, skill, idf, max_usage)
        if score > 0.0:
            scored.append((score, skill, signals))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        Recommendation(
            skill_name=skill.name,
            relevance_score=score,
            reason=_build_reason(context_tokens, skill),
            skill_info=skill,
            matched_signals=signals,
        )
        for score, skill, signals in scored[:top_n]
    ]


def unused_skills(
    days: int = 30,
    catalog: list[SkillInfo] | None = None,
    skills_dir: str | None = None,
) -> list[SkillInfo]:
    """List skills with zero usage in recent sessions."""
    if catalog is None:
        catalog = load_skill_catalog(skills_dir)

    return [s for s in catalog if s.usage_count == 0]


def co_occurring(
    skill_name: str,
    skills_dir: str | None = None,
) -> list[str]:
    """Find skills commonly used alongside the given skill."""
    heatmap_data = _get_heatmap_data(skills_dir)
    return heatmap_data.get("co_occurrence", {}).get(skill_name, [])


# ---------------------------------------------------------------------------
# CLI formatting
# ---------------------------------------------------------------------------

def _format_table(recs: list[Recommendation]) -> str:
    """Format recommendations as an aligned table (default output)."""
    if not recs:
        return "No matching skills found."

    # Header
    lines: list[str] = []
    lines.append(f"{'#':>2}  {'Skill':<35} {'Score':>6}  {'Reason'}")
    lines.append("-" * 80)

    for i, rec in enumerate(recs, 1):
        name = rec.skill_name[:35]
        score = f"{rec.relevance_score:.2f}"
        reason = rec.reason[:40]
        lines.append(f"{i:>2}  {name:<35} {score:>6}  {reason}")

    return "\n".join(lines)


def _format_oneline(recs: list[Recommendation]) -> str:
    """Format as one skill per line, compact."""
    if not recs:
        return "No matching skills found."
    return "\n".join(
        f"{rec.skill_name} ({rec.relevance_score:.2f})" for rec in recs
    )


def _format_explain(recs: list[Recommendation], context: ContextSignals | None = None) -> str:
    """Format with verbose reasoning for each recommendation."""
    if not recs:
        return "No matching skills found."

    lines: list[str] = []

    if context:
        summary = context.summary()
        if summary:
            lines.append("Context Signals Detected:")
            for key, val in summary.items():
                if isinstance(val, list):
                    lines.append(f"  {key}: {', '.join(str(v) for v in val[:5])}")
                else:
                    lines.append(f"  {key}: {val}")
            lines.append("")

    for i, rec in enumerate(recs, 1):
        lines.append(f"{i}. {rec.skill_name} (score: {rec.relevance_score:.4f})")
        if rec.skill_info and rec.skill_info.description:
            desc = rec.skill_info.description[:120]
            lines.append(f"   Description: {desc}")
        lines.append(f"   Reason: {rec.reason}")
        if rec.matched_signals:
            lines.append(f"   Matched signals: {', '.join(rec.matched_signals[:8])}")
        lines.append("")

    return "\n".join(lines)


def _format_recommendations(recs: list[Recommendation]) -> str:
    """Format recommendations for terminal display (bar chart style)."""
    if not recs:
        return "No matching skills found."

    lines: list[str] = []
    for i, rec in enumerate(recs, 1):
        bar_filled = round(rec.relevance_score * 20)
        bar = ("|" * bar_filled).ljust(20)
        lines.append(f"  {i}. {rec.skill_name}")
        lines.append(f"     Score: [{bar}] {rec.relevance_score:.2f}")
        lines.append(f"     {rec.reason}")
        if rec.skill_info and rec.skill_info.description:
            desc = rec.skill_info.description[:100]
            if len(rec.skill_info.description) > 100:
                desc += "..."
            lines.append(f"     {desc}")
        lines.append("")

    return "\n".join(lines)


def _format_catalog(catalog: list[SkillInfo]) -> str:
    """Format full catalog for terminal display."""
    if not catalog:
        return "No skills found."

    max_name = max(len(s.name) for s in catalog)
    lines: list[str] = []
    for skill in catalog:
        name = skill.name.ljust(max_name)
        usage = f"{skill.usage_count:>3}x" if skill.usage_count > 0 else "  -"
        desc = skill.description[:60] if skill.description else "(no description)"
        if skill.description and len(skill.description) > 60:
            desc += "..."
        lines.append(f"  {name}  {usage}  {desc}")

    return "\n".join(lines)


def _format_unused(skills: list[SkillInfo]) -> str:
    """Format unused skills list."""
    if not skills:
        return "All skills have been used recently."

    lines: list[str] = [f"  {len(skills)} unused skills:\n"]
    for skill in skills:
        desc = skill.description[:70] if skill.description else "(no description)"
        if skill.description and len(skill.description) > 70:
            desc += "..."
        lines.append(f"  - {skill.name}: {desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="GRIP Skill Recommender - context-aware skill suggestions"
    )
    parser.add_argument(
        "--context", type=str, default="",
        help="Manual context string (overrides auto-detection)"
    )
    parser.add_argument(
        "--top", type=int, default=5,
        help="Number of recommendations (default: 5)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--explain", action="store_true",
        help="Verbose reasoning for each recommendation"
    )
    parser.add_argument(
        "--oneline", action="store_true",
        help="One skill per line, compact output"
    )
    parser.add_argument(
        "--skills-dir", type=str, default=None,
        help="Path to skills directory"
    )
    parser.add_argument(
        "--no-exclude", action="store_true",
        help="Don't exclude already-active skills"
    )

    # Subcommands for backward compat
    subparsers = parser.add_subparsers(dest="command")

    rec_parser = subparsers.add_parser("recommend", help="Recommend skills for a task")
    rec_parser.add_argument("task", type=str, help="Task description")
    rec_parser.add_argument("--top", type=int, default=5, help="Number of results")

    unused_parser = subparsers.add_parser("unused", help="List unused skills")
    unused_parser.add_argument(
        "--days", type=int, default=30, help="Look-back window"
    )

    subparsers.add_parser("catalog", help="Show full skill catalog")

    co_parser = subparsers.add_parser(
        "co-occurring", help="Skills used alongside another"
    )
    co_parser.add_argument("skill", type=str, help="Skill name")

    args = parser.parse_args()

    # Default behavior: recommend based on context
    if not args.command:
        if args.context:
            signals = ContextSignals(manual_context=args.context)
        else:
            signals = gather_context_signals()

        catalog = load_skill_catalog(args.skills_dir)
        recs = recommend(
            task=args.context,
            catalog=catalog,
            top_n=args.top,
            context=signals,
            exclude_active=not args.no_exclude,
        )

        if args.json:
            output: dict[str, Any] = {
                "context": signals.summary(),
                "recommendations": [r.to_dict() for r in recs],
            }
            json.dump(output, sys.stdout, indent=2)
            print()
        elif args.explain:
            if args.context:
                print(f"Skill Recommendations for: \"{args.context}\"")
            else:
                print("Skill Recommendations (auto-detected context)")
            print("=" * 60)
            print(_format_explain(recs, context=signals))
        elif args.oneline:
            print(_format_oneline(recs))
        else:
            if args.context:
                print(f"Skill Recommendations for: \"{args.context}\"")
            else:
                print("Skill Recommendations (auto-detected context)")
            print("=" * 60)
            print(_format_table(recs))
        return

    # Subcommand mode (backward compat)
    if args.command == "recommend":
        catalog = load_skill_catalog(args.skills_dir)
        recs = recommend(args.task, catalog=catalog, top_n=args.top)

        if args.json:
            json.dump([r.to_dict() for r in recs], sys.stdout, indent=2)
            print()
        else:
            print(f"Skill Recommendations for: \"{args.task}\"")
            print("=" * 60)
            print(_format_recommendations(recs))

    elif args.command == "unused":
        catalog = load_skill_catalog(args.skills_dir)
        skills = unused_skills(days=args.days, catalog=catalog)

        if args.json:
            json.dump([s.to_dict() for s in skills], sys.stdout, indent=2)
            print()
        else:
            print("Unused Skills")
            print("=" * 60)
            print(_format_unused(skills))

    elif args.command == "catalog":
        catalog = load_skill_catalog(args.skills_dir)

        if args.json:
            json.dump([s.to_dict() for s in catalog], sys.stdout, indent=2)
            print()
        else:
            print(f"Skill Catalog ({len(catalog)} skills)")
            print("=" * 60)
            print(_format_catalog(catalog))

    elif args.command == "co-occurring":
        skills = co_occurring(args.skill, skills_dir=args.skills_dir)

        if args.json:
            json.dump(skills, sys.stdout, indent=2)
            print()
        else:
            if skills:
                print(f"Skills co-occurring with '{args.skill}':")
                for s in skills:
                    print(f"  - {s}")
            else:
                print(f"No co-occurrence data for '{args.skill}'.")


if __name__ == "__main__":
    main()
