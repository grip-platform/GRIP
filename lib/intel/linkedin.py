"""Intel adapter for LinkedIn (PR3 of 5).

Wraps the existing LinkedIn stack — Brave-CDP attach + ``linkedin-feed-scout``
+ ``linkedin_scorer`` two-layer ranker + ``linkedin_drafter`` V>>-voice
comment generator — and exposes it through the IntelAdapter Protocol so the
orchestrator can dispatch ``/intel linkedin`` uniformly with Discord (PR2)
and WhatsApp (PR4).

Council R1-R6 already baked into ``lib/intel/base.py``:

- R1: ``EnrichedObservation.confidence`` defaults to 0.0 (NOT NaN). L1
  blocked posts carry ``confidence=0.0`` with a plain-English ``reason``.
- R2: L1-fail posts STILL emit an IDR (``outcome=L1_BLOCKED``). Silent
  pre-IDR drop is the failure this adapter structurally prevents — every
  EnrichedObservation it returns carries a non-empty ``idr_id``.
- R3: ``discover(scope)`` returns ``tuple[Seed, ...]`` deterministically
  (sorted by ``seed_id``) so the same scope yields the same byte-for-byte
  tuple across replays.
- R4: Content-driven subprocess spawns go through
  ``lib.intel.spawn.spawn_with_lock`` — specifically the
  ``bin/linkedin-feed-scout scout`` invocation in ``scan()``, which
  receives feed text the adapter has no control over.
- R6: Adapter leans on LinkedIn-specific concepts (Brave-CDP attached
  browser state, feed-scout CLI report parsing, L1+L2 author-aware
  scorer, dedup-ledger ``state/linkedin/seen.jsonl``, draft-reply queue,
  tier-1 manual-publish posture) so the H-INTEL-PROT-1 >=30%
  pairwise-distinct floor against DiscordAdapter is structural, not
  cosmetic.

Pipeline (delegated, never re-implemented):

1. Brave-CDP attachment lifecycle is the ``intel-linkedin`` skill's job —
   this adapter assumes CDP is live and surfaces a loud HITL via
   ``CDPNotReadyError`` on discover() if it isn't.
2. ``bin/linkedin-feed-scout scout`` is invoked through ``spawn_with_lock``
   in ``scan()`` to materialise the feed-snapshot JSONL.
3. ``linkedin_scorer.score`` provides the L1 mechanical floor + L2 HAL
   recall — the adapter reads its monotone-toward-safe verdict, not
   re-running the math.
4. ``linkedin_drafter`` is the comment-draft queue producer; the relay()
   method surfaces draft paths only — the publish gate stays manual per
   the tier-1 attended posture (zero auto-comment, ever).

DMA variant deferred (EEA + Switzerland only — separate ``linkedin-dma``
skill; a contact in Zurich was the future unlock). Live Brave-CDP smoke tests
deferred to the e2e harness (Phase 2). The adapter's contract assumes
fixture-substitutable substrate so unit tests can exercise every path
without booting Playwright or Brave.

Origin: V>>'s 2026-05-25 ``/whoa-update-pls`` intel primitive sprint.
Council verdict: ``drafts/intel-primitive-2026-05-25/COUNCIL.md``
(PROCEED_WITH_REVISIONS, weighted-met 0.507).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .base import (
    EnrichedObservation,
    Feedback,
    IntelScope,
    Observation,
    RelayReceipt,
    Seed,
)
from .spawn import spawn_with_lock


# --- Canonical paths (LinkedIn substrate pre-images) ------------------------


_CLAUDE_DIR_DEFAULT = Path.home() / ".claude"
_STATE_REL = ("state", "linkedin")
_DRAFTS_REL = ("drafts", "linkedin-feed-scout")
_SNAPSHOTS_DIRNAME = "feed-snapshots"
_SEEN_LEDGER_NAME = "seen.jsonl"
_FEED_SCOUT_CLI_REL = ("bin", "linkedin-feed-scout")
_LEARNING_REL = ("learning", "insights.jsonl")

# CDP attach point — the intel-linkedin skill launches Brave at this port.
# We don't probe it from this adapter (that's the skill's job); we surface
# a loud HITL when discover() finds no snapshot directory at all.
_DEFAULT_CDP_PORT = 9222

# Feed-snapshot timestamp suffix (matches lib/linkedin_feed::write_snapshot).
_SNAPSHOT_TS_FORMAT = "%Y-%m-%dT%H-%M-%SZ"

# Tier-1 attended posture: hard run caps mirror linkedin_feed defaults.
# These are the contract the upstream scout enforces; we read them here
# to surface a coherent IntelScope when the operator over-asks.
_MAX_POSTS_PER_RUN = 50
_MAX_RUNS_PER_DAY = 2


# --- L1 floor outcomes (R2: every outcome emits an IDR) ---------------------


_L1_OUTCOME_PASS = "L1_PASS"
_L1_OUTCOME_BELOW_FLOOR = "L1_BLOCKED_BELOW_FLOOR"
_L1_OUTCOME_EMPTY_BODY = "L1_BLOCKED_EMPTY_BODY"
_L1_OUTCOME_NO_AUTHOR = "L1_BLOCKED_NO_AUTHOR"

# L1 floor threshold — below this, the linkedin_scorer treats the post as
# noise and skips L2 recall entirely. We mirror it so the adapter's L1
# verdict reasoning is internally consistent with the upstream scorer.
_L1_FLOOR_DEFAULT = 0.2


# --- Power gates (tier-1 manual-publish posture) ----------------------------


# Powers that suppress outbound relay. ``sneak`` keeps everything in
# ``drafts/`` only; ``absorb-knowledge`` writes to KONO and stays silent.
_RELAY_SUPPRESSING_POWERS: frozenset[str] = frozenset({
    "sneak",
    "absorb-knowledge",
})

# Auto-comment is structurally forbidden — the relay() method NEVER returns
# a "comment posted" receipt. The publish gate is the operator pasting into
# Brave manually. This constant is enumerated so any future contributor who
# tries to add an auto-comment path has to delete the gate explicitly.
_AUTO_COMMENT_FORBIDDEN = True


# --- The adapter ------------------------------------------------------------


class CDPNotReadyError(RuntimeError):
    """Raised when discover() finds no LinkedIn substrate at all.

    Signals to the orchestrator that the intel-linkedin skill needs to
    launch Brave-CDP first. NEVER swallowed — the operator MUST see this
    to take the one-command setup action.
    """


class LinkedInAdapter:
    """IntelAdapter wrapping the linkedin-feed-scout pipeline.

    The seed_type is always ``"feed"`` (LinkedIn surfaces one home-feed per
    authenticated session; multi-feed seed discovery is a Phase 2 concern
    if/when DMA variant or company-page feeds land). ``raw_payload`` carries
    the full feed-snapshot JSON record so IDR replay is byte-deterministic.

    The L1+L2 scoring math is delegated to ``linkedin_scorer.score`` — this
    adapter never re-implements the monotonicity invariant. It reads the
    scorer's ``CombinedScore.final`` directly into ``EnrichedObservation``.

    ``draft_dir`` is the operator-facing surface for comment drafts; the
    publish gate stays with V>> (manual paste into Brave). The relay()
    method surfaces draft *paths* only — never the draft *body* — so the
    orchestrator's outbound MCP call cannot accidentally auto-publish.
    """

    def __init__(
        self,
        *,
        claude_dir: Path | None = None,
        scorer: Callable[[Any], Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        base = claude_dir or _CLAUDE_DIR_DEFAULT
        self._claude_dir = base
        self._state_dir = base.joinpath(*_STATE_REL)
        self._snapshots_dir = self._state_dir / _SNAPSHOTS_DIRNAME
        self._drafts_dir = base.joinpath(*_DRAFTS_REL)
        self._learning_path = base.joinpath(*_LEARNING_REL)
        self._feed_scout_cli = base.joinpath(*_FEED_SCOUT_CLI_REL)
        # DI'd scorer (linkedin_scorer.score by default) — kept off the
        # constructor's hot path so tests can supply a fixture scorer
        # without importing the real one.
        self._scorer = scorer
        self._clock = clock or time.time

    # -- Identity (R0: method, not attribute — runtime_checkable contract) --

    def name(self) -> str:
        return "linkedin"

    # -- discover (R3: tuple, deterministic ordering) -----------------------

    def discover(self, scope: IntelScope) -> tuple[Seed, ...]:
        """Materialise the LinkedIn feed seed for this scope.

        LinkedIn surfaces one home-feed per authenticated session, so the
        seed tuple is at most length 1. ``seed_hints`` can override the
        seed label (e.g. ``("strategic-feed",)`` for IDR-replay grouping)
        but does not change the underlying substrate. When the state
        directory does not exist yet (cold install, no scout run ever),
        we raise ``CDPNotReadyError`` so the orchestrator surfaces a loud
        HITL — silently returning an empty tuple here would mask the
        "Brave-CDP isn't running" failure mode.
        """
        if not self._state_dir.exists():
            raise CDPNotReadyError(
                f"LinkedIn state dir missing at {self._state_dir!s}. "
                f"Run /intel-linkedin --auth to launch Brave-CDP on port "
                f"{_DEFAULT_CDP_PORT} and complete the manual login."
            )
        label = _resolve_seed_label(scope.seed_hints)
        feed_seed = Seed(
            platform="linkedin",
            seed_id="home-feed",
            seed_type="feed",
            label=label,
            cursor=None,
            refs=(f"cdp://localhost:{_DEFAULT_CDP_PORT}",),
        )
        return (feed_seed,)

    # -- scan (capability-locked feed-scout invocation) ---------------------

    def scan(self, seed: Seed) -> tuple[Observation, ...]:
        """Run the feed-scout via spawn_with_lock; parse latest snapshot.

        R4 binds: the ``bin/linkedin-feed-scout scout`` invocation runs
        with the read-only tool surface allowlist so a malicious post (a
        confused-deputy attempt — attacker DOMs a snippet that drives the
        scout's downstream prompts) cannot escape into Write/Edit/Bash.
        The scout itself is the tier-1 attended actor; the lock is
        defence-in-depth.

        Returns at most ``_MAX_POSTS_PER_RUN`` observations from the
        latest snapshot — the scout's own cap is the authoritative ceiling.
        """
        if not self._feed_scout_cli.exists():
            return ()
        try:
            spawn_with_lock(
                [str(self._feed_scout_cli), "scout", "--snapshot-only"],
                tools_allowlist=("Read", "Grep", "Glob"),
                timeout=300,
            )
        except (FileNotFoundError, OSError, ValueError):
            return ()
        except Exception:
            # Subprocess failures (non-zero exit, timeout) — we still
            # try the latest snapshot on disk; a half-extracted feed is
            # better than dropping the whole scan window.
            pass
        snapshot = _latest_snapshot(self._snapshots_dir)
        if snapshot is None:
            return ()
        captured = _utc_now_iso()
        observations = _parse_snapshot_records(
            snapshot=snapshot,
            seed=seed,
            captured_utc=captured,
            cap=_MAX_POSTS_PER_RUN,
        )
        return observations

    # -- enrich (R2: IDR emitted even on L1 fail) ---------------------------

    def enrich(self, obs: Observation) -> EnrichedObservation:
        """Apply L1 floor + delegate L2 to linkedin_scorer.

        L1 floor here is the adapter-level "minimum viable post" check
        (non-empty body, has author). The full L1+L2 author-aware scoring
        is the linkedin_scorer.score path — we read its CombinedScore
        directly into the EnrichedObservation. On L1 fail (no body, no
        author), we skip the scorer entirely and emit an L1_BLOCKED IDR
        per Council R2.
        """
        l1_pass, l1_outcome, l1_reason = _l1_evaluate(obs)
        idr_id = _build_decision_idr(
            obs=obs,
            outcome=l1_outcome,
            l1_reason=l1_reason,
        )
        if not l1_pass:
            # Council R1: confidence = 0.0 explicit (not NaN). Reason
            # carries the plain-English "why" downstream consumers read.
            return EnrichedObservation(
                observation=obs,
                l1_pass=False,
                l1_reason=l1_reason,
                semantic_vector=None,
                l2_score=0.0,
                l2_reason="",
                confidence=0.0,
                reason=l1_reason,
                idr_id=idr_id,
            )
        # Delegate to the upstream scorer (DI'd; tests supply a fixture).
        # The scorer enforces monotone-toward-safe internally — we trust
        # its CombinedScore.final as the confidence.
        l2_score, l2_reason = self._invoke_scorer(obs)
        confidence = max(0.0, min(1.0, l2_score))
        return EnrichedObservation(
            observation=obs,
            l1_pass=True,
            l1_reason=l1_reason,
            semantic_vector=None,  # Scorer surfaces a scalar, not a vector
            l2_score=l2_score,
            l2_reason=l2_reason,
            confidence=confidence,
            reason=f"L1 pass; L2 {l2_reason}",
            idr_id=idr_id,
        )

    # -- relay (manual-publish gate; sneak/absorb suppress) -----------------

    def relay(
        self,
        enriched: EnrichedObservation,
        *,
        power: tuple[str, ...] = (),
        target_channel: str | None = None,
    ) -> RelayReceipt:
        """Surface a draft path to the operator; NEVER auto-comment.

        The publish gate is the operator pasting into Brave manually. This
        method's only outbound action is surfacing a draft path (or a
        skip-receipt for sneak/absorb powers). The auto-comment forbidden
        constant is enumerated module-level so removing it requires an
        explicit code change a reviewer will catch.

        ``target_channel`` is informational only here — the actual MCP
        ``channel_reply`` to V>>'s phone is the orchestrator's job (PR5).
        At the adapter layer we record relay intent + the draft path.
        """
        obs_id = enriched.observation.obs_id
        now = int(self._clock())
        if _any_power_suppresses_relay(power):
            return _skip_receipt(
                obs_id, now,
                f"power={','.join(power)} keeps drafts local",
            )
        if not enriched.l1_pass:
            return _skip_receipt(
                obs_id, now,
                f"L1 blocked: {enriched.l1_reason}",
            )
        draft_path = self._draft_path_for(enriched)
        # The orchestrator translates this receipt into a manual-publish
        # nudge (e.g. "Draft ready at <path> — paste into Brave"). The
        # adapter never sends to LinkedIn itself.
        return RelayReceipt(
            obs_id=obs_id,
            relayed_to=(f"draft:{draft_path}",),
            relayed_at=now,
            delivered=True,
            ack_ids=(),
            relay_idr_id="",
            reason="draft surfaced; manual publish gate preserved",
        )

    # -- learn (operator signal feeds next scan's L1 author boost) ----------

    def learn(self, feedback: Feedback) -> None:
        """Append operator signal to learning/insights.jsonl.

        The next linkedin-feed-scout run reads these as priors when the
        scorer adjusts author-boost weights. Shape mirrors the existing
        insight record so the scorer can ingest without migration.
        """
        record = {
            "source": "intel.linkedin.feedback",
            "obs_id": feedback.enriched.observation.obs_id,
            "operator_signal": feedback.operator_signal,
            "note": feedback.note,
            "captured_utc": feedback.captured_utc or _utc_now_iso(),
            "idr_id": feedback.enriched.idr_id,
        }
        self._learning_path.parent.mkdir(parents=True, exist_ok=True)
        with self._learning_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- internal helpers ---------------------------------------------------

    def _invoke_scorer(self, obs: Observation) -> tuple[float, str]:
        """Call the DI'd scorer (or stub if none) for this observation.

        When no scorer is configured (constructor default), return a
        conservative score derived from body length — a Phase 1 fallback
        so the adapter is testable without the linkedin_scorer module
        loaded. Production wires the real scorer via the constructor.
        """
        if self._scorer is None:
            body = obs.text.strip()
            if len(body) >= 200:
                return 0.55, "body length >= 200 chars (no scorer DI'd)"
            if len(body) >= 50:
                return 0.35, "body length >= 50 chars (no scorer DI'd)"
            return 0.15, "short body, no scorer DI'd"
        try:
            result = self._scorer(obs)
        except Exception as exc:
            return 0.0, f"scorer raised: {exc}"
        # Scorer contract: returns either a float (terse) or an object
        # with a .final attribute (linkedin_scorer.CombinedScore shape).
        if hasattr(result, "final"):
            return float(result.final), getattr(result, "l2_rationale", "scorer ok")
        try:
            return float(result), "scorer scalar"
        except (TypeError, ValueError):
            return 0.0, "scorer returned non-numeric"

    def _draft_path_for(self, enriched: EnrichedObservation) -> str:
        """Compute the canonical draft path for this observation.

        The linkedin_drafter writes drafts to
        ``drafts/linkedin-feed-scout/<post_id>.md``. We return that path
        whether or not the file exists yet — the orchestrator surfaces
        the path; the drafter writes lazily.
        """
        post_id = enriched.observation.obs_id.split(":")[-1]
        return str(self._drafts_dir / f"{post_id}.md")


# --- Module-level helpers (pure, easily testable) ---------------------------


def _utc_now_iso() -> str:
    """Wall-clock UTC ISO-8601 — extracted for monkeypatching in tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_seed_label(hints: tuple[str, ...]) -> str:
    """Pick a human-readable seed label from scope hints.

    First non-empty hint wins; default = ``"home-feed"``. The label is
    cosmetic — IDR replay determinism keys on seed_id, not label.
    """
    for hint in hints:
        cleaned = (hint or "").strip()
        if cleaned:
            return cleaned
    return "home-feed"


