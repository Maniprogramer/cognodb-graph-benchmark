"""ArangoDB adapter (AQL).

ArangoDB is in the comparison precisely because it is *not* a Cypher engine: it
is a multi-model database with a document store underneath and its own
traversal syntax. Including only Bolt/Cypher platforms would have made the
benchmark a comparison of Neo4j implementations rather than of graph databases.

The trade-off is that "the same logical query" has to be established by hand.
Each query below is annotated with the Cypher it mirrors, and the harness's
result-parity check verifies empirically that both return the same counts.
"""

from __future__ import annotations

import time

from arango import ArangoClient

from .base import GraphAdapter, LoadResult, PlatformSpec

NODE_COLLECTION = "papers"
EDGE_COLLECTION = "cites"
GRAPH_NAME = "citations"

# Mirrors: MATCH (p:Paper {paper_id:$id})-->(a) RETURN count(DISTINCT a)
# uniqueVertices is left at its default so AQL enumerates paths exactly as a
# Cypher fixed-length pattern does; DISTINCT then dedupes the endpoints.
Q_HOP = """
RETURN LENGTH(
  FOR v IN {depth}..{depth} OUTBOUND @start {edge}
    RETURN DISTINCT v._key
)
"""

Q_POINT = """
RETURN LENGTH(FOR d IN {node} FILTER d.paper_id == @id RETURN 1)
"""

Q_FILTERED = """
RETURN LENGTH(
  FOR d IN {node} FILTER d.degree >= @min_degree AND d.year == @year RETURN 1
)
"""

Q_AGGREGATION = """
RETURN LENGTH(
  FOR d IN {node} COLLECT y = d.year WITH COUNT INTO n RETURN y
)
"""

Q_INSERT = """
INSERT {{ _from: @from, _to: @to, benchmark: true }} INTO {edge}
"""

Q_DELETE_ALL_BENCHMARK = """
RETURN LENGTH(
  FOR e IN {edge} FILTER e.benchmark == true LIMIT @limit
    REMOVE e IN {edge} RETURN 1
)
"""

Q_DELETE = """
FOR e IN {edge}
  FILTER e._from == @from AND e._to == @to AND e.benchmark == true
  LIMIT 1
  REMOVE e IN {edge}
"""


