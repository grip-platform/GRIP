#!/usr/bin/env python3
"""
KonoEvolutionMixin — attestation, consensus, metabolism, evolution, consolidation.

Extracted from kono_substrate.py (H013 SOLID decomposition, Phase 4).
ISP boundary: attest, attest_recalled, mine, consensus,
run_metabolism, evolve, consolidate (7 methods).
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

from kono import GRIPInstance, MemoryBlock, Position4D

logger = logging.getLogger(__name__)


class KonoEvolutionMixin:
    """
    Mixin: attestation, consensus, metabolism, evolution, consolidation.

    ISP boundary: attest, attest_recalled, mine, consensus,
    run_metabolism, evolve, consolidate (7 methods).
    """

    def attest(self, embedding_id: int) -> bool:
        if not self.current_instance:
            return False

        conn = self.connect()

        existing = list(conn.execute("""
            SELECT id FROM kono_attestations
            WHERE embedding_id = ? AND instance_id = ?
        """, (embedding_id, self.current_instance.id)))

        if existing:
            return False

        conn.execute("""
            INSERT INTO kono_attestations
            (embedding_id, instance_id, fitness_at_attestation)
            VALUES (?, ?, ?)
        """, (embedding_id, self.current_instance.id, self.current_instance.fitness))

        self._counters.attested += 1
        return True

    def attest_recalled(self, embedding_id: int) -> bool:
        # Deliberately does NOT call _ensure_instance(). Every caller of this
        # method is a READ path — recall() via _finalize_results, the recall
        # worker hook, and focus_cards — and reading memory is not a session.
        # Creating an instance here is how 78k empty auto- shells accumulated:
        # recall()'s own body was clean, so the invariant looked satisfied
        # while the shell was created one level down, right here.
        #
        # Access tracking is unaffected: the UPDATE below fires the
        # kono_track_access trigger (lib/kono_schema.py), which bumps
        # access_count and fitness_score with no instance involved. When a real
        # session IS active, the attestation is still recorded below.
        conn = self.connect()

        conn.execute("""
            UPDATE embeddings
            SET last_accessed_at = ?
            WHERE id = ?
        """, (datetime.now(timezone.utc).isoformat(), embedding_id))

        # Read accounting lives HERE, not in the recall() wrapper: all three
        # callers — recall() via _finalize_results, the recall worker hook,
        # and focus_cards — reach attest_recalled directly, and only here can
        # a read be guaranteed to move the counter no matter which path
        # carried it. _finalize_results no longer bumps _counters.recalled,
        # so this is the single choke point (no double count on recall()).
        self._counters.recalled += 1

        # Only count an attestation when one was actually written. Before the
        # _ensure_instance call was removed above, this else-branch was reached
        # only when instance creation had FAILED, so the counter and the
        # kono_attestations table stayed consistent. Without that call, a
        # sessionless read is the NORMAL path, and incrementing here would
        # report every plain read as an attestation while writing no row —
        # the same false-claim class this change set removes from the UI, and
        # it would corrupt exactly the kind of audit that found the 78k shells.
        # Reads are counted separately, by _counters.recalled.
        if self.current_instance:
            self.attest(embedding_id)

        return True

    def mine(
        self,
        content: str,
        embed_type: str,
        embedding_id: int,
        project_path: Optional[str] = None,
        provenance: Optional[dict] = None,
    ) -> MemoryBlock:
        memory_type, dimension = self.classify_memory(content, embed_type)

        x = self.get_or_create_domain(project_path or "")
        y = self.get_or_create_topic(embed_type)

        position = Position4D(
            x=float(x),
            y=float(y),
            z=5.0,
            t=time.time()
        )

        instance_id = self.current_instance.id if self.current_instance else None
        self.store(embedding_id, position, dimension, instance_id)

        self._write_mine_provenance(embedding_id, provenance)

        block = MemoryBlock(
            id=f"block_{embedding_id}",
            position=position,
            chunks=[embedding_id],
            dimension=dimension
        )

        if self.current_instance:
            block.add_attestation(self.current_instance.id, self.current_instance.fitness)
            self.attest(embedding_id)

        return block

    def _write_mine_provenance(self, embedding_id: int, provenance: Optional[dict]) -> None:
        if not (provenance and self.engine and self.engine.conn):
            return
        self.engine.conn.execute(
            "UPDATE embeddings SET source_platform=?, source_channel_id=?, "
            "source_sender_id=?, source_interaction_id=? WHERE id=?",
            (provenance.get('platform'), provenance.get('channel_id'),
             provenance.get('sender_id'), provenance.get('interaction_id'),
             embedding_id))

    def consensus(self, min_attestations: int = 3) -> List[int]:
        conn = self.connect()

        results = list(conn.execute("""
            SELECT id FROM embeddings
            WHERE attestation_count >= ?
              AND is_permanent = 1
        """, (min_attestations,)))

        return [r[0] for r in results]

    # Elapsed whole days since this row last had decay applied. Written once
    # and interpolated wherever needed so the SET, the ledger advance and the
    # WHERE guard can never disagree about how much time is being charged.
    _ELAPSED_DAYS_SQL = (
        "CAST(julianday(:now) - julianday(last_decayed_at) AS INTEGER)"
    )

    # Rows holding STANDING BEHAVIOURAL INSTRUCTIONS rather than observations.
    # Each entry is (required_column, predicate); a predicate is only used when
    # its column exists, because the base embeddings table is not owned by KONO
    # and older or test databases may not carry it.
    _INSTRUCTION_PREDICATES = (
        ("priority", "priority = 'system'"),
        ("type", "type LIKE 'concept\\_%' ESCAPE '\\'"),
    )

    @staticmethod
    def _embedding_columns(conn) -> set:
        """Column names present on the embeddings table."""
        return {row[1] for row in conn.execute("PRAGMA table_info(embeddings)")}

    @classmethod
    def _decay_exemption_sql(cls, columns: set) -> str:
        """AND-clause excluding standing instructions from decay.

        Instructions are NORMATIVE, not evidential. An episodic memory really
        does get less useful as it ages, which is what a decay curve models.
        A directive stays in force until it is revoked, and nothing about the
        passage of time revokes it -- so decaying one is not forgetting a
        stale fact, it is silently dropping a rule while reporting success.

        Measured on the live database before this change: 621 of 677 rows in
        this class (91.7%) had fallen below the 0.3 recall floor, including
        501 of 530 skill-matching concepts. GRIP had decayed the vocabulary
        it uses to route work, invisibly.

        Derived from columns that already carry the distinction, so no
        backfill is required to make it true.
        """
        predicates = [sql for col, sql in cls._INSTRUCTION_PREDICATES
                      if col in columns]
        if not predicates:
            return ""
        return "AND NOT (" + " OR ".join(predicates) + ")"

    def _init_decay_ledger(self, conn, now: str) -> None:
        """Stamp rows that have never had decay accounted for.

        Fitness is deliberately untouched. Before this ledger existed decay
        was applied per call with no record of how much had already been
        charged, so for an unstamped row that history is simply unavailable.
        Charging it for every day since creation would be inventing a number;
        starting the clock now is the only honest option.
        """
        conn.execute(
            "UPDATE embeddings SET last_decayed_at = :now "
            "WHERE last_decayed_at IS NULL",
            {"now": now},
        )

    def run_metabolism(self) -> int:
        """Apply time-based fitness decay. Idempotent within a day.

        Decay is charged per elapsed DAY, which is what kono_schema documents
        ("Fitness multiplied by this per day"). It previously applied once per
        CALL with no reference to elapsed time at all, so ten calls in one day
        compounded to rate**10 and rows with no last_accessed_at decayed on
        every single call regardless of age.

        Because the ledger advances by exactly the whole days it charges, the
        unconsumed remainder carries forward: a second call the same day
        matches no rows and changes nothing, and N days of decay is rate**N
        however the calls are distributed.

        Returns the number of rows decayed.
        """
        conn = self.connect()
        columns = self._embedding_columns(conn)

        if "last_decayed_at" not in columns:
            # No ledger means no way to know what has already been charged.
            # Refuse loudly rather than fall back to the per-call behaviour
            # this method exists to replace.
            logger.warning(
                "kono: embeddings.last_decayed_at is missing; skipping decay. "
                "Run scripts/kono_decay_migration.py to add it."
            )
            return 0

        config = self._get_decay_config()
        now = datetime.now(timezone.utc).isoformat()

        self._init_decay_ledger(conn, now)

        days = self._ELAPSED_DAYS_SQL
        conn.execute(f"""
            UPDATE embeddings
            SET fitness_score = MIN(1.0, MAX(0.0,
                    fitness_score * pow(
                        :rate * (1 + MIN(:cap, :boost * access_count)),
                        {days}
                    )
                )),
                last_decayed_at = datetime(
                    last_decayed_at, '+' || {days} || ' days'
                )
            WHERE is_permanent = 0
              AND {days} >= 1
              AND (last_accessed_at IS NULL
                   OR julianday(:now) - julianday(last_accessed_at) > 1)
              {self._decay_exemption_sql(columns)}
        """, {
            "now": now,
            "rate": config.get("time_decay_rate", 0.95),
            "cap": config.get("access_boost_cap", 2.0),
            "boost": config.get("access_boost_rate", 0.1),
        })

        result = list(conn.execute("SELECT changes()"))
        return result[0][0] if result else 0

    def evolve(self, generations: int = 1) -> List[GRIPInstance]:
        conn = self.connect()
        config = self._get_decay_config()

        validity_threshold = config.get("fitness_validity_threshold", 0.8)
        survivors = []

        instances = list(conn.execute("""
            SELECT id, generation, parent_id, skills_json, patterns_json,
                   preferences_json, project_context, fitness,
                   created_at, last_active_at
            FROM kono_instances
            ORDER BY fitness DESC
        """))

        for row in instances:
            inst = self._instance_from_row(row)

            if row[9]:
                last_active = datetime.fromisoformat(row[9])
                days = (datetime.now(timezone.utc) - last_active).total_seconds() / 86400
                inst.metabolize(days * generations)

            if inst.fitness >= validity_threshold:
                survivors.append(inst)

            conn.execute("""
                UPDATE kono_instances SET fitness = ? WHERE id = ?
            """, (inst.fitness, inst.id))

        return survivors

    def consolidate(self) -> int:
        conn = self.connect()
        config = self._get_decay_config()

        threshold = int(config.get("permanence_threshold", 3))

        conn.execute("""
            UPDATE embeddings
            SET is_permanent = 1
            WHERE attestation_count >= ?
              AND is_permanent = 0
        """, (threshold,))

        result = list(conn.execute("SELECT changes()"))
        newly = result[0][0] if result else 0

        self._counters.promoted += newly
        if newly > 0:
            self._permanence_events.append({
                'count': newly,
                'timestamp': time.time()
            })

        return newly
