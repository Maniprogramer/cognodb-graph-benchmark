"""Bolt/Cypher adapter -- covers CognoDB Cloud, Neo4j and Memgraph.

CognoDB speaks Bolt and is driven by the official Neo4j driver with no code
changes, so it shares this adapter with Neo4j itself. Memgraph also speaks Bolt
and Cypher but differs in DDL and in a few procedures, so the places where the
dialects genuinely diverge are switched on `flavor`. Everything else -- the
load strategy, and every measured query -- is byte-identical across the three,
which is what makes the comparison fair.
"""

from __future__ import annotations

import time

from neo4j import GraphDatabase

from .base import GraphAdapter, LoadResult, PlatformSpec

# Measured queries. Shared verbatim by all three Bolt platforms.
Q_ONE_HOP = "MATCH (p:Paper {paper_id:$id})-->(a) RETURN count(DISTINCT a) AS c"
Q_TWO_HOP = "MATCH (p:Paper {paper_id:$id})-->()-->(b) RETURN count(DISTINCT b) AS c"
Q_THREE_HOP = "MATCH (p:Paper {paper_id:$id})-->()-->()-->(c) RETURN count(DISTINCT c) AS c"
Q_POINT = "MATCH (p:Paper {paper_id:$id}) RETURN count(p) AS c"
Q_FILTERED = "MATCH (p:Paper) WHERE p.degree >= $min_degree AND p.year = $year RETURN count(p) AS c"
Q_AGGREGATION = "MATCH (p:Paper) RETURN p.year AS year, count(*) AS n"
Q_INSERT = (
    "MATCH (a:Paper {paper_id:$src}), (b:Paper {paper_id:$dst}) "
    "CREATE (a)-[r:CITES {benchmark:true}]->(b)"
)
Q_DELETE = (
    "MATCH (:Paper {paper_id:$src})-[r:CITES {benchmark:true}]->(:Paper {paper_id:$dst}) "
    "DELETE r"
)
Q_NODE_COUNT = "MATCH (n:Paper) RETURN count(n) AS c"
Q_REL_COUNT = "MATCH ()-[r:CITES]->() RETURN count(r) AS c"

LOAD_NODES = (
    "UNWIND $rows AS row "
    "CREATE (n:Paper {paper_id: row.paper_id, year: row.year, degree: row.degree})"
)
LOAD_EDGES = (
    "UNWIND $rows AS row "
    "MATCH (a:Paper {paper_id: row.src}) "
    "MATCH (b:Paper {paper_id: row.dst}) "
    "CREATE (a)-[:CITES]->(b)"
)


