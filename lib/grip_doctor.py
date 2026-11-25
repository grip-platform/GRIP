#!/usr/bin/env python3
"""GRIP Doctor -- comprehensive system health diagnostic.

Checks 8 subsystems: genome, metrics, skills, hooks, memory, cache, rules, config.
Traffic-light scoring (GREEN/YELLOW/RED) with ASCII diagnostic report.

Usage:
    from lib.grip_doctor import run_diagnostics, format_report

    report = run_diagnostics()
    print(format_report(report))

CLI:
    python3 lib/grip_doctor.py              # ASCII report
    python3 lib/grip_doctor.py --json       # JSON output
    python3 lib/grip_doctor.py --subsystem genome  # check specific subsystem
"""

import contextlib
import json
import os
import stat
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from claude_dir import get_claude_dir
from telemetry_field_health import find_always_empty_fields
from typing import Any, Dict, List, Optional, Tuple


# Resolved through the canonical shim (rules/path-portability.md), which strips,
# expands and resolves the variable and -- the property this module was
# hand-rolling for -- only touches Path.home() when the variable is unset, so it
# cannot raise RuntimeError on a machine that can name no home directory.
CLAUDE_DIR = get_claude_dir()


def _mtime_or_zero(path: Path) -> float:
    """Sort key that cannot crash the health report.

    A file can vanish between glob() and stat() -- a sync, a checkout, another
    session's cleanup -- and a health check that raises tells the operator
    nothing about the subsystem it was asked to grade. A missing file sorts
    oldest, so it is simply not chosen as the active index.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@contextlib.contextmanager
def _package_imports_resolvable():
    """Make ``lib.<pkg>`` importable for the body, then put sys.path back.

    Running this as ``python3 lib/grip_doctor.py`` puts lib/ on sys.path but not
    its parent, so bare imports like telemetry_field_health resolve while the
    package-qualified ones (``from lib.hal.embeddings import health``) do not.
    The embeddings check therefore reported "No module named 'lib.hal'" and
    scored 0.00 RED on a machine where lib.hal imports perfectly well -- the
    health command failing itself rather than the subsystem it grades.

    Scoped, because two reviewers were right in turn. The first said a module
    mutating sys.path on IMPORT imposes it on every importer; the second said
    moving it to call time only relocated the same permanent side effect, since
    a long-lived process running diagnostics would resolve every later ``lib.*``
    against this checkout for the rest of its life. Restoring on exit answers
    both: the imported module stays in sys.modules, so the check keeps working,
    and no caller inherits a reordered path.

    The own root is placed at index 0 UNCONDITIONALLY, not "if absent". The
    previous guard skipped the insert whenever the root was already on the path
    at any position -- an editable install or a test-runner layout -- so a
    shadowing entry ahead of it still won, and the claim that the own tree wins
    held only in the exact stripped-path shape this was written for. Being
    present is not the property that matters; being FIRST is.

    CLAUDE_DIR is appended, so an install whose lib/ does not sit beside this
    file still resolves without CLAUDE_DIR ever shadowing the running tree.
    """
    saved = list(sys.path)
    try:
        own = Path(__file__).resolve().parent.parent
        if own.is_dir():
            sys.path.insert(0, str(own))
        if CLAUDE_DIR.is_dir() and str(CLAUDE_DIR) not in sys.path:
            sys.path.append(str(CLAUDE_DIR))
        yield
    finally:
        sys.path[:] = saved



class HealthStatus(Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNKNOWN = "UNKNOWN"


@dataclass
class SubsystemCheck:
    name: str
    status: HealthStatus
    score: float  # 0.0 - 1.0
    details: str
    findings: List[str] = field(default_factory=list)


@dataclass
class DiagnosticReport:
    checks: List[SubsystemCheck]
    overall_score: float
    overall_status: HealthStatus
    timestamp: float
    claude_dir: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "score": c.score,
                    "details": c.details,
                    "findings": c.findings,
                }
                for c in self.checks
            ],
            "overall_score": self.overall_score,
            "overall_status": self.overall_status.value,
            "timestamp": self.timestamp,
            "claude_dir": self.claude_dir,
        }


def score_to_status(score: float) -> HealthStatus:
    """Convert a 0.0-1.0 score to a traffic-light status."""
    if score >= 0.8:
        return HealthStatus.GREEN
    elif score >= 0.5:
        return HealthStatus.YELLOW
    else:
        return HealthStatus.RED


def _read_error(name: str, filename: str, exc: Exception) -> SubsystemCheck:
    """Return a RED check for a file read failure."""
    return SubsystemCheck(
        name=name,
        status=HealthStatus.RED,
        score=0.0,
        details=f"Read error: {exc}",
        findings=[f"Cannot read {filename}: {exc}"],
    )


def _finalise(name: str, score: float, details: str, findings: List[str]) -> SubsystemCheck:
    """Clamp score, derive traffic-light status, and return the final check result."""
    score = max(0.0, min(1.0, score))
    return SubsystemCheck(
        name=name,
        status=score_to_status(score),
        score=score,
        details=details,
        findings=findings,
    )


def _score_genome_data(data: Dict[str, Any]) -> Tuple[float, str, List[str]]:
    """Score genome gene count, fitness distribution, and generation."""
    findings: List[str] = []
    score = 1.0
    genes = data.get("genes", {})
    gene_count = len(genes)
    if gene_count == 0:
        score -= 0.4
        findings.append("Genome contains zero genes")
    elif gene_count < 10:
        score -= 0.2
        findings.append(f"Low gene count: {gene_count}")
    if genes:
        fitness_values = [
            gene.get("fitness", 0.0)
            for gene in genes.values()
            if isinstance(gene, dict) and isinstance(gene.get("fitness", 0.0), (int, float))
        ]
        if fitness_values:
            if all(f == 0.0 for f in fitness_values):
                score -= 0.3
                findings.append("All gene fitness values are zero")
            elif (avg := sum(fitness_values) / len(fitness_values)) < 0.3:
                score -= 0.15
                findings.append(f"Low average fitness: {avg:.2f}")
    generation = data.get("generation", 0)
    if generation == 0:
        score -= 0.1
        findings.append("Genome generation is 0 (never evolved)")
    return score, f"{gene_count} genes, gen {generation}", findings


def _score_metrics_content(lines: List[str], path: Path) -> Tuple[float, List[str]]:
    """Score JSON line validity, file recency, and entry count."""
    findings: List[str] = []
    score = 1.0
    entry_count = len(lines)
    sample_indices = set(
        list(range(min(5, entry_count))) + list(range(max(0, entry_count - 5), entry_count))
    )
    bad_lines = 0
    for idx in sample_indices:
        try:
            json.loads(lines[idx])
        except json.JSONDecodeError:
            bad_lines += 1
    if bad_lines > 0:
        score -= 0.3 * (bad_lines / len(sample_indices))
        findings.append(f"{bad_lines} malformed JSON lines in sample")
    try:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > 7:
            score -= 0.3
            findings.append(f"Metrics store has no entries in last {int(age_days)} days")
        elif age_days > 3:
            score -= 0.15
            findings.append(f"Metrics last updated {int(age_days)} days ago")
    except OSError:
        pass
    if entry_count < 10:
        score -= 0.2
        findings.append(f"Low entry count: {entry_count}")
    return score, findings


def _scan_skill_dirs(skills_dir: Path) -> Tuple[int, int, List[str]]:
    """Return (total, valid, empty_skills) by scanning skill subdirectories."""
    total, valid = 0, 0
    empty_skills: List[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        # "_" marks a shared/partial directory; "." marks a hidden one, and
        # skills/.archive holds RETIRED skills. Counting an archive of things
        # deliberately taken out of service as live skills missing their
        # SKILL.md reports permanent debt that no one can ever clear.
        if not skill_dir.is_dir() or skill_dir.name[:1] in ("_", "."):
            continue
        total += 1
        skill_md = skill_dir / "SKILL.md"
        try:
            if skill_md.exists() and len(skill_md.read_text(encoding="utf-8").strip()) > 10:
                valid += 1
            else:
                empty_skills.append(skill_dir.name)
        except OSError:
            empty_skills.append(skill_dir.name)
    return total, valid, empty_skills


def _scan_hook_files(hooks_dir: Path) -> Tuple[int, int, List[str], List[str]]:
    """Scan hooks dir; return (total, healthy, non_executable, findings)."""
    findings: List[str] = []
    hook_files = [f for f in sorted(hooks_dir.iterdir()) if f.is_file() and f.suffix in (".py", ".sh")]
    total, healthy = len(hook_files), 0
    non_executable: List[str] = []
    # NTFS carries no POSIX permission bits, so Python reports 0o666 for every
    # file on Windows and this test fails for all of them unconditionally --
    # measured 8 of 8 .sh hooks flagged on a machine where every hook runs fine.
    # A check that cannot pass is not a check; it is noise that trains an
    # operator to ignore the report.
    #
    # An earlier version of this comment justified the suppression with "hooks
    # are invoked through an explicit interpreter anyway", and a reviewer
    # correctly pointed out that this is equally true on POSIX and so would
    # argue for deleting the check everywhere. That was the wrong reason. The
    # Windows-specific fact is narrower: NTFS has no execute permission at all,
    # so the bit carries NO information there -- its absence is a property of
    # the filesystem, never of the file. On POSIX the bit is real and a missing
    # one is a genuine finding, which is why the check is kept there and tested.
    exec_bit_is_meaningful = sys.platform != "win32"
    for hf in hook_files:
        try:
            s = hf.stat()
            is_exec = bool(s.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            if s.st_size > 0:
                healthy += 1
                if exec_bit_is_meaningful and not is_exec and hf.suffix != ".py":
                    non_executable.append(hf.name)
            else:
                findings.append(f"Empty hook file: {hf.name}")
        except OSError:
            findings.append(f"Cannot stat hook: {hf.name}")
    if non_executable:
        findings.append(f"Non-executable hooks: {', '.join(non_executable[:5])}")
    return total, healthy, non_executable, findings


# Claude Code truncates MEMORY.md on session load at ~24.4 KiB of CHARACTERS
# (24.4 * 1024 = 24_986; observed CC 2.x on 2026-06-05 — a 46,304-char file was
# reported as "45.2KB", i.e. it counts chars/1024). Long multi-line index
# entries blow this CHARACTER budget while staying well under any LINE count
# (195 lines / 46k chars was silently truncated), so the character budget —
# NOT the line count — is the dimension that actually causes silent index loss.
# This is the one place to update if a future CC version changes the budget.
_HARNESS_INDEX_CHAR_BUDGET = 24_986


def _score_memory_content(content: str, path: Path) -> Tuple[float, List[str]]:
    """Score MEMORY.md for size, staleness, and section coverage.

    Primary size signal is the CHARACTER count against the harness load budget
    (the real truncation dimension); line count is only a secondary proxy.
    """
    findings: List[str] = []
    score = 1.0
    char_count = len(content)
    if char_count > _HARNESS_INDEX_CHAR_BUDGET:
        score -= 0.4
        findings.append(
            f"MEMORY.md over the harness load budget: {char_count} chars "
            f"(> {_HARNESS_INDEX_CHAR_BUDGET}); the index is being SILENTLY "
            f"TRUNCATED on session load — shorten entries to one line "
            f"(< ~200 chars) and move detail into the linked topic files"
        )
    elif char_count > int(_HARNESS_INDEX_CHAR_BUDGET * 0.85):
        score -= 0.15
        findings.append(
            f"MEMORY.md approaching the harness load budget: {char_count} chars "
            f"(budget {_HARNESS_INDEX_CHAR_BUDGET})"
        )
    line_count = len(content.splitlines())
    if line_count > 300:
        score -= 0.15
        findings.append(f"MEMORY.md too many lines: {line_count} (recommended < 300)")
    try:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > 14:
            score -= 0.3
            findings.append(f"MEMORY.md not updated in {int(age_days)} days")
        elif age_days > 7:
            score -= 0.15
            findings.append(f"MEMORY.md last updated {int(age_days)} days ago")
    except OSError:
        pass
    found_sections = sum(1 for s in ["current work", "memory", "todo", "permanent"] if s in content.lower())
    if found_sections < 2:
        score -= 0.1
        findings.append("MEMORY.md may lack standard sections")
    return max(0.0, score), findings


def _scan_rule_files(rules_dir: Path, expected_files: List[str]) -> Tuple[int, List[str], List[str]]:
    """Return (found_count, missing, findings) for expected rule files."""
    findings: List[str] = []
    found, missing = 0, []
    for ef in expected_files:
        p = rules_dir / ef
        try:
            if p.is_file() and len(p.read_text(encoding="utf-8").strip()) > 10:
                found += 1
            else:
                missing.append(ef)
                if p.exists():
                    findings.append(f"Rule file empty or near-empty: {ef}")
        except OSError:
            missing.append(ef)
    if missing:
        findings.append(f"Missing rule files: {', '.join(missing)}")
    return found, missing, findings


def _run_config_checks(claude_dir: Path) -> Tuple[int, int, List[str]]:
    """Run config sub-checks; return (passed, total, findings)."""
    findings: List[str] = []
    passed = 0
    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        try:
            json.loads(settings_path.read_text(encoding="utf-8"))
            passed += 1
        except (json.JSONDecodeError, OSError):
            findings.append("settings.json is corrupted")
    else:
        findings.append("settings.json not found")
    dot_claude = claude_dir / ".claude"
    if dot_claude.exists() and dot_claude.is_dir():
        passed += 1 if (dot_claude / "CLAUDE.md").exists() else 0
        if not (dot_claude / "CLAUDE.md").exists():
            findings.append(".claude/CLAUDE.md not found")
    else:
        findings.append(".claude/ subdirectory not found")
    config_dir = claude_dir / "config"
    if config_dir.exists() and config_dir.is_dir() and any(config_dir.iterdir()):
        passed += 1
    else:
        findings.append("config/ directory not found" if not config_dir.exists() else "config/ directory is empty")
    root_md = claude_dir / "CLAUDE.md"
    try:
        if root_md.exists() and len(root_md.read_text(encoding="utf-8").strip()) > 10:
            passed += 1
        else:
            findings.append("Root CLAUDE.md is empty" if root_md.exists() else "Root CLAUDE.md not found")
    except OSError:
        findings.append("Cannot read root CLAUDE.md")
    return passed, 4, findings


def _scan_cache_files(cache_dir: Path, expected_files: List[str]) -> Tuple[int, int, List[str], List[str]]:
    """Scan cache dir; return (total, valid, missing_expected, findings)."""
    findings: List[str] = []
    json_files = [f for f in sorted(cache_dir.iterdir()) if f.is_file() and f.suffix == ".json"]
    total, valid = len(json_files), 0
    corrupted: List[str] = []
    for jf in json_files:
        try:
            json.loads(jf.read_text(encoding="utf-8"))
            valid += 1
        except (json.JSONDecodeError, OSError):
            corrupted.append(jf.name)
    if corrupted:
        findings.append(f"Corrupted JSON files: {', '.join(corrupted[:5])}")
    existing = {f.name for f in json_files}
    missing = [ef for ef in expected_files if ef not in existing]
    if missing:
        findings.append(f"Missing expected: {', '.join(missing)}")
    return total, valid, missing, findings


# ---------------------------------------------------------------------------
# Subsystem checks
# ---------------------------------------------------------------------------

def check_genome(claude_dir: Path) -> SubsystemCheck:
    """Check genome health: existence, valid JSON, gene count, fitness."""
    name = "Genome"
    genome_path = claude_dir / "cache" / "genome.json"
    if not genome_path.exists():
        return SubsystemCheck(
            name=name,
            status=HealthStatus.RED,
            score=0.0,
            details="genome.json not found",
            findings=["Genome file missing at cache/genome.json"],
        )
    try:
        data = json.loads(genome_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return SubsystemCheck(
            name=name,
            status=HealthStatus.RED,
            score=0.1,
            details=f"Corrupted JSON: {exc}",
            findings=["genome.json contains invalid JSON"],
        )
    except OSError as exc:
        return _read_error(name, "genome.json", exc)
    score, details, findings = _score_genome_data(data)
    return _finalise(name, score, details, findings)


def check_metrics(claude_dir: Path) -> SubsystemCheck:
    """Check metrics JSONL: existence, recency, entry count."""
    name = "Metrics"
    metrics_path = claude_dir / "metrics.jsonl"
    if not metrics_path.exists():
        return SubsystemCheck(
            name=name,
            status=HealthStatus.RED,
            score=0.0,
            details="metrics.jsonl not found",
            findings=["Metrics file missing"],
        )
    try:
        content = metrics_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _read_error(name, "metrics.jsonl", exc)
    lines = [ln for ln in content.strip().splitlines() if ln.strip()]
    if not lines:
        return SubsystemCheck(
            name=name,
            status=HealthStatus.RED,
            score=0.1,
            details="metrics.jsonl is empty",
            findings=["Metrics file exists but contains no entries"],
        )
    score, findings = _score_metrics_content(lines, metrics_path)
    return _finalise(name, score, f"{len(lines)} entries", findings)


def check_skills(claude_dir: Path) -> SubsystemCheck:
    """Check skills directory for valid SKILL.md files."""
    name = "Skills"
    skills_dir = claude_dir / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return SubsystemCheck(
            name=name,
            status=HealthStatus.RED,
            score=0.0,
            details="skills/ directory not found",
            findings=["Skills directory missing"],
        )
    total, valid, empty_skills = _scan_skill_dirs(skills_dir)
    if total == 0:
        return SubsystemCheck(
            name=name,
            status=HealthStatus.YELLOW,
            score=0.5,
            details="No skill directories found",
            findings=["Skills directory exists but contains no skill subdirectories"],
        )
    findings: List[str] = []
    score = valid / total
    if empty_skills:
        shown = empty_skills[:5]
        suffix = f" (+{len(empty_skills) - 5} more)" if len(empty_skills) > 5 else ""
        findings.append(f"Skills missing/empty SKILL.md: {', '.join(shown)}{suffix}")
    if total < 20:
        score = max(0.0, score - 0.1)
        findings.append(f"Low skill count: {total}")
    return _finalise(name, score, f"{valid}/{total} valid skills", findings)


def check_hooks(claude_dir: Path) -> SubsystemCheck:
    """Check hooks directory for PreToolUse hooks that are executable."""
    name = "Hooks"
    hooks_dir = claude_dir / "hooks"
    if not hooks_dir.exists() or not hooks_dir.is_dir():
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="hooks/ directory not found", findings=["Hooks directory missing"],
        )
    total, healthy, non_executable, findings = _scan_hook_files(hooks_dir)
    if total == 0:
        return SubsystemCheck(
            name=name, status=HealthStatus.YELLOW, score=0.5,
            details="No hook scripts found", findings=["hooks/ exists but contains no .py/.sh files"],
        )
    score = healthy / total
    if non_executable:
        score = max(0.0, score - 0.1 * len(non_executable) / total)
    return _finalise(name, score, f"{healthy}/{total} hooks healthy", findings)


def check_memory(claude_dir: Path) -> SubsystemCheck:
    """Check MEMORY.md: existence, size, staleness."""
    name = "Memory"
    # Identity first, recency only as a fallback. projects/ accumulates one
    # directory per machine and per checkout the operator has ever used, and
    # sorting by NAME picked whichever slug happened to sort first -- on A>>'s
    # Windows machine that was projects/-Users-<user>--claude, V>>'s
    # index from a different machine, which the doctor then reported as 28 days
    # stale while this session's own index had been written that morning.
    # Grading the wrong artefact, shape 2 in rules/tests-must-be-able-to-fail.md.
    #
    # The first fix sorted by mtime instead, and a reviewer showed that is a
    # single-writer rule applied to a multi-writer tree: on a shared checkout
    # another machine writing more recently wins, so the doctor grades the
    # BUSIEST foreign index rather than the alphabetically-first one -- the same
    # fault in a new shape. Cross-machine clock skew makes it strictly worse,
    # since an index on a fast clock always sorts first AND always looks fresh.
    #
    # But the active index does not need to be guessed at all. The slug encodes
    # the path it belongs to (rules/path-portability.md), so this machine's own
    # index is derivable rather than inferred. mtime remains only for the case
    # where no slug matches, which is where a heuristic is the honest answer.
    own_slug = str(claude_dir).replace("/", "-").replace(".", "-")
    candidates = list(claude_dir.glob("projects/*/memory/MEMORY.md"))
    mine = [p for p in candidates if p.parent.parent.name == own_slug]
    project_paths = mine or sorted(candidates, key=_mtime_or_zero, reverse=True)
    memory_path = next((p for p in project_paths + [claude_dir / "MEMORY.md"] if p.exists()), None)
    if memory_path is None:
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="MEMORY.md not found", findings=["No MEMORY.md found in expected locations"],
        )
    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _read_error(name, "MEMORY.md", exc)
    if not content.strip():
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.1,
            details="MEMORY.md is empty", findings=["MEMORY.md exists but is empty"],
        )
    score, findings = _score_memory_content(content, memory_path)
    return _finalise(name, score, f"{len(content.splitlines())} lines", findings)


def check_cache(claude_dir: Path) -> SubsystemCheck:
    """Validate cache/ JSON files."""
    name = "Cache"
    cache_dir = claude_dir / "cache"
    if not cache_dir.exists() or not cache_dir.is_dir():
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="cache/ directory not found", findings=["Cache directory missing"],
        )
    total, valid, missing, findings = _scan_cache_files(cache_dir, ["cube-state.json"])
    if total == 0:
        return SubsystemCheck(
            name=name, status=HealthStatus.YELLOW, score=0.5,
            details="No JSON files in cache/", findings=["Cache directory exists but contains no JSON files"],
        )
    score = valid / total
    if missing:
        score = max(0.0, score - 0.1 * len(missing))
    return _finalise(name, score, f"{valid}/{total} valid JSON files", findings)


def check_telemetry_fields(claude_dir: Path) -> SubsystemCheck:
    """Flag telemetry fields that are recorded but have never held a value.

    A hook reading the wrong key from its payload does not fail — it writes a
    row every time and the field is simply blank forever. sentinel-hook.py did
    exactly that with Claude Code's Notification event for 2,241 records before
    anyone noticed, which is why this check exists.
    """
    name = "Telemetry fields"
    log = claude_dir / "cache" / "sentinel.jsonl"
    if not log.is_file():
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="cache/sentinel.jsonl not found",
            findings=["No sentinel telemetry recorded — cannot verify field health"],
        )
    records = _read_jsonl(log)
    if not records:
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="sentinel.jsonl is empty or unreadable",
            findings=["No sentinel telemetry recorded — cannot verify field health"],
        )
    empty = find_always_empty_fields(records)
    if not empty:
        return _finalise(
            name, 1.0, f"no always-empty fields across {len(records)} records", [],
        )
    # Every finding is a field carrying no information at all, so the score is
    # not softened by how many other fields happen to be fine.
    return _finalise(
        name,
        max(0.0, 1.0 - 0.5 * len(empty)),
        f"{len(empty)} always-empty field(s) across {len(records)} records",
        [f.describe() for f in empty],
    )


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping any line that will not parse.

    A health check that raises on one malformed line is worse than useless.
    """
    records: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
    except OSError:
        return []
    return records


