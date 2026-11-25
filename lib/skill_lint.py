#!/usr/bin/env python3
"""
skill_lint.py — Strict Agent-Skills-spec linter + deterministic fixer.

Mirrors the validation rules of strict recursive skill loaders
(pi-coding-agent, OpenCode, the Agent Skills spec):

  - description: required, <= 1024 chars
  - name: must equal the parent directory name
  - name: /^[a-z0-9-]+$/ (no underscores/uppercase), <= 64 chars,
          no leading/trailing/consecutive hyphens
  - frontmatter must be YAML-parseable by a strict parser (eemeli/yaml):
      * plain scalars must not contain ": " (nested-mapping trap)
      * plain scalars must not start with a reserved indicator
        ([ { " ' | > & * ! @ ` % etc.)
      * a value that starts with a quote and has trailing text
        (e.g. - "/grip" command) is a hard parse error

The fixer repairs files in place, deterministically:
  - quotes unsafe scalar values (single-quoted unless the value
    contains a single quote, then double-quoted with escapes)
  - sets name: to the parent directory name
  - synthesises a description from the first meaningful body line
    when missing (the strict loaders DROP skills without a description)
  - truncates descriptions > 1024 chars
  - renames directories with invalid name characters to their
    sanitised form (hyphenated)

These rules approximate the strict loaders' parser (eemeli/yaml); no
verifier script for that parser is in this repository. A complete flow
list or map ([...], {...}) is a collection, not a plain scalar, and is
left as it is.

Usage:
    python3 lib/skill_lint.py check <root...> [--depth-1] [--json]
    python3 lib/skill_lint.py fix   <root...> [--depth-1]
    python3 lib/skill_lint.py attest <root...> [--depth-1]   # receipt only

Exit codes: 0 clean, 1 violations found, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
NAME_RE = re.compile(r"^[a-z0-9-]+$")
# chars that may not START a plain (unquoted) YAML scalar
RESERVED_START = set('[{}\'"|>&*!@`%#')
# "key: value" line
KEYVAL_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
# "- item" line
ITEM_RE = re.compile(r"^\s*-\s?(.*)$")

ATTEST_PATH = Path.home() / ".grip" / "artifacts" / "skill-corpus-attestation.json"


@dataclass
class Violation:
    code: str
    message: str
    path: str = ""


@dataclass
class FileReport:
    path: str
    violations: list[Violation] = field(default_factory=list)
    repaired: bool = False


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_body, rest). None if no frontmatter.

    Same delimiter rules as strict loaders: first line starts with ---,
    closing delimiter is a line that is exactly ---.
    """
    lines = text.split("\n")
    if not lines or not lines[0].startswith("---"):
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text


_FLOW_CLOSE = {"[": "]", "{": "}"}


def _structural_chars(value: str) -> tuple[list[tuple[int, str]], bool]:
    """The characters outside quoted strings, and whether every quote closed.

    Inside double quotes a backslash escapes the next character, so
    [a, "b\\"c"] is one list. Single quotes have no escapes: a doubled ''
    closes and reopens, which still balances.
    """
    outside: list[tuple[int, str]] = []
    quote, escaped = "", False
    for index, ch in enumerate(value):
        if escaped:
            escaped = False
        elif quote == '"' and ch == "\\":
            escaped = True
        elif quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        else:
            outside.append((index, ch))
    return outside, not quote


def _is_flow_collection(value: str) -> bool:
    """True for one complete YAML flow list or map, such as [] or [a, "b c"].

    That is a collection, not a plain scalar, so the plain-scalar rules do not
    apply to it, and quoting it turns the list into a string. The 2026-09-06
    sweep did exactly that to eleven lines in six skills (triggers: [] became
    triggers: '[]'), and lib/skill_recommender.py then ignored their triggers.
    """
    if len(value) < 2 or _FLOW_CLOSE.get(value[0]) != value[-1]:
        return False
    outside, closed = _structural_chars(value)
    depth = 0
    for index, ch in outside:
        depth += (ch in "[{") - (ch in "]}")
        if depth == 0 and index < len(value) - 1:
            return False  # "[a] [b]": the first list closed before the end
    return closed and depth == 0


def unsafe_scalar(value: str) -> str | None:
    """Return the reason a plain scalar is YAML-unsafe, else None.

    Fatal patterns verified against the strict loaders' parser
    (eemeli/yaml):
      - ": " inside a plain scalar (nested-mapping trap)
      - trailing colon
      - first char is a reserved indicator (unquoted)
      - a leading quote followed by more text (quote-then-text)
      - a leading dash followed by space (looks like a sequence)

    A complete flow collection ([...] or {...}) is not a plain scalar and is
    never flagged; see _is_flow_collection.
    """
    if _is_flow_collection(value):
        return None
    if ": " in value:
        return 'contains ": " (nested-mapping trap)'
    if value.endswith(":"):
        return "trailing colon"
    if value[:1] in RESERVED_START:
        return f"starts with reserved character {value[:1]!r}"
    if value[:1] == "-" and value[1:2] in (" ", ""):
        return "starts with a dash"
    return None


