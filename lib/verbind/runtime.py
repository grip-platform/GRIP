"""Verbind runtime -- the production wiring of the gate's dependencies.

``Runtime.build`` turns one install root into everything an adapter needs: the
store, the catalogue, the sealer and a ``GateDeps``. The Claude Code hook and
the CLI both call it, and a Codex or OpenCode adapter would too (GRIP#7319,
GRIP#7320), so one set of conformance fixtures exercises one wiring.

Two deliberate absences, both tracked in GRIP#7321 rather than left as a
placeholder:

  pre_council  not wired in v1 (``None``). The gate treats an absent council
               exactly as the Lean theorem ``council_absent_is_floor`` says:
               the floor's evidence stands. A pre-council can only make the
               evidence more cautious, so its absence never makes a binding
               less safe; it only means an uncertain ranking reaches the
               Koppel round with no second opinion.
  FAC          no reviewed skill structures exist yet, so the meaning floor
               ranks by CF-IOF alone (see ``floor.py``).

SEAL appends a chained receipt to ``state/verbind/receipts.jsonl`` and anchors
it with one leaf on the shared IDR chain (``lib/precog/idr.append_idr``) whose
decision carries ONLY hashes: the verb's, the meaning's and the receipt's
(rules/operator-anonymity.md). A verb hash is the sha256 of a short coined
word, so hashing a dictionary can recover the verb; it never reveals a prompt.
If the leaf cannot be written, ``idr_id`` stays empty and the receipt stands.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import VERBIND_VERSION
from .catalogue import Catalogue, KnownVerbs, leading_verb, readonly_operations
from .floor import DEFAULT_TOP_K, DEFAULT_UNCERTAIN_BELOW, MeaningFloor
from .gate import (Authority, Binding, Envelope, GateDeps, GateResult, KoppelAnswer,
                   KoppelRequest, Meaning, describe_operation, digest, render_surface)
from .koppel import build_payload
from .store import Store, independence, prompt_hash, quality

__all__ = [
    "DEFAULT_ISSUE_REPO",
    "ISSUES_ENV",
    "ISSUE_REPO_ENV",
    "Runtime",
    "Sealer",
    "github_issue_opener",
    "idr_anchor",
    "verb_hash",
]

ISSUES_ENV = "GRIP_VERBIND_ISSUES"          # "off" or "0" disables issue opening
ISSUE_REPO_ENV = "GRIP_VERBIND_ISSUE_REPO"
DEFAULT_ISSUE_REPO = "grip-platform/GRIP"
ISSUE_TIMEOUT_S = 8


def verb_hash(verb: str) -> str:
    return digest("verbind/verb", verb)


def idr_anchor(receipt: dict) -> Optional[str]:
    """One leaf on the shared IDR chain carrying only the receipt's hashes."""
    from lib.precog.idr import append_idr, build_idr  # noqa: PLC0415 -- signer loads lazily

    decision = {"kind": "verbind", "verb_hash": receipt.get("verb_hash"),
                "meaning_hash": receipt.get("meaning_hash"), "receipt_hash": receipt["hash"]}
    leaf = build_idr(prompt="", fingerprint=receipt["hash"], decision=decision,
                     predecessor_idr=None, depth=0, kind="verbind-decision", inputs={})
    append_idr(leaf)
    return leaf.id


