"""FalkorDB adapter (Cypher over RESP).

FalkorDB earns its place in the comparison by being architecturally unlike
everything else here: it stores the graph as sparse adjacency matrices and
answers traversals with GraphBLAS linear algebra, inside a Redis process. It
speaks Cypher, so the *queries* stay comparable to Neo4j, CognoDB and Memgraph
while the engine underneath is completely different -- which is exactly the
contrast that makes a benchmark informative rather than a list of Neo4j forks.
"""

from __future__ import annotations

import time

from falkordb import FalkorDB

from .base import GraphAdapter, LoadResult, PlatformSpec

GRAPH_KEY = "benchmark"

Q_ONE_HOP = "MATCH (p:Paper {paper_id:$id})-->(a) RETURN count(DISTINCT a)"
Q_TWO_HOP = "MATCH (p:Paper {paper_id:$id})-->()-->(b) RETURN count(DISTINCT b)"
Q_THREE_HOP = "MATCH (p:Paper {paper_id:$id})-->()-->()-->(c) RETURN count(DISTINCT c)"
Q_POINT = "MATCH (p:Paper {paper_id:$id}) RETURN count(p)"
Q_FILTERED = "MATCH (p:Paper) WHERE p.degree >= $min_degree AND p.year = $year RETURN count(p)"
Q_AGGREGATION = "MATCH (p:Paper) RETURN p.year, count(*)"
Q_INSERT = (
    "MATCH (a:Paper {paper_id:$src}), (b:Paper {paper_id:$dst}) "
    "CREATE (a)-[:CITES {benchmark:true}]->(b)"
)
Q_DELETE = (
    "MATCH (:Paper {paper_id:$src})-[r:CITES {benchmark:true}]->(:Paper {paper_id:$dst}) "
    "DELETE r"
)
Q_NODE_COUNT = "MATCH (n:Paper) RETURN count(n)"
Q_REL_COUNT = "MATCH ()-[r:CITES]->() RETURN count(r)"

LOAD_NODES = (
    "UNWIND $rows AS row "
    "CREATE (n:Paper {paper_id: row[0], year: row[1], degree: row[2]})"
)
LOAD_EDGES = (
    "UNWIND $rows AS row "
    "MATCH (a:Paper {paper_id: row[0]}) "
    "MATCH (b:Paper {paper_id: row[1]}) "
    "CREATE (a)-[:CITES]->(b)"
)


class FalkorAdapter(GraphAdapter):
    query_language = "Cypher (RESP/GraphBLAS)"

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = config["name"]
        self._db = None
        self._graph = None

    # ---------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        if self._graph is not None:
            return
        self._db = FalkorDB(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 6379)),
            password=self.config.get("password") or None,
        )
        self._graph = self._db.select_graph(self.config.get("graph", GRAPH_KEY))
        self._graph.query("RETURN 1")
        self._connected = True

    def close(self) -> None:
        self._graph = None
        self._db = None
        self._connected = False

    def _q(self, query: str, params: dict | None = None):
        return self._graph.query(query, params=params).result_set

    def _scalar(self, query: str, params: dict | None = None) -> int:
        rows = self._q(query, params)
        return rows[0][0] if rows and rows[0] else 0

    def reset(self) -> None:
        try:
            self._graph.delete()
        except Exception:
            # A graph key that does not exist yet is not an error condition.
            pass
        self._graph = self._db.select_graph(self.config.get("graph", GRAPH_KEY))

    def create_schema(self) -> list[str]:
        for prop in ("paper_id", "year", "degree"):
            try:
                self._graph.query(f"CREATE INDEX FOR (n:Paper) ON (n.{prop})")
            except Exception as exc:
                if "already indexed" not in str(exc).lower():
                    raise
        return ["Paper.paper_id", "Paper.year", "Paper.degree"]

    # ---------------------------------------------------------------- loading

    def load(self, nodes, edges, batch_size: int) -> LoadResult:
        # Rows are passed as positional arrays rather than maps: FalkorDB's
        # parameter serialisation handles flat arrays considerably faster than
        # nested maps, and the resulting CREATE is identical.
        t0 = time.perf_counter()
        for i in range(0, len(nodes), batch_size):
            rows = [[n["paper_id"], n["year"], n["degree"]] for n in nodes[i : i + batch_size]]
            self._q(LOAD_NODES, {"rows": rows})
        t1 = time.perf_counter()

        for i in range(0, len(edges), batch_size):
            rows = [[s, d] for s, d in edges[i : i + batch_size]]
            self._q(LOAD_EDGES, {"rows": rows})
        t2 = time.perf_counter()

        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method=f"falkordb client, UNWIND batching (batch={batch_size})",
        )

    # ---------------------------------------------------------------- workloads

    def one_hop(self, start_id: int) -> int:
        return self._scalar(Q_ONE_HOP, {"id": start_id})

    def two_hop(self, start_id: int) -> int:
        return self._scalar(Q_TWO_HOP, {"id": start_id})

    def three_hop(self, start_id: int) -> int:
        return self._scalar(Q_THREE_HOP, {"id": start_id})

    def point_lookup(self, paper_id: int) -> int:
        return self._scalar(Q_POINT, {"id": paper_id})

    def filtered_lookup(self, min_degree: int, year: int) -> int:
        return self._scalar(Q_FILTERED, {"min_degree": min_degree, "year": year})

    def aggregation(self) -> int:
        return len(self._q(Q_AGGREGATION))

    def insert_edge(self, src_id: int, dst_id: int) -> None:
        self._q(Q_INSERT, {"src": src_id, "dst": dst_id})

    def delete_edge(self, src_id: int, dst_id: int) -> None:
        self._q(Q_DELETE, {"src": src_id, "dst": dst_id})

    # ---------------------------------------------------------------- inspection

    def node_count(self) -> int:
        return self._scalar(Q_NODE_COUNT)

    def relationship_count(self) -> int:
        return self._scalar(Q_REL_COUNT)

    def spec(self) -> PlatformSpec:
        cfg = self.config.get("spec", {})
        return PlatformSpec(
            vcpu=cfg.get("vcpu", "unknown"),
            ram=cfg.get("ram", "unknown"),
            storage=cfg.get("storage", "unknown"),
            tier=cfg.get("tier", "unknown"),
            region=cfg.get("region", "unknown"),
            notes=cfg.get("notes", ""),
            observable=self._observable(),
        )

    def _observable(self) -> dict:
        """Being Redis-backed, FalkorDB reports its own memory directly."""
        try:
            info = self._db.connection.info("memory")
            return {
                "used_memory_bytes": info.get("used_memory"),
                "used_memory_human": info.get("used_memory_human"),
                "used_memory_peak_bytes": info.get("used_memory_peak"),
            }
        except Exception as exc:
            return {"status": f"not observable ({type(exc).__name__})"}