def quote_scalar(value: str) -> str:
    """YAML-quote a scalar so any content is safe."""
    if "'" not in value:
        return "'" + value + "'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def sanitize_dir_name(name: str) -> str:
    out = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    out = re.sub(r"-{2,}", "-", out)
    return out or "skill"


def synthesize_description(name: str, body: str) -> str:
    for line in body.split("\n"):
        s = line.strip()
        if not s or s == "---" or s.startswith("#"):
            continue
        if s.startswith("[TODO:"):
            continue
        s = s.strip("`").strip()
        if not s:
            continue
        return s[:300]
    return f"Skill protocol for {name}."


@dataclass
class _ScanState:
    """Where a line-by-line frontmatter scan stands: the open key, if any."""

    current_key: str | None = None
    current_is_fold: bool = False

    def is_continuation(self, line: str) -> bool:
        """True for an indented line inside a folded or literal block value."""
        return line.startswith((" ", "\t")) and self.current_key is not None and self.current_is_fold


def _is_quoted(value: str) -> bool:
    return len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"')


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _check_keyval(key: str, raw: str, state: _ScanState, parsed: dict[str, str],
                  where: str) -> Violation | None:
    state.current_is_fold = False
    if raw == "" or raw[:1] in (">", "|"):
        state.current_key = key
        state.current_is_fold = raw[:1] in (">", "|")
        parsed.setdefault(key, "")
        return None
    state.current_key = None
    if _is_quoted(raw):
        parsed[key] = raw[1:-1]
        return None
    reason = unsafe_scalar(raw)
    if reason:
        return Violation("yaml-unsafe", f"key {key!r}: value {reason}", where)
    parsed[key] = raw.strip("'\"")
    return None


def _check_item(item: str, current_key: str, where: str) -> Violation | None:
    reason = unsafe_scalar(item) if item and not _is_quoted(item) else None
    if reason:
        return Violation("yaml-unsafe", f"list item under {current_key!r}: {reason}", where)
    return None


def _check_line(line: str, state: _ScanState, parsed: dict[str, str], where: str) -> Violation | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if state.is_continuation(line):
        # block-scalar (folded/literal) continuation — always YAML-safe
        parsed[state.current_key] = (parsed.get(state.current_key, "") + " " + stripped).strip()
        return None
    keyval = KEYVAL_RE.match(line)
    if keyval and not line.startswith((" ", "\t")):
        return _check_keyval(keyval.group(1), keyval.group(2).strip(), state, parsed, where)
    item = ITEM_RE.match(line)
    if item and state.current_key is not None:
        return _check_item(item.group(1).strip(), state.current_key, where)
    return None


def _scan_frontmatter(fm: str, where: str) -> tuple[dict[str, str], Violation | None]:
    """The parsed scalar fields, or the first YAML-unsafe value (which ends the scan)."""
    parsed: dict[str, str] = {}
    state = _ScanState()
    for line in fm.split("\n"):
        problem = _check_line(line, state, parsed, where)
        if problem:
            return parsed, problem
    return parsed, None


def _field_violations(name: str, parsed: dict[str, str], where: str) -> list[Violation]:
    desc = parsed.get("description", "")
    fm_name = parsed.get("name", name)
    checks = [
        (not desc.strip(), "no-description", "description is required"),
        (bool(desc.strip()) and len(desc) > MAX_DESCRIPTION_LENGTH, "desc-too-long",
         f"description is {len(desc)} chars (max {MAX_DESCRIPTION_LENGTH})"),
        (fm_name != name, "name-mismatch", f'name "{fm_name}" != parent dir "{name}"'),
        (len(name) > MAX_NAME_LENGTH, "name-too-long", f"{len(name)} chars (max {MAX_NAME_LENGTH})"),
        (not NAME_RE.match(name), "name-charset", "must be lowercase a-z, 0-9, hyphens only"),
        (name.startswith("-") or name.endswith("-"), "name-hyphen-edge", "must not start or end with a hyphen"),
        ("--" in name, "name-double-hyphen", "must not contain consecutive hyphens"),
    ]
    return [Violation(code, message, where) for failed, code, message in checks if failed]


