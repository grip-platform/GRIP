#!/usr/bin/env python3
"""
KonoLifecycleMixin — database connection and instance persistence.

Extracted from kono_substrate.py (ISP decomposition, Phase 3).
"""

import json
from datetime import datetime, timezone
from typing import Optional, List

import apsw
from kono import GRIPInstance, InstanceDNA


class KonoLifecycleMixin:
    """
    Mixin: database connection and instance persistence.

    ISP boundary: connect, init_session, load_instance, save_instance, close.
    All methods rely only on self.conn, self.db_path, self.current_instance,
    self._counters, and self._permanence_events — attributes defined on
    MemorySubstrate.__init__.

    Required collaborator method (provided by the host class, not this mixin):
    self._instance_from_row(row) -> GRIPInstance maps a DB row to a
    GRIPInstance. Defined on MemorySubstrate (kono_substrate.py); the mixin
    calls it from _load_or_create_instance and load_instance.
    """

    def connect(self) -> apsw.Connection:
        """Get or create database connection."""
        if self.conn is not None:
            return self.conn

        import sqlite_vec

        self.conn = apsw.Connection(str(self.db_path))
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.enableloadextension(True)
        self.conn.loadextension(sqlite_vec.loadable_path())

        return self.conn

    def init_session(
        self,
        instance_id: str,
        generation: int,
        project_path: str,
        skills: Optional[List[str]] = None
    ) -> GRIPInstance:
        conn = self.connect()
        self.current_instance = self._load_or_create_instance(
            conn, instance_id, generation, project_path, skills
        )
        self._touch_instance_active(conn, instance_id)
        self._counters.reset()
        self._permanence_events = []
        return self.current_instance

    def _load_or_create_instance(
        self, conn, instance_id: str, generation: int,
        project_path: str, skills: Optional[List[str]]
    ) -> GRIPInstance:
        existing = list(conn.execute(
            "SELECT * FROM kono_instances WHERE id = ?",
            (instance_id,)
        ))
        if existing:
            return self._instance_from_row(existing[0])
        return self._insert_new_instance(conn, instance_id, generation, project_path, skills)

    @staticmethod
    def _insert_new_instance(
        conn, instance_id: str, generation: int,
        project_path: str, skills: Optional[List[str]]
    ) -> GRIPInstance:
        dna = InstanceDNA(
            skills=skills or [],
            project_context=project_path
        )
        instance = GRIPInstance(
            id=instance_id,
            generation=generation,
            dna=dna,
            fitness=0.5
        )
        conn.execute("""
            INSERT INTO kono_instances
            (id, generation, parent_id, skills_json, patterns_json,
             preferences_json, project_context, fitness, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            instance_id,
            generation,
            None,
            json.dumps(dna.skills),
            json.dumps(dna.patterns),
            json.dumps(dna.preferences),
            project_path,
            0.5,
            datetime.now(timezone.utc).isoformat()
        ))
        return instance

    @staticmethod
    def _touch_instance_active(conn, instance_id: str) -> None:
        conn.execute("""
            UPDATE kono_instances
            SET last_active_at = ?
            WHERE id = ?
        """, (datetime.now(timezone.utc).isoformat(), instance_id))

    def load_instance(self, instance_id: str) -> Optional[GRIPInstance]:
        conn = self.connect()
        result = list(conn.execute(
            "SELECT * FROM kono_instances WHERE id = ?",
            (instance_id,)
        ))
        if not result:
            return None
        return self._instance_from_row(result[0])

    def save_instance(self, instance: GRIPInstance) -> None:
        conn = self.connect()
        conn.execute("""
            INSERT OR REPLACE INTO kono_instances
            (id, generation, parent_id, skills_json, patterns_json,
             preferences_json, project_context, fitness, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            instance.id,
            instance.generation,
            instance.parent_id,
            json.dumps(instance.dna.skills),
            json.dumps(instance.dna.patterns),
            json.dumps(instance.dna.preferences),
            instance.dna.project_context,
            instance.fitness,
            instance.created_at.isoformat(),
            instance.last_active_at.isoformat() if instance.last_active_at else None
        ))

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
