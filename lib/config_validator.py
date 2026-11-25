#!/usr/bin/env python3
"""
config_validator.py -- Validate GRIP configuration files against expected schemas.

Pure Python, no external dependencies. Validates settings.json, genome JSON,
SKILL.md files, mode configs, rules/*.md, and cache/*.json.

Usage:
    python3 lib/config_validator.py --type all
    python3 lib/config_validator.py --type genome --claude-dir ~/.claude
    python3 lib/config_validator.py --type settings --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    """A single validation finding."""
    file_path: str
    line: Optional[int]
    field: Optional[str]
    message: str
    severity: str  # "error", "warning", "info"
    fix_suggestion: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
            "fix_suggestion": self.fix_suggestion,
        }


@dataclass
class ValidationResult:
    """Result of validating a single file."""
    file_path: str
    file_type: str
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_type": self.file_type,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "passed": self.passed,
        }


@dataclass
class ValidationRule:
    """Extensible rule definition for custom validators."""
    name: str
    description: str
    check: Callable[[Any, str], List[ValidationError]]
    file_type: str
    severity: str = "error"


@dataclass
class ValidationReport:
    """Aggregate report across all validated files."""
    results: List[ValidationResult] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def warning_count(self) -> int:
        return sum(len(r.warnings) for r in self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_files": self.total_files,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "warnings": self.warning_count,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Valid models
# ---------------------------------------------------------------------------

VALID_MODELS = {
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-haiku-3-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
}

# Claude Code also accepts shorthand model aliases
VALID_MODEL_ALIASES = {
    "opus", "sonnet", "haiku",
}


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------

def _missing_file_error(path: str, message: str = "File does not exist") -> ValidationError:
    return ValidationError(file_path=path, line=None, field=None, message=message, severity="error")


def _check_file_exists(result: "ValidationResult", path: str, message: str = "File does not exist") -> bool:
    """Append a missing-file error and return False if path does not exist."""
    if not Path(path).exists():
        result.errors.append(_missing_file_error(path, message))
        return False
    return True


def _check_file_nonempty(result: "ValidationResult", path: str) -> bool:
    """Append an empty-file error and return False if file has 0 bytes."""
    if Path(path).stat().st_size == 0:
        result.errors.append(ValidationError(
            file_path=path, line=None, field=None,
            message="File is empty (0 bytes)",
            severity="error",
            fix_suggestion="Add valid JSON content or remove the file",
        ))
        return False
    return True


def _append_error(result: "ValidationResult", path: str, message: str,
                  line: "Optional[int]" = None, field: "Optional[str]" = None,
                  fix_suggestion: "Optional[str]" = None) -> None:
    result.errors.append(ValidationError(
        file_path=path, line=line, field=field,
        message=message, severity="error", fix_suggestion=fix_suggestion,
    ))


def _append_warning(result: "ValidationResult", path: str, message: str,
                    line: "Optional[int]" = None, field: "Optional[str]" = None,
                    fix_suggestion: "Optional[str]" = None) -> None:
    result.warnings.append(ValidationError(
        file_path=path, line=line, field=field,
        message=message, severity="warning", fix_suggestion=fix_suggestion,
    ))


def _load_json_content(result: "ValidationResult", path: str) -> "Optional[Any]":
    """Read path, parse JSON, append error on failure, return data or None."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        _append_error(result, path, f"Invalid JSON: {exc.msg}", line=exc.lineno)
        return None


def _load_validated_json(result: "ValidationResult", path: str) -> "Optional[Any]":
    """Check non-empty then parse JSON; return data or None on any failure."""
    if not _check_file_nonempty(result, path):
        return None
    return _load_json_content(result, path)


def _missing_dir_result(dir_path: str, file_type: str, message: str) -> "ValidationResult":
    """Return a ValidationResult with a single directory-missing error."""
    r = ValidationResult(file_path=dir_path, file_type=file_type)
    _append_error(r, dir_path, message)
    return r


