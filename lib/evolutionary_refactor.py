#!/usr/bin/env python3
"""
Evolutionary Refactoring System for GRIP.

Bio-mimicry pruning/enhancement/gap-analysis for skills, agents, modes, MCP tools.
Inspired by KONO's fitness decay, RSI's candidate pipeline, and natural selection.
Components that provide user-facing value survive; unused components decay and archive.

Usage:
    python3 lib/evolutionary_refactor.py audit      # Scan + report only
    python3 lib/evolutionary_refactor.py propose     # Scan + generate proposals
    python3 lib/evolutionary_refactor.py execute     # Scan + propose + archive (interactive)
    python3 lib/evolutionary_refactor.py fitness     # Show fitness scores
    python3 lib/evolutionary_refactor.py duplicates  # Show duplicate components

Issue #154: Evolutionary refactoring system
Version: 1.0.0
"""

import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SKILLS_DIR = CLAUDE_DIR / "skills"
AGENTS_DIR = CLAUDE_DIR / "agents"
MODES_DIR = CLAUDE_DIR / "modes" / "definitions"
COMMANDS_DIR = CLAUDE_DIR / "commands"
METRICS_PATH = CLAUDE_DIR / "learning" / "metrics.jsonl"
ARCHIVE_DIR = CLAUDE_DIR / ".archive"

# Fitness decay rate per day (KONO-inspired)
DECAY_RATE = 0.95
# Max use_count bonus cap (diminishing returns)
USE_COUNT_CAP = 10

# Protected components - never prune regardless of fitness
PROTECTED = {
    "skill": {
        "context-gate", "asking-users", "concise-communication",
        "design-principles", "mode", "forging-skills", "ultrathink",
        "code-mode", "flow-mode", "pair-mode",
    },
    "agent": {
        "broly", "context-refresh", "dependency-guardian",
        "pair-worker", "pr-workflow",
    },
    "mode": {
        "code-mode", "pair-mode", "flow-mode",
    },
}


@dataclass
class ComponentFitness:
    """Fitness profile for a GRIP component."""
    name: str
    component_type: str  # "skill", "agent", "mode", "mcp_tool"
    fitness_score: float = 0.5
    last_used: str = "never"
    use_count: int = 0
    token_cost: int = 0
    attestations: int = 0
    tier: str = "dormant"
    naming_valid: bool = True
    suggested_name: str = ""
    notes: str = ""
    protected: bool = False


# --- Scanning ---

def scan_components() -> list[ComponentFitness]:
    """Inventory all skills, agents, modes from filesystem."""
    components = []

    # Skills
    if SKILLS_DIR.exists():
        for d in sorted(SKILLS_DIR.iterdir()):
            if d.is_dir() and d.name != ".archive" and d.name != ".archived":
                skill_file = d / "SKILL.md"
                if not skill_file.exists():
                    skill_file = d / "SKILL.grip"
                token_est = _estimate_skill_tokens(skill_file) if skill_file.exists() else 200
                is_protected = d.name in PROTECTED.get("skill", set())
                naming_ok, suggested = validate_naming(d.name, "skill")
                components.append(ComponentFitness(
                    name=d.name,
                    component_type="skill",
                    token_cost=token_est,
                    protected=is_protected,
                    naming_valid=naming_ok,
                    suggested_name=suggested,
                ))

    # Agents
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md")):
            if f.name == "CLAUDE.md":
                continue
            agent_name = f.stem
            is_protected = agent_name in PROTECTED.get("agent", set())
            components.append(ComponentFitness(
                name=agent_name,
                component_type="agent",
                token_cost=_estimate_file_tokens(f),
                protected=is_protected,
            ))

    # Modes
    if MODES_DIR.exists():
        for f in sorted(MODES_DIR.glob("*.yaml")):
            mode_name = f.stem
            is_protected = mode_name in PROTECTED.get("mode", set())
            components.append(ComponentFitness(
                name=mode_name,
                component_type="mode",
                token_cost=_estimate_file_tokens(f),
                protected=is_protected,
            ))

    return components


