"""Command line for the stack-dogfood framework.

Subcommands:

* ``status``  — probe every product (or one) and print the census and checks;
  ``--require-installed`` exits non-zero if any requested product is not fully
  installed; ``--json`` prints the raw reports.
* ``report``  — probe and persist each report to ``state/stack-dogfood/``.
* ``install`` — run the installers for a product's missing cells (``--dry-run``
  to preview, ``--harness`` to limit to one harness).
* ``prove``   — show the idempotent-fold proof (``lib/dogfood_kernel.py``) that
  the install is at (or off) its fixed point.
* ``loop``    — run one CHECK-phase tick (gate → per-product reports + loop
  state); opens no PR and spawns no agent (``--config``, ``--force``).
* ``escalate``— ideate a fix and open a DRAFT PR per public product. Dry-run by
  default (opens nothing); ``--live`` spawns the credential-stripped fix agent
  and, only after the leak sweep and the product's tests pass, opens a draft PR.
  Never merges; HAL and GRIP can never reach draft-pr.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent  # lib/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import dogfood_kernel  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402

from . import products
from .model import Census, InstallDomain, Status
from .report import build_report, write_report


def _names(product: str | None) -> list[str]:
    return [product] if product else list(products.NAMES)


def _print_report(report) -> None:
    print(f"# {report.product}  (residual {report.residual()})")
    for cell in report.cells:
        print(f"  {cell.status.value:<10} {cell.harness}")
    for check in report.checks:
        if "::" in check.key:
            flag = "ok  " if check.ok else "FAIL"
            print(f"  check {flag} {check.key}  {check.detail}")


def cmd_status(args) -> int:
    reports = [build_report(n) for n in _names(args.product)]
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            _print_report(report)
    worst = max((r.residual() for r in reports), default=0)
    if args.require_installed and worst > 0:
        return 1
    return 0


def cmd_report(args) -> int:
    claude_dir = get_claude_dir()
    for name in _names(args.product):
        path = write_report(build_report(name), claude_dir)
        print(f"wrote {path}")
    return 0


def cmd_install(args) -> int:
    mod = products.load(args.product)
    targets = mod.targets()
    if args.harness:
        targets = [t for t in targets if t.harness == args.harness]
        if not targets:
            print(f"no harness {args.harness!r} for {args.product}", file=sys.stderr)
            return 2
    return _run_installs(args.product, targets, dry_run=args.dry_run)


def _run_installs(product: str, targets, *, dry_run: bool) -> int:
    rc = 0
    for target in targets:
        status, detail = target.probe()
        if status is Status.REGISTERED:
            print(f"ok   {product}:{target.harness} already registered")
        elif target.install is None:
            print(f"skip {product}:{target.harness} probe-only ({detail})")
        elif dry_run:
            print(f"DRY  {product}:{target.harness} would install ({detail})")
        else:
            rc = _apply_install(product, target) or rc
    return rc


def _apply_install(product: str, target) -> int:
    """Run the add, then probe again. An add can exit 0 and register nothing
    (hermes cancels on empty input), so only a fresh probe may say installed."""
    res = target.install()
    if not res.ok:
        print(f"FAIL {product}:{target.harness} exit {res.code}: {res.err.strip()[:100]}")
        return 1
    status, detail = target.probe()
    if status is Status.REGISTERED:
        print(f"did  {product}:{target.harness} installed")
        return 0
    print(f"FAIL {product}:{target.harness} the add exited 0, but a fresh probe "
          f"reads {status.value} ({detail})")
    return 1


def cmd_prove(args) -> int:
    domain = InstallDomain()
    for name in _names(args.product):
        census = Census(build_report(name).cells)
        proof = dogfood_kernel.generic_proof(domain, census)
        print(
            f"{name}: at_fixed_point={proof.at_fixed_point} "
            f"residual={proof.residual} idempotent={proof.idempotent}"
        )
        print(f"     {proof.rationale}")
    return 0


def cmd_loop(args) -> int:
    import time

    from .loop import LoopConfig, load_config, run_once

    config = load_config(args.config) if args.config else LoopConfig()
    summary = run_once(config, now_epoch=time.time(), force=args.force)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_escalate(args) -> int:
    import time

    from .escalation import load_escalation, run_wave
    from .loop import LoopConfig, load_config, run_once

    config = load_config(args.config) if args.config else LoopConfig()
    escalation = load_escalation(args.config) if args.config else {}
    now = time.time()
    if args.refresh:
        # The full overnight cycle: run the check phase first so the reports the
        # escalation reads are fresh. If the gate is shut, do not escalate.
        check = run_once(config, now_epoch=now)
        if not check.get("fired"):
            print(json.dumps({"phase": "escalate", "refresh": True, "skipped": check},
                             indent=2))
            return 0
    # The fix-preparation agent runs with this empty GH config dir, so it is
    # unauthenticated and cannot push; only the runner (ambient env) pushes.
    gh_config_dir = get_claude_dir() / "state" / "stack-dogfood" / "agent-gh"
    gh_config_dir.mkdir(parents=True, exist_ok=True)
    summary = run_wave(config, escalation, now_epoch=now,
                       live=args.live, ignore_schedule=args.ignore_schedule,
                       gh_config_dir=str(gh_config_dir))
    print(json.dumps(summary, indent=2))
    return 0


def _add_escalate_parser(sub) -> None:
    escalate = sub.add_parser(
        "escalate",
        help="ideate a fix and open a DRAFT PR per public product (dry-run unless --live)",
    )
    escalate.add_argument("--config", help="path to a stack-dogfood.json config")
    escalate.add_argument(
        "--live", action="store_true",
        help="actually spawn the fix agent and open draft PRs (default: dry-run, opens nothing)",
    )
    escalate.add_argument(
        "--refresh", action="store_true",
        help="run the check phase first (probe + write reports) then escalate -- the full overnight cycle",
    )
    escalate.add_argument(
        "--ignore-schedule", action="store_true",
        help="run --live now even outside the scheduled hour (operator override; "
             "the launchd job never passes this, so a late wake still falls back to check-only)",
    )
    escalate.set_defaults(fn=cmd_escalate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-stack-dogfood",
        description="Dogfood the CodeTonight AI suite (GRASP, HAPPI, HAL, GRIP) onto itself.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="probe and print product state")
    status.add_argument("--product", choices=products.NAMES)
    status.add_argument("--require-installed", action="store_true")
    status.add_argument("--json", action="store_true")
    status.set_defaults(fn=cmd_status)

    report = sub.add_parser("report", help="probe and persist reports")
    report.add_argument("--product", choices=products.NAMES)
    report.set_defaults(fn=cmd_report)

    install = sub.add_parser("install", help="install a product's missing cells")
    install.add_argument("--product", required=True, choices=products.NAMES)
    install.add_argument("--harness")
    install.add_argument("--dry-run", action="store_true")
    install.set_defaults(fn=cmd_install)

    prove = sub.add_parser("prove", help="show the idempotent-fold install proof")
    prove.add_argument("--product", choices=products.NAMES)
    prove.set_defaults(fn=cmd_prove)

    loop = sub.add_parser("loop", help="run one check-phase tick of the overnight loop")
    loop.add_argument("--config", help="path to a stack-dogfood.json config")
    loop.add_argument("--force", action="store_true", help="run even if the gate would skip")
    loop.set_defaults(fn=cmd_loop)

    _add_escalate_parser(sub)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