def _init_json_result(path: str, file_type: str,
                      missing_msg: str = "File does not exist") -> "Tuple[ValidationResult, Optional[Any]]":
    """Create result, check exists, load+parse JSON. Returns (result, data_or_None)."""
    result = ValidationResult(file_path=path, file_type=file_type)
    if not _check_file_exists(result, path, missing_msg):
        return result, None
    data = _load_validated_json(result, path)
    return result, data


def _collect_dir_results(dir_path: str, glob_pattern: str,
                         validator: "Callable[[str], ValidationResult]",
                         file_type: str) -> "List[ValidationResult]":
    """Run validator on each file matching glob_pattern; set file_type on each result."""
    results = []
    for f in sorted(Path(dir_path).glob(glob_pattern)):
        r = validator(str(f))
        r.file_type = file_type
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------

def validate_json_file(
    path: str, schema_rules: Optional[List[ValidationRule]] = None
) -> ValidationResult:
    """Validate a JSON file for syntax and optional schema rules."""
    result, data = _init_json_result(path, "json")
    if data is None:
        return result

    if schema_rules:
        for rule in schema_rules:
            findings = rule.check(data, path)
            for finding in findings:
                if finding.severity == "error":
                    result.errors.append(finding)
                else:
                    result.warnings.append(finding)

    return result


# ---------------------------------------------------------------------------
# Markdown validation
# ---------------------------------------------------------------------------

def validate_markdown_file(
    path: str, required_sections: Optional[List[str]] = None
) -> ValidationResult:
    """Validate a Markdown file has content and required structure."""
    result = ValidationResult(file_path=path, file_type="markdown")
    if not _check_file_exists(result, path):
        return result
    content = Path(path).read_text(encoding="utf-8", errors="replace")

    if not content.strip():
        _append_error(result, path, "File is empty",
                      fix_suggestion="Add content with at least a heading")
        return result

    lines = content.split("\n")
    if not any(line.strip().startswith("#") for line in lines):
        _append_error(result, path, "No heading found (expected line starting with #)",
                      line=1, fix_suggestion="Add a title heading: # Your Title")

    if required_sections:
        content_lower = content.lower()
        for section in required_sections:
            # Look for heading with this text
            found = False
            for line in lines:
                stripped = line.strip().lstrip("#").strip()
                if stripped.lower() == section.lower():
                    found = True
                    break
            if not found:
                _append_warning(result, path, f"Missing expected section: {section}",
                                fix_suggestion=f"Add a section: ## {section}")

    return result


# ---------------------------------------------------------------------------
# Genome validation
# ---------------------------------------------------------------------------

def validate_genome(path: str) -> ValidationResult:
    """Validate a genome JSON file against the Gene/Genome schema."""
    result, data = _init_json_result(path, "genome", "Genome file does not exist")
    if data is None:
        return result

    if not isinstance(data, dict):
        result.errors.append(ValidationError(
            file_path=path, line=None, field=None,
            message="Genome must be a JSON object",
            severity="error",
        ))
        return result

    # Check for genes list
    if "genes" not in data:
        result.errors.append(ValidationError(
            file_path=path, line=None, field="genes",
            message="Missing required field: genes",
            severity="error",
            fix_suggestion='Add a "genes" array to the genome object',
        ))
        return result

    genes = data["genes"]
    if not isinstance(genes, list):
        result.errors.append(ValidationError(
            file_path=path, line=None, field="genes",
            message="genes must be an array",
            severity="error",
        ))
        return result

    if len(genes) == 0:
        result.warnings.append(ValidationError(
            file_path=path, line=None, field="genes",
            message="genes array is empty",
            severity="warning",
            fix_suggestion="Add gene entries to the genome",
        ))

    seen_names: set = set()
    for i, gene in enumerate(genes):
        _validate_gene(result, path, gene, i, seen_names)

    if "generation" in data:
        gen = data["generation"]
        if not isinstance(gen, int) or gen < 0:
            _append_warning(result, path, "generation should be a non-negative integer",
                            field="generation")

    return result


_VALID_COMPONENT_TYPES = {"skill", "agent", "rule", "lib", "hook", "config", "mode"}