def _tree_ok(name: str, state: Any, detail: str) -> SubsystemCheck:
    """Canonical and level: the agents are serving the code that was reviewed."""
    return SubsystemCheck(
        name=name, status=HealthStatus.GREEN, score=1.0,
        details=detail, findings=[],
    )


def _tree_behind(name: str, state: Any, detail: str) -> SubsystemCheck:
    """On canonical but stale: a merged fix is not yet a deployed fix."""
    behind = state.behind
    gap = f"{behind} commit(s)" if behind is not None else "an unknown number of commits"
    return SubsystemCheck(
        name=name, status=HealthStatus.YELLOW, score=0.4,
        details=detail,
        findings=[
            f"Agents are running code {gap} behind canonical — "
            "a merged fix is not yet a deployed fix. Run: git pull --rebase",
        ],
    )


def _tree_off_canonical(name: str, state: Any, detail: str) -> SubsystemCheck:
    """Parked on a feature branch: production is running unreviewed code."""
    return SubsystemCheck(
        name=name, status=HealthStatus.RED, score=0.0,
        details=detail,
        findings=[
            f"Agents are executing branch '{state.branch}', not canonical. "
            "Feature work belongs in a worktree (lib/grip-worktree.sh)",
        ],
    )


def _tree_indeterminate(name: str, state: Any, detail: str) -> SubsystemCheck:
    """INDETERMINATE and anything unrecognised: unknown must never read as fine."""
    return SubsystemCheck(
        name=name, status=HealthStatus.RED, score=0.0,
        details=detail,
        findings=["Could not determine the executing tree's state — treat as unverified"],
    )


