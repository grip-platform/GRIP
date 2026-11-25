#!/usr/bin/env python3
"""Classify what is blocking the public decision chain from publishing.

``bin/grip-chain-sync`` fails CLOSED when its leak gate finds a name-shaped
phrase in a commit subject that sits on neither the redaction denylist nor the
allowlist. That is the shield working correctly -- its pattern matcher flags any
run of Capitalised Words, so GRIP's own Title-Case commit subjects trip it -- and
the remedy is an allowlist entry, per the shield's own doctrine: *"it redacts
allowlisted public brands (over-block -- fix the allowlist, not the shield)."*

Deciding phrase-by-phrase is a recurring chore, because every new commit can add
one. This turns the gate's raw refusal list into a decision sheet ordered by
RISK, so the few phrases that could be a real person come first and the bulk that
are ordinary English come last.

Two mechanical signals, neither a verdict:

  * **dictionary** -- is every word of the phrase a dictionary word? A surname
    carries a token no dictionary holds. This decides the ordering.
  * **repo presence** -- how many tracked GRIP files contain the exact phrase? A
    non-dictionary word GRIP writes about constantly is a coined term, not a
    person.

Requiring both signals to AGREE was tried and rejected: on the 49 phrases
measured 2026-09-22 it left 29 undecided, which is more work for the reader, not
less. The dictionary decides; repo presence splits the remainder.

Output goes OUTSIDE the repository by construction -- the phrases may include a
real identity, which is the class ``rules/no-sensitive-info-in-grip.md`` forbids
committing.

Usage:
    grip-py lib/chain_blockers.py                 # run the gate, sheet to stdout
    grip-py lib/chain_blockers.py --out <path>    # write the sheet to a file
    grip-py lib/chain_blockers.py --report <file> # classify a captured report
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
DICT_PATH = Path("/usr/share/dict/words")

# A phrase in >= this many tracked files is something GRIP writes about, not a
# passing reference. Chosen so a doctrine or module name clears it while a name
# mentioned once in a commit does not.
VOCAB_FLOOR = 5

_BLOCKER = re.compile(r"blocker:(?P<cat>[a-z]+):(?P<phrase>.+?)\s*$")


class RepoOutputRefused(RuntimeError):
    """Raised when the sheet would be written inside a git repository."""


# ---------------------------------------------------------------------------
# Input -- the gate's own refusal list is the only source of truth
# ---------------------------------------------------------------------------


def run_leak_gate(claude_dir: Path = CLAUDE_DIR) -> str:
    """Run the emitter's check-leak and return its report text.

    Read-only: check-leak inspects the ledger and exits non-zero when it finds a
    blocker. A non-zero exit is the normal case here, so it is not an error.
    """
    python = claude_dir / "venv" / "bin" / "python"
    if not python.exists():                       # Windows venv layout
        python = claude_dir / "venv" / "Scripts" / "python.exe"
    res = subprocess.run(
        [str(python), "-m", "lib.idr_public_emitter", "check-leak"],
        cwd=str(claude_dir),
        env={"PYTHONPATH": f"{claude_dir}:{claude_dir / 'lib'}", "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=300,
    )
    return res.stdout + res.stderr


def parse_report(text: str) -> Counter:
    """phrase -> how many times the gate refused it."""
    counts: Counter = Counter()
    for line in text.splitlines():
        match = _BLOCKER.search(line)
        if match:
            counts[match.group("phrase").strip()] += 1
    return counts


# ---------------------------------------------------------------------------
# The two signals
# ---------------------------------------------------------------------------


def load_dictionary(path: Path = DICT_PATH) -> set:
    """The system word list, lowercased. Empty set where the platform has none."""
    if not path.exists():
        return set()
    return {w.strip().lower() for w in path.read_text(errors="ignore").splitlines() if w.strip()}


def is_ordinary_english(phrase: str, words: set) -> bool:
    """True when every alphabetic token of the phrase is a dictionary word.

    With no dictionary available this returns False for everything, which sends
    every phrase to the human-review buckets -- the safe direction, since the
    failure mode of a missing dictionary must never be to auto-allowlist.
    """
    if not words:
        return False
    tokens = re.findall(r"[A-Za-z']+", phrase)
    return bool(tokens) and all(t.lower() in words for t in tokens)


def tracked_files_mentioning(phrase: str, repo: Path) -> int:
    """Tracked files whose contents contain the phrase, case-insensitively.

    Returns -1 when git cannot answer, which ``bucket_of`` treats as "not in the
    repo" -- again the cautious direction.
    """
    try:
        res = subprocess.run(
            ["git", "grep", "-Fil", "--", phrase],
            cwd=str(repo), capture_output=True, text=True, timeout=30,
        )
        return len([ln for ln in res.stdout.splitlines() if ln.strip()])
    except Exception:
        return -1


def bucket_of(files: int, english: bool) -> str:
    """Which review bucket a phrase belongs in. Ordered by risk, not by signal."""
    if english:
        return "english"
    return "coined" if files >= VOCAB_FLOOR else "identity"


def classify(counts: Counter, repo: Path, words: set | None = None) -> dict:
    """Group phrases into risk buckets, commonest first within each."""
    words = load_dictionary() if words is None else words
    buckets: dict = defaultdict(list)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    for phrase, hits in ordered:
        files = tracked_files_mentioning(phrase, repo)
        english = is_ordinary_english(phrase, words)
        buckets[bucket_of(files, english)].append((phrase, hits, files, english))
    return buckets


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_HEADER = """# Decision sheet -- unblocking the public decision chain

