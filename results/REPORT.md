# Benchmark results

Run `20260820T155856Z` · generated from `results.json`.

## Environment

| Field | Value |
|---|---|
| Client machine | macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Client CPUs | 8 |
| Python | 3.13.6 |
| Run started (UTC) | 2026-08-20T15:58:56.752970+00:00 |
| Iterations per read workload | 100 |
| Warm-up iterations | 20 |
| Read-suite repeats | 3 |
| Load batch size | 10000 |
| Concurrency levels | [1, 10, 40] |
| Mixed-workload read ratio | 0.8 |

## Dataset

| Field | Value |
|---|---|
| Source | [SNAP cit-HepTh (arXiv HEP-Th citation network)](https://snap.stanford.edu/data/cit-HepTh.html) |
| Nodes | 27,769 |
| Relationships | 352,768 |
| Sampled | False |
| Nodes with a known year | 11,444 (41.2%) |
| nodes.csv SHA-256 | `1f3b0b5d41e8b2f2…` |
| edges.csv SHA-256 | `0907ed5b2117de9d…` |

## Platforms and tier parity

| Platform | Query language | Tier | vCPU | RAM | Storage | Status |
|---|---|---|---|---|---|---|
| CognoDB Cloud | unknown | Free (c0) | 0.5 vCPU (burstable) | 256 MB | 1 GB | not configured (missing: uri, password) |
| Neo4j | Cypher (Bolt) | Community 5.26, container-capped to parity tier | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB (dataset well under limit) | completed |
| Memgraph | Cypher (Bolt) | Memgraph 2.22 (in-memory transactional), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| ArangoDB | AQL | ArangoDB 3.11 Community, container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| FalkorDB | Cypher (RESP/GraphBLAS) | FalkorDB 4.2 (Redis module), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |

## Result parity check

Every read workload records the value it returned. If two platforms disagree, the queries are not equivalent and the latency comparison below is invalid.

| Workload | All platforms agree | Summed result per platform |
|---|---|---|
| 1-hop | yes | Neo4j=1,669, Memgraph=1,669, ArangoDB=1,669, FalkorDB=1,669 |
| 2-hop | yes | Neo4j=18,013, Memgraph=18,013, ArangoDB=18,013, FalkorDB=18,013 |
| 3-hop | yes | Neo4j=95,111, Memgraph=95,111, ArangoDB=95,111, FalkorDB=95,111 |
| point-lookup | yes | Neo4j=100, Memgraph=100, ArangoDB=100, FalkorDB=100 |
| filtered-lookup | yes | Neo4j=114,100, Memgraph=114,100, ArangoDB=114,100, FalkorDB=114,100 |
| aggregation | yes | Neo4j=1,200, Memgraph=1,200, ArangoDB=1,200, FalkorDB=1,200 |

## Ingest throughput

| Platform | Nodes/s | Rels/s | Total load (s) | Load method |
|---|---|---|---|---|
| Neo4j | 555 | 2,518 | 190.2 | official neo4j driver, UNWIND batching (batch=10000) |
| Memgraph | 6,199 | 34,909 | 14.6 | official neo4j driver, UNWIND batching (batch=10000) |
| ArangoDB | 14,573 | 24,118 | 16.5 | python-arango insert_many batching (batch=10000) |
| FalkorDB | 12,734 | 28,917 | 14.4 | falkordb client, UNWIND batching (batch=10000) |


![Ingest throughput by platform](charts/ingest_throughput.png)

## Traversal latency

| Platform | 1-hop p50 | 1-hop p95 | 2-hop p50 | 2-hop p95 | 3-hop p50 | 3-hop p95 |
|---|---|---|---|---|---|---|
| Neo4j | 5.06 | 70.81 | 4.55 | 65.72 | 6.82 | 96.89 |
| Memgraph | 0.88 | 3.04 | 0.57 | 1.30 | 1.44 | 7.30 |
| ArangoDB | 2.80 | 5.89 | 4.51 | 69.62 | 66.45 | 383.30 |
| FalkorDB | 0.61 | 2.46 | 0.47 | 1.80 | 0.93 | 3.52 |


![Traversal p50 latency by hop depth](charts/traversal_p50.png)


![Traversal p95 latency by hop depth](charts/traversal_p95.png)

## Lookups and aggregation

| Platform | point p50 | point p95 | filtered p50 | filtered p95 | aggregation p50 | aggregation p95 | Indexed properties |
|---|---|---|---|---|---|---|---|
| Neo4j | 3.33 | 72.52 | 6.86 | 85.52 | 11.02 | 96.75 | Paper.paper_id, Paper.year, Paper.degree |
| Memgraph | 0.58 | 1.21 | 1.11 | 4.28 | 7.90 | 74.40 | Paper.paper_id, Paper.year, Paper.degree |
| ArangoDB | 2.65 | 5.06 | 6.87 | 22.51 | 8.93 | 36.59 | papers.paper_id, papers.year, papers.degree |
| FalkorDB | 1.67 | 6.76 | 1.20 | 7.22 | 3.86 | 34.38 | Paper.paper_id, Paper.year, Paper.degree |


![Lookup and aggregation p50 latency](charts/lookups_p50.png)

## Mixed workload concurrency sweep

| Platform | Clients | QPS | p50 ms | p95 ms | p99 ms | Reads | Writes | Errors |
|---|---|---|---|---|---|---|---|---|
| Neo4j | 1 | 115.5 | 2.56 | 57.00 | 82.66 | 1865 | 445 | 0 |
| Neo4j | 10 | 79.1 | 97.53 | 341.65 | 896.34 | 1273 | 311 | 0 |
| Neo4j | 40 | 148.9 | 194.67 | 502.04 | 789.83 | 2477 | 620 | 7 |
| Memgraph | 1 | 882.1 | 0.68 | 2.57 | 7.28 | 14142 | 3504 | 0 |
| Memgraph | 10 | 1,526.9 | 2.79 | 44.53 | 63.18 | 24631 | 5914 | 1 |
| Memgraph | 40 | 1,406.1 | 13.13 | 85.75 | 169.44 | 22576 | 5561 | 2 |
| ArangoDB | 1 | 308.5 | 3.06 | 5.48 | 8.38 | 4957 | 1214 | 0 |
| ArangoDB | 10 | 1,432.1 | 5.08 | 22.74 | 40.49 | 23143 | 5506 | 0 |
| ArangoDB | 40 | 1,357.9 | 23.47 | 65.14 | 97.10 | 21809 | 5377 | 0 |
| FalkorDB | 1 | 1,457.1 | 0.48 | 1.40 | 3.44 | 23320 | 5831 | 0 |
| FalkorDB | 10 | 1,656.0 | 1.61 | 64.10 | 76.58 | 26754 | 6404 | 0 |
| FalkorDB | 40 | 1,627.4 | 6.78 | 84.42 | 98.68 | 26191 | 6414 | 465 |


![Mixed workload scaling](charts/concurrency_scaling.png)

## Cold start vs warm

First touch after load, before any warm-up, against the warm p50 for the same workload.

| Platform | Workload | Cold first-touch ms | Warm p50 ms |
|---|---|---|---|
| Neo4j | 1-hop | 2,044.58 | 5.06 |
| Neo4j | 3-hop | 2,002.13 | 6.82 |
| Neo4j | aggregation | 806.68 | 11.02 |
| Memgraph | 1-hop | 13.78 | 0.88 |
| Memgraph | 3-hop | 13.93 | 1.44 |
| Memgraph | aggregation | 11.51 | 7.90 |
| ArangoDB | 1-hop | 16.61 | 2.80 |
| ArangoDB | 3-hop | 156.05 | 66.45 |
| ArangoDB | aggregation | 14.76 | 8.93 |
| FalkorDB | 1-hop | 376.95 | 0.61 |
| FalkorDB | 3-hop | 3.02 | 0.93 |
| FalkorDB | aggregation | 5.91 | 3.86 |

## Run-to-run variance

The read suite repeated end to end. Spread shows how much of any difference between platforms is just noise.

| Platform | Workload | Runs | p50 per run (ms) | Spread (ms) | Spread % |
|---|---|---|---|---|---|
| Neo4j | 1-hop | 3 | 5.06, 3.65, 2.87 | 2.19 | 56.7% |
| Neo4j | 2-hop | 3 | 4.55, 2.87, 3.73 | 1.68 | 45.3% |
| Neo4j | 3-hop | 3 | 6.82, 6.00, 6.09 | 0.82 | 13.0% |
| Neo4j | point-lookup | 3 | 3.33, 2.91, 1.99 | 1.35 | 49.2% |
| Neo4j | filtered-lookup | 3 | 6.86, 6.02, 6.21 | 0.84 | 13.3% |
| Neo4j | aggregation | 3 | 11.02, 18.72, 8.49 | 10.23 | 80.3% |
| Memgraph | 1-hop | 3 | 0.88, 0.90, 1.13 | 0.25 | 25.4% |
| Memgraph | 2-hop | 3 | 0.57, 0.75, 1.09 | 0.52 | 64.4% |
| Memgraph | 3-hop | 3 | 1.44, 1.55, 1.86 | 0.42 | 26.1% |
| Memgraph | point-lookup | 3 | 0.58, 0.51, 0.53 | 0.07 | 13.2% |
| Memgraph | filtered-lookup | 3 | 1.11, 0.93, 1.04 | 0.18 | 17.4% |
| Memgraph | aggregation | 3 | 7.90, 6.46, 6.43 | 1.46 | 21.1% |
| ArangoDB | 1-hop | 3 | 2.80, 3.31, 2.54 | 0.78 | 26.9% |
| ArangoDB | 2-hop | 3 | 4.51, 4.71, 3.89 | 0.82 | 18.7% |
| ArangoDB | 3-hop | 3 | 66.45, 17.43, 17.63 | 49.02 | 144.9% |
| ArangoDB | point-lookup | 3 | 2.65, 2.27, 1.96 | 0.69 | 30.1% |
| ArangoDB | filtered-lookup | 3 | 6.87, 6.79, 6.02 | 0.86 | 13.1% |
| ArangoDB | aggregation | 3 | 8.93, 8.64, 8.33 | 0.60 | 7.0% |
| FalkorDB | 1-hop | 3 | 0.61, 0.46, 0.56 | 0.14 | 26.6% |
| FalkorDB | 2-hop | 3 | 0.47, 0.59, 0.54 | 0.13 | 24.2% |
| FalkorDB | 3-hop | 3 | 0.93, 1.14, 1.08 | 0.21 | 19.8% |
| FalkorDB | point-lookup | 3 | 1.67, 0.53, 0.34 | 1.33 | 157.6% |
| FalkorDB | filtered-lookup | 3 | 1.20, 0.77, 0.58 | 0.61 | 72.4% |
| FalkorDB | aggregation | 3 | 3.86, 3.71, 3.89 | 0.18 | 4.7% |

## Footprint

| Platform | Nodes stored | Rels stored | Load complete | Observable footprint |
|---|---|---|---|---|
| Neo4j | 27,769 | 352,768 | yes | not observable (JMX store metrics unavailable on this tier) |
| Memgraph | 27,769 | 352,768 | yes | memory_res=184.53MiB, disk_usage=24.99MiB, vertex_count=27,769, edge_count=352,768 |
| ArangoDB | 27,769 | 352,768 | yes | papers_documents=27,769, papers_documents_bytes=2,308,774, cites_documents=352,768, cites_documents_bytes=31,364,366, index_bytes=39,603,478, total_stored_bytes=73,276,618 |
| FalkorDB | 27,769 | 352,768 | yes | used_memory_bytes=32,524,528, used_memory_human=31.02M, used_memory_peak_bytes=79,801,344 |

## Errors and skipped platforms

| Platform | Detail |
|---|---|
| CognoDB Cloud | not configured (missing: uri, password) |