def check_executing_tree(claude_dir: Path) -> SubsystemCheck:
    """Report whether the tree GRIP actually executes is serving canonical code.

    launchd agents (com.grip.channel, com.grip.cos, com.grip.heartbeat,
    com.grip.mesh-bus, com.grip.mesh-client) run code straight out of this
    working tree. Whatever branch an interactive session last left checked out
    therefore decides what production runs — and what any verification read
    returns.

    On 2026-08-03 that tree sat eight days off main and three separate reads
    came back silently wrong. A merged fix is not a deployed fix.

    INDETERMINATE scores as a failure rather than a pass, deliberately: an
    unknown tree state must never be indistinguishable from a healthy one,
    which is the whole failure this check exists to surface.
    """
    name = "Executing tree"
    try:
        from shared_tree_state import BEHIND, OFF_CANONICAL, OK, tree_state
    except ImportError:
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="shared_tree_state unavailable",
            findings=["Cannot determine what code the launchd agents are running"],
        )

    state = tree_state(claude_dir)
    detail = state.detail or state.verdict
    handler = {
        OK: _tree_ok,
        BEHIND: _tree_behind,
        OFF_CANONICAL: _tree_off_canonical,
    }.get(state.verdict, _tree_indeterminate)
    return handler(name, state, detail)


