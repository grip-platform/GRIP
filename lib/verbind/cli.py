"""Verbind CLI -- JSON in, JSON out, behind ``bin/grip-verbind``.

  grip-verbind gate
      stdin   {"prompt": "...", "envelope": {"origin": "...", "harness": "..."}}
      stdout  {verdict, action, verb, args, recommended, koppel_payload,
               binding, receipt_id, reason, surface, application, ...}
  grip-verbind gate --prompt TEXT
      The same, for one prompt typed in a Claude Code session: the envelope
      comes from Claude Code's own markers. This is how the /verbind skill
      works before the hook is activated.
  grip-verbind refine VERB --kind KIND --target SLUG [--note TEXT] [--args TEXT]
  grip-verbind refine VERB --defer
      Settle a Koppel round: bind VERB to the operation the operator chose, or
      defer it. The same command corrects an existing binding.
  grip-verbind list [--readonly]
  grip-verbind forget VERB
  grip-verbind ultralearned VERB --seal ID

``gate`` reads its envelope from stdin: it is the entry point for any adapter.
Everywhere else the envelope comes from ``--envelope-file`` when given,
otherwise from Claude Code's own markers, which a command run inside a Claude
Code session inherits; with no markers the origin is ``unknown`` and nothing
can be bound. Every state change needs an operator envelope. Under Claude
Code the output also carries ``instruction``: the steps for the model.

Exit codes: 0 done; 1 refused (untrusted origin, not coinable, no binding,
malformed seal, not permitted); 2 malformed input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from . import claude_code
from .catalogue import KnownVerbs, is_verb_shape, readonly_operations
from .gate import (Action, Envelope, GateResult, KoppelAnswer, Meaning, describe_operation,
                   gate, gate_answer)
from .runtime import Runtime
from .store import monitored, tested, ultralearned

__all__ = ["main"]

_KINDS = ("skill", "agent", "mode")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grip-verbind",
                                description="Verbind: coined slash verbs, bound through a Koppel round.")
    sub = p.add_subparsers(dest="command", required=True)
    gate_p = sub.add_parser("gate", help="decide one prompt; reads {prompt, envelope} JSON on stdin")
    gate_p.add_argument("--prompt", default=None,
                        help="decide this prompt instead, with the envelope from Claude Code's markers")
    refine = sub.add_parser("refine", help="bind a verb to the operation the operator chose, or defer it")
    refine.add_argument("verb")
    refine.add_argument("--kind", choices=_KINDS, default="skill")
    refine.add_argument("--target", help="the catalogue slug of the chosen operation")
    refine.add_argument("--note", default="", help="the operator's reason, in a few words")
    refine.add_argument("--args", dest="span", default="", help="the words that followed the verb")
    refine.add_argument("--defer", action="store_true", help="leave the verb unbound for good")
    listing = sub.add_parser("list", help="bindings, deferred verbs and the receipt chain")
    listing.add_argument("--readonly", action="store_true", help="also list the bindable operations")
    forget = sub.add_parser("forget", help="drop a binding or a deferral, so the verb is asked again")
    forget.add_argument("verb")
    mark = sub.add_parser("ultralearned", help="record the /ultralearn seal for a binding")
    mark.add_argument("verb")
    mark.add_argument("--seal", required=True)
    for sp in (refine, forget, mark):
        sp.add_argument("--envelope-file", type=Path, default=None,
                        help="the adapter's envelope as JSON (default: Claude Code's markers)")
    return p


def _envelope(path: Optional[Path]) -> Envelope:
    if path is None:
        return claude_code.envelope("", os.environ)
    try:
        return Envelope.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Envelope(origin="unknown")


def _instruction(result: GateResult) -> Optional[str]:
    """The model-facing steps, from the one place that writes them."""
    if result.action is Action.ASK and result.koppel_payload:
        return claude_code.koppel_instructions(result)
    applies = (result.action is Action.APPLY and result.binding is not None
               and (result.application or {}).get("still_permitted"))
    return claude_code.apply_instructions(result) if applies else None


def _result_out(result: GateResult, envelope: Envelope) -> dict:
    out = result.to_dict()
    instruction = _instruction(result) if envelope.harness == claude_code.HARNESS else None
    if instruction:
        out["instruction"] = instruction
    return out


def _gate_input(args: argparse.Namespace) -> tuple:
    """(prompt, envelope) from --prompt and the markers, or from stdin JSON."""
    if args.prompt is not None:
        return args.prompt, claude_code.envelope(args.prompt, os.environ)
    data = json.loads(sys.stdin.read() or "{}")
    if not isinstance(data, dict):
        raise ValueError("stdin must be a JSON object")
    return str(data.get("prompt") or ""), Envelope.from_dict(data.get("envelope"))


def _cmd_gate(args: argparse.Namespace, rt: Runtime) -> tuple:
    try:
        prompt, envelope = _gate_input(args)
    except ValueError:
        return 2, {"error": "stdin must be JSON: {\"prompt\": ..., \"envelope\": {...}}"}
    return 0, _result_out(gate(prompt, envelope, rt.deps), envelope)


def _answer(args: argparse.Namespace) -> Optional[KoppelAnswer]:
    if args.defer:
        return KoppelAnswer("deferred")
    if not args.target:
        return None
    return KoppelAnswer("refined", Meaning(args.kind, args.target, args.note or ""))


def _cmd_refine(args: argparse.Namespace, rt: Runtime) -> tuple:
    verb = args.verb.lower().lstrip("/")
    if not is_verb_shape(verb) or KnownVerbs(rt.catalogue).known(verb):
        return 1, {"error": f"/{verb} is not a coinable verb: it is malformed or already known"}
    answer = _answer(args)
    if answer is None:
        return 2, {"error": "refine needs --target SLUG (with --kind), or --defer"}
    envelope = _envelope(args.envelope_file)
    result = gate_answer(verb, answer, envelope, rt.deps, args=args.span)
    code = 0 if result.action in (Action.APPLY, Action.DEFER) else 1
    return code, _result_out(result, envelope)


def _binding_row(b, rt: Runtime) -> dict:
    allowed = readonly_operations(rt.catalogue)
    permitted = lambda kind, slug: (kind, slug) in allowed  # noqa: E731
    return {**b.to_dict(), "operation": describe_operation(b.meaning),
            "quality": {"tested": tested(b, rt.catalogue.exists, permitted),
                        "monitored": monitored(b), "ultralearned": ultralearned(b)}}


def _cmd_list(args: argparse.Namespace, rt: Runtime) -> tuple:
    ok, broken_at = rt.store.receipts.verify()
    out = {"bindings": [_binding_row(b, rt) for _, b in sorted(rt.store.bindings.current().items())],
           "deferred": rt.store.unresolved.deferred(),
           "receipts": {"count": len(rt.store.receipts.records()), "chain_ok": ok,
                        "broken_at": broken_at}}
    if args.readonly:
        out["readonly"] = [{"kind": k, "slug": s, "operation": describe_operation(Meaning(k, s)),
                            "summary": (rt.catalogue.find(k, s).summary if rt.catalogue.find(k, s) else "")}
                           for k, s in sorted(readonly_operations(rt.catalogue))]
    return 0, out


def _refuse_untrusted(envelope: Envelope, what: str) -> Optional[tuple]:
    if envelope.trusted:
        return None
    return 1, {"error": f"{what} needs the operator's own session", "origin": envelope.origin}


def _cmd_forget(args: argparse.Namespace, rt: Runtime) -> tuple:
    verb, envelope = args.verb.lower().lstrip("/"), _envelope(args.envelope_file)
    refused = _refuse_untrusted(envelope, "forget")
    if refused:
        return refused
    forgot = rt.store.bindings.forget(verb)
    rt.store.unresolved.record(verb, "clear")
    rid = rt.sealer.seal_event("verbind-forget", verb, envelope, forgot_binding=forgot)
    return 0, {"verb": verb, "forgot_binding": forgot, "cleared": True, "receipt_id": rid}


def _cmd_ultralearned(args: argparse.Namespace, rt: Runtime) -> tuple:
    verb, envelope = args.verb.lower().lstrip("/"), _envelope(args.envelope_file)
    refused = _refuse_untrusted(envelope, "an ultralearn mark")
    if refused:
        return refused
    binding = rt.store.bindings.lookup(verb)
    if binding is None:
        return 1, {"error": f"/{verb} has no binding"}
    try:
        rt.store.promotion.mark_ultralearned(binding, args.seal)
    except ValueError as exc:
        return 1, {"error": str(exc)}
    rid = rt.sealer.seal_event("verbind-ultralearned", verb, envelope,
                               meaning_hash=binding.meaning.digest(),
                               binding_version=binding.version, seal=args.seal)
    return 0, {"verb": verb, "binding_version": binding.version, "seal": args.seal, "receipt_id": rid}


_HANDLERS: dict = {"gate": _cmd_gate, "refine": _cmd_refine, "list": _cmd_list,
                   "forget": _cmd_forget, "ultralearned": _cmd_ultralearned}


def main(argv: Optional[list] = None,
         runtime: Optional[Callable[[], Runtime]] = None) -> int:
    args = _parser().parse_args(argv)
    rt = (runtime or Runtime.build)()
    code, out = _HANDLERS[args.command](args, rt)
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    # Run as `python -m lib.verbind.cli`. Re-exec goes through bin/grip-verbind,
    # the runnable twin of this module: re-executing this file as a plain script
    # would break its relative imports.
    from pq_reexec import reexec_if_incapable  # lib/ is on sys.path via the package

    reexec_if_incapable(Path(__file__).resolve().parents[2] / "bin" / "grip-verbind")
    raise SystemExit(main())
