# Benchmark results

Run `20260820T165121Z` · generated from `results.json`.

## Environment

| Field | Value |
|---|---|
| Client machine | macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Client CPUs | 8 |
| Python | 3.13.6 |
| Run started (UTC) | 2026-08-20T16:51:21.701935+00:00 |
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
| CognoDB Cloud | Cypher (Bolt) | Free (c0) | 0.5 vCPU (burstable) | 256 MB | 1 GB | completed |
| Neo4j | Cypher (Bolt) | Community 5.26, container-capped to parity tier | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB (dataset well under limit) | completed |
| Memgraph | Cypher (Bolt) | Memgraph 2.22 (in-memory transactional), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| ArangoDB | AQL | ArangoDB 3.11 Community, container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| FalkorDB | Cypher (RESP/GraphBLAS) | FalkorDB 4.2 (Redis module), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |

## Result parity check

Every read workload records the value it returned. If two platforms disagree, the queries are not equivalent and the latency comparison below is invalid.

| Workload | All platforms agree | Summed result per platform |
|---|---|---|
| 1-hop | yes | CognoDB Cloud=1,669, Neo4j=1,669, Memgraph=1,669, ArangoDB=1,669, FalkorDB=1,669 |
| 2-hop | yes | CognoDB Cloud=18,013, Neo4j=18,013, Memgraph=18,013, ArangoDB=18,013, FalkorDB=18,013 |
| 3-hop | yes | CognoDB Cloud=95,111, Neo4j=95,111, Memgraph=95,111, ArangoDB=95,111, FalkorDB=95,111 |
| point-lookup | yes | CognoDB Cloud=100, Neo4j=100, Memgraph=100, ArangoDB=100, FalkorDB=100 |
| filtered-lookup | yes | CognoDB Cloud=114,100, Neo4j=114,100, Memgraph=114,100, ArangoDB=114,100, FalkorDB=114,100 |
| aggregation | yes | CognoDB Cloud=1,200, Neo4j=1,200, Memgraph=1,200, ArangoDB=1,200, FalkorDB=1,200 |

## Transport baseline

| Platform | Transport p50 (ms) | Transport p95 (ms) | 1-hop p50 (ms) | Share of 1-hop that is transport |
|---|---|---|---|---|
| CognoDB Cloud | 239.37 | 243.21 | 248.02 | 97% |
| Neo4j | 3.38 | 83.37 | 4.07 | 83% |
| Memgraph | 0.67 | 2.73 | 0.42 | 158% |
| ArangoDB | 2.52 | 3.06 | 4.99 | 51% |
| FalkorDB | 0.78 | 1.16 | 1.13 | 69% |

## Ingest throughput

| Platform | Nodes/s | Rels/s | Total load (s) | Load method |
|---|---|---|---|---|
| CognoDB Cloud | 11,354 | 18,393 | 21.6 | official neo4j driver, UNWIND batching (batch=10000) |
| Neo4j | 1,595 | 5,756 | 78.7 | official neo4j driver, UNWIND batching (batch=10000) |
| Memgraph | 29,971 | 43,368 | 9.1 | official neo4j driver, UNWIND batching (batch=10000) |
| ArangoDB | 17,311 | 21,271 | 18.2 | python-arango insert_many batching (batch=10000) |
| FalkorDB | 14,276 | 28,367 | 14.4 | falkordb client, UNWIND batching (batch=10000) |


![Ingest throughput by platform](charts/ingest_throughput.png)

## Traversal latency

| Platform | 1-hop p50 | 1-hop p95 | 2-hop p50 | 2-hop p95 | 3-hop p50 | 3-hop p95 |
|---|---|---|---|---|---|---|
| CognoDB Cloud | 248.02 | 325.86 | 240.92 | 304.45 | 263.37 | 444.14 |
| Neo4j | 4.07 | 89.74 | 3.11 | 65.81 | 4.66 | 89.06 |
| Memgraph | 0.42 | 0.99 | 0.44 | 0.72 | 1.11 | 4.58 |
| ArangoDB | 4.99 | 66.41 | 4.82 | 77.79 | 20.96 | 200.04 |
| FalkorDB | 1.13 | 2.10 | 0.92 | 1.54 | 1.46 | 3.51 |


![Traversal p50 latency by hop depth](charts/traversal_p50.png)


![Traversal p95 latency by hop depth](charts/traversal_p95.png)

## Lookups and aggregation