class ArangoAdapter(GraphAdapter):
    query_language = "AQL"

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = config["name"]
        self._client = None
        self._db = None

    # ---------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        if self._db is not None:
            return
        self._client = ArangoClient(
            hosts=self.config["uri"],
            request_timeout=float(self.config.get("query_timeout", 60)),
        )
        sys_db = self._client.db(
            "_system", username=self.config["user"], password=self.config["password"]
        )
        db_name = self.config.get("database", "benchmark")
        if not sys_db.has_database(db_name):
            sys_db.create_database(db_name)
        self._db = self._client.db(
            db_name, username=self.config["user"], password=self.config["password"]
        )
        self._connected = True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            self._connected = False

    def _aql(self, query: str, **bind):
        cursor = self._db.aql.execute(query, bind_vars=bind or None)
        return list(cursor)

    def reset(self) -> None:
        if self._db.has_graph(GRAPH_NAME):
            self._db.delete_graph(GRAPH_NAME, drop_collections=True)
        for name in (EDGE_COLLECTION, NODE_COLLECTION):
            if self._db.has_collection(name):
                self._db.delete_collection(name)

    def create_schema(self) -> list[str]:
        graph = self._db.create_graph(GRAPH_NAME)
        graph.create_edge_definition(
            edge_collection=EDGE_COLLECTION,
            from_vertex_collections=[NODE_COLLECTION],
            to_vertex_collections=[NODE_COLLECTION],
        )
        papers = self._db.collection(NODE_COLLECTION)
        papers.add_persistent_index(fields=["paper_id"], name="paper_id_idx")
        papers.add_persistent_index(fields=["year"], name="paper_year_idx")
        papers.add_persistent_index(fields=["degree"], name="paper_degree_idx")
        return ["papers.paper_id", "papers.year", "papers.degree"]

    # ---------------------------------------------------------------- loading

    def load(self, nodes, edges, batch_size: int) -> LoadResult:
        papers = self._db.collection(NODE_COLLECTION)
        cites = self._db.collection(EDGE_COLLECTION)

        t0 = time.perf_counter()
        for i in range(0, len(nodes), batch_size):
            docs = [
                {
                    "_key": str(n["paper_id"]),
                    "paper_id": n["paper_id"],
                    "year": n["year"],
                    "degree": n["degree"],
                }
                for n in nodes[i : i + batch_size]
            ]
            papers.insert_many(docs, overwrite=False)
        t1 = time.perf_counter()

        for i in range(0, len(edges), batch_size):
            docs = [
                {"_from": f"{NODE_COLLECTION}/{s}", "_to": f"{NODE_COLLECTION}/{d}"}
                for s, d in edges[i : i + batch_size]
            ]
            cites.insert_many(docs, overwrite=False)
        t2 = time.perf_counter()

        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method=f"python-arango insert_many batching (batch={batch_size})",
        )

    # ---------------------------------------------------------------- workloads

    def _hop(self, depth: int, start_id: int) -> int:
        query = Q_HOP.format(depth=depth, edge=EDGE_COLLECTION)
        return self._aql(query, start=f"{NODE_COLLECTION}/{start_id}")[0]

    def one_hop(self, start_id: int) -> int:
        return self._hop(1, start_id)

    def two_hop(self, start_id: int) -> int:
        return self._hop(2, start_id)

    def three_hop(self, start_id: int) -> int:
        return self._hop(3, start_id)

    def point_lookup(self, paper_id: int) -> int:
        return self._aql(Q_POINT.format(node=NODE_COLLECTION), id=paper_id)[0]

    def filtered_lookup(self, min_degree: int, year: int) -> int:
        return self._aql(
            Q_FILTERED.format(node=NODE_COLLECTION), min_degree=min_degree, year=year
        )[0]

    def aggregation(self) -> int:
        return self._aql(Q_AGGREGATION.format(node=NODE_COLLECTION))[0]

    def insert_edge(self, src_id: int, dst_id: int) -> None:
        self._aql(
            Q_INSERT.format(edge=EDGE_COLLECTION),
            **{"from": f"{NODE_COLLECTION}/{src_id}", "to": f"{NODE_COLLECTION}/{dst_id}"},
        )

    def delete_edge(self, src_id: int, dst_id: int) -> None:
        self._aql(
            Q_DELETE.format(edge=EDGE_COLLECTION),
            **{"from": f"{NODE_COLLECTION}/{src_id}", "to": f"{NODE_COLLECTION}/{dst_id}"},
        )

    def delete_benchmark_edges(self) -> int:
        removed = 0
        query = Q_DELETE_ALL_BENCHMARK.format(edge=EDGE_COLLECTION)
        while True:
            count = self._aql(query, limit=5000)[0]
            removed += count
            if count == 0:
                return removed

    # ---------------------------------------------------------------- inspection

    def node_count(self) -> int:
        return self._db.collection(NODE_COLLECTION).count()

    def relationship_count(self) -> int:
        return self._db.collection(EDGE_COLLECTION).count()

    def _noop_query(self) -> None:
        self._aql("RETURN 1")

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
        """ArangoDB exposes per-collection figures, so a real footprint is
        available here where most managed Bolt tiers report nothing."""
        try:
            papers = self._db.collection(NODE_COLLECTION)
            cites = self._db.collection(EDGE_COLLECTION)
            p_stats, c_stats = papers.statistics(), cites.statistics()
            node_bytes = p_stats.get("documents_size", 0)
            edge_bytes = c_stats.get("documents_size", 0)
            index_bytes = (
                p_stats.get("indexes", {}).get("size", 0)
                + c_stats.get("indexes", {}).get("size", 0)
            )
            return {
                "papers_documents": papers.count(),
                "papers_documents_bytes": node_bytes,
                "cites_documents": cites.count(),
                "cites_documents_bytes": edge_bytes,
                "index_bytes": index_bytes,
                "total_stored_bytes": node_bytes + edge_bytes + index_bytes,
            }
        except Exception as exc:
            return {"status": f"not observable ({type(exc).__name__})"}
