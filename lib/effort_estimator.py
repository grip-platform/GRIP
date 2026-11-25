#!/usr/bin/env python3
"""effort_estimator.py — Heuristic effort frontmatter for GRIP skills.

Assigns effort levels (low/medium/high) based on line count and complexity
indicators. Used to batch-populate effort frontmatter for CC 2.1.80.

Usage:
    python3 lib/effort_estimator.py scan              # Show all skills missing effort
    python3 lib/effort_estimator.py apply              # Apply effort to all missing skills
    python3 lib/effort_estimator.py apply --dry-run    # Preview without writing
"""

import os
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SKILLS_DIR = CLAUDE_DIR / "skills"

# Complexity indicators that bump effort up
HIGH_INDICATORS = {
    "converge(", "falsif", "PARAMOUNT", "research-council", "broly",
    "multi-step", "recursive", "genome", "SOLID", "GRASP",
}
LOW_INDICATORS = {
    "single command", "one-liner", "simple", "prevents", "blocks",
    "enforces", "cleanup", "lint",
}


def estimate_effort(skill_path: Path) -> str:
    """Estimate effort for a skill based on size and complexity."""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception:
        return "medium"

    lines = len(text.splitlines())
    text_lower = text.lower()

    # Count complexity indicators
    high_hits = sum(1 for kw in HIGH_INDICATORS if kw.lower() in text_lower)
    low_hits = sum(1 for kw in LOW_INDICATORS if kw.lower() in text_lower)

    # Line-based heuristic
    if lines > 400 or high_hits >= 3:
        return "high"
    if lines < 150 and low_hits >= 2:
        return "low"
    if lines < 80:
        return "low"
    if lines > 250:
        return "high"
    return "medium"


def has_effort(skill_path: Path) -> bool:
    """Check if skill already has effort frontmatter."""
    try:
        text = skill_path.read_text(encoding="utf-8")
        # Check within first 500 chars (frontmatter area)
        return "effort:" in text[:500]
    except Exception:
        return True  # Skip unreadable files


def add_effort(skill_path: Path, effort: str, dry_run: bool = False) -> bool:
    """Add effort frontmatter after the status line."""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception:
        return False

    if "effort:" in text[:500]:
        return False  # Already has effort

    # Insert after 'status:' line
    lines = text.split("\n")
    inserted = False
    for i, line in enumerate(lines):
        if line.startswith("status:"):
            lines.insert(i + 1, f"effort: {effort}")
            inserted = True
            break

    if not inserted:
        # Fallback: insert after 'version:' line
        for i, line in enumerate(lines):
            if line.startswith("version:"):
                lines.insert(i + 1, f"effort: {effort}")
                inserted = True
                break

    if not inserted:
        return False

    if not dry_run:
        skill_path.write_text("\n".join(lines))
    return True


def scan_skills() -> list[dict]:
    """Scan all skills and report effort status."""
    results = []
    if not SKILLS_DIR.exists():
        return results

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        has = has_effort(skill_file)
        effort = estimate_effort(skill_file) if not has else None
        lines = len(skill_file.read_text().splitlines()) if skill_file.exists() else 0

        results.append({
            "name": skill_dir.name,
            "has_effort": has,
            "estimated": effort,
            "lines": lines,
        })
    return results


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "scan"
    dry_run = "--dry-run" in args

    skills = scan_skills()
    missing = [s for s in skills if not s["has_effort"]]
    has = [s for s in skills if s["has_effort"]]

    if cmd == "scan":
        print(f"Total skills: {len(skills)}")
        print(f"With effort:  {len(has)}")
        print(f"Missing:      {len(missing)}")
        print()
        if missing:
            print(f"{'Skill':<45s} {'Lines':>5s} {'Est':>6s}")
            print("-" * 58)
            for s in sorted(missing, key=lambda x: -x["lines"]):
                print(f"{s['name']:<45s} {s['lines']:>5d} {s['estimated']:>6s}")

    elif cmd == "apply":
        applied = 0
        for s in missing:
            skill_file = SKILLS_DIR / s["name"] / "SKILL.md"
            if add_effort(skill_file, s["estimated"], dry_run=dry_run):
                applied += 1
                status = "DRY" if dry_run else "OK"
                print(f"[{status}] {s['name']:<40s} -> {s['estimated']}")

        print(f"\n{'Would apply' if dry_run else 'Applied'}: {applied}/{len(missing)}")
    else:
        print(f"Usage: effort_estimator.py {{scan|apply}} [--dry-run]")
        sys.exit(1)


if __name__ == "__main__":
    main()
