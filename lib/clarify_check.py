#!/usr/bin/env python3
"""lib/clarify_check.py: the /clarify invariants that a program can check.

/clarify rewrites a passage for the person who reads it or acts on it
(skills/clarify/SKILL.md). Until this module, two of its invariants were rules
for the writer only:

- Nothing added. Each number, code span, path, issue id and URL in the output
  is also in the passage. An added token is how a rewrite invents a fact, so it
  is an error. A line in a fenced block must also be in the passage.
- Receipts kept. An OBSERVED claim names an anchor that exists: a path on disk,
  down to its line when a line is given, or a URL that answers when you ask for
  the URLs to be fetched. An UNVERIFIED claim carries, in backticks, the command
  that would check it.

It also warns about each token of the passage that the output drops and never
names, for example on a "Left out:" line.

With --keep-all, as the simple preset of /clarify requires, a dropped token is an
error, and a token that the output names only on a "Left out:" line still counts
as dropped. That preset keeps every fact, so nothing may be left out.

What it cannot check: a promise, condition or step made only of ordinary words,
and whether a claim is true. Those stay with the two readers in the skill.

Usage: grip-py lib/clarify_check.py PASSAGE OUTPUT [--root DIR] [--fetch-urls] [--keep-all] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ste_lint import Finding  # noqa: E402

CODE_SPAN = re.compile(r"`([^`\n]+)`")
URL = re.compile(r"https?://[^\s)>\]`'\"]+")
ISSUE = re.compile(r"(?<![\w#])(?:[A-Za-z][\w.-]*)?#\d+\b")
PATH = re.compile(r"(?<![\w/.~-])(?:~/|\./|/)?(?:[\w.-]+/)+[\w-]+\.[A-Za-z]\w*(?::\d+)?")
FILE_SPAN = re.compile(r"(?:~/|\./|/)?(?:[\w.-]+/)*[\w-]+\.[A-Za-z]\w*(?::\d+)?")
BARE_FILE_LINE = re.compile(r"(?<![\w/.~-])[\w-]{2,}\.[A-Za-z]\w*:\d+\b")
NUMBER = re.compile(r"(?<![\w.#/-])\d[\d.,:/%-]*")
LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
COUNT_LINE = re.compile(r"^\s*\d+ claims?:", re.I)
STATUS_LINE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\**(OBSERVED|INFERRED|UNVERIFIED|REFUTED)\b")
LEFT_OUT = re.compile(r"^\W*Left out\W*:", re.I)
TRAILING = ".,;:-/"


def _prose_tokens(line: str) -> list[tuple[str, str]]:
    """(kind, token) for one line of prose, each token taken out before the next kind."""
    body = LIST_MARKER.sub("", line, count=1)
    spans = CODE_SPAN.findall(body)
    rest = CODE_SPAN.sub(" ", body)
    urls = [url.rstrip(TRAILING) for url in URL.findall(rest)]
    rest = URL.sub(" ", rest)
    issues = ISSUE.findall(rest)
    rest = ISSUE.sub(" ", rest)
    paths = PATH.findall(rest)
    numbers = [number.rstrip(TRAILING) for number in NUMBER.findall(PATH.sub(" ", rest))]
    tagged = [*(("code", s) for s in spans), *(("url", u) for u in urls), *(("issue", i) for i in issues),
              *(("path", p) for p in paths), *(("number", n) for n in numbers)]
    return [(kind, token) for kind, token in tagged if token.strip()]


def tokens_of(text: str) -> list[tuple[int, str, str]]:
    """(line, kind, token) for every checkable token; a fenced line is one code token."""
    found: list[tuple[int, str, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif in_fence and line.strip():
            found.append((number, "code", line.strip()))
        elif not in_fence and not COUNT_LINE.match(line):
            found.extend((number, kind, token) for kind, token in _prose_tokens(line))
    return found


def _present(token: str, kind: str, text: str) -> bool:
    if kind == "number":
        return re.search(rf"(?<![\d.]){re.escape(token)}(?!\d)", text) is not None
    return token in text


def _added(passage: str, output: str) -> list[Finding]:
    return [Finding("added", line, "error", f"'{token}' ({kind}) is not in the passage")
            for line, kind, token in tokens_of(output) if not _present(token, kind, passage)]


def _kept_text(output: str, keep_all: bool) -> str:
    """The part of the output that can keep a token: all of it, or all but its "Left out:" lines."""
    if not keep_all:
        return output
    return "\n".join(line for line in output.splitlines() if not LEFT_OUT.match(line))


def _dropped(passage: str, output: str, keep_all: bool = False) -> list[Finding]:
    """One finding naming the passage's tokens that the output never mentions.

    One finding, not one per token: a one-line preset drops most tokens on
    purpose, and thirty separate warnings would bury the one that matters.
    With ``keep_all`` the finding is an error, because the simple preset keeps
    every token and a "Left out:" line does not excuse a drop.
    """
    kept = _kept_text(output, keep_all)
    first_seen = {(kind, token): line for line, kind, token in reversed(tokens_of(passage))}
    gone = [(line, token) for (kind, token), line in sorted(first_seen.items(), key=lambda item: item[1])
            if not _present(token, kind, kept)]
    if not gone:
        return []
    shown = ", ".join(f"'{token}' (passage line {line})" for line, token in gone[:10])
    more = f", and {len(gone) - 10} more" if len(gone) > 10 else ""
    if keep_all:
        severity = "error"
        advice = "The simple preset keeps each one exactly as written, and a 'Left out:' line does not count"
    else:
        severity = "warning"
        advice = "Name each one you left out on purpose on a 'Left out:' line"
    return [Finding("dropped", gone[0][0], severity,
                    f"{len(gone)} tokens of the passage are not in the output: {shown}{more}. {advice}")]


def _path_holds(anchor: str, root: Path) -> bool:
    """True when the anchor's file exists and, given a line, has at least that many lines."""
    name, _, line = anchor.partition(":")
    path = Path(name).expanduser()
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return path.is_dir() and not line
    if not line:
        return True
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle) >= int(line)


