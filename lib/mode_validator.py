"""Mode-skill reference validator for GRIP.

Validates that all skills referenced in mode YAML definitions exist
and can be found in the skills directory.

Usage:
    python3 lib/mode_validator.py           # Validate all modes
    python3 lib/mode_validator.py code      # Validate specific mode
"""

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
MODES_DIR = CLAUDE_DIR / "modes" / "definitions"
SKILLS_DIR = CLAUDE_DIR / "skills"


def validate_mode(mode_name: str) -> dict:
    """Validate a single mode's skill references.

    Returns dict with 'valid' bool, 'missing' list, 'found' list.
    """
    mode_file = MODES_DIR / f"{mode_name}.yaml"
    if not mode_file.exists():
        mode_file = MODES_DIR / f"{mode_name}-mode.yaml"
    if not mode_file.exists():
        return {"valid": False, "error": f"Mode file not found: {mode_name}", "missing": [], "found": []}

    if yaml is None:
        # Fallback: regex parse for skill references under skills.always/optional
        content = mode_file.read_text()
        skills = []
        in_skills_block = False
        for line in content.split("\n"):
            stripped = line.strip()
            # Detect skills: section
            if stripped == "skills:":
                in_skills_block = True
                continue
            # Detect end of skills block (new top-level key)
            if in_skills_block and not line.startswith(" ") and not line.startswith("\t") and stripped:
                in_skills_block = False
            # Inside skills block, collect list items (skip subsection keys like always:/optional:)
            if in_skills_block and stripped.startswith("- "):
                skill = stripped[2:].strip().strip('"').strip("'")
                if skill and not skill.endswith(":"):
                    skills.append(skill)
    else:
        with open(mode_file) as f:
            data = yaml.safe_load(f)
        raw_skills = data.get("skills", []) if isinstance(data, dict) else []
        # Skills can be a flat list or a dict with always/optional keys
        if isinstance(raw_skills, dict):
            skills = []
            for key in ("always", "optional"):
                val = raw_skills.get(key, [])
                if isinstance(val, list):
                    skills.extend(val)
        elif isinstance(raw_skills, list):
            skills = raw_skills
        else:
            skills = []

    found = []
    missing = []
    for skill in skills:
        skill_dir = SKILLS_DIR / skill
        skill_md = skill_dir / "SKILL.md"
        skill_grip = skill_dir / "SKILL.grip"
        if skill_dir.exists() and (skill_md.exists() or skill_grip.exists()):
            found.append(skill)
        else:
            missing.append(skill)

    return {
        "valid": len(missing) == 0,
        "mode": mode_name,
        "found": found,
        "missing": missing,
        "total_skills": len(skills),
    }


def validate_all() -> list:
    """Validate all mode definitions. Returns list of validation results."""
    results = []
    if not MODES_DIR.exists():
        return results

    for mode_file in sorted(MODES_DIR.glob("*.yaml")):
        mode_name = mode_file.stem.replace("-mode", "")
        result = validate_mode(mode_name)
        results.append(result)

    return results


def validate_summary() -> str:
    """One-line validation summary for hooks and dashboards."""
    results = validate_all()
    passed = sum(1 for r in results if r["valid"])
    total = len(results)
    missing = sum(len(r["missing"]) for r in results)
    return f"[MODES] {passed}/{total} valid, {total - passed} with {missing} missing skill refs"


def update_blocklist() -> dict:
    """Write missing skills to cache/missing-skills.json for tracking."""
    import json
    results = validate_all()
    missing_map: dict[str, list[str]] = {}
    for r in results:
        for skill in r.get("missing", []):
            if skill not in missing_map:
                missing_map[skill] = []
            missing_map[skill].append(r.get("mode", "unknown"))

    blocklist_path = CLAUDE_DIR / "cache" / "missing-skills.json"
    blocklist_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "missing_skills": [
            {"skill": skill, "referenced_by": modes}
            for skill, modes in sorted(missing_map.items())
        ],
        "missing_skills_count": len(missing_map),
    }

    with open(blocklist_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    return {"updated": len(missing_map), "path": str(blocklist_path)}


def main():
    import json

    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        print(validate_summary())
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--update-blocklist":
        result = update_blocklist()
        print(f"Updated {result['path']} with {result['updated']} missing skills")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] != "--json":
        result = validate_mode(sys.argv[1])
        if result.get("error"):
            print(f"ERROR: {result['error']}")
            sys.exit(1)
        status = "PASS" if result["valid"] else "FAIL"
        print(f"[{status}] {result['mode']}: {result['total_skills']} skills, {len(result['missing'])} missing")
        for m in result["missing"]:
            print(f"  MISSING: {m}")
        sys.exit(0 if result["valid"] else 1)

    results = validate_all()
    passed = sum(1 for r in results if r["valid"])
    failed = sum(1 for r in results if not r["valid"])
    total_missing = sum(len(r["missing"]) for r in results)

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            status = "PASS" if r["valid"] else "FAIL"
            print(f"[{status}] {r.get('mode', '?')}: {r['total_skills']} skills, {len(r['missing'])} missing")
            for m in r["missing"]:
                print(f"  MISSING: {m}")

        print(f"\n{passed} passed, {failed} failed, {total_missing} missing skill refs")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