| Platform | point p50 | point p95 | filtered p50 | filtered p95 | aggregation p50 | aggregation p95 | Indexed properties |
|---|---|---|---|---|---|---|---|
| CognoDB Cloud | 247.36 | 289.46 | 245.63 | 306.11 | 286.84 | 347.23 | Paper.paper_id, Paper.year, Paper.degree |
| Neo4j | 2.44 | 45.73 | 5.35 | 82.64 | 9.99 | 97.75 | Paper.paper_id, Paper.year, Paper.degree |
| Memgraph | 0.37 | 0.57 | 0.81 | 0.98 | 6.30 | 36.62 | Paper.paper_id, Paper.year, Paper.degree |
| ArangoDB | 3.39 | 4.75 | 7.47 | 27.60 | 9.28 | 37.01 | papers.paper_id, papers.year, papers.degree |
| FalkorDB | 0.93 | 2.56 | 0.98 | 1.53 | 4.92 | 39.71 | Paper.paper_id, Paper.year, Paper.degree |


![Lookup and aggregation p50 latency](charts/lookups_p50.png)

## Mixed workload concurrency sweep

| Platform | Clients | QPS | p50 ms | p95 ms | p99 ms | Reads | Writes | Errors |
|---|---|---|---|---|---|---|---|---|
| CognoDB Cloud | 1 | 3.8 | 238.87 | 325.18 | 922.73 | 63 | 13 | 0 |
| CognoDB Cloud | 10 | 39.3 | 248.15 | 272.85 | 304.71 | 636 | 157 | 0 |
| CognoDB Cloud | 40 | 152.9 | 247.73 | 274.45 | 306.69 | 2558 | 640 | 0 |
| Neo4j | 1 | 215.7 | 1.45 | 7.99 | 72.74 | 3455 | 859 | 0 |
| Neo4j | 10 | 184.3 | 10.49 | 105.81 | 285.40 | 2975 | 713 | 0 |
| Neo4j | 40 | 279.0 | 98.33 | 283.55 | 578.74 | 4503 | 1128 | 7 |
| Memgraph | 1 | 2,273.3 | 0.34 | 0.91 | 2.04 | 36378 | 9126 | 0 |
| Memgraph | 10 | 1,386.1 | 3.43 | 47.17 | 60.41 | 22407 | 5321 | 0 |
| Memgraph | 40 | 1,547.4 | 18.26 | 67.80 | 86.59 | 24868 | 6094 | 1 |
| ArangoDB | 1 | 259.7 | 3.82 | 5.20 | 6.28 | 4150 | 1044 | 0 |
| ArangoDB | 10 | 826.3 | 8.98 | 37.99 | 46.60 | 13367 | 3166 | 0 |
| ArangoDB | 40 | 970.1 | 39.07 | 70.43 | 87.82 | 15588 | 3833 | 0 |
| FalkorDB | 1 | 1,288.2 | 0.66 | 1.24 | 1.70 | 20636 | 5131 | 0 |
| FalkorDB | 10 | 1,729.5 | 1.63 | 66.08 | 77.07 | 27911 | 6686 | 0 |
| FalkorDB | 40 | 1,676.5 | 6.79 | 85.04 | 92.59 | 27002 | 6594 | 869 |


![Mixed workload scaling](charts/concurrency_scaling.png)

## Cold start vs warm

First touch after load, before any warm-up, against the warm p50 for the same workload.

| Platform | Workload | Cold first-touch ms | Warm p50 ms |
|---|---|---|---|
| CognoDB Cloud | 1-hop | 301.61 | 248.02 |
| CognoDB Cloud | 3-hop | 367.67 | 263.37 |
| CognoDB Cloud | aggregation | 332.26 | 286.84 |
| Neo4j | 1-hop | 637.20 | 4.07 |
| Neo4j | 3-hop | 1,315.21 | 4.66 |
| Neo4j | aggregation | 311.84 | 9.99 |
| Memgraph | 1-hop | 6.58 | 0.42 |
| Memgraph | 3-hop | 7.39 | 1.11 |
| Memgraph | aggregation | 6.92 | 6.30 |
| ArangoDB | 1-hop | 16.88 | 4.99 |
| ArangoDB | 3-hop | 111.92 | 20.96 |
| ArangoDB | aggregation | 14.91 | 9.28 |
| FalkorDB | 1-hop | 458.73 | 1.13 |
| FalkorDB | 3-hop | 3.24 | 1.46 |
| FalkorDB | aggregation | 5.08 | 4.92 |

## Run-to-run variance

The read suite repeated end to end. Spread shows how much of any difference between platforms is just noise.