def _validate_gene(result: ValidationResult, path: str,
                   gene: Any, idx: int, seen_names: set) -> None:
    """Validate a single gene entry from the genome genes array."""
    prefix = f"genes[{idx}]"
    if not isinstance(gene, dict):
        _append_error(result, path, f"{prefix}: gene must be an object", field=prefix)
        return

    if "name" not in gene:
        _append_error(result, path, f"{prefix}: missing required field 'name'",
                      field=f"{prefix}.name",
                      fix_suggestion=f"Add 'name' field to {prefix}")
    else:
        name = gene["name"]
        if not isinstance(name, str) or not name.strip():
            _append_error(result, path, f"{prefix}: name must be a non-empty string",
                          field=f"{prefix}.name")
        elif name in seen_names:
            _append_warning(result, path, f"{prefix}: duplicate gene name '{name}'",
                            field=f"{prefix}.name",
                            fix_suggestion=f"Remove or rename duplicate gene '{name}'")
        else:
            seen_names.add(name)

    if "fitness" in gene:
        fitness = gene["fitness"]
        if isinstance(fitness, (int, float)):
            if not (0.0 <= fitness <= 1.0):
                _append_error(result, path,
                              f"{prefix}: fitness {fitness} out of range [0.0, 1.0]",
                              field=f"{prefix}.fitness",
                              fix_suggestion=f"fitness {fitness} should be between 0.0 and 1.0 (inclusive)")
        else:
            _append_error(result, path,
                          f"{prefix}: fitness must be a number, got {type(fitness).__name__}",
                          field=f"{prefix}.fitness")

    if "component_type" in gene and gene["component_type"] not in _VALID_COMPONENT_TYPES:
        _append_warning(result, path,
                        f"{prefix}: unknown component_type '{gene['component_type']}'",
                        field=f"{prefix}.component_type",
                        fix_suggestion=f"Valid types: {', '.join(sorted(_VALID_COMPONENT_TYPES))}")

    if "generation_born" in gene:
        gen = gene["generation_born"]
        if not isinstance(gen, int) or gen < 0:
            _append_warning(result, path,
                            f"{prefix}: generation_born should be a non-negative integer",
                            field=f"{prefix}.generation_born")


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

def _validate_env_section(
    data: dict, path: str, result: ValidationResult,
) -> None:
    """Validate the env section of settings (effort level check)."""
    if "env" not in data:
        return
    env = data["env"]
    if not isinstance(env, dict):
        _append_error(result, path, "env must be an object", field="env")
        return
    if "CLAUDE_CODE_EFFORT_LEVEL" in env:
        valid_levels = {"low", "medium", "high"}
        level = env["CLAUDE_CODE_EFFORT_LEVEL"]
        if level not in valid_levels:
            _append_warning(result, path, f"Unknown effort level '{level}'",
                            field="env.CLAUDE_CODE_EFFORT_LEVEL",
                            fix_suggestion=f"Valid levels: {', '.join(sorted(valid_levels))}")


def _validate_permissions_section(
    data: dict, path: str, result: ValidationResult,
) -> None:
    """Validate the permissions.defaultMode field."""
    if "permissions" not in data:
        return
    perms = data["permissions"]
    if isinstance(perms, dict) and "defaultMode" in perms:
        valid_modes = {"default", "allowAll", "bypassAll"}
        mode = perms["defaultMode"]
        if mode not in valid_modes:
            _append_warning(result, path, f"Unknown permission mode '{mode}'",
                            field="permissions.defaultMode",
                            fix_suggestion=f"Valid modes: {', '.join(sorted(valid_modes))}")


