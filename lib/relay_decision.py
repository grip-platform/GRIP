"""relay_decision — deterministic address-inference decision + IDR for GRIP #2720.

Every inbound relay event receives one Decision record. The record is:
  - notarised via happi.md/1.1 ``cmd:"idr.emit"`` when the HAL surface is
    reachable (PR #328 / feat/hal-raillm-grip-runtime); emitted as a GRIP-side
    mirror into logs/relay-dispatcher.jsonl now (forward-compatible by
    construction — converges onto the HAL surface on merge).
  - named an "IDR" only when notarised via idr.emit/audit; a plain
    "decision-trace" otherwise. Code honours that conditionality.

Leakage guard (PARAMOUNT): confidence/intent/rationale MUST NOT reach external
or Slack-Connect parties. The plain-language trace is operator-DM-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Classification labels
# ---------------------------------------------------------------------------
CLASSIFICATION_CONTEXT = "context"
CLASSIFICATION_ACTIONABLE = "actionable"
CLASSIFICATION_NOT_ADDRESSED = "not-addressed"

# happi.md/1.1 version tag (matches events.py::emit_idr in HAL PR #328)
_HAPPI_VERSION = "happi/1.1"

# Operator user_id — stable across DM channel rotations (operational-context.md).
# Env-overridable (HAL Council #2723 B6): a rotated/deactivated id can be
# corrected without a code change; defaults to the canonical V>> user_id.
OPERATOR_USER_ID = os.environ.get("GRIP_RELAY_OPERATOR_USER_ID", "U0EXAMPLE01")


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    """Per-inbound address-inference decision record.

    Classification:
      context          — ambient context, not requiring a reply
      actionable       — clearly aimed at GRIP, requires a reply
      not-addressed    — message present but not for GRIP

    Confidence is deterministic: sum of 4 boolean signals / 4.0.
    """
    envelope_id: str
    classification: str
    confidence: float          # 0.0 – 1.0, step 0.25
    posted: bool               # whether the dispatcher will post a reply
    rationale: str             # one short sentence
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    idr_ref: Optional[dict] = None  # set after notarisation


# ---------------------------------------------------------------------------
# Signal 1 — GRIP addressed (vocative / direct-address aware)
# ---------------------------------------------------------------------------
# Vocative forms — LEADING ("grip,"/"@grip"/"hey grip") AND TRAILING
# ("…, grip." / "…, @grip!"). The "grip <imperative>" sentence-start case is
# handled in _signal_grip_addressed (it reuses _IMPERATIVE_WORDS, not a
# blanket `^grip\s+\w` which over-matched third-person statements).
#
# The trailing arm requires a PRECEDING COMMA and END-OF-STRING, so a
# third-person mention still does NOT match — "grip nexus is down",
# "the grip is loose", "grip's view", "ask grip, then me",
# "i'll show grip this later" all stay NOT-addressed. The #2723
# anti-false-actionable conservatism (name+verb, not name+noun) is fully
# preserved; this only adds the unambiguous comma-delimited end vocative.
#
# Origin: 2026-05-16 V>> empirical falsification — "Give me a summary of
# the day, GRIP." (a trailing vocative) was classified context→silent
# because only leading vocatives were recognised.
_GRIP_VOCATIVE = re.compile(
    r"""
    (?:
        ^@grip\b              # @grip leading
      | ^grip\s*[,:\.]        # "grip," / "grip:" / "grip." at start
      | \bhey\s+grip\b        # "hey grip"
      | ,\s*@?grip[!?.]*$     # "…, grip." trailing vocative (comma + EOL)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _signal_grip_addressed(text_lower: str) -> bool:
    """Signal 1: GRIP is *addressed* — a leading vocative
    ("grip,"/"@grip"/"hey grip"), a TRAILING vocative ("…, grip." / "…, grip"),
    OR the name at sentence start immediately followed by an imperative
    ("grip list the PRs"). A bare mention is NOT addressed.

    HAL Council #2723: the old `^grip\\s+\\w` arm matched ANY word after
    "grip" at start, so "grip nexus is down" / "grip django" read as
    addressed -> false actionable -> unintended channel replies. The
    imperative requirement (reusing _IMPERATIVE_WORDS, DRY) encodes the
    actual intent: name + request verb, not name + noun.
    """
    s = text_lower.strip()
    if _GRIP_VOCATIVE.search(s):
        return True
    toks = s.split()
    return (
        len(toks) >= 2
        and toks[0].rstrip(",:.!?") == "grip"
        and toks[1].rstrip(",:.!?") in _IMPERATIVE_WORDS
    )


# ---------------------------------------------------------------------------
# Signal 2 — imperative verb at sentence start
# ---------------------------------------------------------------------------
# Curated in-code set (KISS — a config-file loader would be YAGNI for a
# closed verb list; expand here, not via imperatives.json).
_IMPERATIVE_WORDS = frozenset([
    "do", "run", "list", "show", "tell", "check", "create", "update",
    "fix", "send", "make", "get", "fetch", "summarise", "summarize",
    "find", "search", "analyse", "analyze", "help", "give", "open",
    # Operational verbs (AGORA #2723: "Stop the deployment" must classify
    # as actionable — incomplete set broke the deterministic guarantee).
    "stop", "start", "set", "build", "deploy", "restart", "kill",
    "push", "pull", "merge", "revert", "add", "remove", "delete",
    "write", "read", "explain", "review", "enable", "disable",
    "generate", "draft", "prepare", "schedule", "cancel", "retry",
    "verify", "confirm", "investigate", "debug", "refactor", "test",
    "ship", "post", "reply", "escalate", "rollback", "redeploy",
])


def _signal_imperative(text_lower: str) -> bool:
    """Signal 2: starts with an imperative / request verb."""
    first = text_lower.split()[0] if text_lower.split() else ""
    return first.rstrip(".,!?:") in _IMPERATIVE_WORDS


# ---------------------------------------------------------------------------
# Signal 3 — answerable question
# ---------------------------------------------------------------------------
# Interrogatives ONLY. Auxiliaries (do/is/are/did/can/will…) are NOT here:
# sentence-initial "do" is imperative ("do something") far more often than
# interrogative ("do you…?"), and a genuine auxiliary question reliably ends
# with "?" (caught by the explicit branch below). HAL Council #2723 B2:
# "do" living in BOTH _IMPERATIVE_WORDS and the old _QUESTION_WORDS made
# "do something" score a false question → false ACTIONABLE.
_QUESTION_WORDS = frozenset([
    "what", "where", "when", "who", "why", "how", "which", "whom", "whose",
])


def _signal_question(text_lower: str) -> bool:
    """Signal 3: an answerable question — ends with '?' OR opens with a
    wh-interrogative. Auxiliary-led questions without a '?' are treated as
    ambiguous (favour silence — the issue's not-addressed→silent priority)."""
    stripped = text_lower.strip()
    if stripped.endswith("?"):
        return True
    first = stripped.split()[0] if stripped.split() else ""
    return first.rstrip(".,!?:") in _QUESTION_WORDS


# ---------------------------------------------------------------------------
# Signal 4 — active prior session (thread context)
# ---------------------------------------------------------------------------
def _signal_prior_thread(has_prior_session: bool) -> bool:
    """Signal 4: channel has an active prior Claude session (thread context)."""
    return has_prior_session


# ---------------------------------------------------------------------------
# Confidence computation (3-rung calibration)
# ---------------------------------------------------------------------------
# Rung A: addressed + ≥1 other signal → 0.75–1.00 (always > ACTIONABLE)
# Rung B: not addressed but imperative/question present → 0.25–0.75
# Rung C: grip merely mentioned (not addressed) + no request signals → 0.00–0.25
#
# Key invariant: A > B > C. Rung C is below ACTIONABLE by design.


# ---------------------------------------------------------------------------
# Slack mrkdwn normalisation — signals must see SEMANTIC text, not formatting
# ---------------------------------------------------------------------------
# Origin: 2026-05-16 V>> empirical falsification (3rd in the relay chain).
# A whole-message-italic inbound "_Give me a summary of the day, GRIP._"
# scored "no direct signals" → context → silent: the leading "_" broke the
# imperative match ("_give" ≠ "give") and the trailing "_" (after the ".")
# broke the trailing-vocative anchor. Slack sends emphasis as literal
# delimiters in the raw text. Peel ONLY a matched whole-message wrapper
# (same emphasis char at both ends, iteratively for nested *_…_*); interior
# delimiters are preserved so snake_case / "relay_dispatcher.py" survive.
_EMPHASIS = ("*", "_", "~")


def _strip_mrkdwn(text: str) -> str:
    """Peel matched whole-message Slack mrkdwn emphasis wrappers
    (_italic_ / *bold* / ~strike~ / nested *_combo_*). Conservative: only a
    leading+trailing pair of the SAME emphasis char is removed, so an
    interior "_" (identifiers, file paths) is never touched."""
    s = text.strip()
    while len(s) >= 2 and s[0] in _EMPHASIS and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def compute_confidence(text: str, has_prior_session: bool) -> float:
    """Deterministic confidence from 4 boolean signals (0.25 each)."""
    low = _strip_mrkdwn(text).lower().strip()
    signals = (
        _signal_grip_addressed(low),
        _signal_imperative(low),
        _signal_question(low),
        _signal_prior_thread(has_prior_session),
    )
    return round(sum(signals) / 4.0, 2)


# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------
_THRESHOLD_ACTIONABLE = 0.50    # ≥ 2/4 signals → actionable → reply
_THRESHOLD_CONTEXT = 0.25       # 1/4 signal → ambient context → no reply
# below _THRESHOLD_CONTEXT → not-addressed → silent


def classify(confidence: float, *, addressed: bool, question: bool) -> str:
    """ACTIONABLE requires GRIP actually addressed OR an answerable question
    — a NECESSARY condition, not merely a 0.50 confidence sum.

    HAL Council #2723 B2: a bare imperative in an active thread
    ("do something") scores imperative+prior = 0.50 but is NOT addressed to
    GRIP — it must not auto-reply (the issue's core "not-addressed → silent"
    invariant). The issue's rung-B implicit case is preserved: a question in
    context still replies; only address-less, question-less imperatives are
    demoted to CONTEXT (silent, still notarised)."""
    if confidence >= _THRESHOLD_ACTIONABLE and (addressed or question):
        return CLASSIFICATION_ACTIONABLE
    if confidence >= _THRESHOLD_CONTEXT:
        return CLASSIFICATION_CONTEXT
    return CLASSIFICATION_NOT_ADDRESSED


def _rationale(classification: str, confidence: float, text: str) -> str:
    low = _strip_mrkdwn(text).lower().strip()
    parts = []
    if _signal_grip_addressed(low):
        parts.append("GRIP addressed")
    if _signal_imperative(low):
        parts.append("imperative verb")
    if _signal_question(low):
        parts.append("question form")
    signals_desc = ", ".join(parts) if parts else "no direct signals"
    return f"{classification} ({confidence:.0%} confidence — {signals_desc})"


# ---------------------------------------------------------------------------
# IDR notarisation — happi.md/1.1 mirror, chained to prior telemetry line
# ---------------------------------------------------------------------------
def _read_prior_sha(telemetry_path: Path) -> str:
    """Read the sha256 from the last telemetry line, or return 64 zero hex."""
    try:
        with telemetry_path.open("rb") as fh:
            # Seek backwards to find last non-empty line efficiently
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return "0" * 64
            chunk = min(size, 4096)
            fh.seek(size - chunk)
            data = fh.read(chunk)
        lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        if not lines:
            return "0" * 64
        obj = json.loads(lines[-1])
        # Accept idr_ref.sha256 or top-level sha256
        ref = obj.get("idr_ref") or {}
        return ref.get("sha256") or obj.get("sha256") or "0" * 64
    except Exception:  # noqa: BLE001  file absent / corrupt — chain from zero
        return "0" * 64


def _chain_sha256(prior_sha: str, envelope_raw: bytes, *event_lines: str) -> str:
    """sha256(prior_sha_hex_bytes + envelope_raw + each_event_line_utf8)."""
    h = hashlib.sha256(prior_sha.encode("utf-8"))
    h.update(envelope_raw)
    for line in event_lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def build_idr_ref(
    envelope_id: str,
    envelope_raw: bytes,
    completed_line: str,
    *,
    telemetry_path: Path,
) -> dict:
    """Build the happi.md/1.1 idr_ref object, chained to the prior telemetry line.

    Schema (verbatim from events.py::emit_idr in HAL PR #328):
      {"v":"happi/1.1","id":<envelope_id>,"type":"idr","ts":<ms>,
       "idr_ref":{"sha256":<hex64>,"cid":<null|str>,
                  "model_versions":<list[str]>,"block_anchor":<null|int>}}

    Decision is deterministic (no LLM): model_versions=[], cid=null.
    block_anchor stays None until HAL PR #328 audit-chain replay is wired.
    """
    prior_sha = _read_prior_sha(telemetry_path)
    sha = _chain_sha256(prior_sha, envelope_raw, completed_line)
    return {
        "v": _HAPPI_VERSION,
        "id": envelope_id,
        "type": "idr",
        "ts": int(time.time() * 1000),
        "idr_ref": {
            "sha256": sha,
            "cid": None,
            "model_versions": [],
            "block_anchor": None,
        },
    }


# ---------------------------------------------------------------------------
# Public API — build one Decision per inbound event
# ---------------------------------------------------------------------------
def decide(event: dict, has_prior_session: bool, *, telemetry_path: Path) -> Decision:
    """Return the decision for a single relay event (deterministic, no I/O except prior sha read)."""
    text = event.get("text", "")
    envelope_id = f"relay:{event.get('channel_id', '')}:{event.get('ts', int(time.time()))}"
    low = _strip_mrkdwn(text).lower().strip()
    confidence = compute_confidence(text, has_prior_session)
    classification = classify(
        confidence,
        addressed=_signal_grip_addressed(low),
        question=_signal_question(low),
    )
    posted = classification == CLASSIFICATION_ACTIONABLE
    rationale = _rationale(classification, confidence, text)

    envelope_raw = json.dumps(event, separators=(",", ":")).encode("utf-8")
    completed_line = json.dumps(
        {"type": "completed", "classification": classification,
         "confidence": confidence},
        separators=(",", ":")
    )
    idr_ref = build_idr_ref(
        envelope_id, envelope_raw, completed_line, telemetry_path=telemetry_path
    )

    return Decision(
        envelope_id=envelope_id,
        classification=classification,
        confidence=confidence,
        posted=posted,
        rationale=rationale,
        idr_ref=idr_ref,
    )


# ---------------------------------------------------------------------------
# Selective plain-language projection (operator-DM-only)
# LEAKAGE GUARD: internal-scope only — never send to external/Slack-Connect.
# Emit ONLY on low-confidence OR silent/denied decisions.
# ---------------------------------------------------------------------------
_LOW_CONFIDENCE_THRESHOLD = 0.50  # below this → emit trace


def should_emit_trace(decision: Decision) -> bool:
    """True when operator trace is warranted (not-addressed or low confidence)."""
    return (
        decision.classification == CLASSIFICATION_NOT_ADDRESSED
        or decision.confidence < _LOW_CONFIDENCE_THRESHOLD
    )


def format_trace(decision: Decision) -> str:
    """One-line human-readable trace for the operator.

    INTERNAL ONLY — never post to external parties.
    """
    action_map = {
        CLASSIFICATION_ACTIONABLE: "replied",
        CLASSIFICATION_CONTEXT: "noted (no reply)",
        CLASSIFICATION_NOT_ADDRESSED: "silent — not addressed to GRIP",
    }
    action = action_map.get(decision.classification, decision.classification)
    # Audit back-link = the IDR chain sha256 (NOT envelope_id: that embeds
    # the channel id, which the PARAMOUNT leakage guard forbids in trace
    # text — see test_trace_text_carries_no_channel_or_external_identifier).
    # The sha256 is a hash (zero channel/external identifier) and IS the
    # notarisation key: an auditor resolves it against the IDR/telemetry
    # chain back to the exact source message. Leakage-safe AND traceable
    # (V>> 2026-05-16: an audit trail must link back to its subject).
    idr = decision.idr_ref or {}
    sha = (idr.get("idr_ref") or {}).get("sha256") or ("0" * 64)
    return (
        f"[relay-idr] {action} | "
        f"confidence {decision.confidence:.0%} | "
        f"{decision.rationale} | "
        f"idr {sha}"
    )