| Platform | Workload | Runs | p50 per run (ms) | Spread (ms) | Spread % |
|---|---|---|---|---|---|
| CognoDB Cloud | 1-hop | 3 | 248.02, 247.61, 240.17 | 7.84 | 3.2% |
| CognoDB Cloud | 2-hop | 3 | 240.92, 240.50, 240.88 | 0.41 | 0.2% |
| CognoDB Cloud | 3-hop | 3 | 263.37, 269.24, 266.66 | 5.87 | 2.2% |
| CognoDB Cloud | point-lookup | 3 | 247.36, 241.00, 239.45 | 7.91 | 3.3% |
| CognoDB Cloud | filtered-lookup | 3 | 245.63, 245.69, 246.03 | 0.39 | 0.2% |
| CognoDB Cloud | aggregation | 3 | 286.85, 292.63, 290.71 | 5.79 | 2.0% |
| Neo4j | 1-hop | 3 | 4.07, 1.92, 1.70 | 2.38 | 92.7% |
| Neo4j | 2-hop | 3 | 3.11, 2.36, 1.79 | 1.32 | 54.5% |
| Neo4j | 3-hop | 3 | 4.66, 3.69, 3.04 | 1.61 | 42.5% |
| Neo4j | point-lookup | 3 | 2.44, 1.77, 1.54 | 0.91 | 47.2% |
| Neo4j | filtered-lookup | 3 | 5.35, 4.17, 3.75 | 1.59 | 36.0% |
| Neo4j | aggregation | 3 | 9.99, 8.52, 9.85 | 1.47 | 15.6% |
| Memgraph | 1-hop | 3 | 0.42, 0.71, 0.80 | 0.38 | 58.2% |
| Memgraph | 2-hop | 3 | 0.44, 0.99, 0.80 | 0.55 | 74.2% |
| Memgraph | 3-hop | 3 | 1.11, 1.69, 1.63 | 0.58 | 39.4% |
| Memgraph | point-lookup | 3 | 0.37, 0.66, 0.81 | 0.44 | 71.5% |
| Memgraph | filtered-lookup | 3 | 0.81, 1.27, 1.47 | 0.66 | 55.5% |
| Memgraph | aggregation | 3 | 6.30, 6.55, 6.40 | 0.25 | 3.9% |
| ArangoDB | 1-hop | 3 | 4.99, 3.83, 3.13 | 1.86 | 46.6% |
| ArangoDB | 2-hop | 3 | 4.82, 4.79, 4.21 | 0.61 | 13.3% |
| ArangoDB | 3-hop | 3 | 20.96, 20.17, 18.77 | 2.19 | 11.0% |
| ArangoDB | point-lookup | 3 | 3.39, 3.00, 2.73 | 0.66 | 21.7% |
| ArangoDB | filtered-lookup | 3 | 7.47, 7.27, 7.12 | 0.36 | 4.9% |
| ArangoDB | aggregation | 3 | 9.28, 9.10, 8.96 | 0.32 | 3.5% |
| FalkorDB | 1-hop | 3 | 1.13, 0.94, 0.65 | 0.48 | 53.1% |
| FalkorDB | 2-hop | 3 | 0.92, 0.93, 0.97 | 0.06 | 6.0% |
| FalkorDB | 3-hop | 3 | 1.46, 1.35, 1.45 | 0.10 | 7.3% |
| FalkorDB | point-lookup | 3 | 0.93, 0.75, 0.64 | 0.29 | 38.1% |
| FalkorDB | filtered-lookup | 3 | 0.98, 1.10, 1.11 | 0.13 | 12.1% |
| FalkorDB | aggregation | 3 | 4.92, 4.82, 4.63 | 0.30 | 6.2% |

## Footprint

| Platform | Nodes stored | Rels stored | Load complete | Observable footprint |
|---|---|---|---|---|
| CognoDB Cloud | 27,769 | 352,768 | yes | not observable (JMX store metrics unavailable on this tier) |
| Neo4j | 27,769 | 352,768 | yes | not observable (JMX store metrics unavailable on this tier) |
| Memgraph | 27,769 | 352,768 | yes | memory_res=184.95MiB, disk_usage=25.88MiB, vertex_count=27,769, edge_count=352,768 |
| ArangoDB | 27,769 | 352,768 | yes | papers_documents=27,769, papers_documents_bytes=2,325,026, cites_documents=352,768, cites_documents_bytes=30,063,316, index_bytes=39,670,027, total_stored_bytes=72,058,369 |
| FalkorDB | 27,769 | 352,768 | yes | used_memory_bytes=32,718,992, used_memory_human=31.20M, used_memory_peak_bytes=79,570,568 |