def _validate_hooks_section(
    data: dict, path: str, result: ValidationResult,
) -> None:
    """Validate hooks structure, events, matchers (regex), and hook groups."""
    if "hooks" not in data:
        return
    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        _append_error(result, path, "hooks must be an object", field="hooks")
        return

    valid_hook_events = {
        "SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
        "PreCompact", "TaskCompleted", "UserPromptSubmit",
        "ConfigChange", "Notification", "Stop",
    }
    for event_name in hooks:
        if event_name not in valid_hook_events:
            _append_warning(result, path, f"Unknown hook event '{event_name}'",
                            field=f"hooks.{event_name}",
                            fix_suggestion=f"Valid events: {', '.join(sorted(valid_hook_events))}")

        event_hooks = hooks[event_name]
        if not isinstance(event_hooks, list):
            _append_error(result, path, f"hooks.{event_name} must be an array",
                          field=f"hooks.{event_name}")
            continue

        for j, hook_group in enumerate(event_hooks):
            if not isinstance(hook_group, dict):
                _append_error(result, path, "Hook group must be an object",
                              field=f"hooks.{event_name}[{j}]")
                continue

            matcher = hook_group.get("matcher")
            if matcher is not None:
                if not isinstance(matcher, str):
                    _append_warning(result, path,
                                    f"matcher should be a string, got {type(matcher).__name__}",
                                    field=f"hooks.{event_name}[{j}].matcher",
                                    fix_suggestion='Use a regex string pattern (e.g. ".*" or "Bash|Edit")')
                elif matcher:
                    try:
                        re.compile(matcher)
                    except re.error as exc:
                        _append_error(result, path,
                                      f'Invalid regex matcher "{matcher}": {exc}',
                                      field=f"hooks.{event_name}[{j}].matcher",
                                      fix_suggestion='Fix regex pattern (e.g. use ".*" instead of "*")')

            if "hooks" not in hook_group:
                _append_error(result, path, "Hook group missing 'hooks' array",
                              field=f"hooks.{event_name}[{j}].hooks")
                continue

            for k, hook in enumerate(hook_group["hooks"]):
                if not isinstance(hook, dict):
                    _append_error(result, path, "Hook entry must be an object",
                                  field=f"hooks.{event_name}[{j}].hooks[{k}]")
                    continue
                if "type" not in hook:
                    _append_error(result, path, "Hook missing required 'type' field",
                                  field=f"hooks.{event_name}[{j}].hooks[{k}].type")
                if "command" not in hook:
                    _append_error(result, path, "Hook missing required 'command' field",
                                  field=f"hooks.{event_name}[{j}].hooks[{k}].command")


def _validate_model_field(
    data: dict, path: str, result: ValidationResult,
) -> None:
    """Validate the model field against VALID_MODELS."""
    if "model" not in data:
        return
    model = data["model"]
    if isinstance(model, str) and model not in VALID_MODELS and model not in VALID_MODEL_ALIASES:
        result.warnings.append(ValidationError(
            file_path=path, line=None, field="model",
            message=f"Unknown model '{model}'",
            severity="warning",
            fix_suggestion=f"Known models: {', '.join(sorted(VALID_MODELS | VALID_MODEL_ALIASES))}",
        ))


def validate_settings(path: str) -> ValidationResult:
    """Validate settings.json for required GRIP configuration."""
    result, data = _init_json_result(path, "settings", "Settings file does not exist")
    if data is None:
        return result

    if not isinstance(data, dict):
        result.errors.append(ValidationError(
            file_path=path, line=None, field=None,
            message="Settings must be a JSON object",
            severity="error",
        ))
        return result

    _validate_env_section(data, path, result)
    _validate_permissions_section(data, path, result)
    _validate_hooks_section(data, path, result)
    _validate_model_field(data, path, result)

    return result


# ---------------------------------------------------------------------------
# Directory validators
# ---------------------------------------------------------------------------

