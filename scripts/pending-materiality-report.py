#!/usr/bin/env python3
"""Count how the pending store's questions classify. COUNTS ONLY.

Questions can carry personal-matter content, so this prints no question text,
no summary, no session name and no question id: only how many questions fall
under each verdict, each HALT class and each source, and, given hand labels,
how many agree with them.

It runs the full classifier (`lib/pending_materiality.classify`): the
deterministic floor first, then a cheap model wherever the floor abstains. That
is the layer the Stop hook deliberately does not run, so the "floor alone" line
is what the hook's gate would do, and the other lines are what the model adds.

Usage:
    grip-py scripts/pending-materiality-report.py [--store PATH] [--labels PATH]

    --store   the pending store to read; default <CLAUDE_DIR>/state/pending-questions.json
    --labels  a JSON object mapping question_id to a hand label: "chore",
              "decision", or one of the four HALT classes. Prints agreement counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.pending_materiality import HALT_CLASSES, Materiality, classify  # noqa: E402

# The four cells of the agreement table: (hand label, what the classifier did).
_CELLS = (("decision", "keep"), ("chore", "keep"), ("decision", "chore"),
          ("chore", "chore"))


def _default_store() -> Path:
    from lib.claude_dir import get_claude_dir

    return get_claude_dir() / "state" / "pending-questions.json"


def load_rows(path: Path) -> list[dict]:
    """Every row in the store. A missing or unreadable store is an empty one."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    return [row for row in data.values() if isinstance(row, dict)]


def classify_rows(rows: list[dict], classify_fn=classify) -> list[tuple[str, Materiality]]:
    """(question_id, verdict) per row. The text reaches the classifier and nothing else."""
    return [(str(row.get("question_id", "")),
             classify_fn(row.get("step_text") or row.get("summary") or ""))
            for row in rows]


def tally(results: list[tuple[str, Materiality]]) -> dict[str, Counter]:
    """Counts per verdict, HALT class and source, plus what the floor alone decided."""
    verdicts = [m for _, m in results]
    return {
        "verdict": Counter(m.verdict for m in verdicts),
        "halt class": Counter(m.halt_class or "(none)" for m in verdicts),
        "source": Counter(m.source for m in verdicts),
        # The floor's own verdicts come back with source "floor"; everything
        # else is a floor abstention that the model or the default then decided.
        "floor alone": Counter(m.verdict if m.source == "floor" else "abstained"
                               for m in verdicts),
    }


def render_counts(total: int, counts: dict[str, Counter]) -> str:
    lines = ["Pending materiality report: counts only, no question text.",
             f"questions classified: {total}"]
    for name, counter in counts.items():
        cells = "  ".join(f"{key} {value}" for key, value in sorted(counter.items()))
        lines.append(f"{name + ':':13} {cells}")
    return "\n".join(lines)


def _as_truth(label: str) -> str | None:
    """A hand label as "chore" or "decision"; None when it is not a known label."""
    if label == "chore":
        return "chore"
    if label == "decision" or label in HALT_CLASSES:
        return "decision"
    return None


def agreement(results: list[tuple[str, Materiality]], labels: dict) -> dict:
    """Agreement counts against hand labels, overall, per source and per HALT class."""
    overall, by_source, classes, unlabelled = Counter(), Counter(), Counter(), 0
    for qid, m in results:
        label = str(labels.get(qid, ""))
        truth = _as_truth(label)
        if truth is None:
            unlabelled += 1
            continue
        called = "chore" if m.verdict == "chore" else "keep"
        overall[(truth, called)] += 1
        by_source[(m.source, truth, called)] += 1
        if label in HALT_CLASSES and m.halt_class:
            classes["agree" if label == m.halt_class else "differ"] += 1
    return {"overall": overall, "by_source": by_source, "classes": classes,
            "unlabelled": unlabelled}


def _source_cells(by_source: Counter, source: str) -> str:
    return " / ".join(str(by_source[(source, t, c)]) for t, c in _CELLS)


def render_agreement(agr: dict) -> str:
    kept_d, kept_c, chore_d, chore_c = (agr["overall"][cell] for cell in _CELLS)
    lines = [
        f"agreement with hand labels: {kept_d + kept_c + chore_d + chore_c} labelled, "
        f"{agr['unlabelled']} unlabelled",
        f"  kept by the classifier:    labelled decision {kept_d}, labelled chore {kept_c}",
        f"  called chore:              labelled decision {chore_d}, labelled chore {chore_c}",
        f"  chore precision {chore_c} of {chore_d + chore_c}; "
        f"chore recall {chore_c} of {kept_c + chore_c}",
        f"  HALT class, where both named one: {agr['classes']['agree']} agree, "
        f"{agr['classes']['differ']} differ",
        "  by source (kept decision / kept chore / chore decision / chore chore):",
    ]
    for source in sorted({key[0] for key in agr["by_source"]}):
        lines.append(f"    {source}: {_source_cells(agr['by_source'], source)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, default=None,
                        help="pending store to read (default: the live store)")
    parser.add_argument("--labels", type=Path, default=None,
                        help="JSON object of question_id -> hand label")
    args = parser.parse_args(argv)

    results = classify_rows(load_rows(args.store or _default_store()))
    print(render_counts(len(results), tally(results)))
    if args.labels is not None:
        print()
        print(render_agreement(agreement(results, json.loads(args.labels.read_text()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
