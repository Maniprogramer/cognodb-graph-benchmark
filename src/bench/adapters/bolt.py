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
Q_DELETE_ALL_BENCHMARK = (
    "MATCH ()-[r:CITES {benchmark:true}]->() WITH r LIMIT $limit "
    "DELETE r RETURN count(r) AS c"
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
            connection_acquisition_timeout=self.config.get("query_timeout", 60),
            max_transaction_retry_time=self.config.get("query_timeout", 60),
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
            # Batched for the same reason as Neo4j below. Memgraph is an
            # in-memory engine, so a single delete of the whole graph has to
            # hold the entire transaction delta in the same 256 MB the data
            # already occupies.
            for statement in (
                "MATCH ()-[r]->() WITH r LIMIT $limit DELETE r RETURN count(r) AS c",
                "MATCH (n) WITH n LIMIT $limit DELETE n RETURN count(n) AS c",
            ):
                while True:
                    rows = self._run(statement, limit=1000)
                    if not rows or rows[0]["c"] == 0:
                        break
            for stmt in self._existing_memgraph_indexes():
                self._run(stmt)
            return
        # Neo4j: delete relationships first, then nodes, in small batches.
        #
        # DETACH DELETE on a node also loads and deletes all of its
        # relationships inside the same transaction, so on a dense node it
        # builds a very large transaction state. With a 96 MB heap this does
        # not merely run slowly -- an earlier version of this method using
        # 10,000-node DETACH DELETE batches drove the JVM into
        # OutOfMemoryError partway through the reset, leaving the container
        # running but the database dead. Deleting relationships separately in
        # 1,000-row batches keeps each transaction small enough to survive.
        for statement in (
            "MATCH ()-[r]->() WITH r LIMIT $limit DELETE r RETURN count(r) AS c",
            "MATCH (n) WITH n LIMIT $limit DELETE n RETURN count(n) AS c",
        ):
            while True:
                rows = self._run(statement, limit=1000)
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
        """Ask the server to block until indexes are online, if it can.

        Not every Bolt-compatible platform implements db.awaitIndexes --
        CognoDB does not -- so this is best-effort only and is never relied on
        for correctness. `_await_node_visibility` below is the check that
        actually guards the load.
        """
        if self.flavor == "memgraph":
            return  # Memgraph builds indexes synchronously
        try:
            self._run("CALL db.awaitIndexes(300)")
        except Exception:
            pass

    def _await_node_visibility(self, sample_ids: list[int], timeout: float = 120.0) -> float:
        """Block until freshly written nodes are findable by indexed lookup.

        This exists because of a silent-data-loss bug, not as a precaution.
        The edge load matches endpoints by `paper_id`; if the index backing
        that lookup has not finished populating, the MATCH returns **zero rows
        and no error**, so every CREATE is skipped and the load reports success
        having written nothing. On CognoDB this produced a complete graph of
        27,769 nodes and 0 relationships while claiming ~24,000 rels/sec.

        A fixed sleep cannot fix this -- the delay is not knowable in advance.
        Probing the exact lookup the edge load depends on is the only check
        that means anything, so that is what this does.
        """
        deadline = time.perf_counter() + timeout
        probes = [i for i in sample_ids[:3]]
        waited = 0.0
        while time.perf_counter() < deadline:
            found = sum(
                self._run(Q_POINT, id=node_id)[0]["c"] for node_id in probes
            )
            if found == len(probes):
                return waited
            time.sleep(1.0)
            waited += 1.0
        raise RuntimeError(
            f"nodes still not visible to indexed lookup after {timeout:.0f}s; "
            "loading edges now would silently create nothing"
        )

    # ---------------------------------------------------------------- loading

    def load(self, nodes, edges, batch_size: int) -> LoadResult:
        node_start = time.perf_counter()
        for i in range(0, len(nodes), batch_size):
            self._run(LOAD_NODES, rows=nodes[i : i + batch_size])
        node_seconds = time.perf_counter() - node_start

        # Wait for the nodes to become findable by indexed lookup before
        # loading edges. Deliberately excluded from the reported timings: this
        # is the harness synchronising with the server, not work we asked the
        # server to do, and charging it to ingest would penalise a platform for
        # our own bookkeeping. See _await_node_visibility for why it exists.
        self._await_node_visibility([n["paper_id"] for n in nodes])

        edge_rows = [{"src": s, "dst": d} for s, d in edges]
        edge_start = time.perf_counter()
        for i in range(0, len(edge_rows), batch_size):
            self._run(LOAD_EDGES, rows=edge_rows[i : i + batch_size])
        relationship_seconds = time.perf_counter() - edge_start

        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=node_seconds,
            relationship_seconds=relationship_seconds,
            total_seconds=node_seconds + relationship_seconds,
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

    def delete_benchmark_edges(self) -> int:
        # Batched: a single DELETE of a large write set exhausts the heap on a
        # 256 MB instance, the same failure mode as reset().
        removed = 0
        while True:
            rows = self._run(Q_DELETE_ALL_BENCHMARK, limit=5000)
            count = rows[0]["c"] if rows else 0
            removed += count
            if count == 0:
                return removed

    # ---------------------------------------------------------------- inspection

    def node_count(self) -> int:
        return self._run(Q_NODE_COUNT)[0]["c"]

    def relationship_count(self) -> int:
        return self._run(Q_REL_COUNT)[0]["c"]

    def _noop_query(self) -> None:
        self._run("RETURN 1 AS x")

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