def _url_answers(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "grip-clarify-check"})
    try:
        with urllib.request.urlopen(request, timeout=8):
            return True
    except urllib.error.HTTPError as error:
        return error.code not in (404, 410)
    except (OSError, ValueError):
        return False


def _anchors(line: str) -> tuple[list[str], list[str]]:
    """The file anchors and URLs named on one receipt line.

    A file anchor is a path with a directory, a code span that is a file name, or
    a bare ``name.ext:line``. A bare name with no line, such as "Node.js", is not
    taken as a file, because ordinary words contain dots too.
    """
    urls = [url.rstrip(TRAILING) for url in URL.findall(line)]
    bare = URL.sub(" ", line)
    spans = [span for span in CODE_SPAN.findall(bare) if FILE_SPAN.fullmatch(span)]
    files = {*PATH.findall(bare), *BARE_FILE_LINE.findall(bare), *spans}
    return sorted(files), urls


def _url_findings(line: int, urls: list[str], fetch: bool) -> list[Finding]:
    if not fetch:
        return [Finding("anchor", line, "note", f"URL not fetched: {url} (add --fetch-urls to check it answers)")
                for url in urls]
    return [Finding("anchor", line, "error", f"URL does not answer: {url}") for url in urls if not _url_answers(url)]


def _observed(line: int, text: str, root: Path, fetch: bool) -> list[Finding]:
    files, urls = _anchors(text)
    if not files and not urls and not CODE_SPAN.search(text):
        return [Finding("receipt", line, "error", "an OBSERVED claim names no anchor")]
    missing = [Finding("anchor", line, "error", f"'{anchor}' is not on disk, or has fewer lines than that")
               for anchor in files if not _path_holds(anchor, root)]
    return missing + _url_findings(line, urls, fetch)


def _receipts(output: str, root: Path, fetch: bool) -> list[Finding]:
    findings: list[Finding] = []
    for number, text in enumerate(output.splitlines(), 1):
        status = STATUS_LINE.match(text)
        if status and status.group(1) == "OBSERVED":
            findings.extend(_observed(number, text, root, fetch))
        elif status and status.group(1) == "UNVERIFIED" and not CODE_SPAN.search(text):
            findings.append(Finding("receipt", number, "error",
                                    "an UNVERIFIED claim carries no command to check it; put one in backticks"))
    return findings


def check(passage: str, output: str, root: Path, fetch_urls: bool = False,
          keep_all: bool = False) -> list[Finding]:
    """Every finding for ``output`` as a rewrite of ``passage``; anchors resolve against ``root``.

    ``keep_all`` is the simple preset's rule: each dropped token is an error.
    """
    return [*_added(passage, output), *_dropped(passage, output, keep_all), *_receipts(output, root, fetch_urls)]


def summary_of(findings: list[Finding]) -> dict:
    count = {severity: sum(f.severity == severity for f in findings) for severity in ("error", "warning", "note")}
    return {"errors": count["error"], "warnings": count["warning"], "notes": count["note"],
            "findings": [asdict(finding) for finding in findings]}


def main(argv: list[str] | None = None) -> int:
    """Exit 0 with no errors, 1 with errors, 2 when an input cannot be read."""
    parser = argparse.ArgumentParser(description="Check a /clarify output against its passage.")
    parser.add_argument("passage")
    parser.add_argument("output")
    parser.add_argument("--root", default=".", help="directory that anchor paths are relative to")
    parser.add_argument("--fetch-urls", action="store_true", help="check that each OBSERVED URL answers")
    parser.add_argument("--keep-all", action="store_true",
                        help="the simple preset: each token of the passage must be in the output, or it is an error")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        passage, output = (Path(p).read_text(encoding="utf-8") for p in (args.passage, args.output))
    except OSError as exc:
        print(f"clarify_check: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 2
    findings = check(passage, output, Path(args.root), args.fetch_urls, args.keep_all)
    summary = summary_of(findings)
    rows = [f"line {f.line}  {f.rule}  {f.detail}" for f in findings]
    tally = f"{summary['errors']} errors, {summary['warnings']} warnings, {summary['notes']} notes"
    print(json.dumps(summary, indent=1) if args.json else "\n".join([*rows, tally]))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