def check_rules(claude_dir: Path) -> SubsystemCheck:
    """Verify rules/ directory has expected files."""
    name = "Rules"
    rules_dir = claude_dir / "rules"
    if not rules_dir.exists() or not rules_dir.is_dir():
        return SubsystemCheck(
            name=name, status=HealthStatus.RED, score=0.0,
            details="rules/ directory not found", findings=["Rules directory missing"],
        )
    expected = [
        "CLAUDE.md", "efficiency-rules.md", "bash-safety.md", "commit-standards.md",
        "session-protocol.md", "python-async-safety.md", "operational-principles.md",
        "path-portability.md", "learned-insights.md",
    ]
    found, _, findings = _scan_rule_files(rules_dir, expected)
    return _finalise(name, found / len(expected), f"{found}/{len(expected)} expected rules present", findings)


def check_config(claude_dir: Path) -> SubsystemCheck:
    """Check settings.json, .claude directory structure, config/ directory."""
    name = "Config"
    passed, total, findings = _run_config_checks(claude_dir)
    return _finalise(name, passed / total, f"{passed}/{total} config checks passed", findings)


def check_embeddings(claude_dir: Path) -> SubsystemCheck:  # noqa: ARG001
    """Check embeddings backend health (lembed legacy or ollama via HAL shim).

    Surfaces silent failure modes: ollama daemon down, model not pulled,
    latency regression. Per Rule 17 (plain language): the `details` field
    is operator-readable; remediation guidance comes from the shim.

    W6: default switched from lembed to ollama. The lembed branch remains
    for explicit `GRIP_EMBED_BACKEND=lembed` opt-in.
    """
    name = "Embeddings"
    backend = os.environ.get("GRIP_EMBED_BACKEND", "ollama")
    if backend == "lembed":
        # lembed is RETIRED (W6/H218 removed the in-process model layer). The
        # `lembed` branch of get_engine now returns a bare, model-less class, so
        # it produces NO embeddings — report that honestly instead of a hollow
        # GREEN. See rules/ + docs/EMBEDDINGS-ARCHITECTURE.md.
        return _finalise(
            name,
            0.5,
            "backend: lembed is RETIRED (W6/H218 removed the in-process model) — "
            "unset GRIP_EMBED_BACKEND to use ollama",
            ["lembed returns no embeddings; set GRIP_EMBED_BACKEND=ollama or unset it"],
        )
    try:
        with _package_imports_resolvable():
            from lib.hal.embeddings import health  # noqa: WPS433 — local import
            h = health()
    except Exception as exc:  # noqa: BLE001
        return _finalise(name, 0.0, f"shim import failed: {exc}", [str(exc)])
    if h.get("ok"):
        details = (
            f"backend: ollama | model: {h.get('model', '?')} | "
            f"dim: {h.get('dimension', '?')} | latency: {h.get('latency_ms', '?')}ms"
        )
        return _finalise(name, 1.0, details, [])
    err = h.get("error", "unknown error")
    return _finalise(name, 0.0, f"backend: ollama unhealthy", [err])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Subsystem registry: name -> check function
