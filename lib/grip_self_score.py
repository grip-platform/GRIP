"""GRIP self-score — deterministic config-quality scoring of ~/.claude.

Inspired by Caliber (caliber-ai-org/ai-setup) but applied to GRIP's own
infrastructure. Pure mechanical: cross-references config files against real
filesystem state. No LLM calls — score is reproducible, gameable only by
making structural improvements.

Categories (100-pt total):
- Rules    : 25  (rules/ directory health)
- Skills   : 25  (skills/ directory health)
- MCP      : 15  (~/.claude/.mcp.json validation)
- Identity : 10  (facts/identity.md presence)
- Memory   : 15  (learning/ + projects/<self>/memory/ state)
- Bonus    : 10  (hypothesis registry + recent /save)

Grade thresholds: >=80 A, 60-79 B, 40-59 C, <40 D.

Usage:
    python3 lib/grip_self_score.py                   # human-readable
    python3 lib/grip_self_score.py --json            # machine-readable
    python3 lib/grip_self_score.py --check Rules     # one category

Exit codes for hook integration:
    0 — score >= 60 (B or better)
    1 — score 40-59 (C — warnings)
    2 — score < 40 (D — broken)

Mapping origin: Wave 5 of /absorb-knowledge sprint 2026-05-03.
Master hypothesis H316; cluster sub-hypothesis H319 confirmed via Gentner SMT
(score 100/100, 7/7 causal relations, 0 violations).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from path_encoding import encode_project_path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass
class CheckResult:
    """Result of a single deterministic check.

    structured_fix_per_check (Caliber pattern): every failing check carries
    machine-readable fix data so downstream tools (/grip-doctor, hooks) can
    act mechanically without re-deriving the diagnosis.
    """
    check_id: str
    category: str
    passed: bool
    points_earned: float
    points_max: float
    message: str
    suggested_fix: str = ""


@dataclass
class CategoryScore:
    """Aggregate score for one category."""
    name: str
    earned: float
    max: float
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.max == 0:
            return "N/A"
        ratio = self.earned / self.max
        if ratio >= 0.9:
            return "A"
        if ratio >= 0.7:
            return "B"
        if ratio >= 0.5:
            return "C"
        return "D"


@dataclass
class GripSelfScore:
    """Top-level score result."""
    total: float
    grade: str
    categories: dict[str, CategoryScore] = field(default_factory=dict)

    @classmethod
    def from_categories(cls, cats: dict[str, CategoryScore]) -> "GripSelfScore":
        total = sum(c.earned for c in cats.values())
        if total >= 80:
            grade = "A"
        elif total >= 60:
            grade = "B"
        elif total >= 40:
            grade = "C"
        else:
            grade = "D"
        return cls(total=round(total, 1), grade=grade, categories=cats)


# ── Rules integrity (25 pts) ────────────────────────────────────────────


def check_rules_dir_exists() -> CheckResult:
    """Rules directory exists with at least 5 .md files."""
    rules = CLAUDE_DIR / "rules"
    if not rules.exists():
        return CheckResult(
            "rules_dir_exists", "Rules", False, 0, 5,
            "rules/ directory missing",
            "Create ~/.claude/rules/ and populate from GRIP install",
        )
    md_files = list(rules.glob("*.md"))
    if len(md_files) < 5:
        return CheckResult(
            "rules_dir_exists", "Rules", False, 2, 5,
            f"rules/ has only {len(md_files)} .md files (expected >=5)",
            "Restore missing rules from GRIP install",
        )
    return CheckResult(
        "rules_dir_exists", "Rules", True, 5, 5,
        f"rules/ has {len(md_files)} .md files",
    )


def _path_refs_in_file(
    path: Path, pattern: re.Pattern
) -> list[tuple[str, str]]:
    """Return (filename, ref) pairs for backtick-quoted paths in one file.

    Extracted helper so check_rules_paths_resolve can flat-iterate at one
    level (BIG-O gate: avoid nested loops). Each call is O(content_length).
    """
    return [
        (path.name, m.group(1))
        for m in pattern.finditer(path.read_text(errors="replace"))
    ]


def check_rules_paths_resolve() -> CheckResult:
    """Path references inside rules/*.md resolve on disk.

    Caliber's filesystem_cross_reference pattern: any backtick-quoted relative
    path in a rule must resolve to a real file under CLAUDE_DIR.
    """
    rules = CLAUDE_DIR / "rules"
    if not rules.exists():
        return CheckResult(
            "rules_paths_resolve", "Rules", False, 0, 10,
            "rules/ directory missing — cannot check",
        )
    pattern = re.compile(r"`([a-z_/]+\.(?:md|py|sh|json|toml|yaml))`")
    all_refs: list[tuple[str, str]] = []
    for rule_file in rules.glob("*.md"):
        all_refs.extend(_path_refs_in_file(rule_file, pattern))
    if not all_refs:
        return CheckResult(
            "rules_paths_resolve", "Rules", True, 10, 10,
            "No path refs found (skipped)",
        )
    broken = [
        f"{name}:{ref}" for name, ref in all_refs
        if not (CLAUDE_DIR / ref).exists()
    ]
    total_refs = len(all_refs)
    ratio = 1 - (len(broken) / total_refs)
    earned = round(ratio * 10, 1)
    return CheckResult(
        "rules_paths_resolve", "Rules", ratio >= 0.95, earned, 10,
        f"{total_refs - len(broken)}/{total_refs} path refs resolve "
        f"({ratio*100:.0f}%)",
        "; ".join(broken[:3]) if broken else "",
    )


def check_rules_have_code_blocks() -> CheckResult:
    """Rules contain fenced code blocks (concrete examples, not just prose)."""
    rules = CLAUDE_DIR / "rules"
    if not rules.exists():
        return CheckResult(
            "rules_have_code_blocks", "Rules", False, 0, 10, "rules/ missing",
        )
    files_with_blocks = 0
    total = 0
    for rule_file in rules.glob("*.md"):
        if rule_file.name == "CLAUDE.md":
            continue
        total += 1
        content = rule_file.read_text(errors="replace")
        if content.count("```") >= 2:
            files_with_blocks += 1
    if total == 0:
        return CheckResult(
            "rules_have_code_blocks", "Rules", False, 0, 10,
            "No rule files found",
        )
    ratio = files_with_blocks / total
    earned = round(ratio * 10, 1)
    return CheckResult(
        "rules_have_code_blocks", "Rules", ratio >= 0.7, earned, 10,
        f"{files_with_blocks}/{total} rules have code blocks "
        f"({ratio*100:.0f}%)",
        "Add concrete examples to abstract rules" if ratio < 0.7 else "",
    )


# ── Skills integrity (25 pts) ───────────────────────────────────────────


def check_skills_dir_exists() -> CheckResult:
    """Skills directory exists with >=10 subdirectories."""
    skills = CLAUDE_DIR / "skills"
    if not skills.exists():
        return CheckResult(
            "skills_dir_exists", "Skills", False, 0, 5, "skills/ missing",
        )
    skill_dirs = [d for d in skills.iterdir() if d.is_dir()]
    if len(skill_dirs) < 10:
        return CheckResult(
            "skills_dir_exists", "Skills", False, 2, 5,
            f"skills/ has only {len(skill_dirs)} subdirs",
        )
    return CheckResult(
        "skills_dir_exists", "Skills", True, 5, 5,
        f"skills/ has {len(skill_dirs)} skill directories",
    )


def check_skills_have_skill_md() -> CheckResult:
    """Each skill subdir has SKILL.md."""
    skills = CLAUDE_DIR / "skills"
    if not skills.exists():
        return CheckResult(
            "skills_have_skill_md", "Skills", False, 0, 10, "skills/ missing",
        )
    skill_dirs = [d for d in skills.iterdir() if d.is_dir()]
    if not skill_dirs:
        return CheckResult(
            "skills_have_skill_md", "Skills", False, 0, 10, "No skills",
        )
    with_md = sum(1 for d in skill_dirs if (d / "SKILL.md").exists())
    ratio = with_md / len(skill_dirs)
    earned = round(ratio * 10, 1)
    missing = [d.name for d in skill_dirs if not (d / "SKILL.md").exists()]
    return CheckResult(
        "skills_have_skill_md", "Skills", ratio >= 0.95, earned, 10,
        f"{with_md}/{len(skill_dirs)} skills have SKILL.md "
        f"({ratio*100:.0f}%)",
        f"Missing: {', '.join(missing[:5])}" if missing else "",
    )


def check_skills_md_have_code_blocks() -> CheckResult:
    """SKILL.md files contain fenced code blocks."""
    skills = CLAUDE_DIR / "skills"
    if not skills.exists():
        return CheckResult(
            "skills_md_have_code_blocks", "Skills", False, 0, 10,
            "skills/ missing",
        )
    skill_dirs = [
        d for d in skills.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    ]
    if not skill_dirs:
        return CheckResult(
            "skills_md_have_code_blocks", "Skills", False, 0, 10,
            "No SKILL.md files",
        )
    with_blocks = sum(
        1 for d in skill_dirs
        if (d / "SKILL.md").read_text(errors="replace").count("```") >= 2
    )
    ratio = with_blocks / len(skill_dirs)
    earned = round(ratio * 10, 1)
    return CheckResult(
        "skills_md_have_code_blocks", "Skills", ratio >= 0.7, earned, 10,
        f"{with_blocks}/{len(skill_dirs)} SKILL.md have code blocks "
        f"({ratio*100:.0f}%)",
    )


# ── MCP config (15 pts) ─────────────────────────────────────────────────


def check_mcp_json_valid() -> CheckResult:
    """~/.claude/.mcp.json is valid JSON with configured servers."""
    mcp_path = CLAUDE_DIR / ".mcp.json"
    if not mcp_path.exists():
        return CheckResult(
            "mcp_json_valid", "MCP", False, 0, 15, ".mcp.json missing",
            "Create ~/.claude/.mcp.json with at least one MCP server",
        )
    try:
        data = json.loads(mcp_path.read_text())
    except json.JSONDecodeError as e:
        return CheckResult(
            "mcp_json_valid", "MCP", False, 0, 15,
            f".mcp.json invalid JSON: {e}",
            "Fix JSON syntax in .mcp.json",
        )
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    if not servers:
        return CheckResult(
            "mcp_json_valid", "MCP", False, 5, 15,
            ".mcp.json has no servers (only structure validates)",
        )
    valid = sum(
        1 for cfg in servers.values()
        if isinstance(cfg, dict)
        and (cfg.get("command") or cfg.get("url") or cfg.get("type"))
    )
    if valid == len(servers):
        return CheckResult(
            "mcp_json_valid", "MCP", True, 15, 15,
            f".mcp.json: {len(servers)} valid MCP servers",
        )
    return CheckResult(
        "mcp_json_valid", "MCP", False, round(15 * valid / len(servers), 1), 15,
        f".mcp.json: {valid}/{len(servers)} servers fully configured",
    )


# ── Identity (10 pts) ───────────────────────────────────────────────────


def check_identity_present() -> CheckResult:
    """facts/identity.md exists with X>> signature."""
    facts = CLAUDE_DIR / "facts"
    if not facts.exists():
        return CheckResult(
            "identity_present", "Identity", False, 0, 10, "facts/ missing",
            "Run /activate-grip to set up identity",
        )
    identity_md = facts / "identity.md"
    if not identity_md.exists():
        return CheckResult(
            "identity_present", "Identity", False, 2, 10,
            "facts/identity.md missing",
        )
    content = identity_md.read_text(errors="replace")
    if re.search(r"[A-Z]>>", content):
        return CheckResult(
            "identity_present", "Identity", True, 10, 10,
            "identity.md has signature",
        )
    return CheckResult(
        "identity_present", "Identity", False, 5, 10,
        "identity.md present but no X>> signature found",
    )


# ── Memory state (15 pts) ───────────────────────────────────────────────


def check_insights_jsonl_valid() -> CheckResult:
    """learning/insights.jsonl is valid JSONL."""
    insights = CLAUDE_DIR / "learning" / "insights.jsonl"
    if not insights.exists():
        return CheckResult(
            "insights_jsonl_valid", "Memory", False, 0, 8,
            "learning/insights.jsonl missing",
        )
    lines = [
        line for line in insights.read_text(errors="replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return CheckResult(
            "insights_jsonl_valid", "Memory", False, 2, 8, "insights.jsonl empty",
        )
    valid = 0
    for line in lines:
        try:
            json.loads(line)
            valid += 1
        except json.JSONDecodeError:
            pass
    ratio = valid / len(lines)
    if ratio >= 0.95:
        return CheckResult(
            "insights_jsonl_valid", "Memory", True, 8, 8,
            f"insights.jsonl: {valid}/{len(lines)} valid lines",
        )
    return CheckResult(
        "insights_jsonl_valid", "Memory", False, round(ratio * 8, 1), 8,
        f"insights.jsonl: {valid}/{len(lines)} valid ({ratio*100:.0f}%)",
        "Run learning-janitor to clean malformed entries",
    )


def check_memory_md_present() -> CheckResult:
    """projects/<self>/memory/MEMORY.md exists with substantive content."""
    project_path = encode_project_path(CLAUDE_DIR)
    memory_md = CLAUDE_DIR / "projects" / project_path / "memory" / "MEMORY.md"
    # Fallback: any project's MEMORY.md
    if not memory_md.exists():
        projects = CLAUDE_DIR / "projects"
        if projects.exists():
            for proj in projects.iterdir():
                cand = proj / "memory" / "MEMORY.md"
                if cand.exists():
                    memory_md = cand
                    break
    if not memory_md.exists():
        return CheckResult(
            "memory_md_present", "Memory", False, 0, 7,
            "projects/<self>/memory/MEMORY.md missing",
        )
    size = memory_md.stat().st_size
    if size < 100:
        return CheckResult(
            "memory_md_present", "Memory", False, 2, 7,
            f"MEMORY.md too small ({size}B)",
        )
    return CheckResult(
        "memory_md_present", "Memory", True, 7, 7,
        f"MEMORY.md present ({size}B)",
    )


# ── Bonus (10 pts) ──────────────────────────────────────────────────────


def check_hypothesis_registry_healthy() -> CheckResult:
    """Hypothesis confirmation rate is in scientific-method.md target band."""
    hyp_path = CLAUDE_DIR / "learning" / "hypotheses.jsonl"
    if not hyp_path.exists():
        return CheckResult(
            "hypothesis_registry_healthy", "Bonus", False, 0, 5,
            "learning/hypotheses.jsonl missing",
        )
    confirmed = 0
    refuted = 0
    total = 0
    for line in hyp_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = entry.get("status", "")
        total += 1
        if status == "confirmed":
            confirmed += 1
        elif status == "refuted":
            refuted += 1
    if total == 0:
        return CheckResult(
            "hypothesis_registry_healthy", "Bonus", False, 0, 5,
            "No hypotheses registered",
        )
    decided = confirmed + refuted
    if decided == 0:
        return CheckResult(
            "hypothesis_registry_healthy", "Bonus", False, 2, 5,
            f"{total} hypotheses, none decided yet",
        )
    rate = confirmed / decided
    if 0.30 <= rate <= 0.70:
        return CheckResult(
            "hypothesis_registry_healthy", "Bonus", True, 5, 5,
            f"Confirmation rate {rate*100:.0f}% (in target band 30-70%)",
        )
    return CheckResult(
        "hypothesis_registry_healthy", "Bonus", False, 2, 5,
        f"Confirmation rate {rate*100:.0f}% (outside 30-70% band)",
        "Predictions too easy (>70%) or too hard (<30%)",
    )


def check_recent_save() -> CheckResult:
    """insights.jsonl modified within 7 days (proxy for recent /save)."""
    insights = CLAUDE_DIR / "learning" / "insights.jsonl"
    if not insights.exists():
        return CheckResult(
            "recent_save", "Bonus", False, 0, 5,
            "No insights file to check freshness",
        )
    age_days = (time.time() - insights.stat().st_mtime) / 86400
    if age_days <= 7:
        return CheckResult(
            "recent_save", "Bonus", True, 5, 5,
            f"insights.jsonl modified {age_days:.1f}d ago",
        )
    return CheckResult(
        "recent_save", "Bonus", False, max(0.0, 5 - age_days / 7), 5,
        f"insights.jsonl stale ({age_days:.0f}d ago)",
        "Run /save to refresh memory state",
    )


# ── Dispatch table ──────────────────────────────────────────────────────

CATEGORY_CHECKS: dict[str, list[Callable[[], CheckResult]]] = {
    "Rules": [
        check_rules_dir_exists,
        check_rules_paths_resolve,
        check_rules_have_code_blocks,
    ],
    "Skills": [
        check_skills_dir_exists,
        check_skills_have_skill_md,
        check_skills_md_have_code_blocks,
    ],
    "MCP": [check_mcp_json_valid],
    "Identity": [check_identity_present],
    "Memory": [check_insights_jsonl_valid, check_memory_md_present],
    "Bonus": [check_hypothesis_registry_healthy, check_recent_save],
}


def _run_category(
    name: str, checks: list[Callable[[], CheckResult]]
) -> CategoryScore:
    """Execute the checks for one category and aggregate into CategoryScore.

    Extracted helper so compute_score's outer loop never wraps an inner
    comprehension (BIG-O gate: avoid nested loops). One level of for inside
    each function.
    """
    results = [check() for check in checks]
    earned = sum(r.points_earned for r in results)
    max_pts = sum(r.points_max for r in results)
    return CategoryScore(
        name=name,
        earned=round(earned, 1),
        max=max_pts,
        checks=results,
    )


def compute_score(claude_dir: Path | None = None) -> GripSelfScore:
    """Run all checks against a CLAUDE_DIR target.

    The global CLAUDE_DIR is temporarily swapped, restored in finally so
    the function is safe to call repeatedly with different targets (tests).
    """
    global CLAUDE_DIR
    saved = CLAUDE_DIR
    try:
        if claude_dir is not None:
            CLAUDE_DIR = claude_dir
        cats: dict[str, CategoryScore] = {}
        for cat_name, checks in CATEGORY_CHECKS.items():
            cats[cat_name] = _run_category(cat_name, checks)
        return GripSelfScore.from_categories(cats)
    finally:
        CLAUDE_DIR = saved


def _format_check_lines(checks: list[CheckResult]) -> list[str]:
    """Render individual check results as human-readable lines.

    Extracted so _format_text never has a `for c in cat.checks` inside its
    `for name, cat in score.categories.items()` outer loop.
    """
    lines: list[str] = []
    for c in checks:
        mark = "✓" if c.passed else "✗"
        lines.append(
            f"  {mark} {c.check_id} ({c.points_earned}/{c.points_max}): "
            f"{c.message}"
        )
        if c.suggested_fix and not c.passed:
            lines.append(f"    -> fix: {c.suggested_fix}")
    return lines


def _format_text(score: GripSelfScore, only: str | None) -> str:
    """Human-readable rendering."""
    out = [f"\nGRIP Self-Score: {score.total}/100 ({score.grade})", "=" * 50]
    for name, cat in score.categories.items():
        if only and only.lower() != name.lower():
            continue
        out.append(f"\n[{cat.grade}] {name}: {cat.earned}/{cat.max}")
        out.extend(_format_check_lines(cat.checks))
    return "\n".join(out)


def _serialise_category(cat: CategoryScore) -> dict:
    """Build a JSON-friendly dict for one CategoryScore.

    Extracted so _format_json's outer dict comp never contains an inner list
    comp (BIG-O gate: avoid nested loops).
    """
    return {
        "earned": cat.earned,
        "max": cat.max,
        "grade": cat.grade,
        "checks": [asdict(c) for c in cat.checks],
    }


def _format_json(score: GripSelfScore) -> str:
    """Machine-readable rendering."""
    out = {
        "total": score.total,
        "grade": score.grade,
        "categories": {
            name: _serialise_category(cat)
            for name, cat in score.categories.items()
        },
    }
    return json.dumps(out, indent=2, default=str)


def main() -> int:
    """CLI entry point. Returns hook-friendly exit code."""
    parser = argparse.ArgumentParser(description="GRIP ~/.claude self-score")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--check", help="Run only one category")
    args = parser.parse_args()
    score = compute_score()
    print(_format_json(score) if args.json else _format_text(score, args.check))
    if score.total >= 60:
        return 0
    if score.total >= 40:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