class Sealer:
    """SEAL: a chained receipt, anchored by a hash-only IDR leaf."""

    def __init__(self, store: Store, catalogue: Catalogue,
                 anchor: Optional[Callable[[dict], Optional[str]]] = idr_anchor) -> None:
        self.store = store
        self.catalogue = catalogue
        self.anchor = anchor

    def seal_result(self, result: GateResult, envelope: Envelope, prompt: str) -> Optional[str]:
        """Seal one gate decision -- the outcome its path actually took.

        A prompt with no verb leaves no record (L0, zero cost).
        """
        if result.verb is None:
            return None
        binding = result.binding
        return self.seal_body({
            "kind": "verbind-decision",
            "outcome": result.outcome.value,
            "verdict": result.verdict.value,
            "action": result.action.value,
            "reason": result.reason,
            "verb_hash": verb_hash(result.verb),
            "meaning_hash": binding.meaning.digest() if binding else None,
            "binding_version": binding.version if binding else None,
            "prompt_hash": prompt_hash(prompt) if prompt else None,
            "independent": result.independent,
            "round": result.round,
            **_envelope_fields(envelope),
        })

    def seal_event(self, kind: str, verb: str, envelope: Envelope, **fields) -> str:
        """Seal a decision made outside the gate: a forget, an ultralearn mark."""
        return self.seal_body({"kind": kind, "verb_hash": verb_hash(verb),
                               **_envelope_fields(envelope), **fields})

    def seal_body(self, body: dict) -> str:
        versions = {"verbind": VERBIND_VERSION, "catalogue": self.catalogue.digest(),
                    "floor": {"uncertain_below": DEFAULT_UNCERTAIN_BELOW, "top_k": DEFAULT_TOP_K}}
        return self.store.receipts.append({**body, "versions": versions},
                                          anchor=self.anchor)["receipt_id"]


def _envelope_fields(envelope: Envelope) -> dict:
    """The envelope as a receipt keeps it: origin and harness clear, the rest hashed."""
    return {"origin": envelope.origin, "harness": envelope.harness,
            "session_hash": digest("verbind/session", envelope.session) if envelope.session else None,
            "ref_hash": digest("verbind/ref", envelope.ref) if envelope.ref else None}


def _issues_enabled() -> bool:
    return os.environ.get(ISSUES_ENV, "").strip().lower() not in ("0", "off", "false", "no")


def _issue_body(binding: Binding) -> str:
    """No prompt text and no operator note: only what the binding is."""
    m = binding.meaning
    return "\n".join([
        f"The verbinding `/{binding.verb}` reached N={binding.uses} independent uses and passed "
        "all three quality gates (tested, monitored, ultralearned), so Verbind v1 opened this "
        "issue instead of writing a skill itself.",
        "",
        f"- bound operation: {describe_operation(m)} ({m.kind} `{m.target}`)",
        f"- binding version: {binding.version}",
        f"- ultralearn seal: `{binding.ultralearn_seal}`",
        f"- meaning hash: `{m.digest()}`",
        "",
        f"Next step: crystallise it with `/create`. Promotion grants no new capability, so the "
        f"skill must do no more than {describe_operation(m)} does. Contract: docs/VERBIND.md.",
    ])


