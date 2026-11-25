"""Deterministic personalised PageRank on the directed two-hop neighbourhood."""
from __future__ import annotations


def _neighbourhood(adjacency, seeds):
    nodes, frontier = set(seeds), set(seeds)
    for _ in range(2):
        reached = {target for node in sorted(frontier) for target in adjacency.get(node, ())}
        frontier = reached - nodes
        nodes.update(reached)
    return sorted(nodes)


def _step(nodes, adjacency, scores, teleport, alpha):
    dangling = sum(scores[node] for node in nodes if not adjacency[node])
    updated = {node: (1 - alpha + alpha * dangling) * teleport[node] for node in nodes}
    for node in nodes:
        targets = adjacency[node]
        if targets:
            _spread(updated, targets, alpha * scores[node] / len(targets))
    return updated


def _spread(updated, targets, share):
    """Add one node's outgoing share to each target; total work per step is O(edges)."""
    for target in targets:
        updated[target] += share


def personalised_pagerank(edges, seeds, *, alpha=0.85, iterations=15) -> list[tuple[str, float]]:
    if not 0 <= alpha <= 1 or iterations < 0:
        raise ValueError("alpha must be in [0,1] and iterations nonnegative")
    seeds = sorted(set(seeds))
    if not seeds:
        return []
    adjacency = {}
    for source, target in sorted(set(edges)):
        adjacency.setdefault(source, []).append(target)
    nodes = _neighbourhood(adjacency, seeds)
    node_set = set(nodes)
    adjacency = {node: [t for t in adjacency.get(node, ()) if t in node_set] for node in nodes}
    teleport = {node: (1 / len(seeds) if node in seeds else 0.0) for node in nodes}
    scores = teleport.copy()
    for _ in range(iterations):
        scores = _step(nodes, adjacency, scores, teleport, alpha)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
