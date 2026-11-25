"""A dictionary for GRIP's ASD-STE100 linter that ships none of ASD's words.

GRIP is not certified or endorsed by ASD. ASD-STE100 is a trademark of ASD.
This tool ships no ASD text; the local lexicon is built from the operator's
own copy of the standard. AI-assisted text; human review applies.

WHY THE REPOSITORY HOLDS NO WORD LIST. ASD-STE100 Issue 9 (15 January 2025)
has about 900 approved and about 1,200 non-approved words, and the
specification is ASD's copyright. Its reproduction clause gives free
reproduction to eight named categories of organisation, and an open tool is
not one of them. So the dictionary comes in two parts:

- The seed, config/ste/grip-seed.json, is GRIP's own list. It holds seven
  plain-English word swaps from a public overview sheet (commence -> START
  and the like), the inflected forms GRIP's linter already carried, and
  GRIP's own technical names and technical verbs.
- The local lexicon is built by this module from a text export of the
  operator's own copy of the standard. It lives at
  <claude dir>/state/ste/lexicon.json, in a directory git ignores. Never
  commit that file or share it.

load_lexicon() always loads the seed and adds the local lexicon when there
is one. Lexicon.source says which: "seed" or "seed+local". With the seed
alone, a word the lexicon does not know is only a word the seed does not
hold, not a word the standard rejects.

BUILDING THE LOCAL LEXICON. Export your PDF as text yourself, keeping its
columns, and keep the export out of every repository:

    pdftotext -layout <your copy>.pdf <export>.txt
    bin/grip-py lib/ste_lexicon.py build <export>.txt

The builder reads layout, not meaning, so its counts are a claim to check,
not a result to trust. It takes a line as a dictionary entry when the
line's first column holds a word, or a phrase of up to three words,
followed by a part of speech in brackets: (n), (v), (adj), (adv), (prep),
(conj), (pron) or (art). Then:

- an entry in UPPER CASE is approved, with any forms that follow its part
  of speech after commas, as in "BLORP (v), BLORPS, BLORPED";
- an entry in lower case is not approved, and its approved alternative is
  the first run of UPPER-CASE words after it on the same line;
- a lower-case entry with no such run is counted as incomplete and not kept;
- a line that starts more than COLUMN_SLACK characters to the right of its
  page's first column is a wrapped cell, not an entry.

The build prints how many approved and non-approved entries it read.
Compare them with the standard's own totals, about 900 and about 1,200. A
large gap means the export's layout differs from what the parser expects.
The builder writes nothing when it finds no approved or no non-approved
entries. It does not fill in technical names or technical verbs: both lists
start empty, for you to add your own.

usage: bin/grip-py lib/ste_lexicon.py status | build TEXT_FILE [--issue N] | check WORD...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from claude_dir import get_claude_dir
except ImportError:  # imported as lib.ste_lexicon, with only the repository root on sys.path
    from lib.claude_dir import get_claude_dir

NOTICE = ("GRIP is not certified or endorsed by ASD. ASD-STE100 is a trademark of ASD. "
          "This tool ships no ASD text; the local lexicon is built from the operator's own "
          "copy of the standard. AI-assisted text; human review applies.")
LOCAL_NOTICE = ("This file was built from the operator's own copy of ASD-STE100. Keep it "
                "private: never commit it or share it.")

#: Under config/, which git tracks. The repository's data/ directory is ignored as
#: runtime data, so a seed kept there would never reach a clone or a CI run.
SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "ste" / "grip-seed.json"
LOCAL_PATH = get_claude_dir() / "state" / "ste" / "lexicon.json"

#: The word lists of a lexicon file. "unapproved" is the fourth, a word -> alternative map.
LISTS = ("approved", "technical_names", "technical_verbs")
ENTRY_COUNTS = ("approved_entries", "unapproved_entries", "incomplete_entries")
CHECK_LINE = ("read {approved_entries} approved and {unapproved_entries} non-approved entries "
              "({incomplete_entries} incomplete); check them against the standard's own totals, "
              "about 900 approved and about 1,200 non-approved words")

#: The parts of speech the parser recognises after a first-column word.
POS = r"(?:n|v|adj|adv|prep|conj|pron|art)"
ENTRY = re.compile(
    rf"(?P<indent>[ \t]*)(?P<word>[A-Za-z][A-Za-z'’-]*(?: [A-Za-z][A-Za-z'’-]*){{0,2}}) ?"
    rf"\({POS}(?:, ?{POS})*\)(?P<forms>(?:, ?[A-Z][A-Z'’-]*)*)(?P<rest>.*)")
UPPER_RUN = re.compile(r"\b[A-Z][A-Z'’-]*(?: [A-Z][A-Z'’-]*)*\b")
ISSUE = re.compile(r"\bissue\s+(\d{1,2})\b", re.IGNORECASE)
#: How far right of its page's first column an entry may start, for a ragged export.
COLUMN_SLACK = 3


@dataclass(frozen=True)
class Lexicon:
    """The words GRIP's linter can look up. Every key is in lower case.

    repr shows the source and the counts only: a lexicon built from the
    operator's copy must not reach a log or a test report through repr.
    """
    approved: frozenset[str] = field(repr=False)
    unapproved: dict[str, str] = field(repr=False)  # word -> approved alternative
    technical_names: frozenset[str] = field(repr=False)
    technical_verbs: frozenset[str] = field(repr=False)
    source: str  # "seed" | "seed+local"
    counts: dict[str, int]
    #: The counts of the LOCAL build alone, empty for the seed alone. The scorer's size
    #: gate reads these, so the seed's own entries never pad a build towards the floor.
    local_counts: dict[str, int] = field(default_factory=dict)

    def is_approved(self, word: str) -> bool:
        """True for an approved word, a technical name or a technical verb, in any case."""
        key = _key(word)
        return key in self.approved or key in self.technical_names or key in self.technical_verbs

    def alternative(self, word: str) -> str | None:
        """The approved word to use instead of ``word``, or None when it is not listed
        as unapproved. A word can be approved in one part of speech and unapproved in
        another; this lexicon does not tag parts of speech, so it reports both facts."""
        return self.unapproved.get(_key(word))


def _key(word: str) -> str:
    """A word as the lexicon stores it: lower case, straight apostrophes, single spaces."""
    return " ".join(word.replace("’", "'").lower().split())


def _is_word_list(words) -> bool:
    return isinstance(words, list) and all(isinstance(word, str) for word in words)


def _is_word_map(words) -> bool:
    return isinstance(words, dict) and all(
        isinstance(alternative, str) and alternative.strip() for alternative in words.values())


def _fields(data) -> tuple[dict, dict]:
    """The unapproved map and the word lists of a lexicon file, checked for shape.
    Raises ValueError when the data is not a lexicon."""
    if not isinstance(data, dict):
        raise ValueError("the file is not a JSON object")
    missing = [name for name in ("unapproved", *LISTS) if name not in data]
    if missing:
        raise ValueError(f"the file has no {missing[0]!r} key")
    if not _is_word_map(data["unapproved"]):
        raise ValueError("'unapproved' is not a map of word to approved alternative")
    if not all(_is_word_list(data[name]) for name in LISTS):
        raise ValueError("a word list is not a list of words")
    return data["unapproved"], {name: data[name] for name in LISTS}


def _parts(path: Path) -> dict:
    """The four lists of one lexicon file, with their keys normalised. Raises OSError
    when the file cannot be read and ValueError when it is not a lexicon."""
    unapproved, lists = _fields(json.loads(path.read_text(encoding="utf-8")))
    parts = {name: {_key(word) for word in words} - {""} for name, words in lists.items()}
    parts["unapproved"] = {_key(word): alternative.strip()
                           for word, alternative in unapproved.items() if _key(word)}
    return parts


def _merged(seed: dict, local: dict) -> dict:
    """Both lexicons together. Where both list an unapproved word, the local entry
    wins: it comes from the standard itself."""
    merged = {name: seed[name] | local[name] for name in LISTS}
    merged["unapproved"] = {**seed["unapproved"], **local["unapproved"]}
    return merged


def _counts(parts: dict) -> dict[str, int]:
    return {name: len(parts[name]) for name in ("approved", "unapproved", *LISTS[1:])}


def _lexicon(parts: dict, source: str, local: dict | None = None) -> Lexicon:
    return Lexicon(approved=frozenset(parts["approved"]), unapproved=dict(parts["unapproved"]),
                   technical_names=frozenset(parts["technical_names"]),
                   technical_verbs=frozenset(parts["technical_verbs"]), source=source, counts=_counts(parts),
                   local_counts=_counts(local) if local is not None else {})


def load_lexicon() -> Lexicon | None:
    """The seed, with the local lexicon added when one is present.

    Returns None, with one line on stderr, only when the seed itself is missing
    or malformed. A local lexicon that cannot be read is named on stderr and
    left out, and the result then says source "seed".
    """
    try:
        seed = _parts(SEED_PATH)
    except (OSError, ValueError) as exc:
        print(f"ste_lexicon: cannot load the seed {_shown(SEED_PATH)}: {exc}", file=sys.stderr)
        return None
    try:
        local = _parts(LOCAL_PATH)
    except FileNotFoundError:
        return _lexicon(seed, "seed")
    except (OSError, ValueError) as exc:
        print(f"ste_lexicon: leaving out the local lexicon {_shown(LOCAL_PATH)}: {exc}", file=sys.stderr)
        return _lexicon(seed, "seed")
    return _lexicon(_merged(seed, local), "seed+local", local)


def _entries(text: str):
    """Each first-column entry of the text, page by page. A line that starts more
    than COLUMN_SLACK characters right of its page's first column is a wrapped cell."""
    for page in text.split("\f"):
        found = [match for match in map(ENTRY.match, page.splitlines())
                 if match and (match["word"].isupper() or match["word"].islower())]
        if found:
            column = min(len(match["indent"]) for match in found) + COLUMN_SLACK
            yield from (match for match in found if len(match["indent"]) <= column)