class BoltAdapter(GraphAdapter):
    query_language = "Cypher (Bolt)"

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = config["name"]
        self.flavor = config.get("flavor", "neo4j")  # neo4j | memgraph
        self._driver = None

    # ---------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        if self._driver is not None:
            return
        self._driver = GraphDatabase.driver(
            self.config["uri"],
            auth=(self.config["user"], self.config["password"]),
            max_connection_pool_size=self.config.get("pool_size", 50),
            connection_timeout=self.config.get("connection_timeout", 30),
        )
        self._driver.verify_connectivity()
        self._connected = True

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            self._connected = False

    def _run(self, query: str, **params):
        with self._driver.session() as session:
            return list(session.run(query, **params))

    def reset(self) -> None:
        if self.flavor == "memgraph":
            self._run("MATCH (n) DETACH DELETE n")
            for stmt in self._existing_memgraph_indexes():
                self._run(stmt)
            return
        # Neo4j: delete in batches so a large graph does not blow the heap in
        # one transaction. On a 256 MB instance a single DETACH DELETE of
        # 350k relationships reliably runs out of memory.
        while True:
            rows = self._run(
                "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS c"
            )
            if not rows or rows[0]["c"] == 0:
                break
        for name in self._existing_neo4j_indexes():
            self._run(f"DROP INDEX {name} IF EXISTS")

    def _existing_neo4j_indexes(self) -> list[str]:
        try:
            rows = self._run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name")
            return [r["name"] for r in rows]
        except Exception:
            return []

    def _existing_memgraph_indexes(self) -> list[str]:
        try:
            rows = self._run("SHOW INDEX INFO")
        except Exception:
            return []
        drops = []
        for r in rows:
            label, prop = r.get("label"), r.get("property")
            if label and prop:
                drops.append(f"DROP INDEX ON :{label}({prop})")
            elif label:
                drops.append(f"DROP INDEX ON :{label}")
        return drops

    def create_schema(self) -> list[str]:
        if self.flavor == "memgraph":
            statements = [
                "CREATE INDEX ON :Paper(paper_id)",
                "CREATE INDEX ON :Paper(year)",
                "CREATE INDEX ON :Paper(degree)",
            ]
        else:
            statements = [
                "CREATE INDEX paper_id_idx IF NOT EXISTS FOR (n:Paper) ON (n.paper_id)",
                "CREATE INDEX paper_year_idx IF NOT EXISTS FOR (n:Paper) ON (n.year)",
                "CREATE INDEX paper_degree_idx IF NOT EXISTS FOR (n:Paper) ON (n.degree)",
            ]
        for stmt in statements:
            self._run(stmt)
        self._await_indexes()
        return ["Paper.paper_id", "Paper.year", "Paper.degree"]

    def _await_indexes(self) -> None:
        """Block until indexes are online; an index still building would make
        the load look artificially fast and the first queries artificially slow."""
        if self.flavor == "memgraph":
            return  # Memgraph builds indexes synchronously
        try:
            self._run("CALL db.awaitIndexes(300)")
        except Exception:
            time.sleep(2)

    # ---------------------------------------------------------------- loading

    def load(self, nodes, edges, batch_size: int) -> LoadResult:
        t0 = time.perf_counter()
        for i in range(0, len(nodes), batch_size):
            self._run(LOAD_NODES, rows=nodes[i : i + batch_size])
        t1 = time.perf_counter()

        edge_rows = [{"src": s, "dst": d} for s, d in edges]
        for i in range(0, len(edge_rows), batch_size):
            self._run(LOAD_EDGES, rows=edge_rows[i : i + batch_size])
        t2 = time.perf_counter()

        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method=f"official neo4j driver, UNWIND batching (batch={batch_size})",
        )

    # ---------------------------------------------------------------- workloads

    def one_hop(self, start_id: int) -> int:
        return self._run(Q_ONE_HOP, id=start_id)[0]["c"]

    def two_hop(self, start_id: int) -> int:
        return self._run(Q_TWO_HOP, id=start_id)[0]["c"]

    def three_hop(self, start_id: int) -> int:
        return self._run(Q_THREE_HOP, id=start_id)[0]["c"]

    def point_lookup(self, paper_id: int) -> int:
        return self._run(Q_POINT, id=paper_id)[0]["c"]

    def filtered_lookup(self, min_degree: int, year: int) -> int:
        return self._run(Q_FILTERED, min_degree=min_degree, year=year)[0]["c"]

    def aggregation(self) -> int:
        return len(self._run(Q_AGGREGATION))

    def insert_edge(self, src_id: int, dst_id: int) -> None:
        self._run(Q_INSERT, src=src_id, dst=dst_id)

    def delete_edge(self, src_id: int, dst_id: int) -> None:
        self._run(Q_DELETE, src=src_id, dst=dst_id)

    # ---------------------------------------------------------------- inspection

    def node_count(self) -> int:
        return self._run(Q_NODE_COUNT)[0]["c"]

    def relationship_count(self) -> int:
        return self._run(Q_REL_COUNT)[0]["c"]

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
        """Whatever footprint the server exposes. Absent on most managed tiers."""
        if self.flavor == "memgraph":
            try:
                row = self._run("SHOW STORAGE INFO")
                info = {r["storage info"]: r["value"] for r in row} if row else {}
                return {
                    k: info[k]
                    for k in ("memory_res", "disk_usage", "vertex_count", "edge_count")
                    if k in info
                }
            except Exception as exc:
                return {"status": f"not observable ({type(exc).__name__})"}
        try:
            rows = self._run(
                'CALL dbms.queryJmx("org.neo4j:instance=kernel#0,name=Store file sizes") '
                "YIELD attributes RETURN attributes"
            )
            attrs = rows[0]["attributes"]
            return {"total_store_bytes": attrs.get("TotalStoreSize", {}).get("value")}
        except Exception:
            return {"status": "not observable (JMX store metrics unavailable on this tier)"}
