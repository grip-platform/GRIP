"""Command-line interface for bin/grip-lexicon.

  measure    per-file and per-tier figures against their budgets
  ratchet    lower each ratchet in config/rules-scope.json to today's figure
  lint       every mechanical KERN check (exit 1 on any error)
  encode     English meanings -> defined terms, to a fixed point
  expand     defined terms -> English: the Rosetta view a human reads
  normalise  put the LEXICON at its own fixed point
  ledger     write or check the clause ledger (docs/doctrine/ledger.json)
  probe      the multi-model comprehension gate, printed as a Babel card

``--root`` defaults to the checkout this tool lives in, never to the shared
tree, so a measurement always describes the files you are editing.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from . import kernel as kern
from . import ledger, lint, measure, probe, surfaces
from .lexicon import LexiconError, normalise, parse
from .tokencount import Cached, ClaudeDifferential, standard

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def _counters(args: argparse.Namespace) -> list:
    counters = standard([n.strip() for n in args.tokenizers.split(",") if n.strip()])
    if args.claude:
        # Imported here, not at module top, on purpose: only --claude needs it,
        # and the cache is state, not code - it lives in the install's state dir,
        # which outlives any one worktree, keyed by content so it never goes stale.
        from claude_dir import get_claude_dir  # on sys.path via lib/lexicon/surfaces
        cache = get_claude_dir() / "state" / "lexicon" / "token-cache.json"
        counters.append(Cached(ClaudeDifferential(model=args.claude), cache))
    return counters


def cmd_measure(args: argparse.Namespace) -> int:
    report = measure.measure(_root(args), _counters(args))
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    elif args.markdown:
        print(measure.render_markdown(report), end="")
    else:
        print(measure.render(report))
    if args.annotate:
        for line in measure.annotations(report):
            print(line)
    return 1 if args.check and report.ceilings_breached else 0


def cmd_ratchet(args: argparse.Namespace) -> int:
    rows = measure.lower_ratchet(_root(args))
    if not rows:
        print(f"no ratchet is declared in {measure.SCOPE_REL}")
    for row in rows:
        print(measure.ratchet_line(row))
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    findings = lint.lint(_root(args), strict=args.strict)
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.level:<5} {f.check:<22} {f.where}: {f.message}")
        errors = sum(1 for f in findings if f.level == "error")
        print(f"{errors} error(s), {len(findings) - errors} warning(s)")
    return 1 if any(f.level == "error" for f in findings) else 0


def _lexicon_codec(root: Path):
    surface = surfaces.lexicon(root)
    if surface is None:
        raise SystemExit(f"no LEXICON at {root / surfaces.LEXICON_REL}")
    return parse(surface.text).codec()


def _read_input(path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()


def cmd_encode(args: argparse.Namespace) -> int:
    sys.stdout.write(_lexicon_codec(_root(args)).encode(_read_input(args.file)))
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    sys.stdout.write(_lexicon_codec(_root(args)).expand(_read_input(args.file)))
    return 0


def cmd_normalise(args: argparse.Namespace) -> int:
    path = _root(args) / surfaces.LEXICON_REL
    text = path.read_text(encoding="utf-8")
    fixed = normalise(text)
    if not args.write:
        sys.stdout.write(fixed)
        return 0
    # Exact bytes, compared with the bytes on disk, because the lint reads the file
    # byte for byte. A text-mode write gives CRLF line endings on Windows, which is
    # not the normal form. And a file that arrived with CRLF endings must be written
    # again although its text, read with newline translation, equals the normal form.
    data = fixed.encode("utf-8")
    if data != path.read_bytes():
        path.write_bytes(data)
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    root = _root(args)
    rows = lint.ledger_rows(root)
    if args.write:
        print(f"wrote {ledger.write(root, rows)} ({len(rows)} kernel(s))")
        return 0
    stored = ledger.load(root)
    gaps = [g for rule, current in rows.items() for g in ledger.warden(rule, current, stored)]
    for gap in gaps:
        print(f"{gap.kind}: {gap.detail}")
    return 1 if gaps else 0


def _checked_bank(doc: dict, bank: str) -> probe.ItemSet:
    """The bank for --by-question, refused unless it is the one the results used.

    Answers are keyed by question id, so a different bank, or a later edit of
    the same one, would pin answers to the wrong questions without an error.
    """
    item_set = probe.load_items(Path(bank))
    saved = doc.get("bank_sha256")
    if saved and saved != item_set.bank_sha256:
        raise SystemExit(f"{bank} is not the bank these results were run against "
                         f"(results name {saved[:16]}..., this file is "
                         f"{item_set.bank_sha256[:16]}...)")
    if not saved:
        print("(these results predate bank digests, so the bank could not be checked)")
    return item_set


def _card_from_file(path: Path, bank: str | None) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    item_set = _checked_bank(doc, bank) if bank else None
    runs = [probe.Run(**r) for r in doc["runs"]]
    verdicts = [probe.Verdict(**v) for v in doc["verdicts"]]
    print(probe.babel_card(doc["rule"], runs, verdicts,
                           (doc["gate"]["passed"], doc["gate"]["reason"])))
    if doc.get("pooled"):
        saved = doc["pooled"]
        print(probe.pooled_line((saved["difference"], saved["low"], saved["high"]), saved["margin"]))
    if item_set:
        print(probe.question_view(item_set.items, runs))
    return 0 if doc["gate"]["passed"] or not probe.judged_a_kernel(runs) else 1


def _kernel_condition(root: Path, kernel_rel: str | None) -> tuple[str | None, str | None]:
    if not kernel_rel:
        return None, None
    lex = surfaces.lexicon(root)
    if lex is None or lex.encrypted:
        raise SystemExit(f"the kernel condition needs a readable {surfaces.LEXICON_REL}")
    return (root / kernel_rel).read_text(encoding="utf-8"), lex.text


def cmd_probe(args: argparse.Namespace) -> int:
    """Without --kernel this validates the items (original vs control) only."""
    if args.card:
        return _card_from_file(Path(args.card), args.by_question)
    if args.by_question:
        raise SystemExit("--by-question goes with --card; a gate run with --kernel prints it anyway")
    root = _root(args)
    item_set = probe.load_items(Path(args.items))
    kernel, lexicon_text = _kernel_condition(root, args.kernel)
    systems = probe.conditions(probe.original_text(item_set, root), kernel, lexicon_text)
    seats = [probe.seat(s.strip()) for s in args.seats.split(",") if s.strip()]
    with ThreadPoolExecutor(max_workers=len(seats)) as pool:
        per_seat = list(pool.map(lambda s: probe.run_seat(s, systems, item_set.items), seats))
    runs = [r for rs in per_seat for r in rs]
    verdicts = [probe.judge_seat(rs, args.margin, args.control_gap) for rs in per_seat]
    outcome = probe.gate(verdicts)
    pooled = probe.pooled_difference(item_set.items, runs) if kernel else None
    doc = probe.to_json(item_set, runs, verdicts, outcome,
                        probe.sha256_text(kernel) if kernel else None, pooled, args.margin)
    if args.out:
        Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(probe.babel_card(item_set.rule, runs, verdicts, outcome))
    if kernel:
        print(probe.pooled_line(pooled, args.margin))
        print(probe.question_view(item_set.items, runs))
    return 0 if outcome[0] or not kernel else 1


def _add_checks(sub) -> None:
    m = sub.add_parser("measure", help="figures against budgets")
    m.add_argument("--tokenizers", default="o200k,cl100k,bytes",
                   help="comma list from: o200k, cl100k, gpt2, bytes")
    m.add_argument("--claude", metavar="MODEL", help="also count with Claude (spends model calls)")
    form = m.add_mutually_exclusive_group()
    form.add_argument("--json", action="store_true")
    form.add_argument("--markdown", action="store_true", help="the pull-request job summary")
    m.add_argument("--annotate", action="store_true",
                   help="also print a GitHub warning or notice for each ratchet not met exactly")
    m.add_argument("--check", action="store_true", help="exit 1 when a ceiling is breached")
    m.set_defaults(fn=cmd_measure)
    r = sub.add_parser("ratchet", help="lower each ratchet to today's figure (never raises one)")
    r.set_defaults(fn=cmd_ratchet)
    lt = sub.add_parser("lint", help="every mechanical check")
    lt.add_argument("--strict", action="store_true", help="term warnings become errors")
    lt.add_argument("--json", action="store_true")
    lt.set_defaults(fn=cmd_lint)


def _add_text_commands(sub) -> None:
    for name, fn, helptext in (("encode", cmd_encode, "English -> defined terms"),
                               ("expand", cmd_expand, "defined terms -> English (Rosetta)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("file", nargs="?", help="input file (default: stdin)")
        p.set_defaults(fn=fn)
    n = sub.add_parser("normalise", help="put the LEXICON at its fixed point")
    n.add_argument("--write", action="store_true", help="rewrite the file instead of printing")
    n.set_defaults(fn=cmd_normalise)


def _add_ledger(sub) -> None:
    lg = sub.add_parser("ledger", help="the clause ledger")
    mode = lg.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate docs/doctrine/ledger.json")
    mode.add_argument("--check", action="store_true", help="exit 1 if the ledger is stale")
    lg.set_defaults(fn=cmd_ledger)


def _add_probe(sub) -> None:
    pr = sub.add_parser("probe", help="multi-model comprehension gate (Babel card)")
    src = pr.add_mutually_exclusive_group(required=True)
    src.add_argument("--items", help="pre-registered item file, e.g. eval/kern/<rule>.json")
    src.add_argument("--card", help="re-render the Babel card from a saved --out file")
    pr.add_argument("--by-question", metavar="BANK",
                    help="with --card: also list the questions the kernel lost, seat by seat")
    pr.add_argument("--kernel", help="kernel file (relative to --root); omit to validate items")
    pr.add_argument("--seats", default=",".join(probe.DEFAULT_SEATS),
                    help="council seat names and claude:<model> seats, comma-separated")
    pr.add_argument("--margin", type=float, default=5.0,
                    help="points the kernel may fall below the original (default 5)")
    pr.add_argument("--control-gap", type=float, default=20.0,
                    help="points the control must fall below the original (default 20)")
    pr.add_argument("--out", help="write the full results as JSON")
    pr.set_defaults(fn=cmd_probe)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grip-lexicon", description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="checkout to read (default: the one this tool lives in)")
    sub = parser.add_subparsers(dest="command", required=True)
    for add in (_add_checks, _add_text_commands, _add_ledger, _add_probe):
        add(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (LexiconError, kern.KernelError) as exc:
        print(f"grip-lexicon: {exc}", file=sys.stderr)
        return 2