def check_text(path: Path, text: str) -> list[Violation]:
    """Validate a SKILL.md's frontmatter against the strict rules."""
    fm, _body = split_frontmatter(text)
    if fm is None:
        return [Violation("no-description", "no frontmatter; description is required", str(path))]
    parsed, unsafe = _scan_frontmatter(fm, str(path))
    if unsafe:
        return [unsafe]  # file would be dropped by a strict loader; rest is moot
    return _field_violations(path.parent.name, parsed, str(path))


def _frontmatter_from_body(name: str, body: str) -> str:
    """A whole new file for a SKILL.md that has no frontmatter at all."""
    rest = body
    if rest.startswith("---") and (rest == "---" or rest[3:4] == "\n"):
        rest = rest[4:].lstrip("\n")
    desc = synthesize_description(name, rest)
    return f"---\nname: {name}\ndescription: {quote_scalar(desc)}\n---\n{rest}"


def _repair_keyval(line: str, key: str, value: str, state: _ScanState, seen_keys: set[str]) -> str:
    seen_keys.add(key)
    state.current_key, state.current_is_fold = None, False
    if value == "" or value[:1] in (">", "|"):
        state.current_key = key
        state.current_is_fold = value[:1] in (">", "|")
        return line
    if unsafe_scalar(value) and not _is_quoted(value):
        return f"{_indent(line)}{key}: {quote_scalar(value)}"
    return line


def _repair_item(line: str, item: str) -> str:
    if item and not _is_quoted(item) and unsafe_scalar(item):
        return f"{_indent(line)}- {quote_scalar(item)}"
    return line


def _repair_line(line: str, state: _ScanState, seen_keys: set[str]) -> str:
    if line.strip() == "" or line.lstrip().startswith("#"):
        return line
    if state.is_continuation(line):
        return line  # block-scalar (folded/literal) continuation — leave untouched
    keyval = KEYVAL_RE.match(line)
    if keyval and not line.startswith((" ", "\t")):
        return _repair_keyval(line, keyval.group(1), keyval.group(2).strip(), state, seen_keys)
    item = ITEM_RE.match(line)
    if item and state.current_key is not None:
        return _repair_item(line, item.group(1).strip())
    return line


def _top_level_index(lines: list[str], key: str) -> int | None:
    """Index of the first unindented ``key:`` line, or None."""
    return next((i for i, line in enumerate(lines)
                 if not line.startswith((" ", "\t")) and (m := KEYVAL_RE.match(line)) and m.group(1) == key),
                None)


def _ensure_name(out: list[str], name: str) -> bool:
    """Make ``name:`` equal the parent directory; True when a line changed."""
    index = _top_level_index(out, "name")
    if index is None:
        out.insert(0, f"name: {name}")
        return True
    if out[index] != f"name: {name}":
        out[index] = f"name: {name}"
        return True
    return False


def _insert_description(out: list[str], desc: str) -> None:
    index = _top_level_index(out, "name")
    entry = f"description: {quote_scalar(desc)}"
    if index is None:
        out.append(entry)
    else:
        out.insert(index + 1, entry)


def _unquoted(value: str) -> str:
    value = value.strip()
    return value[1:-1] if _is_quoted(value) else value


def _truncate_description(out: list[str]) -> bool:
    """Cut a description longer than MAX_DESCRIPTION_LENGTH; True when cut."""
    index = _top_level_index(out, "description")
    if index is None:
        return False
    line = out[index]
    value = _unquoted(KEYVAL_RE.match(line).group(2))
    if len(value) <= MAX_DESCRIPTION_LENGTH:
        return False
    out[index] = f"{_indent(line)}description: {quote_scalar(value[:MAX_DESCRIPTION_LENGTH])}"
    return True


def repair_text(path: Path, text: str) -> tuple[str, bool]:
    """Deterministically repair a SKILL.md. Returns (new_text, changed)."""
    name = path.parent.name
    fm, body = split_frontmatter(text)
    if fm is None:
        return _frontmatter_from_body(name, body), True
    state, seen_keys = _ScanState(), set()
    lines = fm.split("\n")
    out = [_repair_line(line, state, seen_keys) for line in lines]
    changed = out != lines
    changed = _ensure_name(out, name) or changed
    if "description" not in seen_keys:
        _insert_description(out, synthesize_description(name, body))
        changed = True
    changed = _truncate_description(out) or changed
    return "---\n" + "\n".join(out) + f"\n---\n{body}", changed


