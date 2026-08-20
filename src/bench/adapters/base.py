"""Adapter interface every platform must implement.

The interface is deliberately narrow: each method is one *logical* workload,
and each adapter expresses that workload in its own query language. That is how
"same logical queries everywhere" is enforced structurally rather than by
convention -- there is no way to give one platform an easier question.

Every read workload returns a **count**, not rows. Two reasons:

1. It keeps result-set serialisation out of the measurement, so we time the
   database rather than the driver's row marshalling.
2. Counts are directly comparable across platforms. The harness asserts that
   every database returns the same count for the same start node; a mismatch
   means the queries are not equivalent and the comparison is invalid. This
   parity check is the main defence against accidentally benchmarking two
   different questions.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class LoadResult:
    """Outcome of loading the dataset into one platform."""

    nodes_loaded: int
    relationships_loaded: int
    node_seconds: float
    relationship_seconds: float
    total_seconds: float
    method: str

    @property
    def nodes_per_second(self) -> float:
        return self.nodes_loaded / self.node_seconds if self.node_seconds > 0 else 0.0

    @property
    def relationships_per_second(self) -> float:
        return (
            self.relationships_loaded / self.relationship_seconds
            if self.relationship_seconds > 0
            else 0.0
        )

    def to_dict(self) -> dict:
        return {
            "nodes_loaded": self.nodes_loaded,
            "relationships_loaded": self.relationships_loaded,
            "node_seconds": round(self.node_seconds, 3),
            "relationship_seconds": round(self.relationship_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "nodes_per_second": round(self.nodes_per_second, 1),
            "relationships_per_second": round(self.relationships_per_second, 1),
            "method": self.method,
        }


@dataclass
class PlatformSpec:
    """Advertised resources for a platform, recorded verbatim in the README.

    `observable` distinguishes numbers the platform actually reports from the
    tier's advertised figures. Section 5.2 requires saying "not observable"
    where a footprint cannot be measured, so unmeasurable fields stay None.
    """

    vcpu: str
    ram: str
    storage: str
    tier: str
    region: str
    notes: str = ""
    observable: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "vcpu": self.vcpu,
            "ram": self.ram,
            "storage": self.storage,
            "tier": self.tier,
            "region": self.region,
            "notes": self.notes,
            "observable": self.observable or {"status": "not observable"},
        }


class GraphAdapter(ABC):
    """One managed graph database under test."""

    #: Human-readable platform name used in result tables.
    name: str = "unnamed"
    #: Query language actually executed, reported so language differences are visible.
    query_language: str = "unknown"

    def __init__(self, config: dict):
        self.config = config
        self._connected = False

    # ---------------------------------------------------------------- lifecycle

    @abstractmethod
    def connect(self) -> None:
        """Open a connection/pool. Must be idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Release all resources."""

    @abstractmethod
    def reset(self) -> None:
        """Drop all data so a load starts from an empty database."""

    @abstractmethod
    def create_schema(self) -> list[str]:
        """Create indexes. Returns a description of what was indexed.

        Section 5.2 requires stating which properties are indexed on each
        platform, so this returns the list rather than logging it.
        """

    @abstractmethod
    def load(self, nodes: list[dict], edges: list[tuple[int, int]], batch_size: int) -> LoadResult:
        """Bulk-load the canonical dataset."""

    # ---------------------------------------------------------------- workloads

    @abstractmethod
    def one_hop(self, start_id: int) -> int:
        """Distinct nodes exactly 1 outgoing hop from `start_id`."""

    @abstractmethod
    def two_hop(self, start_id: int) -> int:
        """Distinct nodes exactly 2 outgoing hops from `start_id`."""

    @abstractmethod
    def three_hop(self, start_id: int) -> int:
        """Distinct nodes exactly 3 outgoing hops from `start_id`."""

    @abstractmethod
    def point_lookup(self, paper_id: int) -> int:
        """Fetch a single node by its indexed primary property. Returns 1 or 0."""

    @abstractmethod
    def filtered_lookup(self, min_degree: int, year: int) -> int:
        """Count nodes matching an indexed range predicate plus an equality filter."""

    @abstractmethod
    def aggregation(self) -> int:
        """Group nodes by year and count. Returns the number of groups."""

    @abstractmethod
    def insert_edge(self, src_id: int, dst_id: int) -> None:
        """Write path for the mixed workload."""

    @abstractmethod
    def delete_edge(self, src_id: int, dst_id: int) -> None:
        """Undo `insert_edge` so the mixed workload does not grow the graph without bound."""

    # ---------------------------------------------------------------- inspection

    @abstractmethod
    def node_count(self) -> int:
        """Total nodes, used to verify the load landed completely."""

    @abstractmethod
    def relationship_count(self) -> int:
        """Total relationships, used to verify the load landed completely."""

    @abstractmethod
    def spec(self) -> PlatformSpec:
        """Advertised tier resources plus whatever footprint the platform exposes."""

    def sample_start_nodes(self, candidates: list[int], count: int, seed: int) -> list[int]:
        """Choose traversal start nodes.

        Uses the same seed for every platform so all databases traverse from an
        identical node set -- otherwise one platform could draw an easier
        neighbourhood than another.
        """
        import random

        rng = random.Random(seed)
        if count >= len(candidates):
            return list(candidates)
        return rng.sample(candidates, count)

    def warmup(self, start_nodes: list[int], iterations: int) -> None:
        """Touch every workload before measurement so caches are populated."""
        for i in range(iterations):
            node = start_nodes[i % len(start_nodes)]
            self.one_hop(node)
            self.two_hop(node)
            self.point_lookup(node)
        self.aggregation()

    @contextmanager
    def timed(self):
        """Yield a one-element list that receives elapsed milliseconds."""
        holder: list[float] = []
        start = time.perf_counter()
        try:
            yield holder
        finally:
            holder.append((time.perf_counter() - start) * 1000.0)
