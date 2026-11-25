"""Copilot graph pillar — relationship and dependency mapping.

GraphEngine reads the knowledge log and entity graph state to emit
Cytoscape JSON (nodes + edges arrays). The output is suitable for
rendering in a Cytoscape.js or grip-apps graph widget.

Falsifier: if build_graph() returns a dict without both 'nodes' and
'edges' keys, the Cytoscape contract is broken.

Schema:
    node: {"data": {"id": str, "label": str, "type": str}}
    edge: {"data": {"id": str, "source": str, "target": str, "label": str}}

CLI:
    python3 -m lib.aide.graph [--pretty]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from lib.aide import jsonl_io, paths


def _node(node_id: str, label: str, node_type: str) -> Dict[str, Any]:
    return {"data": {"id": node_id, "label": label[:80], "type": node_type}}


def _edge(source: str, target: str, label: str) -> Dict[str, Any]:
    return {"data": {"id": f"{source}__{target}__{label}",
                     "source": source, "target": target, "label": label}}


class GraphEngine:
    """Build a Cytoscape JSON graph from copilot state.

    Sources (all fail-safe — missing files produce empty partials):
    * ``paths.KNOWLEDGE_LOG`` — knowledge records; actors become person nodes
    * ``paths.ENTITY_GRAPH``  — explicit entity snapshot (optional)
    * ``paths.PREDICTIONS``   — pending predictions become prediction nodes
    """

    def __init__(self) -> None:
        paths.ensure_state_dir()

    def build_graph(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return Cytoscape JSON: ``{"nodes": [...], "edges": [...]}``.

        Always returns both keys even when all sources are empty.
        """
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        self._ingest_entity_snapshot(nodes, edges)
        self._ingest_knowledge_log(nodes, edges)
        self._ingest_predictions(nodes)
        return {"nodes": list(nodes.values()), "edges": edges}

    def _ingest_entity_snapshot(
        self, nodes: Dict[str, Dict[str, Any]], edges: List[Dict[str, Any]]
    ) -> None:
        raw = jsonl_io.read_json(paths.ENTITY_GRAPH, default={})
        for n in raw.get("nodes", []):
            nid = n.get("id", "")
            if nid:
                nodes[nid] = _node(nid, n.get("label", nid), n.get("type", "project"))
        for e in raw.get("edges", []):
            src, tgt = e.get("source", ""), e.get("target", "")
            if src and tgt:
                edges.append(_edge(src, tgt, e.get("label", "related")))

    def _ingest_knowledge_log(
        self, nodes: Dict[str, Dict[str, Any]], edges: List[Dict[str, Any]]
    ) -> None:
        for rec in jsonl_io.stream(paths.KNOWLEDGE_LOG):
            rid = rec.get("record_id", "")
            if not rid:
                continue
            text = (rec.get("text") or rec.get("summary") or "")[:80]
            source = rec.get("source", "unknown")
            k_id = f"knowledge:{rid}"
            nodes[k_id] = _node(k_id, text or rid, "knowledge")
            src_id = f"person:{source}"
            if src_id not in nodes:
                nodes[src_id] = _node(src_id, source, "person")
            edges.append(_edge(src_id, k_id, "contributed"))
            for actor in rec.get("actors", []):
                a_id = f"person:{actor}"
                if a_id not in nodes:
                    nodes[a_id] = _node(a_id, actor, "person")
                edges.append(_edge(a_id, k_id, "involved_in"))

    def _ingest_predictions(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        for rec in jsonl_io.stream(paths.PREDICTIONS):
            if rec.get("outcome") is not None:
                continue
            pid = rec.get("pid", "")
            if pid:
                nodes[f"prediction:{pid}"] = _node(
                    f"prediction:{pid}", rec.get("claim", pid)[:80], "prediction"
                )


def build_graph() -> Dict[str, List[Dict[str, Any]]]:
    """Return Cytoscape JSON from default state paths."""
    return GraphEngine().build_graph()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Copilot graph — emit Cytoscape JSON")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(build_graph(), indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