SUBSYSTEM_CHECKS: Dict[str, Any] = {
    "genome": check_genome,
    "metrics": check_metrics,
    "skills": check_skills,
    "hooks": check_hooks,
    "memory": check_memory,
    "cache": check_cache,
    "telemetry": check_telemetry_fields,
    "executing tree": check_executing_tree,
    "rules": check_rules,
    "config": check_config,
    "embeddings": check_embeddings,
}

# Weights for overall score (must sum to 1.0)
SUBSYSTEM_WEIGHTS: Dict[str, float] = {
    "genome": 0.07,
    "metrics": 0.07,
    "skills": 0.12,
    "hooks": 0.12,
    "memory": 0.12,
    "cache": 0.07,
    "rules": 0.12,
    "config": 0.07,
    "embeddings": 0.08,
    # Telemetry field health earns a share because a field that never carries a
    # value is a correctness failure the other checks cannot see: the hook runs,
    # the log grows, and every dashboard reads healthy. Weights are rebalanced
    # rather than appended so they still sum to exactly 1.00.
    "telemetry": 0.08,
    # The executing tree earns a share because every other check here reads
    # that same tree. If it is serving a stale or off-canonical branch, each
    # of them answers confidently about the wrong code — so this one qualifies
    # all the others. Weights are rebalanced rather than appended so they
    # still sum to exactly 1.00.
    "executing tree": 0.08,
}


