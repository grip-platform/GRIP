#!/usr/bin/env python3
"""
KonoQueryMixin — semantic recall, statistics, session summaries, permanence events.

Extracted from kono_substrate.py (H013 SOLID decomposition, Phase 4).
ISP boundary: recall, get_compact_stats, get_session_summary,
format_session_summary, get_stats, get_permanence_events,
store_session_summary.
"""

from typing import Any, Dict, List, Optional


class KonoQueryMixin:
    """
    Mixin: semantic recall, statistics, session summaries, permanence events.

    ISP boundary: recall, get_compact_stats, get_session_summary,
    format_session_summary, get_stats, get_permanence_events,
    store_session_summary.
    """

    def recall(
        self,
        query: str,
        limit: int = 10,
        dimension: Optional[int] = None,
        min_fitness: float = 0.3,  # decay floor: memories decayed to ~0 are stale (see run_metabolism)
        min_score: float = 0.0,
        expand_spatial: bool = True,
        project_path: Optional[str] = None,
        namespace: Optional[str] = None,
        include_merged: bool = True,
        platform: Optional[str] = None,
        channel_id: Optional[str] = None,
        sender_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Reading memory is not a session: recall must not spawn an auto-
        # instance (fix(kono): 78k empty shells grew because every recall,
        # store, and attest created one).
        accessible_ns = self._resolve_accessible_namespaces(namespace, include_merged)

        filter_grid_x: Optional[int] = None
        if project_path is not None:
            filter_grid_x = self._resolve_project_grid_x(project_path)
            if filter_grid_x is None:
                return []

        hybrid_hits = self._execute_hybrid_search(
            query, dimension, limit, filter_grid_x=filter_grid_x
        )
        if not hybrid_hits:
            return []

        results, seen_ids = self._enrich_and_filter(
            hybrid_hits, accessible_ns, filter_grid_x, min_fitness, dimension,
            platform=platform, channel_id=channel_id, sender_id=sender_id,
        )

        if expand_spatial and results:
            results.extend(self._expand_from_results(
                results[:3], limit, min_fitness, seen_ids,
                filter_grid_x=filter_grid_x,
            ))

        return self._finalize_results(results, limit, min_score=min_score, query=query)

    def get_compact_stats(self) -> str:
        stats = self.get_stats()
        # Default True so a caller supplying its own get_stats (a test double,
        # a subclass) is never mislabelled as uninitialised.
        if not stats.get('initialised', True):
            return "substrate not initialised - no memories stored yet"
        total = stats['embeddings']['total']
        permanent = stats['embeddings']['permanent']
        instances = stats['instances']['total']
        valid = stats['instances']['valid']
        domains = stats['domains']
        return (
            f"{total} memories ({permanent} permanent) | "
            f"{instances} instances ({valid} active) | "
            f"{domains} domains"
        )

    def get_session_summary(self) -> Dict[str, int]:
        return {
            'stored': self._counters.stored,
            'recalled': self._counters.recalled,
            'attested': self._counters.attested,
            'newly_permanent': self._counters.promoted,
        }

    def format_session_summary(self) -> str:
        s = self.get_session_summary()
        parts = []
        if s['stored']:
            parts.append(f"+{s['stored']} stored")
        if s['recalled']:
            parts.append(f"{s['recalled']} recalled")
        if s['attested']:
            parts.append(f"{s['attested']} attested")
        if s['newly_permanent']:
            parts.append(f"{s['newly_permanent']} promoted to permanent")
        return ', '.join(parts) if parts else 'no activity'

    #: Tables get_stats reads. They are created by EmbeddingEngine.init_schema
    #: (lib/embeddings.py) and lib/kono_schema.py -- never by connect(), which
    #: only opens the file and loads extensions.
    _STATS_TABLES = ("embeddings", "kono_instances", "kono_domains", "kono_topics")

    def _missing_stats_tables(self, conn) -> List[str]:
        """Which of the tables get_stats reads are absent from this database."""
        present = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        return [name for name in self._STATS_TABLES if name not in present]

    def get_stats(self) -> Dict[str, Any]:
        conn = self.connect()

        missing = self._missing_stats_tables(conn)
        if missing:
            # A substrate that was never bootstrapped. Report it as UNKNOWN,
            # not as zero: a missing table means nothing has been measured,
            # while an empty table means the measurement ran and found none.
            # The counts below preserve the documented shape so existing
            # callers that index straight into stats['embeddings']['total']
            # keep working instead of raising KeyError.
            return {
                "initialised": False,
                "missing_tables": missing,
                "embeddings": {"total": 0, "permanent": 0, "by_dimension": {}},
                "instances": {"total": 0, "valid": 0, "content_bearing": 0},
                "domains": 0,
                "topics": 0,
            }

        total = list(conn.execute("SELECT COUNT(*) FROM embeddings"))[0][0]
        permanent = list(conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE is_permanent = 1"
        ))[0][0]
        by_dimension = dict(conn.execute(
            "SELECT dimension, COUNT(*) FROM embeddings GROUP BY dimension"
        ))

        instances = list(conn.execute("SELECT COUNT(*) FROM kono_instances"))[0][0]
        valid_instances = list(conn.execute(
            "SELECT COUNT(*) FROM kono_instances WHERE fitness >= 0.8"
        ))[0][0]
        # Content-bearing instances: those linked to at least one attestation.
        # "valid" (fitness >= 0.8) reads as 0 for the 78k session-tracker
        # shells (default fitness 0.5), which looks broken when it is not.
        content_bearing = list(conn.execute(
            "SELECT COUNT(DISTINCT instance_id) FROM kono_attestations"
        ))[0][0]

        domains = list(conn.execute("SELECT COUNT(*) FROM kono_domains"))[0][0]
        topics = list(conn.execute("SELECT COUNT(*) FROM kono_topics"))[0][0]

        return {
            "initialised": True,
            "embeddings": {
                "total": total,
                "permanent": permanent,
                "by_dimension": by_dimension
            },
            "instances": {
                "total": instances,
                "valid": valid_instances,
                "content_bearing": content_bearing
            },
            "domains": domains,
            "topics": topics
        }

    def get_permanence_events(self) -> List[Dict[str, Any]]:
        events = list(self._permanence_events)
        self._permanence_events.clear()
        return events

    def store_session_summary(
        self,
        generation: int,
        instance_id: str,
        message_count: int,
        achievement: str,
        project_path: Optional[str] = None,
    ) -> Optional[int]:
        content = (
            f"[session_summary] Gen {generation} | {instance_id[:8]} | "
            f"{message_count} msgs | {achievement}"
        )
        return self.store_session_memory(
            content=content,
            memory_type="episodic",
            project_path=project_path,
        )