def _estimate_skill_tokens(path: Path) -> int:
    """Estimate token cost of a skill file (~4 chars per token)."""
    try:
        size = path.stat().st_size
        return max(100, size // 4)
    except OSError:
        return 200


def _estimate_file_tokens(path: Path) -> int:
    """Estimate token cost from file size."""
    try:
        size = path.stat().st_size
        return max(50, size // 4)
    except OSError:
        return 100


# --- Usage Measurement ---

def measure_usage(components: list[ComponentFitness]) -> list[ComponentFitness]:
    """Enrich components with usage data from metrics.jsonl."""
    if not METRICS_PATH.exists():
        return components

    skill_counts: Counter = Counter()
    mode_counts: Counter = Counter()
    command_counts: Counter = Counter()
    last_used: dict[str, str] = {}

    try:
        with open(METRICS_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = e.get("event", "")
                ts = e.get("timestamp", "")

                if event == "skill_invoked":
                    name = e.get("skill", "")
                    if name:
                        skill_counts[name] += 1
                        last_used[f"skill:{name}"] = ts
                elif event == "mode_activated":
                    name = e.get("mode", "")
                    if name:
                        mode_counts[name] += 1
                        last_used[f"mode:{name}"] = ts
                elif event == "command_invoked":
                    name = e.get("command", "")
                    if name:
                        command_counts[name] += 1
                        last_used[f"command:{name}"] = ts
                elif event == "mcp_tool_used":
                    name = e.get("tool", "")
                    if name:
                        last_used[f"mcp_tool:{name}"] = ts
    except OSError:
        pass

    for comp in components:
        key = f"{comp.component_type}:{comp.name}"
        if comp.component_type == "skill":
            comp.use_count = skill_counts.get(comp.name, 0)
        elif comp.component_type == "mode":
            # Strip -mode suffix for matching
            base = comp.name.replace("-mode", "")
            comp.use_count = mode_counts.get(base, 0) + mode_counts.get(comp.name, 0)
        elif comp.component_type == "agent":
            # Agents don't have direct usage tracking yet
            comp.use_count = 0

        lu = last_used.get(key, "")
        if lu:
            comp.last_used = lu

    return components


# --- Fitness Calculation ---

def calculate_fitness(comp: ComponentFitness) -> float:
    """KONO-inspired fitness: base * decay^days * (1 + 0.1 * use_count).

    Protected components get a floor of 0.8.
    Components never used get base 0.3.
    Components with usage start at 0.6.
    """
    if comp.protected:
        comp.fitness_score = max(0.8, comp.fitness_score)
        comp.tier = classify_tier(comp.fitness_score)
        return comp.fitness_score

    # Base fitness from usage
    if comp.use_count > 0:
        base = 0.6
    else:
        base = 0.3

    # Days since last use
    days = _days_since(comp.last_used)

    # Decay: 0.95^days
    decay = math.pow(DECAY_RATE, days)

    # Use count bonus (capped)
    use_bonus = 1.0 + 0.1 * min(comp.use_count, USE_COUNT_CAP)

    fitness = base * decay * use_bonus

    # Clamp to [0, 1]
    comp.fitness_score = max(0.0, min(1.0, fitness))
    comp.tier = classify_tier(comp.fitness_score)
    return comp.fitness_score


def _days_since(timestamp: str) -> float:
    """Days since an ISO timestamp. Returns 30 if 'never' or unparseable."""
    if not timestamp or timestamp == "never":
        return 30.0
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400)
    except (ValueError, TypeError):
        return 30.0


def classify_tier(fitness: float) -> str:
    """Classify into tier based on fitness score."""
    if fitness >= 0.8:
        return "essential"
    elif fitness >= 0.5:
        return "active"
    elif fitness >= 0.2:
        return "dormant"
    return "dead"


# --- Duplicate Detection ---

def detect_duplicates(components: list[ComponentFitness],
                      threshold: float = 0.7) -> list[dict]:
    """Find functionally duplicate components using name similarity."""
    duplicates = []
    seen = set()

    for i, a in enumerate(components):
        for b in components[i + 1:]:
            if a.component_type != b.component_type:
                continue
            pair_key = (a.name, b.name)
            if pair_key in seen:
                continue

            similarity = _name_similarity(a.name, b.name)
            if similarity >= threshold:
                seen.add(pair_key)
                duplicates.append({
                    "component_a": a.name,
                    "component_b": b.name,
                    "type": a.component_type,
                    "similarity": round(similarity, 2),
                    "recommendation": _duplicate_recommendation(a, b),
                })

    # Also detect known MCP duplication patterns
    duplicates.extend(_detect_mcp_duplicates())

    return duplicates


def _name_similarity(a: str, b: str) -> float:
    """Compute similarity between two component names."""
    # Normalize: remove common suffixes
    a_norm = a.replace("-mode", "").replace("-agent", "").replace("_", "-")
    b_norm = b.replace("-mode", "").replace("-agent", "").replace("_", "-")
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _duplicate_recommendation(a: ComponentFitness, b: ComponentFitness) -> str:
    """Recommend which duplicate to keep."""
    if a.protected and not b.protected:
        return f"Keep '{a.name}' (protected), archive '{b.name}'"
    if b.protected and not a.protected:
        return f"Keep '{b.name}' (protected), archive '{a.name}'"
    if a.use_count >= b.use_count:
        return f"Keep '{a.name}' ({a.use_count} uses), consider archiving '{b.name}' ({b.use_count} uses)"
    return f"Keep '{b.name}' ({b.use_count} uses), consider archiving '{a.name}' ({a.use_count} uses)"


def _detect_mcp_duplicates() -> list[dict]:
    """Detect known MCP tool duplication (grip-apps vs claude_ai_GRIP, etc.)."""
    duplicates = []
    # Known duplication pattern: grip-apps tools are duplicated as claude_ai_GRIP
    grip_apps_tools = [
        "grip_options_picker", "grip_confirmation", "grip_checklist",
        "grip_kanban", "grip_decision_matrix", "grip_wizard", "grip_inbox",
        "grip_data_table", "grip_digest", "grip_progress", "grip_journal",
        "grip_concept_map", "grip_timeline", "grip_retrospective",
        "grip_threat_matrix", "grip_phase_stepper", "grip_scoring_rubric",
        "grip_idea_canvas", "grip_circuit_dashboard", "grip_review_annotator",
        "grip_gap_visualiser",
    ]
    for tool in grip_apps_tools:
        duplicates.append({
            "component_a": f"mcp__grip-apps__{tool}",
            "component_b": f"mcp__claude_ai_GRIP__{tool}",
            "type": "mcp_tool",
            "similarity": 1.0,
            "recommendation": "Deduplicate: keep one MCP server, remove the other (~7k tokens savings)",
        })

    # Slack duplication
    slack_tools = [
        "slack_list_channels", "slack_post_message", "slack_get_channel_history",
        "slack_get_thread_replies", "slack_get_users", "slack_get_user_profile",
    ]
    for tool in slack_tools:
        duplicates.append({
            "component_a": f"mcp__slack__{tool}",
            "component_b": f"mcp__claude_ai_Slack__slack_*",
            "type": "mcp_tool",
            "similarity": 0.85,
            "recommendation": "Deduplicate Slack MCP servers (~3k tokens savings)",
        })

    return duplicates


# --- Naming Validation ---

# Gerund patterns: words ending in -ing
# Old regex had catastrophic backtracking on long hyphenated names (ReDoS).
# e.g. 'electron-cross-platform-patterns' took 6.5s to fail.
# Fix: simple O(n) check — any hyphen-delimited part ending in 'ing'.
def _has_gerund(name: str) -> bool:
    return any(part.endswith("ing") for part in name.split("-"))
# Exempt names (brand names, short commands)
NAMING_EXEMPT = {
    "broly", "agy", "kono", "ralph", "shipit", "demo", "mode",
    "flow", "pair", "ultrathink",
}

def validate_naming(name: str, component_type: str) -> tuple[bool, str]:
    """Check if a component name follows naming conventions.

    Skills should use gerund form (e.g., 'creating-wizards').
    Commands stay short. Agents use descriptive roles.

    Returns:
        (is_valid, suggested_name)
    """
    if name in NAMING_EXEMPT:
        return True, ""

    if component_type == "skill":
        # Skills with -mode suffix are fine (they're mode skills)
        if name.endswith("-mode"):
            return True, ""
        # Check for gerund
        if _has_gerund(name):
            return True, ""
        # Suggest gerund form
        suggested = _suggest_gerund(name)
        return False, suggested

    # Agents and modes have looser naming
    return True, ""


def _suggest_gerund(name: str) -> str:
    """Suggest a gerund form for a skill name."""
    # Common transformations
    transforms = {
        "branch-cleanup": "cleaning-branches",
        "node-cleanup": "cleaning-node-artifacts",
        "strat-ops": "strategizing-operations",
        "legal-ops": "managing-legal-operations",
        "image-optim": "optimizing-images",
        "context-refresh": "refreshing-context",
        "context-gate": "gating-context",
        "session-resume": "resuming-sessions",
        "skill-creation-best-practices": "creating-skills-best-practices",
        "e2e-test-generation": "generating-e2e-tests",
        "context-extension": "extending-context",
        "pr-automation": "automating-pull-requests",
        "decision-locking": "locking-decisions",
        "mcp-apps": "building-mcp-apps",
        "mcp-surface-protocol": "managing-mcp-surface",
        "mcp-mode-protocol": "managing-mcp-modes",
    }
    if name in transforms:
        return transforms[name]

    # Generic: prepend a verb
    parts = name.split("-")
    if len(parts) >= 2:
        return f"managing-{name}"
    return f"using-{name}"


# --- Red-Team Analysis ---

def red_team_analysis(comp: ComponentFitness) -> str:
    """Self-critical analysis: is this component actually useful?"""
    notes = []

    if comp.use_count == 0 and not comp.protected:
        notes.append("ZERO usage recorded. Consider if this adds value or just adds complexity.")
    if comp.token_cost > 1000 and comp.use_count < 3:
        notes.append(f"High token cost ({comp.token_cost}) with low usage ({comp.use_count}). Poor ROI.")
    if comp.tier == "dead" and not comp.protected:
        notes.append("DEAD tier. Archive candidate unless there's a reason it hasn't been used.")
    if not comp.naming_valid:
        notes.append(f"Naming violation. Suggested: '{comp.suggested_name}'")

    return "; ".join(notes) if notes else "Healthy"


# --- Gap Analysis ---

def gap_analysis(components: list[ComponentFitness]) -> list[dict]:
    """Compare component inventory against common workflow patterns."""
    existing_names = {c.name for c in components}
    gaps = []

    # Known useful patterns that might be missing
    expected_capabilities = {
        "debugging-authentication": "Auth debugging workflow (OAuth, JWT, sessions)",
        "monitoring-performance": "Runtime performance monitoring",
        "managing-database-migrations": "Database schema migration workflow",
        "generating-api-documentation": "Auto-generate API docs from code",
    }

    for capability, description in expected_capabilities.items():
        # Check if any existing component covers this
        covered = False
        for name in existing_names:
            if _name_similarity(capability, name) > 0.6:
                covered = True
                break
        if not covered:
            gaps.append({
                "gap": capability,
                "description": description,
                "evidence": "Not found in component inventory",
            })

    return gaps


# --- Proposal Generation ---

def propose_changes(components: list[ComponentFitness],
                    duplicates: list[dict] = None,
                    gaps: list[dict] = None) -> list[dict]:
    """Generate ranked list of proposed changes."""
    proposals = []

    # Prune candidates (dead tier, not protected)
    for comp in components:
        if comp.tier == "dead" and not comp.protected:
            proposals.append({
                "action": "prune",
                "component": comp.name,
                "type": comp.component_type,
                "fitness": comp.fitness_score,
                "token_savings": comp.token_cost,
                "reason": f"Dead tier (fitness {comp.fitness_score:.2f}), no usage",
            })

    # Rename candidates
    for comp in components:
        if not comp.naming_valid and comp.suggested_name:
            proposals.append({
                "action": "rename",
                "component": comp.name,
                "type": comp.component_type,
                "suggested_name": comp.suggested_name,
                "reason": "Naming convention violation (not gerund form)",
            })

    # Duplicate removals
    if duplicates:
        for dup in duplicates:
            proposals.append({
                "action": "deduplicate",
                "component": f"{dup['component_a']} / {dup['component_b']}",
                "type": dup["type"],
                "similarity": dup["similarity"],
                "reason": dup["recommendation"],
            })

    # Gap proposals
    if gaps:
        for gap in gaps:
            proposals.append({
                "action": "add",
                "component": gap["gap"],
                "type": "skill",
                "reason": gap["description"],
            })

    # Sort: prune first (immediate savings), then deduplicate, rename, add
    action_order = {"prune": 0, "deduplicate": 1, "rename": 2, "add": 3}
    proposals.sort(key=lambda p: action_order.get(p["action"], 99))

    return proposals


# --- Archive ---

def archive_component(name: str, component_type: str, reason: str = "") -> dict:
    """Move a component to .archive/ with manifest. Never deletes."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = ARCHIVE_DIR / today

    if component_type == "skill":
        source = SKILLS_DIR / name
    elif component_type == "agent":
        source = AGENTS_DIR / f"{name}.md"
    elif component_type == "mode":
        source = MODES_DIR / f"{name}.yaml"
    else:
        return {"error": f"Unknown component type: {component_type}"}

    if not source.exists():
        return {"error": f"Source not found: {source}"}

    # Check protection
    if name in PROTECTED.get(component_type, set()):
        return {"error": f"'{name}' is protected and cannot be archived"}

    archive_path.mkdir(parents=True, exist_ok=True)
    dest = archive_path / source.name
    if dest.exists():
        return {"error": f"Already archived: {dest}"}

    if source.is_dir():
        shutil.copytree(source, dest)
        shutil.rmtree(source)
    else:
        shutil.copy2(source, dest)
        source.unlink()

    # Write manifest entry
    manifest_path = archive_path / "manifest.json"
    manifest = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            manifest = []

    manifest.append({
        "name": name,
        "type": component_type,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "source": str(source),
    })
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return {"archived": str(dest), "manifest": str(manifest_path)}


# --- Report Generation ---

def generate_report(components: list[ComponentFitness],
                    duplicates: list[dict],
                    proposals: list[dict],
                    gaps: list[dict]) -> str:
    """Generate human-readable evolution report."""
    lines = []
    lines.append("## Evolutionary Refactoring Report")
    lines.append(f"**Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Token budget
    total_tokens = sum(c.token_cost for c in components)
    prune_savings = sum(p.get("token_savings", 0) for p in proposals if p["action"] == "prune")
    lines.append("### Token Budget")
    lines.append(f"- Component tokens: ~{total_tokens:,} ({total_tokens * 100 // 1000000}% of 1M)")
    lines.append(f"- Projected savings from pruning: ~{prune_savings:,} tokens")
    lines.append("")

    # Tier distribution
    tiers = Counter(c.tier for c in components)
    lines.append("### Tier Distribution")
    lines.append(f"- Essential: {tiers.get('essential', 0)}")
    lines.append(f"- Active: {tiers.get('active', 0)}")
    lines.append(f"- Dormant: {tiers.get('dormant', 0)}")
    lines.append(f"- Dead: {tiers.get('dead', 0)}")
    lines.append("")

    # Prune candidates
    prune_list = [p for p in proposals if p["action"] == "prune"]
    if prune_list:
        lines.append("### Prune Candidates (Dead tier)")
        lines.append("")
        lines.append("| Component | Type | Fitness | Token Cost | Reason |")
        lines.append("|-----------|------|---------|------------|--------|")
        for p in prune_list[:15]:
            lines.append(
                f"| {p['component']} | {p['type']} | {p.get('fitness', 0):.2f} "
                f"| {p.get('token_savings', 0)} | {p['reason'][:60]} |"
            )
        lines.append("")

    # Naming violations
    rename_list = [p for p in proposals if p["action"] == "rename"]
    if rename_list:
        lines.append("### Rename Candidates (naming violations)")
        lines.append("")
        lines.append("| Current Name | Type | Suggested Name |")
        lines.append("|--------------|------|----------------|")
        for p in rename_list[:15]:
            lines.append(f"| {p['component']} | {p['type']} | {p.get('suggested_name', '')} |")
        lines.append("")

    # Duplicates
    if duplicates:
        lines.append("### Duplicate Components")
        lines.append("")
        lines.append("| Component A | Component B | Similarity | Recommendation |")
        lines.append("|-------------|-------------|------------|----------------|")
        for d in duplicates[:10]:
            lines.append(
                f"| {d['component_a'][:30]} | {d['component_b'][:30]} "
                f"| {d['similarity']:.0%} | {d['recommendation'][:50]} |"
            )
        lines.append("")

    # Gaps
    if gaps:
        lines.append("### Gap Analysis")
        lines.append("")
        lines.append("| Gap | Description |")
        lines.append("|-----|-------------|")
        for g in gaps:
            lines.append(f"| {g['gap']} | {g['description']} |")
        lines.append("")

    # Red-team notes
    flagged = [c for c in components if c.notes and c.notes != "Healthy"]
    if flagged:
        lines.append("### Red-Team Findings")
        lines.append("")
        lines.append("| Component | Type | Notes |")
        lines.append("|-----------|------|-------|")
        for c in flagged[:10]:
            lines.append(f"| {c.name} | {c.component_type} | {c.notes[:80]} |")
        lines.append("")

    return "\n".join(lines)


# --- GitHub Issue ---

def create_github_issue(report: str, proposals: list[dict],
                        dry_run: bool = False) -> dict:
    """Post evolutionary refactoring report as GitHub issue."""
    prune_count = sum(1 for p in proposals if p["action"] == "prune")
    rename_count = sum(1 for p in proposals if p["action"] == "rename")
    dedup_count = sum(1 for p in proposals if p["action"] == "deduplicate")

    title = f"chore: evolutionary refactoring - {prune_count} prune, {rename_count} rename, {dedup_count} dedup"

    body = (
        f"{report}\n\n"
        f"---\n"
        f"*Generated by `lib/evolutionary_refactor.py` (Issue #154)*"
    )

    if dry_run:
        return {"dry_run": True, "title": title, "body_preview": body[:300]}

    try:
        result = subprocess.run(
            ["gh", "issue", "create", "-R", "grip-platform/GRIP",
             "--title", title, "--body", body,
             "--label", "enhancement"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"issue_url": result.stdout.strip()}
        return {"error": result.stderr.strip()}
    except Exception as e:
        return {"error": str(e)}


# --- Full Pipeline ---

def run_audit() -> dict:
    """Full audit pipeline: scan, measure, score, analyze, report."""
    components = scan_components()
    components = measure_usage(components)

    for comp in components:
        calculate_fitness(comp)
        comp.notes = red_team_analysis(comp)

    duplicates = detect_duplicates(components)
    gaps = gap_analysis(components)
    proposals = propose_changes(components, duplicates, gaps)
    report = generate_report(components, duplicates, proposals, gaps)

    return {
        "components": components,
        "duplicates": duplicates,
        "gaps": gaps,
        "proposals": proposals,
        "report": report,
    }


# --- CLI ---

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/evolutionary_refactor.py <audit|propose|execute|fitness|duplicates>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cmd = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if cmd == "audit":
        result = run_audit()
        print(result["report"])
        print(f"\nTotal proposals: {len(result['proposals'])}")

    elif cmd == "propose":
        result = run_audit()
        print(result["report"])
        issue = create_github_issue(result["report"], result["proposals"], dry_run=dry_run)
        if issue.get("issue_url"):
            print(f"\nIssue created: {issue['issue_url']}")
        elif issue.get("dry_run"):
            print(f"\n[DRY-RUN] Would create: {issue['title']}")
        else:
            print(f"\nIssue creation failed: {issue.get('error', 'unknown')}")

    elif cmd == "execute":
        result = run_audit()
        print(result["report"])
        prune_candidates = [p for p in result["proposals"] if p["action"] == "prune"]
        if not prune_candidates:
            print("\nNo prune candidates found.")
            return
        print(f"\n{len(prune_candidates)} prune candidate(s). Use --confirm to archive.")
        if "--confirm" in sys.argv:
            for p in prune_candidates:
                archived = archive_component(p["component"], p["type"], p["reason"])
                if "error" in archived:
                    print(f"  SKIP {p['component']}: {archived['error']}")
                else:
                    print(f"  ARCHIVED {p['component']} -> {archived['archived']}")

    elif cmd == "fitness":
        components = scan_components()
        components = measure_usage(components)
        for comp in components:
            calculate_fitness(comp)
        # Sort by fitness ascending (worst first)
        components.sort(key=lambda c: c.fitness_score)
        print(f"{'Name':<40} {'Type':<8} {'Fitness':>8} {'Uses':>5} {'Tier':<10} {'Protected'}")
        print("-" * 90)
        for c in components:
            prot = "YES" if c.protected else ""
            print(f"{c.name:<40} {c.component_type:<8} {c.fitness_score:>8.3f} {c.use_count:>5} {c.tier:<10} {prot}")

    elif cmd == "duplicates":
        components = scan_components()
        duplicates = detect_duplicates(components)
        if not duplicates:
            print("No duplicates detected.")
            return
        print(f"{'Component A':<35} {'Component B':<35} {'Sim':>5} Recommendation")
        print("-" * 110)
        for d in duplicates:
            print(f"{d['component_a'][:34]:<35} {d['component_b'][:34]:<35} {d['similarity']:>5.0%} {d['recommendation'][:40]}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