def validate_skills_dir(skills_dir: str) -> List[ValidationResult]:
    """Validate all SKILL.md files in a skills directory."""
    results = []
    p = Path(skills_dir)

    if not p.exists() or not p.is_dir():
        return [_missing_dir_result(skills_dir, "skills_dir", "Skills directory does not exist")]

    for skill_dir in sorted(p.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith("."):
            continue

        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            result = validate_markdown_file(str(skill_md))
            result.file_type = "skill"

            # Skills should have a description (content beyond the heading)
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            lines = [l for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
            if len(lines) < 2:
                _append_warning(result, str(skill_md),
                                "Skill has minimal content (less than 2 non-heading lines)",
                                fix_suggestion="Add a description explaining what this skill does")
        else:
            result = ValidationResult(file_path=str(skill_md), file_type="skill")
            _append_warning(result, str(skill_md),
                            f"Skill directory '{skill_dir.name}' missing SKILL.md",
                            fix_suggestion=f"Create {skill_md} with a title and description")

        results.append(result)

    return results


def validate_rules_dir(rules_dir: str) -> List[ValidationResult]:
    """Validate all .md files in the rules directory."""
    if not Path(rules_dir).is_dir():
        return [_missing_dir_result(rules_dir, "rules_dir", "Rules directory does not exist")]
    return _collect_dir_results(rules_dir, "*.md", validate_markdown_file, "rule")


def validate_cache_dir(cache_dir: str) -> List[ValidationResult]:
    """Validate all .json files in the cache directory."""
    if not Path(cache_dir).is_dir():
        return [_missing_dir_result(cache_dir, "cache_dir", "Cache directory does not exist")]
    return _collect_dir_results(cache_dir, "*.json", validate_json_file, "cache")


# ---------------------------------------------------------------------------
# Full validation
# ---------------------------------------------------------------------------

def _validate_yaml_file(cfg_file: "Path") -> ValidationResult:
    """Validate a single YAML config file for non-empty content and name presence."""
    p = str(cfg_file)
    result = ValidationResult(file_path=p, file_type="config")
    content = cfg_file.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        _append_error(result, p, "Config file is empty")
        return result
    has_name = any(
        line.strip().startswith("name:") or line.strip().startswith("# ")
        for line in content.split("\n")
    )
    if not has_name:
        _append_warning(result, p, "Config file has no name or title comment",
                        field="name",
                        fix_suggestion="Add a 'name:' field or '# Title' comment")
    return result


def _validate_config_dir(config_dir: "Path", report: ValidationReport) -> None:
    """Validate YAML and JSON files in the config directory."""
    if not config_dir.exists():
        return
    for cfg_file in sorted(config_dir.iterdir()):
        if cfg_file.suffix in (".yaml", ".yml"):
            report.results.append(_validate_yaml_file(cfg_file))
        elif cfg_file.suffix == ".json":
            report.results.append(validate_json_file(str(cfg_file)))


def run_full_validation(claude_dir: Optional[str] = None) -> ValidationReport:
    """Run validation across all GRIP configuration files."""
    if claude_dir is None:
        claude_dir = os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude"))

    report = ValidationReport()
    base = Path(claude_dir)

    settings_path = base / "settings.json"
    if settings_path.exists():
        report.results.append(validate_settings(str(settings_path)))

    for genome_candidate in [base / "data" / "genome.json",
                              base / "cache" / "genome.json",
                              base / "genome.json"]:
        if genome_candidate.exists():
            report.results.append(validate_genome(str(genome_candidate)))

    for subdir, validator in [
        ("skills", validate_skills_dir),
        ("rules", validate_rules_dir),
        ("cache", validate_cache_dir),
    ]:
        d = base / subdir
        if d.exists():
            report.results.extend(validator(str(d)))

    _validate_config_dir(base / "config", report)
    return report


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(report: ValidationReport) -> str:
    """Format a validation report as ASCII text."""
    lines = []
    lines.append("=" * 60)
    lines.append("GRIP Config Validation Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(
        f"Total: {report.total_files} files | "
        f"Passed: {report.passed_count} | "
        f"Failed: {report.failed_count} | "
        f"Warnings: {report.warning_count}"
    )
    lines.append("")

    # Group by status
    failed = [r for r in report.results if not r.passed]
    warned = [r for r in report.results if r.passed and r.warnings]
    clean = [r for r in report.results if r.passed and not r.warnings]

    if failed:
        lines.append("-" * 60)
        lines.append("FAILED:")
        lines.append("-" * 60)
        for r in failed:
            lines.append(f"  [FAIL] {r.file_path} ({r.file_type})")
            for e in r.errors:
                loc = f" line {e.line}" if e.line else ""
                fld = f" [{e.field}]" if e.field else ""
                lines.append(f"         ERROR{loc}{fld}: {e.message}")
                if e.fix_suggestion:
                    lines.append(f"         FIX: {e.fix_suggestion}")
            for w in r.warnings:
                fld = f" [{w.field}]" if w.field else ""
                lines.append(f"         WARN{fld}: {w.message}")
                if w.fix_suggestion:
                    lines.append(f"         FIX: {w.fix_suggestion}")
        lines.append("")

    if warned:
        lines.append("-" * 60)
        lines.append("WARNINGS:")
        lines.append("-" * 60)
        for r in warned:
            lines.append(f"  [WARN] {r.file_path} ({r.file_type})")
            for w in r.warnings:
                fld = f" [{w.field}]" if w.field else ""
                lines.append(f"         WARN{fld}: {w.message}")
                if w.fix_suggestion:
                    lines.append(f"         FIX: {w.fix_suggestion}")
        lines.append("")

    if clean:
        lines.append("-" * 60)
        lines.append("PASSED:")
        lines.append("-" * 60)
        for r in clean:
            lines.append(f"  [PASS] {r.file_path} ({r.file_type})")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_json(report: ValidationReport) -> str:
    """Format a validation report as JSON."""
    return json.dumps(report.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_all(args, claude_dir: str, report: "ValidationReport") -> "Optional[int]":
    return run_full_validation(claude_dir)


def _cmd_settings(args, claude_dir: str, report: "ValidationReport") -> "Optional[int]":
    files = args.files or [str(Path(claude_dir) / "settings.json")]
    for f in files:
        report.results.append(validate_settings(f))
    return None


def _cmd_genome(args, claude_dir: str, report: "ValidationReport") -> "Optional[int]":
    if args.files:
        for f in args.files:
            report.results.append(validate_genome(f))
    else:
        for candidate in ["data/genome.json", "genome.json", "cache/genome.json"]:
            p = Path(claude_dir) / candidate
            if p.exists():
                report.results.append(validate_genome(str(p)))
    return None


def _cmd_files_required(validator, label: str) -> "Callable":
    def _cmd(args, claude_dir: str, report: "ValidationReport") -> "Optional[int]":
        if not args.files:
            print(f"Error: --type {label} requires file arguments", file=sys.stderr)
            return 1
        for f in args.files:
            report.results.append(validator(f))
        return None
    return _cmd


def _cmd_dir(validator, subdir: str) -> "Callable":
    def _cmd(args, claude_dir: str, report: "ValidationReport") -> "Optional[int]":
        report.results.extend(validator(str(Path(claude_dir) / subdir)))
        return None
    return _cmd


_VALIDATION_COMMANDS: Dict[str, "Callable"] = {
    "all":      _cmd_all,
    "settings": _cmd_settings,
    "genome":   _cmd_genome,
    "json":     _cmd_files_required(validate_json_file, "json"),
    "markdown": _cmd_files_required(validate_markdown_file, "markdown"),
    "skills":   _cmd_dir(validate_skills_dir, "skills"),
    "rules":    _cmd_dir(validate_rules_dir, "rules"),
    "cache":    _cmd_dir(validate_cache_dir, "cache"),
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate GRIP configuration files"
    )
    parser.add_argument(
        "--type",
        choices=list(_VALIDATION_COMMANDS),
        default="all",
        help="Type of validation to run (default: all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output report as JSON",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help="Path to .claude directory (default: ~/.claude)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific files to validate (for json/markdown/genome/settings types)",
    )

    args = parser.parse_args(argv)
    claude_dir = args.claude_dir or os.environ.get(
        "CLAUDE_DIR", os.path.expanduser("~/.claude")
    )

    report = ValidationReport()
    cmd = _VALIDATION_COMMANDS[args.type]
    result = cmd(args, claude_dir, report)
    if isinstance(result, int):
        return result
    if isinstance(result, ValidationReport):
        report = result

    if args.json_output:
        print(format_json(report))
    else:
        print(format_report(report))

    return 0 if report.failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