def github_issue_opener(binding: Binding) -> Optional[str]:
    """PROMOTE's one external effect: open the issue, return its URL or None."""
    if not _issues_enabled():
        return None
    repo = os.environ.get(ISSUE_REPO_ENV) or DEFAULT_ISSUE_REPO
    title = f"Verbind: promote /{binding.verb} to a skill (bound to {describe_operation(binding.meaning)})"
    try:
        out = subprocess.run(["gh", "issue", "create", "-R", repo, "--title", title,
                              "--body", _issue_body(binding)],
                             capture_output=True, text=True, timeout=ISSUE_TIMEOUT_S,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.strip().splitlines() if out.returncode == 0 else []
    url = lines[-1].strip() if lines else ""
    return url if url.startswith("https://") else None


class _LazyFloor:
    """Fit the meaning floor only when a fresh verb from a trusted origin needs it."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue
        self._floor: Optional[MeaningFloor] = None

    def __call__(self, prompt: str, verb: str, args: str):
        if self._floor is None:
            self._floor = MeaningFloor(self.catalogue.skills())
        return self._floor.evidence(verb, args)


@dataclass(frozen=True)
class Runtime:
    """Everything an adapter needs, built from one install root."""

    store: Store
    catalogue: Catalogue
    sealer: Sealer
    deps: GateDeps

    @classmethod
    def build(cls, root: Optional[Path] = None, *, state_root: Optional[Path] = None,
              catalogue: Optional[Catalogue] = None,
              koppel: Optional[Callable[[KoppelRequest], KoppelAnswer]] = None,
              opener: Callable[[Binding], Optional[str]] = github_issue_opener,
              anchor: Optional[Callable[[dict], Optional[str]]] = idr_anchor,
              clock: Callable[[], float] = time.time) -> "Runtime":
        from claude_dir import get_claude_dir  # noqa: PLC0415 -- resolved at call time

        root = Path(root) if root is not None else get_claude_dir()
        cat = catalogue or Catalogue(root)
        store = Store.at(state_root if state_root is not None else root / "state" / "verbind", clock)
        sealer = Sealer(store, cat, anchor)
        deps = _deps(store, cat, sealer, koppel, opener, clock)
        return cls(store, cat, sealer, deps)


def _permitted(catalogue: Catalogue) -> Callable[[str, str], bool]:
    return lambda kind, slug: (kind, slug) in readonly_operations(catalogue)


def _deps(store: Store, cat: Catalogue, sealer: Sealer,
          koppel: Optional[Callable], opener: Callable, clock: Callable) -> GateDeps:
    known = KnownVerbs(cat)
    permitted = _permitted(cat)

    def leading_unknown_verb(prompt: str) -> tuple:
        lv = leading_verb(prompt)
        if lv is None or known.known(lv.verb):
            return None, ""
        return lv.verb, lv.args

    def capability_floor(envelope: Envelope) -> Authority:
        if not envelope.trusted:
            return Authority(False, f"origin:{envelope.origin}")
        return Authority(True, "operator", readonly_operations(cat))

    def koppel_handoff(req: KoppelRequest) -> KoppelAnswer:
        payload = build_payload(req.verb, req.evidence, permitted)
        return KoppelAnswer("pending", payload={**payload, "round": req.round})

    def apply(binding: Binding, envelope: Envelope, args: str) -> dict:
        m = binding.meaning
        return {"operation": describe_operation(m), "kind": m.kind, "target": m.target,
                "args": args, "still_permitted": permitted(m.kind, m.target)}

    def commit(binding: Binding) -> Binding:
        store.bindings.commit(binding)
        store.unresolved.record(binding.verb, "clear")  # a refine after a deferral wins
        return binding

    def promote(binding: Binding) -> None:
        rec = store.promotion.promote(binding, opener)
        if rec is not None:
            sealer.seal_body({"kind": "verbind-promotion", "verb_hash": verb_hash(binding.verb),
                              "meaning_hash": binding.meaning.digest(),
                              "binding_version": binding.version, "status": rec["status"]})

    def record_denied(verb: str, meaning: Optional[Meaning], reason: str) -> None:
        store.unresolved.record(verb, "denied", reason=reason,
                                meaning_hash=meaning.digest() if meaning else None)

    return GateDeps(
        leading_unknown_verb=leading_unknown_verb,
        unresolved=store.unresolved.unresolved,
        lookup=store.bindings.lookup,
        independent=lambda prompt, b, env: independence(store.receipts, prompt, env, clock)[0],
        record_use=store.bindings.record_use,
        quality=lambda b: quality(b, exists=cat.exists, permitted=permitted),
        promote=promote,
        capability_floor=capability_floor,
        floor_evidence=_LazyFloor(cat),
        koppel=koppel or koppel_handoff,
        verbind=store.bindings.make,
        commit=commit,
        apply=apply,
        record_unresolved=lambda verb, reason: store.unresolved.record(verb, "unresolved", reason=reason),
        record_denied=record_denied,
        rounds_used=store.unresolved.rounds_used,
        record_round=lambda verb: store.unresolved.record(verb, "round"),
        seal=sealer.seal_result,
        surface=render_surface,
        pre_council=None,
    )