`bin/grip-chain-sync` is failing CLOSED: its leak gate refuses to publish
because {hits} name-shaped spans across {distinct} distinct phrases sit on
**neither** the redaction denylist nor the allowlist.

Every one is categorised `person` by the shield's pattern matcher, which flags
any run of Capitalised Words -- so GRIP's own commit subjects trip it. That is
the shield working and the allowlist being incomplete.

Two outcomes per phrase, and the record publishes either way:

  - **GRIP vocabulary or a public brand** -> `allowlist` in
    `config/redaction-denylist.json`; the phrase publishes verbatim.
  - **a real identity** -> `entities` in the same file; the phrase publishes as
    an opaque `[PERSON-N]` token and the record stays readable.

The `files` column is a hint, not a verdict: how many tracked GRIP files contain
the exact phrase. Nothing is published by reading this sheet.
"""

_SECTIONS = (
    ("identity", "READ THESE FIRST -- shaped like a real name",
     "Carries a word no dictionary holds, and appears in no tracked GRIP file. "
     "That is the shape of a surname or a handle. Denylist unless you recognise "
     "it; the record still publishes, with the phrase as an opaque token."),
    ("coined", "Probably a coined GRIP term -- allowlist",
     "Not a dictionary word, but GRIP writes about it repeatedly: a module, a "
     "product, a doctrine. Allowlisting publishes it verbatim."),
    ("english", "Ordinary English from commit subjects -- bulk allowlist",
     "Every word is a dictionary word, so a surname is unlikely. Skim for the "
     "exception: a real person CAN be named in dictionary words, so this lowers "
     "the risk, it does not remove it."),
)

_TABLE_HEAD = (
    "| phrase | refusals | tracked files | ordinary English | allowlist | denylist |",
    "|---|---|---|---|---|---|",
)

_APPLY = """## Apply

`config/redaction-denylist.json` is git-crypt encrypted, so edit it from a
checkout that can decrypt, then re-run the gate:

```bash
bin/grip-chain-sync --dry-run
```

Zero blockers means the chain is publishable. A `--dry-run` never touches the
public repo; only a bare `grip-chain-sync` publishes, and only when the ledger
actually changed.

Source of truth: `lib/idr_public_emitter check-leak`, the same gate the publish
path runs -- so a sheet with every box ticked is a publishable chain.
"""


def _rows(bucket: list) -> list:
    if not bucket:
        return ["| *(none)* | | | | | |"]
    return [
        f"| `{phrase}` | {hits} | {files} | {'yes' if english else 'no'} | [ ] | [ ] |"
        for phrase, hits, files, english in bucket
    ]


def render(buckets: dict, counts: Counter) -> str:
    """The full sheet as markdown."""
    out = [_HEADER.format(hits=sum(counts.values()), distinct=len(counts)), ""]
    for key, title, hint in _SECTIONS:
        out += [f"## {title}", "", f"*{hint}*", "", *_TABLE_HEAD, *_rows(buckets.get(key, [])), ""]
    out.append(_APPLY)
    return "\n".join(out)


def assert_outside_repo(path: Path) -> None:
    """Refuse to write the sheet inside a git repository.

    The phrases can include a real identity, and committing one is the class
    ``rules/no-sensitive-info-in-grip.md`` forbids. Same posture as
    ``rules/paramount-backups.md``, which raises on a backup root inside a repo.
    """
    probe = path.expanduser().resolve().parent
    res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(probe if probe.exists() else Path.home()),
        capture_output=True, text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        raise RepoOutputRefused(
            f"{path} is inside the git repository at {res.stdout.strip()} -- the "
            "sheet can name a real person, so it is written outside every repo. "
            "Pass an --out path under your backups root instead."
        )


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", type=Path,
                    help="classify a previously captured check-leak report instead of running it")
    ap.add_argument("--out", type=Path,
                    help="write the sheet here (must be outside any git repo); default stdout")
    ap.add_argument("--repo", type=Path, default=CLAUDE_DIR,
                    help="the GRIP checkout to measure phrase presence against")
    args = ap.parse_args(argv)

    text = args.report.read_text() if args.report else run_leak_gate()
    counts = parse_report(text)
    if not counts:
        print("no blockers -- the chain is publishable", file=sys.stderr)
        return 0

    buckets = classify(counts, args.repo)
    sheet = render(buckets, counts)

    if args.out:
        try:
            assert_outside_repo(args.out)
        except RepoOutputRefused as exc:
            # A CLI refusal is one line, not a traceback: the reader needs the
            # recovery instruction, not this module's call stack.
            print(f"[grip-chain-blockers] {exc}", file=sys.stderr)
            return 2
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet)
        # Counts only on stderr: the phrases themselves stay in the file.
        print(f"refusals {sum(counts.values())} across {len(counts)} phrases", file=sys.stderr)
        for key, _title, _hint in _SECTIONS:
            print(f"  {key:<10}: {len(buckets.get(key, []))}", file=sys.stderr)
        print(f"sheet: {args.out}", file=sys.stderr)
    else:
        print(sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