def compute_overall(checks: List[SubsystemCheck]) -> Tuple[float, HealthStatus]:
    """Compute weighted average score and overall status."""
    if not checks:
        return 0.0, HealthStatus.UNKNOWN

    total_weight = 0.0
    weighted_sum = 0.0

    for check in checks:
        name_lower = check.name.lower()
        weight = SUBSYSTEM_WEIGHTS.get(name_lower, 1.0 / len(checks))
        weighted_sum += check.score * weight
        total_weight += weight

    overall = weighted_sum / total_weight if total_weight > 0 else 0.0
    overall = max(0.0, min(1.0, overall))
    return overall, score_to_status(overall)


def run_diagnostics(
    claude_dir: Optional[Path] = None,
    subsystems: Optional[List[str]] = None,
) -> DiagnosticReport:
    """Run all or specified subsystem checks.

    Args:
        claude_dir: Override CLAUDE_DIR. Defaults to env or ~/.claude.
        subsystems: List of subsystem names to check. None = all.

    Returns:
        DiagnosticReport with all check results.
    """
    cdir = Path(claude_dir) if claude_dir else CLAUDE_DIR

    checks_to_run = SUBSYSTEM_CHECKS
    if subsystems:
        checks_to_run = {
            k: v for k, v in SUBSYSTEM_CHECKS.items() if k in subsystems
        }

    results: List[SubsystemCheck] = []
    for name, check_fn in checks_to_run.items():
        try:
            result = check_fn(cdir)
            results.append(result)
        except Exception as exc:
            results.append(
                SubsystemCheck(
                    name=name.capitalize(),
                    status=HealthStatus.RED,
                    score=0.0,
                    details=f"Check crashed: {exc}",
                    findings=[f"Unexpected error: {exc}"],
                )
            )

    overall_score, overall_status = compute_overall(results)

    return DiagnosticReport(
        checks=results,
        overall_score=overall_score,
        overall_status=overall_status,
        timestamp=time.time(),
        claude_dir=str(cdir),
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _bar(score: float, width: int = 10) -> str:
    """Render a score as a block bar: [████████░░]."""
    filled = int(round(score * width))
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def format_report(report: DiagnosticReport, use_json: bool = False) -> str:
    """Format a DiagnosticReport as ASCII or JSON.

    Args:
        report: The diagnostic report to format.
        use_json: If True, return JSON string. Otherwise ASCII.

    Returns:
        Formatted string.
    """
    if use_json:
        return json.dumps(report.to_dict(), indent=2)

    lines: List[str] = []
    lines.append("GRIP Doctor -- System Health Diagnostic")
    lines.append("=" * 43)
    lines.append("")

    # Subsystem table
    for check in report.checks:
        name_padded = check.name.ljust(12)
        bar = _bar(check.score)
        score_str = f"{check.score:.2f}"
        status_str = check.status.value
        lines.append(f"  {name_padded} {bar} {score_str}  {status_str}")

    lines.append("")
    lines.append(
        f"Overall: {report.overall_score:.2f} {report.overall_status.value}"
    )

    # Collect all findings
    all_findings: List[str] = []
    for check in report.checks:
        all_findings.extend(check.findings)

    if all_findings:
        lines.append(f"Findings: {len(all_findings)} issues detected")
        for finding in all_findings:
            lines.append(f"  - {finding}")
    else:
        lines.append("Findings: No issues detected")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="GRIP Doctor -- comprehensive system health diagnostic"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )
    parser.add_argument(
        "--subsystem",
        type=str,
        help="Check a specific subsystem (e.g. genome, metrics, skills)",
    )
    parser.add_argument(
        "--claude-dir",
        type=str,
        help="Override CLAUDE_DIR path",
    )
    args = parser.parse_args()

    cdir = Path(args.claude_dir) if args.claude_dir else None
    subsystems = [args.subsystem] if args.subsystem else None

    report = run_diagnostics(claude_dir=cdir, subsystems=subsystems)
    print(format_report(report, use_json=args.json))


if __name__ == "__main__":
    main()