def walk_skill_files(root: Path, depth_1: bool = False) -> list[Path]:
    """Find SKILL.md files like the strict loaders do.

    A directory containing SKILL.md is a skill root — do not recurse
    into it. Follows directory symlinks. Skips dotdirs and node_modules.
    ``depth_1`` only inspects the root's direct children (GRIP convention:
    skills/_archived/ lives under skills/ and is skipped).
    """
    found: list[Path] = []

    def scan_dir(d: Path) -> tuple[Path | None, list[Path]]:
        """Return (skill_md, subdirs) for one directory."""
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return None, []
        skill_md: Path | None = None
        subdirs: list[Path] = []
        for e in entries:
            if e.name.startswith(".") or e.name == "node_modules":
                continue
            p = Path(e.path)
            if e.is_dir(follow_symlinks=True):
                subdirs.append(p)
            elif e.is_file(follow_symlinks=True) and e.name == "SKILL.md":
                skill_md = p
        return skill_md, subdirs

    root_md, root_dirs = scan_dir(root)
    if root_md is not None:
        return [root_md]  # the root IS a skill dir
    if depth_1:
        for p in root_dirs:
            sm = p / "SKILL.md"
            if sm.is_file():
                found.append(sm)
        return found
    stack: list[Path] = list(reversed(root_dirs))
    while stack:
        d = stack.pop()
        skill_md, subdirs = scan_dir(d)
        if skill_md is not None:
            found.append(skill_md)
            continue  # skill root: do not recurse
        stack.extend(reversed(subdirs))
    return found


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _format_report(r: FileReport) -> str:
    return "\n".join(f"{v.code}\t{r.path}\t{v.message}" for v in r.violations)


def _print_violations(bad: list[FileReport]) -> None:
    for r in bad:
        print(_format_report(r))


def cmd_check(roots: list[Path], depth_1: bool, json_out: bool) -> int:
    files = list(chain.from_iterable(walk_skill_files(r, depth_1) for r in roots))
    reports = [FileReport(str(f), check_text(f, load_text(f))) for f in files]
    bad = [r for r in reports if r.violations]
    if json_out:
        print(json.dumps([asdict(r) for r in bad], indent=2))
    else:
        _print_violations(bad)
        print(f"{len(bad)}/{len(reports)} skills violate strict loader rules")
    return 1 if bad else 0


def cmd_fix(roots: list[Path], depth_1: bool) -> int:
    repaired = 0
    renamed = 0
    files = list(chain.from_iterable(walk_skill_files(r, depth_1) for r in roots))
    for f in files:
        text = load_text(f)
        new_text, changed = repair_text(f, text)
        if changed:
            f.write_text(new_text, encoding="utf-8")
            repaired += 1
    # rename invalid dir names (only depth-1: we never touch nested skill roots)
    dirs = chain.from_iterable(
        sorted(root.iterdir(), key=lambda d: d.name) for root in roots
    )
    for d in dirs:
        try:
            if not d.is_dir() or d.name.startswith("."):
                continue
            good = sanitize_dir_name(d.name)
            if good != d.name and (d / "SKILL.md").is_file():
                target = d.parent / good
                if target.exists():
                    print(f"skip rename {d} -> {target}: target exists", file=sys.stderr)
                    continue
                d.rename(target)
                renamed += 1
        except OSError:
            pass
    print(f"repaired {repaired} SKILL.md files, renamed {renamed} directories")
    return 0


def root_digest(root: Path, files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: str(p)):
        h.update(str(f).encode())
        h.update(b"\0")
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()


def _count_root_violations(files: list[Path]) -> int:
    bad = 0
    for f in files:
        if check_text(f, load_text(f)):
            bad += 1
    return bad


def cmd_attest(roots: list[Path], depth_1: bool) -> int:
    total = 0
    bad = 0
    per_root: dict[str, dict] = {}
    for root in roots:
        files = walk_skill_files(root, depth_1)
        total += len(files)
        root_bad = _count_root_violations(files)
        bad += root_bad
        per_root[str(root)] = {
            "skills": len(files),
            "violations": root_bad,
            "sha256": root_digest(root, files),
        }
    receipt = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "lib/skill_lint.py",
        "rules": "strict-loader spec: description required; name == parent dir; "
                 "name charset ^[a-z0-9-]+$; YAML-safe plain scalars",
        "total_skills": total,
        "violations": bad,
        "roots": per_root,
    }
    ATTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ATTEST_PATH.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"attestation written to {ATTEST_PATH}: {bad} violations across {total} skills")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "fix", "attest"])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--depth-1", action="store_true",
                    help="only validate direct children (GRIP convention: "
                         "_archived/ lives under skills/ and is skipped)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    roots = [Path(r) for r in args.roots]
    if args.command == "check":
        return cmd_check(roots, args.depth_1, args.json)
    if args.command == "fix":
        return cmd_fix(roots, args.depth_1)
    return cmd_attest(roots, args.depth_1)


if __name__ == "__main__":
    raise SystemExit(main())
