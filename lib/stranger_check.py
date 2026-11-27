#!/usr/bin/env python3
"""Stranger check: install and test a public repository the way its README says.

A stranger clones the public repository anonymously, runs the command lines its
README prints, in order, in one shell with input closed, and reports what
failed. The checker types no command of its own, except where a README gives
none (GRIP's smoke check, HAPPI's download). Those lines are labelled
``checker`` in every report, so a reader can tell the README's words from ours.

Where it runs:

  --where here   in a throwaway HOME on this machine, with no git credential
                 helper (this is what CI runs)
  --where vps    in a fresh ubuntu:24.04 container, as a non-root user, on the
                 ssh target named by $GRIP_STRANGER_SSH (it needs docker)

Usage:

  grip-stranger-check <target> [--where here|vps] [--tarball BUILD.tar.gz]
                      [--json] [--files] [--print-script] [--timeout SECONDS]
  grip-stranger-check --list

``--tarball`` checks a local build before it is pushed: the README is read from
the tarball, and the README's clone URL is pointed at a git repository made from
the tarball. Exit codes: 0 green, 1 red, 2 the check could not run.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

MARK = "@@STRANGER"
CMD_RE = re.compile(r"^@@STRANGER CMD (\d+)@@$")
RC_RE = re.compile(r"^@@STRANGER RC (\d+) (\d+)@@$")
HEAD_RE = re.compile(r"^@@STRANGER HEAD (\S+) ([0-9a-f]{7,40})@@$")
TIMEOUT_MARK = "@@STRANGER TIMEOUT@@"
COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b")
SUMMARY_RE = re.compile(r"\b\d+ (passed|failed|skipped|errors?)\b.* in [\d.]+s\b")
FAILING_RE = re.compile(r"^(FAILED|ERROR) (\S+?\.py)(?:::.*?)?(?: - (.*))?$")
# pytest's long report: a "=== FAILURES ===" or "=== ERRORS ===" section, one
# "___ test name ___" header per test, its "E   ..." error lines, its "path:line:"
# frame locations, and "--- Captured ... ---" output that belongs to the test, not the error.
SECTION_RE = re.compile(r"^={3,} (.+?) ={3,}$")
TEST_HEADER_RE = re.compile(r"^_{3,} (.+?) _{3,}$")
ERROR_LINE_RE = re.compile(r"^E(?:\s+(.*))?$")
# A location never starts with whitespace or with "E" and whitespace, so neither an
# error line nor an indented message line that mentions "x.py:12:" is read as one.
LOCATION_RE = re.compile(r"^(?!E\s)(\S.*?\.py):(\d+):(?: (.*))?$")
CAPTURED_RE = re.compile(r"^-{3,} Captured .+ -{3,}$")
# The report's caps: error lines kept per test, characters per line, rows per list.
ERROR_LINES_PER_TEST = 8
ERROR_LINE_CHARS = 300
CUT_MARK = "..."
MORE_MARK = "[more lines cut]"
REPORT_ROWS = 12
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DEFAULT_TIMEOUT_S = 2400
CONTAINER_IMAGE = "ubuntu:24.04"
SSH_ENV = "GRIP_STRANGER_SSH"
# Names a stranger's shell must not inherit: tokens, keys, passwords, sessions, URLs that can
# carry credentials, the ssh agent. A denylist covers the usual names only, so the
# workflow passes no secret at all; the container run inherits nothing.
SECRET_ENV_RE = re.compile(r"TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE|ACCESS_KEY|ASKPASS|_KEY$|^KEY$|_PAT$|"
                           r"SSH_AUTH_SOCK|_URL$|_DSN$", re.IGNORECASE)


class StrangerError(Exception):
    """The check could not run: a README line is missing, or a setting is absent."""


@dataclass(frozen=True)
class Line:
    """One command the stranger types.

    ``pattern`` names exactly one line inside a README code block. A line with
    no pattern is the checker's own, used only where the README gives none.
    ``expect`` is a regex the command's output must contain.
    """
    label: str
    pattern: str = ""
    text: str = ""
    expect: str = ""


@dataclass(frozen=True)
class Target:
    name: str
    repo: str
    readme_url: str
    lines: tuple
    platforms: tuple = ("linux", "macos", "windows")
    note: str = ""


_GRIP_SMOKE = (
    'cd ~/.claude && py=venv/bin/python && { [ -x "$py" ] || py=venv/Scripts/python.exe; } && '
    '"$py" -c "import lib.merkle, lib.decision_record, lib.paramount_backups, lib.pq_dual_sign; '
    "print('smoke: core modules import')\""
)

TARGETS: dict = {
    "grip": Target(
        name="grip",
        repo="https://github.com/grip-platform/GRIP.git",
        readme_url="https://raw.githubusercontent.com/grip-platform/GRIP/main/README.md",
        lines=(
            Line("install", pattern=r"^git clone https://github\.com/grip-platform/GRIP\.git ~/\.claude && cd ~/\.claude && \./scripts/install\.sh$"),
            Line("smoke", text=_GRIP_SMOKE, expect=r"smoke: core modules import"),
        ),
        note="GRIP's README gives no test command, so a checker smoke line stands in until public tests ship.",
    ),
    "hal": Target(
        name="hal",
        repo="https://github.com/grip-platform/HAL",
        readme_url="https://raw.githubusercontent.com/grip-platform/HAL/main/README.md",
        lines=(
            Line("install", pattern=r"^git clone https://github\.com/grip-platform/HAL ~/\.hal$"),
            Line("install", pattern=r"^(?:python3 -m venv ~/\.hal-test-venv && ~/\.hal-test-venv/bin/pip install .+|bash ~/\.hal/bin/test-venv\.sh)$"),
            Line("test", pattern=r"^cd ~/\.hal && HAL_HOME=~/\.hal (?:~/\.hal-test-venv/bin/python|bash bin/test-venv\.sh python) -m pytest .*tests$"),
        ),
    ),
    "happi": Target(
        name="happi",
        repo="https://github.com/CodeTonight-SA/HAPPI",
        readme_url="https://raw.githubusercontent.com/CodeTonight-SA/HAPPI/main/README.md",
        lines=(
            Line("download", text="git clone https://github.com/CodeTonight-SA/HAPPI happi && cd happi"),
            Line("test", pattern=r"^bash HAPPI\.md$"),
            Line("test", pattern=r"^echo .*\| bash HAPPI\.md run$", expect=r'"type":\s*"completed"'),
        ),
        note="HAPPI's README says to take HAPPI.md as-is and gives no download command, so the checker clones it.",
    ),
    "grasp": Target(
        name="grasp",
        repo="https://github.com/CodeTonight-SA/grasp",
        readme_url="https://raw.githubusercontent.com/CodeTonight-SA/grasp/main/README.md",
        lines=(
            Line("install", pattern=r"^git clone https://github\.com/CodeTonight-SA/grasp$"),
            Line("install", pattern=r"^cd grasp$"),
            Line("install", pattern=r"^python3 -m venv \.venv && \.venv/bin/pip install -e \. pytest$"),
            Line("test", pattern=r"^\.venv/bin/python -m pytest tests -q$"),
        ),
        platforms=("linux", "macos"),
        note="GRASP is POSIX-only by design (it uses fcntl), so Windows is unsupported.",
    ),
}


@dataclass(frozen=True)
class Command:
    index: int
    label: str
    source: str
    text: str
    expect: str = ""


@dataclass
class Report:
    target: str
    where: str
    origin: str
    commands: list = field(default_factory=list)
    heads: dict = field(default_factory=dict)
    tests: dict = field(default_factory=dict)
    failing_files: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    verdict: str = "RED"
    failures: list = field(default_factory=list)


# ── README parsing ──────────────────────────────────────────────────────────

def code_lines(readme: str) -> list:
    """Every command line inside a fenced code block, continuations joined."""
    lines, inside, pending = [], False, ""
    for raw in readme.splitlines():
        if FENCE_RE.match(raw):
            inside, pending = not inside, ""
            continue
        if not inside:
            continue
        pending, done = _join_continuation(pending, raw.strip())
        if done:
            lines.append(done)
    return lines


def _join_continuation(pending: str, line: str) -> tuple:
    if line.endswith("\\"):
        return pending + line[:-1].rstrip() + " ", ""
    joined = (pending + line).strip()
    if not joined or joined.startswith("#"):
        return "", ""
    return "", joined


def resolve_commands(target: Target, readme: str) -> list:
    """The commands the stranger types, in order. Raises when the README drifts."""
    available = code_lines(readme)
    commands = []
    for index, line in enumerate(target.lines, start=1):
        if not line.pattern:
            commands.append(Command(index, line.label, "checker", line.text, line.expect))
            continue
        commands.append(Command(index, line.label, "README", _one_match(target, line, available), line.expect))
    return commands


def _one_match(target: Target, line: Line, available: list) -> str:
    found = [text for text in available if re.search(line.pattern, text)]
    if len(found) != 1:
        raise StrangerError(
            f"{target.name}: the README has {len(found)} code lines matching {line.pattern!r}, "
            "not exactly one; the README changed, so update the target or the README"
        )
    return found[0]


# ── the script the stranger runs ────────────────────────────────────────────

_PRELUDE = r"""set -u
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:-} -p no:cacheprovider -rfE --tb=line"
export COLUMNS=400 GIT_TERMINAL_PROMPT=0 PIP_DISABLE_PIP_VERSION_CHECK=1
cd "$HOME" || { printf '\n@@STRANGER RC 0 1@@\n'; exit 0; }
stranger_heads() {
  for d in "$HOME"/* "$HOME"/.[!.]*; do
    [ -d "$d/.git" ] && printf '\n@@STRANGER HEAD %s %s@@\n' "${d#"$HOME"/}" "$(git -C "$d" rev-parse --short=9 HEAD)"
  done
  return 0
}
stranger_run() {
  printf '\n@@STRANGER CMD %s@@\n' "$1"
  eval "$2" </dev/null
  stranger_rc=$?
  printf '\n@@STRANGER RC %s %s@@\n' "$1" "$stranger_rc"
  return "$stranger_rc"
}
"""

_TARBALL_SETUP = r"""stranger_src="$HOME/.stranger-src"
mkdir -p "$stranger_src" && tar -xzf {tarball} {strip} -C "$stranger_src" \
  && git -C "$stranger_src" init -q && git -C "$stranger_src" add -A \
  && git -C "$stranger_src" -c user.name=stranger -c user.email=stranger@example.invalid commit -qm build \
  || {{ printf '\n@@STRANGER RC 0 1@@\n'; exit 0; }}
"""


def build_script(target: Target, commands: list, tarball: str = "", strip: bool = False) -> str:
    """One bash script: run each command in order, stop at the first failure."""
    parts = [_PRELUDE]
    if tarball:
        if not any(target.repo in command.text for command in commands):
            raise StrangerError(f"--tarball found no line that clones {target.repo}, so the run would test "
                                "GitHub's copy instead of the build; the README's clone URL changed")
        parts.append(_TARBALL_SETUP.format(tarball=shlex.quote(tarball),
                                           strip="--strip-components=1" if strip else ""))
    for command in commands:
        text = command.text.replace(target.repo, '"file://$stranger_src"') if tarball else command.text
        parts.append(f"stranger_run {command.index} {shlex.quote(text)} || {{ stranger_heads; exit 0; }}\n")
    parts.append("stranger_heads\n")
    return "".join(parts)


def container_script(inner: str) -> str:
    """Wrap a script to run as a fresh non-root user inside ubuntu:24.04."""
    if "\nSTRANGER_INNER\n" in f"\n{inner}\n":
        raise StrangerError("the inner script contains its own heredoc terminator")
    return (
        "set -u\nexport DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update -qq </dev/null >/dev/null 2>&1 && apt-get install -y -qq git python3 python3-venv "
        "python3-pip curl ca-certificates </dev/null >/dev/null 2>&1 "
        "|| { printf '\\n@@STRANGER RC 0 1@@\\napt-get failed\\n'; exit 0; }\n"
        "useradd -m -s /bin/bash stranger </dev/null\n"
        f"cat > /inner.sh <<'STRANGER_INNER'\n{inner}STRANGER_INNER\n"
        "chmod 644 /inner.sh\nsu - stranger -c 'bash /inner.sh' </dev/null\n"
    )


# ── output parsing ──────────────────────────────────────────────────────────

def segments(output: str) -> dict:
    """{command index: the output that command printed}."""
    found, current = {}, None
    for line in output.splitlines():
        start = CMD_RE.match(line)
        if start:
            current = int(start.group(1))
            found[current] = []
        elif RC_RE.match(line):
            current = None
        elif current is not None:
            found[current].append(line)
    return {index: "\n".join(lines) for index, lines in found.items()}


def exit_codes(output: str) -> dict:
    return {int(m.group(1)): int(m.group(2)) for m in map(RC_RE.match, output.splitlines()) if m}


def heads(output: str) -> dict:
    return {m.group(1): m.group(2) for m in map(HEAD_RE.match, output.splitlines()) if m}


def pytest_counts(text: str) -> dict:
    """The counts on pytest's last summary line, or {} when there is none."""
    summaries = [line for line in text.splitlines() if SUMMARY_RE.search(line)]
    if not summaries:
        return {}
    counts = {}
    for number, word in COUNT_RE.findall(summaries[-1]):
        counts["errors" if word.startswith("error") else word] = int(number)
    return counts


def failing_files(text: str) -> list:
    """[{file, count, first}] from pytest's -rfE lines, most failures first."""
    by_file: dict = {}
    for line in text.splitlines():
        match = FAILING_RE.match(line.strip())
        if not match:
            continue
        entry = by_file.setdefault(match.group(2), {"file": match.group(2), "count": 0, "first": ""})
        entry["count"] += 1
        entry["first"] = entry["first"] or (match.group(3) or "")[:200]
    return sorted(by_file.values(), key=lambda e: (-e["count"], e["file"]))


class _FailureReader:
    """Reads pytest's FAILURES and ERRORS sections into [{test, where, lines}].

    The checker runs pytest with --tb=line. That prints each failure as its "E" line,
    further message lines indented four spaces, any captured output, and then the
    location line, "path:line: <the error's first line>"; a setup error keeps its
    "___ ERROR at setup ___" header. In the long format each test has a header and its
    last frame location is the one that raised. Captured output is never read as part
    of an error. After it, a failure ends at the location that repeats the error's first
    line, or failing that at a location in a file the summary lists, so one unusual
    message can never hide every failure after it.
    """

    def __init__(self, files: "list | None" = None) -> None:
        self.entries: list = []
        self._files = files or []
        self._open: "dict | None" = None
        self._head = ""
        self._inside = self._captured = self._message = False

    def feed(self, line: str) -> None:
        section = SECTION_RE.match(line)
        if section:
            self.close()
            self._inside = section.group(1) in ("FAILURES", "ERRORS")
        elif self._inside:
            self._feed_inside(line)

    def close(self) -> None:
        self._open, self._head = None, ""
        self._captured = self._message = False

    def _start(self, test: str) -> dict:
        self.close()
        self._open = {"test": test, "where": "", "lines": []}
        self.entries.append(self._open)
        return self._open

    def _feed_inside(self, line: str) -> None:
        header, location = TEST_HEADER_RE.match(line), LOCATION_RE.match(line)
        if header:
            self._start(header.group(1))
        elif location:
            self._on_location(f"{location.group(1)}:{location.group(2)}", (location.group(3) or "").strip())
        elif CAPTURED_RE.match(line):
            self._captured, self._message = True, False
        elif not self._captured:
            self._feed_message(line)

    def _on_location(self, where: str, message: str) -> None:
        if self._captured:
            self._end_captured(where, message)
        elif self._open is None:
            if message:  # --tb=line with no "E" line: the location line carries the error
                entry = self._start("")
                entry["where"] = where
                self._add(entry, message)
                self.close()
        elif self._open["test"]:
            self._open["where"] = where  # long format: the last frame is the one that raised
        else:
            self._open["where"] = where  # --tb=line: the location line ends the failure
            self.close()

    def _end_captured(self, where: str, message: str) -> None:
        """After captured output, --tb=line's own location repeats the error's first line;
        a location in a file the summary lists is the fallback. Any other location there
        is the test's output."""
        entry = self._open
        if entry is None:
            self.close()
        elif not entry["test"] and (message == self._head or _summary_name(where, self._files)):
            entry["where"] = where
            self.close()

    def _feed_message(self, line: str) -> None:
        error = ERROR_LINE_RE.match(line)
        if error:
            text = error.group(1) or ""
            entry = self._open if self._open is not None else self._start("")
            if not self._message:
                # A new block starts after any non-error line, such as the blank line and
                # "The above exception ..." between chained errors, so _head ends up as the
                # last error's first line, the one --tb=line's location repeats.
                self._head = text
            self._add(entry, text)
            self._message = True
        elif self._message and line.startswith("    "):
            self._add(self._open, line.strip())
        else:
            self._message = False

    @staticmethod
    def _add(entry: dict, text: str) -> None:
        lines = entry["lines"]
        if len(lines) < ERROR_LINES_PER_TEST:
            cut = ERROR_LINE_CHARS - len(CUT_MARK)
            lines.append(text if len(text) <= ERROR_LINE_CHARS else text[:cut] + CUT_MARK)
        elif len(lines) == ERROR_LINES_PER_TEST:
            lines.append(MORE_MARK)  # more lines followed: say so rather than drop them silently


def _summary_name(where: str, files: list) -> str:
    """The name the summary gives the file in 'path:line', or "" when it lists none.
    The longest match wins, so "tests/test_x.py" beats a root-level "test_x.py"."""
    path = where.rpartition(":")[0].replace("\\", "/")
    matches = [name for name in files if path == name or path.endswith("/" + name)]
    return max(matches, key=len, default="")


def _short_where(where: str, files: list) -> str:
    """'path:line' with the path as the summary names that file, else the file name."""
    if ":" not in where:
        return where
    path, _, line = where.rpartition(":")
    name = _summary_name(where, files) or path.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{name}:{line}"


def failure_lines(text: str) -> list:
    """[{test, where, lines}] for each failure and error pytest reported, in order.

    ``lines`` holds the error's own lines, at most ERROR_LINES_PER_TEST and then MORE_MARK
    when more followed, each at most ERROR_LINE_CHARS and ending in CUT_MARK when cut;
    ``where`` is the file and line that
    raised, the file named as the summary names it; ``test`` is the header pytest
    printed, or "" where --tb=line prints none.
    """
    files = [entry["file"] for entry in failing_files(text)]
    reader = _FailureReader(files)
    for raw in text.splitlines():
        reader.feed(raw.rstrip())
    for entry in reader.entries:
        entry["where"] = _short_where(entry["where"], files)
    return reader.entries


# ── the verdict ─────────────────────────────────────────────────────────────

def judge(report: Report, commands: list, output: str) -> Report:
    codes, parts = exit_codes(output), segments(output)
    report.heads = heads(output)
    if codes.get(0):
        report.reasons.append("the checker's own setup failed before any README line ran")
    for command in commands:
        report.commands.append(_command_row(command, codes, parts, report.reasons))
    tested = "\n".join(parts.get(c.index, "") for c in commands if c.label == "test")
    _judge_tests(report, commands, tested)
    if TIMEOUT_MARK in output:
        report.reasons.append("the run timed out")
    report.verdict = "RED" if report.reasons else "GREEN"
    return report


def _command_row(command: Command, codes: dict, parts: dict, reasons: list) -> dict:
    row = {"index": command.index, "label": command.label, "source": command.source,
           "text": command.text, "rc": codes.get(command.index)}
    if row["rc"] is None:
        reasons.append(f"line {command.index} never ran")
    elif row["rc"] != 0:
        reasons.append(f"line {command.index} ({command.label}) exited {row['rc']}: {command.text}")
    elif command.expect and not re.search(command.expect, parts.get(command.index, "")):
        reasons.append(f"line {command.index} printed nothing matching {command.expect!r}")
    return row


def _judge_tests(report: Report, commands: list, tested: str) -> None:
    if not any(c.label == "test" and c.text and "pytest" in c.text for c in commands):
        return
    report.tests = pytest_counts(tested)
    report.failing_files = failing_files(tested)
    report.failures = failure_lines(tested)
    if not report.tests:
        report.reasons.append("no pytest summary line was printed")
    elif report.tests.get("failed") or report.tests.get("errors"):
        report.reasons.append(f"pytest: {report.tests.get('failed', 0)} failed, {report.tests.get('errors', 0)} errors")


def render(report: Report, all_files: bool = False) -> str:
    out = [f"stranger check: {report.target} ({report.origin}, where={report.where})"]
    out += [f"  clone {name} at {sha}" for name, sha in sorted(report.heads.items())]
    for row in report.commands:
        state = "not run" if row["rc"] is None else ("ok" if row["rc"] == 0 else f"exit {row['rc']}")
        out.append(f"  [{row['source']:7}] {row['label']:8} {state:8} {row['text']}")
    if report.tests:
        out.append("  tests: " + ", ".join(f"{n} {w}" for w, n in sorted(report.tests.items())))
    shown = report.failing_files if all_files else report.failing_files[:REPORT_ROWS]
    if shown:
        out.append(f"  failing files ({len(report.failing_files)}):")
        out += [f"    {e['count']:4} {e['file']} :: {e['first']}" for e in shown]
    errors = report.failures if all_files else report.failures[:REPORT_ROWS]
    if errors:
        out.append(f"  first errors ({len(errors)} of {len(report.failures)} tests):")
        for entry in errors:
            out.append("    " + (" ".join(x for x in (entry["where"], entry["test"]) if x) or "?"))
            out += [f"      {line}" for line in entry["lines"]]
    out += [f"  why red: {reason}" for reason in report.reasons]
    out.append(f"VERDICT: {report.verdict}")
    return "\n".join(out)


# ── running it ──────────────────────────────────────────────────────────────

def stranger_env(home: str) -> dict:
    """This machine's environment minus anything secret, with a throwaway HOME.

    The rest is kept because Windows needs SYSTEMROOT and its friends to start
    Python at all. git reads an empty global config and no system config, so no
    credential helper reaches the clone.
    """
    env = {key: value for key, value in os.environ.items() if not SECRET_ENV_RE.search(key)}
    gitconfig = Path(home) / ".stranger-gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    env.update(HOME=home.replace("\\", "/"), TMPDIR=home.replace("\\", "/"), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(gitconfig).replace("\\", "/"))
    return env


def bash_for(platform: str, which=None) -> str:
    """The bash that runs a README script on this machine.

    Anywhere but Windows that is plain ``bash``. On Windows, CreateProcess searches
    System32 before PATH, and ``System32\\bash.exe`` is the WSL launcher: on a runner
    with no Linux distribution it runs no README line at all (GRIP#9965). So use the
    ``bash.exe`` Git for Windows ships beside the ``git`` on PATH, or say plainly
    that there is none.

    A ``bash`` on PATH outside System32 is taken first. Otherwise the search covers
    up to three folders above ``git.exe``, because which ``git.exe`` PATH finds
    depends on the shell: inside Git Bash, as on a GitHub runner, it is
    ``Git\\mingw64\\bin\\git.exe``, whose grandparent ``mingw64`` holds no bash
    (GRIP#9971).
    """
    if platform != "windows":
        return "bash"
    import shutil
    from itertools import product
    find = which or shutil.which
    on_path = find("bash")
    if on_path and "system32" not in on_path.lower():
        return on_path
    git = find("git")
    roots = list(Path(git).resolve().parents)[:3] if git else []
    candidates = (root / sub / "bash.exe" for root, sub in product(roots, ("bin", "usr/bin")))
    found = next((str(c) for c in candidates if c.is_file()), None)
    if found:
        return found
    raise StrangerError("no Git Bash found: install Git for Windows, whose bash.exe sits beside "
                        "git.exe; the WSL bash.exe in System32 runs no README line")


def run_here(script: str, timeout: int) -> tuple:
    """Run in a throwaway HOME, with no secrets and no git credentials."""
    with tempfile.TemporaryDirectory(prefix="stranger-") as home:
        path = Path(home) / ".stranger.sh"
        path.write_text(script, encoding="utf-8")
        return _run([bash_for(_platform()), str(path).replace("\\", "/")], None,
                    stranger_env(home), timeout, home)


def run_vps(script: str, timeout: int) -> tuple:
    host = os.environ.get(SSH_ENV, "").strip()
    if not host:
        raise StrangerError(f"--where vps needs {SSH_ENV}=<user>@<host>, an ssh target that can run docker")
    remote = f"sudo docker run --rm -i {CONTAINER_IMAGE} bash -s"
    return _run(["ssh", "-o", "BatchMode=yes", host, remote], container_script(script), None, timeout, None)


def _run(argv: list, stdin: "str | None", env: "dict | None", timeout: int, cwd: "str | None") -> tuple:
    """(everything printed, exit code); a timeout keeps what was printed and marks it."""
    try:
        proc = subprocess.run(argv, input=stdin, env=env, cwd=cwd, timeout=timeout, text=True,
                              encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return f"{partial}\n{TIMEOUT_MARK}\n", None
    return proc.stdout, proc.returncode


def require_checker_output(output: str, code: "int | None") -> None:
    """A run that printed no marker never reached the README: the machine failed, not it."""
    if MARK in output or TIMEOUT_MARK in output:
        return
    raise StrangerError(f"the run printed no checker output (exit {code}), so the machine, ssh or docker "
                        f"failed before any README line ran: {output.strip()[-300:]!r}")


# ── where the README comes from ─────────────────────────────────────────────

def fetch_readme(target: Target) -> str:
    """The README as GitHub serves it; a network failure is the machine's, never the README's."""
    try:
        with urllib.request.urlopen(target.readme_url, timeout=30) as response:
            return response.read().decode("utf-8")
    except OSError as exc:
        raise StrangerError(f"could not fetch {target.readme_url}: {exc}") from exc


def tarball_readme(path: str) -> tuple:
    """(README text, strip the single top folder?) from a build tarball."""
    with tarfile.open(path) as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        strip = len(tops) == 1 and not any(n == "README.md" or n == "./README.md" for n in names)
        wanted = f"{tops.pop()}/README.md" if strip else next((n for n in names if n.lstrip("./") == "README.md"), "")
        member = tar.extractfile(wanted) if wanted else None
        if member is None:
            raise StrangerError(f"{path} holds no README.md")
        return io.TextIOWrapper(member, encoding="utf-8").read(), strip


# ── command line ────────────────────────────────────────────────────────────

def _platform() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def _parse(argv: "list | None") -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="grip-stranger-check", description=__doc__.split("\n\n")[0])
    parser.add_argument("target", nargs="?", choices=sorted(TARGETS))
    parser.add_argument("--list", action="store_true", help="list the targets and stop")
    parser.add_argument("--where", choices=("here", "vps"), default="here")
    parser.add_argument("--tarball", default="", help="check a local build tarball instead of GitHub")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--files", action="store_true", help="list every failing file, not the top 12")
    parser.add_argument("--print-script", action="store_true", help="print the script and stop")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    return parser.parse_args(argv)


def _list() -> int:
    for target in TARGETS.values():
        print(f"{target.name:6} {target.repo}  platforms: {', '.join(target.platforms)}")
        if target.note:
            print(f"       {target.note}")
    return 0


def check(args: argparse.Namespace) -> Report:
    target = TARGETS[args.target]
    if args.where == "here" and _platform() not in target.platforms:
        raise StrangerError(f"{target.name} does not support {_platform()}: {target.note}")
    if args.tarball and args.where != "here":
        raise StrangerError("--tarball runs with --where here; the VPS run clones from GitHub")
    readme, strip = tarball_readme(args.tarball) if args.tarball else (fetch_readme(target), False)
    commands = resolve_commands(target, readme)
    tarball = str(Path(args.tarball).resolve()).replace("\\", "/") if args.tarball else ""
    script = build_script(target, commands, tarball, strip)
    if args.print_script:
        print(container_script(script) if args.where == "vps" else script)
        raise SystemExit(0)
    output, code = (run_vps if args.where == "vps" else run_here)(script, args.timeout)
    require_checker_output(output, code)
    origin = f"tarball {args.tarball}" if args.tarball else "github"
    return judge(Report(target.name, args.where, origin), commands, output)


def main(argv: "list | None" = None) -> int:
    args = _parse(argv)
    if args.list or not args.target:
        return _list()
    try:
        report = check(args)
    except StrangerError as exc:
        print(f"stranger check could not run: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(report), indent=2) if args.json else render(report, args.files))
    return 0 if report.verdict == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