def _read(text: str) -> tuple[set[str], dict[str, str], Counter]:
    """The approved words and their forms, the unapproved words with their
    alternatives, and a tally of the entries read."""
    approved, unapproved, tally = set(), {}, Counter()
    for entry in _entries(text):
        word = entry["word"]
        if word.isupper():
            approved.update(_key(form) for form in (word, *entry["forms"].split(",")) if form.strip())
            tally["approved_entries"] += 1
            continue
        alternative = UPPER_RUN.search(entry["rest"])
        if alternative:
            unapproved.setdefault(_key(word), alternative.group(0))
            tally["unapproved_entries"] += 1
        else:
            tally["incomplete_entries"] += 1
    return approved, unapproved, tally


def _issue_of(text: str) -> str:
    """The issue number the text names most often (its page headers), or "unknown"."""
    named = Counter(ISSUE.findall(text)).most_common(1)
    return named[0][0] if named else "unknown"


def _write(target: Path, record: dict) -> None:
    """Replace the file in one step, so a failed write leaves the last lexicon in place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.partial")
    partial.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(partial, target)


def build_local(pdf_text_path: Path, out: Path | None = None, issue: str | None = None) -> dict:
    """Build the local lexicon from a text export of the operator's own copy.

    ``pdf_text_path`` is what ``pdftotext -layout`` wrote for the operator's PDF.
    The record is written to ``out`` (by default LOCAL_PATH, read when this is
    called) and returned. ``issue`` defaults to the issue the text names most
    often. Raises OSError when the text cannot be read or the record cannot be
    written, and ValueError, writing nothing, when the text holds no approved
    or no non-approved entries.

    The parser reads layout (see the module docstring), so check the entry
    counts against the standard's own totals: about 900 approved and about
    1,200 non-approved words. Never commit any output of this function.
    """
    raw = Path(pdf_text_path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    approved, unapproved, tally = _read(text)
    if not (approved and unapproved):
        raise ValueError(
            f"found {tally['approved_entries']} approved and {tally['unapproved_entries']} "
            "non-approved entries, and a dictionary has both, so nothing was written; export "
            "the PDF with `pdftotext -layout`, which keeps each dictionary row on one line")
    record = {
        "_notice": f"{NOTICE} {LOCAL_NOTICE}",
        "issue": issue or _issue_of(text),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "counts": {"approved": len(approved), "unapproved": len(unapproved),
                   "technical_names": 0, "technical_verbs": 0,
                   **{name: tally[name] for name in ENTRY_COUNTS}},
        "approved": sorted(approved),
        "unapproved": dict(sorted(unapproved.items())),
        "technical_names": [],
        "technical_verbs": [],
    }
    _write(LOCAL_PATH if out is None else Path(out), record)
    return record


def _shown(path: Path) -> str:
    """``path`` with the home directory written as ~, so pasted output names no user."""
    home, text = os.path.expanduser("~"), str(path)
    inside = home not in ("", "~", os.sep) and text.startswith(home + os.sep)
    return "~" + text[len(home):] if inside else text


def _say(*lines: str) -> None:
    """Print to the operator. Every message ends with the notice."""
    print("\n".join([*lines, NOTICE]))


def _local_lines() -> list[str]:
    """What the status says about the local lexicon: where it is and what its build
    recorded. Counts only, never words. A file that load_lexicon() leaves out is
    reported as unreadable here too, for the same reason, so the two agree."""
    try:
        record = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
        _fields(record)
    except FileNotFoundError:
        return [f"local: none at {_shown(LOCAL_PATH)}; "
                "make one with: bin/grip-py lib/ste_lexicon.py build TEXT_FILE"]
    except (OSError, ValueError) as exc:
        return [f"local: unreadable at {_shown(LOCAL_PATH)}: {exc}"]
    lines = [f"local: {_shown(LOCAL_PATH)}, issue {record.get('issue', 'unknown')}, "
             f"built {record.get('built_at', 'unknown')}"]
    counts = record.get("counts")
    if isinstance(counts, dict) and set(ENTRY_COUNTS) <= counts.keys():
        lines.append("its build " + CHECK_LINE.format(**counts))
    return lines


def _status() -> int:
    lexicon = load_lexicon()
    if lexicon is None:
        _say("source: none -- the seed is missing or malformed")
        return 1
    _say(f"source: {lexicon.source}",
         "approved {approved}, unapproved {unapproved}, technical names {technical_names}, "
         "technical verbs {technical_verbs}".format(**lexicon.counts),
         f"seed: {_shown(SEED_PATH)}", *_local_lines())
    return 0


def _build(text_file: Path, issue: str | None) -> int:
    try:
        record = build_local(text_file, issue=issue)
    except (OSError, ValueError) as exc:
        print(f"ste_lexicon: {exc}", file=sys.stderr)
        return 1
    counts = record["counts"]
    _say(f"wrote {_shown(LOCAL_PATH)}: issue {record['issue']}, "
         f"from a text file with sha256 {record['source_sha256'][:16]}",
         "approved {approved}, unapproved {unapproved}".format(**counts),
         CHECK_LINE.format(**counts))
    return 0


def _verdict(lexicon: Lexicon, word: str) -> str:
    """Every list ``word`` is on, in one line, or "unknown"."""
    key = _key(word)
    found = [label for label, words in (("technical name", lexicon.technical_names),
                                         ("technical verb", lexicon.technical_verbs),
                                         ("approved", lexicon.approved)) if key in words]
    if key in lexicon.unapproved:
        found.append(f"unapproved -> {lexicon.unapproved[key]}")
    return ", ".join(found) or "unknown"


def _check(word: str) -> int:
    lexicon = load_lexicon()
    if lexicon is None:
        _say(f"{word}: no lexicon -- the seed is missing or malformed")
        return 1
    _say(f"{word}: {_verdict(lexicon, word)} (lexicon: {lexicon.source})")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/grip-py lib/ste_lexicon.py", epilog=NOTICE,
        description="The ASD-STE100 lexicon for GRIP's linter: a public seed, and a local "
                    "lexicon built from your own copy of the standard.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="say which lexicon loads and its counts, never its words")
    build = commands.add_parser("build", help="build the local lexicon from a pdftotext -layout export")
    build.add_argument("text_file", type=Path)
    build.add_argument("--issue", help="the issue of the standard (default: the one the text names most)")
    check = commands.add_parser("check", help="look up a word or a phrase")
    check.add_argument("words", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Exit 0 on success, 1 when the lexicon cannot load or a build fails, 2 on a usage error."""
    args = _parser().parse_args(argv)
    if args.command == "build":
        return _build(args.text_file, args.issue)
    if args.command == "check":
        return _check(" ".join(args.words))
    return _status()


if __name__ == "__main__":
    sys.exit(main())