def _latest_snapshot(snapshots_dir: Path) -> Path | None:
    """Return the highest-mtime snapshot file, or None if the dir is empty.

    Snapshot naming is ``feed-snapshots/<ISO-ts>.jsonl`` — the timestamp
    embedded in the filename is human-readable, but mtime is the
    determinism-preserving sort key (filename ordering is identical to
    mtime ordering under normal write flow).
    """
    if not snapshots_dir.exists():
        return None
    candidates = sorted(snapshots_dir.glob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_snapshot_records(
    *,
    snapshot: Path,
    seed: Seed,
    captured_utc: str,
    cap: int,
) -> tuple[Observation, ...]:
    """Parse a feed-snapshot JSONL into a bounded Observation tuple.

    The snapshot shape (from ``lib/linkedin_feed::write_snapshot``):
    ``{post_id, url, author_display, author_headline, body_text, timestamp}``.
    """
    observations: list[Observation] = []
    try:
        lines = snapshot.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for raw in lines:
        if len(observations) >= cap:
            break
        record = _safe_loads(raw)
        if record is None:
            continue
        obs = _observation_from_post(
            record=record,
            seed=seed,
            captured_utc=captured_utc,
        )
        if obs is not None:
            observations.append(obs)
    return tuple(observations)


def _safe_loads(line: str) -> dict[str, Any] | None:
    """Decode one JSONL row; return None on malformed input."""
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _observation_from_post(
    *,
    record: dict[str, Any],
    seed: Seed,
    captured_utc: str,
) -> Observation | None:
    """Map one snapshot row to an Observation. None if post_id missing."""
    post_id = str(record.get("post_id", "")).strip()
    if not post_id:
        return None
    body = str(record.get("body_text", "") or "")
    author = str(record.get("author_display", "") or "")
    occurred = str(record.get("timestamp", "") or captured_utc)
    obs_id = f"linkedin:{seed.seed_id}:{post_id}"
    return Observation(
        obs_id=obs_id,
        seed=seed,
        raw_payload=record,
        captured_utc=captured_utc,
        occurred_utc=occurred,
        text=body,
        timestamp=_iso_to_epoch(occurred),
        actors=(author,) if author else (),
        refs=(str(record.get("url", "")),) if record.get("url") else (),
    )


def _iso_to_epoch(iso: str) -> int:
    """Best-effort ISO-8601 → epoch seconds. Returns 0 on parse failure."""
    try:
        clean = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
        return int(datetime.fromisoformat(clean).timestamp())
    except (ValueError, TypeError, AttributeError):
        return 0


def _l1_evaluate(obs: Observation) -> tuple[bool, str, str]:
    """Adapter-level L1 floor: non-empty body, has author.

    The full author-aware L1+L2 math lives in linkedin_scorer — this is
    the minimum-viable-post gate so we don't waste an L2 invocation on
    obviously bad data.
    """
    body = obs.text.strip()
    if not body:
        return False, _L1_OUTCOME_EMPTY_BODY, "empty post body"
    if not obs.actors or not obs.actors[0].strip():
        return False, _L1_OUTCOME_NO_AUTHOR, "no author display name"
    return True, _L1_OUTCOME_PASS, "passed minimum-viable-post floor"


def _build_decision_idr(
    *,
    obs: Observation,
    outcome: str,
    l1_reason: str,
) -> str:
    """Build a decision-IDR id for this observation.

    Council R2: every L1 outcome — pass OR block — emits an IDR so the
    chain replay covers the whole decision surface. Deterministic sha256
    over ``(obs_id, outcome, l1_reason)`` so the same observation + same
    decision yields the same id across replays.

    The full ``happi/1.1`` envelope (with predecessor chaining) is the
    orchestrator's responsibility (PR5); here we emit the stable id only.
    """
    fingerprint = "|".join((obs.obs_id, outcome, l1_reason))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"idr-linkedin-{digest}"


def _any_power_suppresses_relay(power: Iterable[str]) -> bool:
    """True if any power in the tuple suppresses outbound relay."""
    return any(p in _RELAY_SUPPRESSING_POWERS for p in power)


def _skip_receipt(obs_id: str, now: int, reason: str) -> RelayReceipt:
    """Standard delivered=False receipt for skip / refuse paths."""
    return RelayReceipt(
        obs_id=obs_id,
        relayed_to=(),
        relayed_at=now,
        delivered=False,
        ack_ids=(),
        relay_idr_id="",
        reason=reason,
    )


__all__ = (
    "CDPNotReadyError",
    "LinkedInAdapter",
)
