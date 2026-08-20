"""Workload definitions and start-node selection.

Start nodes are chosen **once, from the canonical CSVs**, before any database
is touched, and the same list is handed to every platform. Two properties
matter:

*Identical across platforms.* If each database drew its own random start nodes,
one could land on a sparse neighbourhood and look fast for reasons that have
nothing to do with the engine.

*Non-trivial reach.* A node with no 3-hop path makes the 3-hop query return
instantly with an empty result, which measures parsing overhead rather than
traversal. Selecting only nodes that genuinely have depth-3 reachability is
what makes the traversal numbers mean anything -- an early smoke test on this
dataset returned 0 for both 2-hop and 3-hop precisely because the start node
was a leaf.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkloadPlan:
    """Everything the runner needs to drive identical work on every platform."""

    start_nodes: list[int]
    point_lookup_ids: list[int]
    filtered_min_degree: int
    filtered_year: int
    write_pairs: list[tuple[int, int]]
    seed: int

    def to_dict(self) -> dict:
        return {
            "start_node_count": len(self.start_nodes),
            "start_nodes_sample": self.start_nodes[:5],
            "point_lookup_count": len(self.point_lookup_ids),
            "filtered_min_degree": self.filtered_min_degree,
            "filtered_year": self.filtered_year,
            "write_pair_count": len(self.write_pairs),
            "seed": self.seed,
        }


def _adjacency(edges: list[tuple[int, int]]) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = defaultdict(list)
    for src, dst in edges:
        adj[src].append(dst)
    return adj


def _has_depth_three(adj: dict[int, list[int]], node: int) -> bool:
    """True if at least one path of exactly 3 outgoing hops leaves `node`."""
    for a in adj.get(node, ()):
        for b in adj.get(a, ()):
            if adj.get(b):
                return True
    return False


def select_start_nodes(
    edges: list[tuple[int, int]],
    count: int,
    seed: int,
    min_out_degree: int = 2,
) -> list[int]:
    """Deterministically pick start nodes that have real 3-hop reachability."""
    adj = _adjacency(edges)
    candidates = sorted(n for n, outs in adj.items() if len(outs) >= min_out_degree)
    eligible = [n for n in candidates if _has_depth_three(adj, n)]

    if not eligible:
        raise ValueError("no nodes with 3-hop reachability; dataset too sparse")

    rng = random.Random(seed)
    if count >= len(eligible):
        return eligible
    return sorted(rng.sample(eligible, count))


def build_plan(
    nodes: list[dict],
    edges: list[tuple[int, int]],
    start_node_count: int = 100,
    seed: int = 1337,
) -> WorkloadPlan:
    """Derive every workload parameter from the dataset itself."""
    start_nodes = select_start_nodes(edges, start_node_count, seed)

    rng = random.Random(seed + 1)

    # Point lookups hit ids drawn from the whole node set, not just start
    # nodes, so the lookup workload is not biased toward high-degree papers.
    all_ids = [n["paper_id"] for n in nodes]
    point_lookup_ids = rng.sample(all_ids, min(start_node_count, len(all_ids)))

    # Pick the filter constants from the data so the predicate is selective but
    # non-empty. A predicate matching zero rows would make every platform look
    # equally fast at doing nothing.
    years = [n["year"] for n in nodes if n["year"] > 0]
    filtered_year = max(set(years), key=years.count) if years else 0
    degrees = sorted(n["degree"] for n in nodes if n["degree"] > 0)
    filtered_min_degree = degrees[int(len(degrees) * 0.75)] if degrees else 1

    # Write pairs for the mixed workload. Reusing existing node ids avoids
    # paying node-creation cost inside a workload that is meant to measure
    # relationship writes.
    write_pairs = [
        (rng.choice(all_ids), rng.choice(all_ids)) for _ in range(max(1000, start_node_count * 10))
    ]

    return WorkloadPlan(
        start_nodes=start_nodes,
        point_lookup_ids=point_lookup_ids,
        filtered_min_degree=filtered_min_degree,
        filtered_year=filtered_year,
        write_pairs=write_pairs,
        seed=seed,
    )


#: Read workloads driven by the runner. Name -> callable(adapter, plan, index).
READ_WORKLOADS = {
    "1-hop": lambda a, p, i: a.one_hop(p.start_nodes[i % len(p.start_nodes)]),
    "2-hop": lambda a, p, i: a.two_hop(p.start_nodes[i % len(p.start_nodes)]),
    "3-hop": lambda a, p, i: a.three_hop(p.start_nodes[i % len(p.start_nodes)]),
    "point-lookup": lambda a, p, i: a.point_lookup(p.point_lookup_ids[i % len(p.point_lookup_ids)]),
    "filtered-lookup": lambda a, p, i: a.filtered_lookup(p.filtered_min_degree, p.filtered_year),
    "aggregation": lambda a, p, i: a.aggregation(),
}
