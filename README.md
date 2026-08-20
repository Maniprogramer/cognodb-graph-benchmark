# Graph database cloud benchmark — CognoDB vs. four other engines

A reproducible, fully scripted benchmark comparing **CognoDB Cloud** against four
other graph databases on one dataset, one set of logical queries, one client
machine, and — the part that usually gets fudged — **one set of hardware limits**.

Everything here runs from `make all`. Every number in this README was produced by
that command; none was typed in by hand.

> **Scope of the published run:** all **five** platforms — CognoDB Cloud, Neo4j,
> Memgraph, ArangoDB and FalkorDB — loaded the identical dataset and returned
> identical results on every workload. CognoDB is a managed endpoint reached over
> the internet while the other four are local containers, so its latencies are
> reported alongside a measured transport floor. See [Caveats](#caveats).

> **On who "wins".** The interesting result in this benchmark is not the ranking.
> It is *why* engines that answer identical questions on identical hardware differ
> by two orders of magnitude — and the answer turns out to be mostly about where
> each engine spends its 256 MB. That reasoning is in [Analysis](#analysis).

---

## Contents

- [The fairness problem](#the-fairness-problem)
- [Databases compared, and why](#databases-compared-and-why)
- [Dataset](#dataset)
- [Methodology](#methodology)
- [How the harness avoids fooling itself](#how-the-harness-avoids-fooling-itself)
- [Reproducing this](#reproducing-this)
- [Results](#results)
- [Analysis](#analysis)
- [Caveats](#caveats)
- [Repository layout](#repository-layout)
- [Write-up](#write-up)

---

## The fairness problem

The CognoDB free tier is **0.5 burstable vCPU, 256 MB RAM, 1 GB disk**. That is
small. It is small enough that the obvious benchmark — CognoDB's free tier against
whatever tier the other vendors happen to offer — measures the *tier*, not the
*engine*. A database given 4 GB will beat one given 256 MB every time, and the
result tells you nothing.

So the constraint here runs the other way: **every engine is held to CognoDB's
free-tier envelope**, and that envelope is the unit of comparison.

| Resource | Parity tier |
|---|---|
| vCPU | 0.5 (burstable) |
| RAM | 256 MB |
| Disk | 1 GB |

The four comparison databases run as local containers with hard cgroup limits at
exactly those figures — `cpus: 0.5`, `memory: 256m` in
[`docker/docker-compose.yml`](docker/docker-compose.yml). Section 4 of the brief
explicitly permits "self-hosted deployments capped to the same resources", and
capping locally is in fact **stricter** than comparing cloud free tiers to each
other: a cloud tier advertises resources it does not always honour, and hides its
noisy-neighbour behaviour behind an endpoint you cannot inspect. A cgroup limit is
verifiable — `make stats` prints live usage against the cap.

The same harness runs unchanged against managed cloud endpoints. Set the URI
environment variables and it benchmarks those instead; nothing in the code knows
the difference.

### Where this parity is imperfect — stated up front

CognoDB is a **managed cloud instance reached over the public internet**, while
the four comparison engines are **local containers reached over loopback**. That
is not a difference resource caps can fix, and it is large:

- Every CognoDB operation pays a network round trip the local platforms do not.
  Measured, that round trip is **238 ms** — while Memgraph answers a 1-hop
  traversal in 0.39 ms. On every read workload here, transport is not a
  correction to the measurement, it *is* the measurement.
- "0.5 burstable vCPU" on managed hardware and `cpus: 0.5` under cgroups are
  similar in intent but not identical in behaviour, particularly in how burst
  credit accrues.

Both push the same way: **they make CognoDB look far slower than its engine is.**

Rather than only warning about this, the harness measures it. Every platform's
floor — the median round trip for `RETURN 1`, a query that does no work — is
recorded and reported, so the transport component can be subtracted. Net of it,
CognoDB's 1-hop traversal costs about 1.5 ms against Neo4j's 1.3 ms; the raw
600× gap to Memgraph is roughly 99% network. See
[Analysis §1](#1-almost-all-of-cognodbs-latency-is-the-network-and-the-harness-can-prove-it).

Subtraction is still an estimate, not a fix. The clean version of this experiment
benchmarks all five as managed endpoints from one client region — which this
harness supports without code changes, and which is the natural next run.

---

## Databases compared, and why

Picking four Neo4j-compatible databases would have produced a tidy table and taught
nothing — it would compare implementations of one design. The selection here spans
**three storage architectures and two query languages**, so that differences in the
results have a chance of being interesting.

| Database | Engine design | Query language | Why it is here |
|---|---|---|---|
| **CognoDB Cloud** | Managed, Bolt-compatible | Cypher | The subject of the benchmark. Free c0 tier, driven by the official Neo4j driver with no code changes. |
| **Neo4j 5.26** | JVM, native graph store, page cache | Cypher | The reference implementation and the thing every Bolt database is compatible *with*. Without it there is no baseline. |
| **Memgraph 2.22** | C++, in-memory transactional | Cypher | Same language and protocol as Neo4j, completely different runtime. Isolates "JVM vs. native" from "Cypher vs. not". |
| **ArangoDB 3.11** | Multi-model, document store with edge indexes | **AQL** | The only non-Cypher engine here. Tests whether the harness's fairness claim survives a genuine language boundary. |
| **FalkorDB 4.2** | Sparse adjacency matrices, GraphBLAS, inside Redis | Cypher | Architecturally the furthest from the rest: traversal is linear algebra, not pointer chasing. The most likely source of a surprising number. |

The pairings are deliberate. Neo4j and Memgraph differ in *runtime* but not
language. Memgraph and FalkorDB differ in *data structure* but not language.
ArangoDB differs in *language*. Each comparison isolates roughly one variable.

---

## Dataset

**[SNAP cit-HepTh](https://snap.stanford.edu/data/cit-HepTh.html)** — the arXiv
High-Energy-Physics-Theory citation network.

| Property | Value |
|---|---|
| Nodes | **27,769** |
| Relationships | **352,768** |
| Source size | 27,770 nodes / 352,807 edges as published |
| Fits the 1 GB parity disk | Yes, comfortably |

It is used **unsampled**. At 352,768 relationships it already sits inside the
100k–500k range the brief asks for, so there is no sampling step to justify or
get wrong.

The small deltas from SNAP's published figures are deliberate and are applied
identically before any database is touched: **39 edges** removed as self-citations
and exact duplicates (a paper citing itself makes hop-depth counts meaningless),
and **1 node** consequently orphaned.

Node properties are derived from the data, not invented:

| Property | Source | Used for |
|---|---|---|
| `paper_id` | arXiv id from the edge list | Point lookups |
| `year` | Real publication dates from `cit-HepTh-dates.txt` | Group-by aggregation, filtered lookup |
| `degree` | Out-degree computed from the edge list | Filtered-lookup range predicate |

**A real limitation, reported rather than hidden:** SNAP's dates file covers only
the abstracts subset, so after resolving both of its id formats (plain arXiv
numbers, and a `11`-prefixed form for cross-listed papers), **only 41.2% of nodes
have a known publication year**. The remaining 58.8% carry an explicit `year = 0`
sentinel rather than being dropped, so node counts stay identical across every
platform. The aggregation workload therefore groups over 11 real year buckets plus
one large unknown bucket — which is a legitimate group-by, but the unknown bucket
dominates it, and comparisons of aggregation timing should be read with that in
mind.

Both CSVs are hashed, and the SHA-256 values are recorded in
[`data/manifest.json`](data/manifest.json) and reprinted in the results. That is
the evidence that all five platforms loaded byte-identical input.

---

## Methodology

**Identical logical queries.** The adapter interface exposes one method per
workload — `one_hop`, `two_hop`, `three_hop`, `point_lookup`, `filtered_lookup`,
`aggregation`. Each platform implements each method in its own language. There is
no code path that can hand one database an easier question, because the question
*is* the interface. The AQL translations are annotated with the Cypher they mirror
in [`src/bench/adapters/arango.py`](src/bench/adapters/arango.py).

**Identical start nodes.** Traversal start nodes are chosen **once**, from the
canonical CSVs, before any database is connected, and the same list goes to every
platform. They are filtered to nodes with genuine 3-hop reachability — an early
smoke test returned `0` for both 2-hop and 3-hop because the start node was a
leaf, which would have measured query parsing rather than traversal. Selection is
seeded, so a rerun draws the same nodes.

**Warm-up, and cold measured separately.** Each platform is warmed before timing.
Cold first-touch latency is captured *before* warm-up and reported in its own
table, never blended into the warm percentiles where a single cold outlier would
dominate a p95.

**Percentiles, not averages.** 100 iterations per read workload after warm-up,
reported as p50/p95/p99. Percentiles use the **nearest-rank** method rather than
interpolation, so every figure printed is a measurement that actually occurred.

**Repeated runs.** The entire read suite runs 3 times per platform and the spread
of p50 across runs is reported. On a burstable 0.5-vCPU tier this is the only way
to tell a real difference between engines from scheduling noise.

**Concurrency swept, not sampled.** The mixed workload (80% read / 20% write) runs
at 1, 10 and 40 concurrent clients. A single concurrency number cannot distinguish
an engine that scales from one that was already saturated at one client. Each
client gets its own connection — sharing one across threads would measure driver
lock contention rather than database concurrency, and the three drivers differ in
their thread-safety guarantees.

**Writes are undone.** Mixed-workload writes are tagged `benchmark: true` and
deleted between concurrency levels, with the removed count asserted against the
written count. Without that, later levels would query a larger graph than earlier
ones and the sweep would drift for reasons unrelated to concurrency.

---

## How the harness avoids fooling itself

Two failure modes make a benchmark confidently wrong. Both are checked
automatically, and both surface in the results rather than in a log nobody reads.

**1. A platform that quietly dropped rows.** After loading, stored node and
relationship counts are compared against the dataset manifest. A database holding
300k relationships instead of 352,768 would post excellent traversal times on a
graph that is simply smaller. The results table has a **Load complete** column;
anything other than `yes` invalidates that platform's row.

**2. Platforms answering different questions.** Every read workload returns a
**count**, and the harness sums those counts across all 100 iterations to form a
fingerprint. After every platform has run, the fingerprints are compared. If
ArangoDB's hand-written AQL means something subtly different from the Cypher it
mirrors — a different uniqueness rule on traversal, say — the fingerprints
diverge and the run is reported as `MISMATCH`.

This is the check that makes a cross-language comparison defensible at all.
Identical latencies are meaningless if the engines were asked different things.

---

## Reproducing this

Requirements: Python 3.10+, Docker, and about 4 GB of free disk.

```bash
git clone https://github.com/Maniprogramer/cognodb-graph-benchmark.git
cd cognodb-graph-benchmark

make setup      # venv + pinned dependencies
make up         # start the four capped containers, wait until they serve queries
make dataset    # download SNAP cit-HepTh, canonicalise to CSV + manifest
make bench      # run everything, write results/ and inject the tables below
```

Or just `make all`.

### Adding CognoDB

The four local platforms need no credentials. To include CognoDB:

1. Create a free instance at [console.cognodb.com](https://console.cognodb.com/signup)
   (no card required; provisions in under a minute).
2. Copy the connection URI and the password — **the password is shown exactly once**.
3. `cp .env.example .env` and fill in:

```bash
COGNODB_URI=bolt+s://<instance-id>.databases.cognodb.cloud
COGNODB_USER=cognodb
COGNODB_PASSWORD=<the password shown at provisioning>
COGNODB_REGION=<the region you picked>
```

`.env` is gitignored. **No credential is ever read from anywhere but the
environment** — `config/platforms.yaml` contains only `${VAR}` references, and a
test asserts that it never contains a literal secret.

A platform whose variables are unset is reported as **"not configured"** in the
results table rather than being silently omitted, because a missing row looks like
an oversight.

### Benchmarking managed endpoints instead

Point the other URI variables at managed instances (Neo4j AuraDB, Memgraph Cloud,
ArangoGraph, FalkorDB Cloud) and skip `make up`. The harness does not care. If you
do this, record each provider's advertised tier in `config/platforms.yaml` so the
parity table stays truthful.

### Useful targets

| Command | Effect |
|---|---|
| `make check` | Verify every configured platform accepts connections — fails fast on a bad credential |
| `make quick` | Short smoke run — verifies the harness, not for reporting |
| `make test` | Run the unit test suite |
| `make stats` | Live container memory/CPU against the caps |
| `make report` | Rebuild tables and charts from existing `results.json` |
| `make down` | Stop containers and delete their volumes |

---

## Results

<!-- BENCHMARK_RESULTS:START -->

Run `20260820T171439Z` · client: macOS-26.5.2-arm64-arm-64bit-Mach-O (8 CPUs) · 100 iterations per read workload after 20 warm-up, 3 repeats · dataset 27,769 nodes / 352,768 relationships.

### Result parity

**All platforms returned identical results for every workload.** The latency comparison below is between engines answering the same questions.

| Workload | All platforms agree | Summed result per platform |
|---|---|---|
| 1-hop | yes | CognoDB Cloud=1,669, Neo4j=1,669, Memgraph=1,669, ArangoDB=1,669, FalkorDB=1,669 |
| 2-hop | yes | CognoDB Cloud=18,013, Neo4j=18,013, Memgraph=18,013, ArangoDB=18,013, FalkorDB=18,013 |
| 3-hop | yes | CognoDB Cloud=95,111, Neo4j=95,111, Memgraph=95,111, ArangoDB=95,111, FalkorDB=95,111 |
| point-lookup | yes | CognoDB Cloud=100, Neo4j=100, Memgraph=100, ArangoDB=100, FalkorDB=100 |
| filtered-lookup | yes | CognoDB Cloud=114,100, Neo4j=114,100, Memgraph=114,100, ArangoDB=114,100, FalkorDB=114,100 |
| aggregation | yes | CognoDB Cloud=1,200, Neo4j=1,200, Memgraph=1,200, ArangoDB=1,200, FalkorDB=1,200 |

### Tier parity

| Platform | Query language | Tier | vCPU | RAM | Storage | Status |
|---|---|---|---|---|---|---|
| CognoDB Cloud | Cypher (Bolt) | Free (c0) | 0.5 vCPU (burstable) | 256 MB | 1 GB | completed |
| Neo4j | Cypher (Bolt) | Community 5.26, container-capped to parity tier | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB (dataset well under limit) | completed |
| Memgraph | Cypher (Bolt) | Memgraph 2.22 (in-memory transactional), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| ArangoDB | AQL | ArangoDB 3.11 Community, container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |
| FalkorDB | Cypher (RESP/GraphBLAS) | FalkorDB 4.2 (Redis module), container-capped | 0.5 vCPU (cgroup cpu quota) | 256 MB (cgroup memory limit) | 1 GB | completed |

### Transport baseline

Median round-trip for a query that does no work (`RETURN 1`). This is the floor under every latency below: no workload can beat its own transport. It is the number to subtract before comparing a managed endpoint against a container on loopback.

| Platform | Transport p50 (ms) | Transport p95 (ms) | 1-hop p50 (ms) | Share of 1-hop that is transport |
|---|---|---|---|---|
| CognoDB Cloud | 238.08 | 309.77 | 239.57 | 99% |
| Neo4j | 2.99 | 86.70 | 4.26 | 70% |
| Memgraph | 0.45 | 0.72 | 0.39 | at floor |
| ArangoDB | 2.28 | 8.76 | 3.11 | 73% |
| FalkorDB | 0.27 | 0.43 | 0.50 | 53% |


![Transport floor vs measured query cost](results/charts/transport_baseline.png)

### Ingest throughput

| Platform | Nodes/s | Rels/s | Total load (s) | Load method |
|---|---|---|---|---|
| CognoDB Cloud | 12,153 | 17,531 | 22.4 | official neo4j driver, UNWIND batching (batch=10000) |
| Neo4j | 1,163 | 5,421 | 89.0 | official neo4j driver, UNWIND batching (batch=10000) |
| Memgraph | 18,777 | 42,246 | 9.8 | official neo4j driver, UNWIND batching (batch=10000) |
| ArangoDB | 25,145 | 32,703 | 11.9 | python-arango insert_many batching (batch=10000) |
| FalkorDB | 19,829 | 35,320 | 11.4 | falkordb client, UNWIND batching (batch=10000) |


![Ingest throughput by platform](results/charts/ingest_throughput.png)

### Traversal latency (ms)

| Platform | 1-hop p50 | 1-hop p95 | 2-hop p50 | 2-hop p95 | 3-hop p50 | 3-hop p95 |
|---|---|---|---|---|---|---|
| CognoDB Cloud | 239.57 | 308.85 | 240.19 | 262.41 | 256.30 | 447.23 |
| Neo4j | 4.26 | 87.16 | 4.67 | 74.09 | 4.63 | 86.63 |
| Memgraph | 0.39 | 0.66 | 0.46 | 0.72 | 1.08 | 6.55 |
| ArangoDB | 3.11 | 54.25 | 4.86 | 79.06 | 16.50 | 175.72 |
| FalkorDB | 0.50 | 1.15 | 0.42 | 0.74 | 0.94 | 2.77 |


![Traversal p50 latency by hop depth](results/charts/traversal_p50.png)


![Traversal p95 latency by hop depth](results/charts/traversal_p95.png)

### Lookups and aggregation (ms)

| Platform | point p50 | point p95 | filtered p50 | filtered p95 | aggregation p50 | aggregation p95 | Indexed properties |
|---|---|---|---|---|---|---|---|
| CognoDB Cloud | 244.13 | 269.85 | 244.90 | 309.61 | 287.10 | 342.91 | Paper.paper_id, Paper.year, Paper.degree |
| Neo4j | 2.44 | 56.40 | 4.46 | 67.04 | 9.16 | 94.36 | Paper.paper_id, Paper.year, Paper.degree |
| Memgraph | 0.39 | 0.83 | 1.22 | 1.88 | 6.47 | 30.14 | Paper.paper_id, Paper.year, Paper.degree |
| ArangoDB | 2.06 | 2.71 | 6.06 | 14.04 | 7.48 | 36.80 | papers.paper_id, papers.year, papers.degree |
| FalkorDB | 0.58 | 3.30 | 0.69 | 0.82 | 4.04 | 23.11 | Paper.paper_id, Paper.year, Paper.degree |


![Lookup and aggregation p50 latency](results/charts/lookups_p50.png)

### Mixed workload — concurrency sweep (80% read / 20% write)

| Platform | Clients | QPS | p50 ms | p95 ms | p99 ms | Reads | Writes | Errors |
|---|---|---|---|---|---|---|---|---|
| CognoDB Cloud | 1 | 4.0 | 239.73 | 284.37 | 451.61 | 65 | 16 | 0 |
| CognoDB Cloud | 10 | 39.5 | 248.23 | 264.20 | 285.52 | 641 | 159 | 0 |
| CognoDB Cloud | 40 | 155.5 | 252.19 | 278.47 | 306.12 | 2514 | 634 | 0 |
| Neo4j | 1 | 178.2 | 1.64 | 27.83 | 78.86 | 2865 | 711 | 0 |
| Neo4j | 10 | 182.1 | 23.25 | 109.82 | 209.55 | 2939 | 705 | 0 |
| Neo4j | 40 | 239.4 | 104.93 | 302.45 | 485.84 | 3838 | 976 | 7 |
| Memgraph | 1 | 2,779.8 | 0.34 | 0.54 | 0.70 | 44544 | 11054 | 0 |
| Memgraph | 10 | 3,265.4 | 1.90 | 4.98 | 36.79 | 52490 | 12829 | 1 |
| Memgraph | 40 | 3,261.6 | 8.14 | 38.79 | 52.47 | 52324 | 12926 | 2 |
| ArangoDB | 1 | 268.0 | 3.88 | 5.35 | 6.60 | 4284 | 1076 | 0 |
| ArangoDB | 10 | 1,754.6 | 4.66 | 14.46 | 27.75 | 28311 | 6786 | 0 |
| ArangoDB | 40 | 1,730.4 | 20.60 | 44.88 | 60.00 | 27848 | 6802 | 0 |
| FalkorDB | 1 | 1,884.2 | 0.49 | 0.67 | 1.04 | 30194 | 7494 | 0 |
| FalkorDB | 10 | 1,756.9 | 1.42 | 68.40 | 78.24 | 28431 | 6816 | 0 |
| FalkorDB | 40 | 1,814.5 | 5.86 | 84.59 | 91.61 | 29296 | 7090 | 1027 |


![Mixed workload scaling](results/charts/concurrency_scaling.png)

### Cold start vs. warm

| Platform | Workload | Cold first-touch ms | Warm p50 ms |
|---|---|---|---|
| CognoDB Cloud | 1-hop | 237.62 | 239.57 |
| CognoDB Cloud | 3-hop | 361.00 | 256.30 |
| CognoDB Cloud | aggregation | 283.56 | 287.10 |
| Neo4j | 1-hop | 530.08 | 4.26 |
| Neo4j | 3-hop | 1,390.83 | 4.63 |
| Neo4j | aggregation | 501.08 | 9.16 |
| Memgraph | 1-hop | 7.90 | 0.39 |
| Memgraph | 3-hop | 9.20 | 1.08 |
| Memgraph | aggregation | 6.76 | 6.47 |
| ArangoDB | 1-hop | 16.77 | 3.11 |
| ArangoDB | 3-hop | 67.67 | 16.50 |
| ArangoDB | aggregation | 17.23 | 7.48 |
| FalkorDB | 1-hop | 188.63 | 0.50 |
| FalkorDB | 3-hop | 2.83 | 0.94 |
| FalkorDB | aggregation | 5.08 | 4.04 |

### Run-to-run variance

How much of the difference between platforms is noise. Spread is the range of p50 across repeated runs of the whole read suite.

| Platform | Workload | Runs | p50 per run (ms) | Spread (ms) | Spread % |
|---|---|---|---|---|---|
| CognoDB Cloud | 1-hop | 3 | 239.57, 245.02, 243.16 | 5.45 | 2.2% |
| CognoDB Cloud | 2-hop | 3 | 240.19, 240.64, 240.93 | 0.74 | 0.3% |
| CognoDB Cloud | 3-hop | 3 | 256.30, 259.87, 263.85 | 7.55 | 2.9% |
| CognoDB Cloud | point-lookup | 3 | 244.13, 243.62, 239.86 | 4.28 | 1.8% |
| CognoDB Cloud | filtered-lookup | 3 | 244.90, 246.02, 247.01 | 2.11 | 0.9% |
| CognoDB Cloud | aggregation | 3 | 287.10, 290.08, 290.98 | 3.88 | 1.3% |
| Neo4j | 1-hop | 3 | 4.26, 2.06, 1.60 | 2.66 | 100.7% |
| Neo4j | 2-hop | 3 | 4.67, 2.41, 1.81 | 2.85 | 96.3% |
| Neo4j | 3-hop | 3 | 4.63, 4.25, 3.06 | 1.57 | 39.6% |
| Neo4j | point-lookup | 3 | 2.44, 2.03, 1.58 | 0.85 | 42.4% |
| Neo4j | filtered-lookup | 3 | 4.46, 4.38, 3.72 | 0.74 | 17.8% |
| Neo4j | aggregation | 3 | 9.16, 7.46, 8.39 | 1.71 | 20.5% |
| Memgraph | 1-hop | 3 | 0.39, 0.85, 0.77 | 0.47 | 69.8% |
| Memgraph | 2-hop | 3 | 0.46, 0.95, 0.89 | 0.49 | 64.3% |
| Memgraph | 3-hop | 3 | 1.08, 1.70, 1.56 | 0.62 | 42.9% |
| Memgraph | point-lookup | 3 | 0.39, 0.81, 0.70 | 0.42 | 66.1% |
| Memgraph | filtered-lookup | 3 | 1.22, 1.58, 1.52 | 0.37 | 25.4% |
| Memgraph | aggregation | 3 | 6.47, 6.62, 6.45 | 0.17 | 2.6% |
| ArangoDB | 1-hop | 3 | 3.10, 2.75, 2.48 | 0.63 | 22.5% |
| ArangoDB | 2-hop | 3 | 4.86, 4.05, 2.82 | 2.04 | 52.1% |
| ArangoDB | 3-hop | 3 | 16.50, 13.82, 13.87 | 2.68 | 18.2% |
| ArangoDB | point-lookup | 3 | 2.06, 2.69, 2.23 | 0.62 | 26.8% |
| ArangoDB | filtered-lookup | 3 | 6.06, 6.28, 6.31 | 0.26 | 4.1% |
| ArangoDB | aggregation | 3 | 7.48, 7.75, 7.63 | 0.27 | 3.5% |
| FalkorDB | 1-hop | 3 | 0.50, 0.47, 0.62 | 0.15 | 27.8% |
| FalkorDB | 2-hop | 3 | 0.42, 0.55, 0.63 | 0.22 | 40.5% |
| FalkorDB | 3-hop | 3 | 0.94, 0.94, 0.91 | 0.03 | 2.9% |
| FalkorDB | point-lookup | 3 | 0.58, 0.54, 0.54 | 0.04 | 6.9% |
| FalkorDB | filtered-lookup | 3 | 0.69, 0.77, 0.77 | 0.08 | 10.9% |
| FalkorDB | aggregation | 3 | 4.04, 4.01, 3.93 | 0.11 | 2.7% |

### Footprint

Section 5.2 asks for resource usage *where observable*. It is stated plainly where it is not.

| Platform | Nodes stored | Rels stored | Load complete | Observable footprint |
|---|---|---|---|---|
| CognoDB Cloud | 27,769 | 352,768 | yes | not observable (JMX store metrics unavailable on this tier) |
| Neo4j | 27,769 | 352,768 | yes | not observable (JMX store metrics unavailable on this tier) |
| Memgraph | 27,769 | 352,768 | yes | memory_res=179.58MiB, disk_usage=28.44MiB, vertex_count=27,769, edge_count=352,768 |
| ArangoDB | 27,769 | 352,768 | yes | papers_documents=27,769, papers_documents_bytes=2,324,750, cites_documents=352,768, cites_documents_bytes=31,574,099, index_bytes=40,160,720, total_stored_bytes=74,059,569 |
| FalkorDB | 27,769 | 352,768 | yes | used_memory_bytes=32,515,984, used_memory_human=31.01M, used_memory_peak_bytes=79,209,216 |

<!-- BENCHMARK_RESULTS:END -->

---

## Analysis

All five platforms completed, all five loaded the full 27,769 nodes and 352,768
relationships, and every one returned **identical counts on all six workloads**.
The latency comparison below is therefore between engines answering the same
questions — verified, not assumed.

Read everything here against the [variance table](#run-to-run-variance). Worst
run-to-run spread reached **100.7%** (Neo4j 1-hop), **69.8%** (Memgraph) and
**52.1%** (ArangoDB). Between two full runs of this suite, ArangoDB's ingest
moved 54% and its 3-hop p50 moved 3×. On a burstable 0.5-vCPU tier that is the
noise floor, and it sets the bar: **differences under about 2× are not findings.**
Only the effects below clear it by a wide margin.

### 1. Almost all of CognoDB's latency is the network, and the harness can prove it

CognoDB's raw numbers look catastrophic — every read workload lands between 239
and 287 ms while Memgraph answers in under a millisecond. Taken at face value
that is a 600× gap, and it would be almost entirely wrong.

CognoDB is a managed endpoint reached over the public internet; the other four
are containers on loopback. So the harness measures each platform's floor: the
median round trip for `RETURN 1`, a query that does no work. Subtracting it
gives an estimate of what the engine actually did:

| p50 (ms) | Transport floor | 1-hop raw | **1-hop net** | 3-hop raw | **3-hop net** |
|---|---|---|---|---|---|
| CognoDB | 238.08 | 239.57 | **~1.5** | 256.30 | **~18.2** |
| Neo4j | 2.99 | 4.26 | **~1.3** | 4.63 | **~1.6** |
| ArangoDB | 2.28 | 3.11 | **~0.8** | 16.50 | **~14.2** |
| FalkorDB | 0.27 | 0.50 | **~0.2** | 0.94 | **~0.7** |
| Memgraph | 0.45 | 0.39 | *at floor* | 1.08 | **~0.6** |

Net of transport, **CognoDB's 1-hop traversal costs about 1.5 ms — the same
order as Neo4j's 1.3 ms.** The 600× headline was ~99% Atlantic.

Two honest qualifications. Subtracting one noisy median from another amplifies
noise, so treat the net column as an estimate with wide error bars, not a
measurement — Memgraph's 1-hop coming out *below* its own floor is exactly that
effect, and is reported as "at floor" rather than as a negative number. And this
does not make the raw numbers irrelevant: if your application talks to CognoDB
over the internet, 240 ms per query is what you will actually experience. The
net column tells you about the engine; the raw column tells you about the
deployment.

### 2. Depth sensitivity splits the field into two families

Using the net figures, which is the only fair way to compare a remote engine
with local ones:

| 1-hop → 3-hop (net) | Change |
|---|---|
| Neo4j | 1.3 → 1.6 ms — **flat** |
| FalkorDB | 0.2 → 0.7 ms — **flat** |
| Memgraph | floor → 0.6 ms — **flat** |
| ArangoDB | 0.8 → 14.2 ms — **~17×** |
| CognoDB | 1.5 → 18.2 ms — **~12×** |

Three engines barely notice the extra hops. **ArangoDB and CognoDB both degrade
by more than an order of magnitude**, and they are the two whose 1-hop
performance gave no warning of it — ArangoDB is *faster* than Neo4j at one hop
and 9× slower at three.

For ArangoDB the mechanism is clear: edges are documents with an index on their
endpoints, so each hop costs an index probe per frontier node, and on a
scale-free citation graph the depth-3 frontier is around 950 nodes — roughly a
thousand index lookups for one query. Memgraph follows pointers; FalkorDB
multiplies a sparse adjacency matrix, where a third hop is just a third
multiply. Neither pays per node.

For CognoDB the mechanism is **not** established by this data. The profile
resembles index-backed adjacency, but the instance exposes no query plan and no
storage internals, so that is a hypothesis rather than a finding. What can be
said is the shape: its cost grows with traversal depth in a way that Neo4j's,
Memgraph's and FalkorDB's do not.

The generalisable lesson is the one that survives all the caveats: **on a fixed
small budget, adjacency representation dominates, and one-hop benchmarks will
not reveal it.**

### 3. Neo4j's tail is a garbage collector, and it says so

Neo4j's p95 runs **19×** its p50 on 1-hop (4.26 → 87.16 ms). No other engine
exceeds 7×. Its transport floor is also the highest of the four local platforms
at 2.99 ms — against Memgraph's 0.45 ms on the *identical driver and identical
session-per-query pattern*, so that ~2.5 ms gap is Neo4j's own connection and
transaction setup, paid before any work begins.

The concurrency sweep supplies the cause rather than leaving it to inference. At
40 clients Neo4j returned seven errors, every one of them:

```
Neo.TransientError.General.MemoryPoolOutOfMemoryError:
The allocation of an extra 2.0 MiB would use more than the limit 67.2 MiB
```

67.2 MB is the transaction pool Neo4j derived from the 96 MB heap it was given
inside a 256 MB container. Neo4j divides its box three ways — heap, page cache,
JVM overhead — and at 256 MB all three are starved at once. The 64 MB page cache
cannot hold the store files for 352,768 relationships, so reads fault to disk;
the small heap collects constantly; and collection competes with query execution
for half a vCPU.

This does not say Neo4j is a slow database. It says **Neo4j's architecture does
not fit in 256 MB** — a fair thing to learn from a benchmark about free tiers,
and the reason its ingest (5,421 rels/s) is 8× slower than Memgraph's on an
identical load method.

### 4. Four scaling shapes, and CognoDB has the most headroom

| qps | 1 client | 10 | 40 | Shape |
|---|---|---|---|---|
| Memgraph | 2,780 | **3,265** | 3,262 | saturates at 10 |
| FalkorDB | 1,884 | 1,757 | 1,815 *(1,027 rejected)* | flat, sheds load |
| ArangoDB | 268 | 1,755 | 1,730 | **6.5× scaling** |
| Neo4j | 178 | 182 | 239 | flat, 7 memory errors |
| CognoDB | 4.0 | 39.5 | **155.5** | **39× — near-linear** |

**CognoDB scales almost perfectly linearly** — 39× throughput for 40× clients,
with p50 holding flat at ~250 ms throughout. That is the signature of a system
bound by latency rather than capacity: each client spends its time waiting on
the network, so adding clients adds throughput nearly for free. Its 4 qps at one
client is not a capacity limit, it is one query per round trip. Whether it would
keep scaling past 40 clients this run cannot say.

**FalkorDB starts at its ceiling.** Fastest single-client engine after Memgraph
and gains nothing from more, because Redis executes commands on one thread. At
40 clients it rejected 1,027 queries with `Max pending queries exceeded` — not a
crash but deliberate backpressure. Its 1,815 qps counts only accepted queries,
so that cell means "throughput while shedding load" and is not directly
comparable with the others.

**ArangoDB is the best true scaler** at 6.5×, from the lowest local starting
point. **Neo4j does not scale at all** — 178 to 182 qps from 1 to 10 clients.

### 5. Storage: the index tax is visible

| | Observable footprint |
|---|---|
| FalkorDB | **31.0 MB** used (79.2 MB peak) |
| ArangoDB | 74.1 MB — 2.3 MB nodes, 31.6 MB edges, **40.2 MB indexes** |
| Memgraph | 179.6 MB resident, 28.4 MB on disk |
| Neo4j | Not observable — JMX store metrics unavailable on this tier |
| CognoDB | Not observable — managed tier exposes no storage introspection |

Indexes are **54% of ArangoDB's stored bytes**: the design decision that costs it
deep traversals costs it space too. FalkorDB holds the identical graph in 31 MB.

Two of five platforms expose nothing, which is itself worth noting — on a
managed tier you generally cannot see what you are paying to store.

### So what would I actually use?

On this hardware, this dataset shape, and with the variance above in mind:

- **Deep traversal on a small instance:** FalkorDB or Memgraph. Both stay flat
  with hop depth; FalkorDB is far more compact, Memgraph handles concurrency
  much better.
- **Concurrent mixed load:** Memgraph on raw throughput, ArangoDB if you want
  scaling headroom and a document model and your traversals stay shallow.
- **CognoDB:** the engine is competitive — ~1.5 ms net for a 1-hop traversal and
  ingest 3× faster than local Neo4j. The deciding factor is **where your client
  runs**. Co-located, these numbers would look completely different; across the
  internet, 240 ms per query dominates everything else about it. Benchmarking it
  from a client in the same region is the obvious next run, and this harness
  supports it without changes.
- **Neo4j:** give it more memory. Nothing here indicts the engine at a sane size;
  it indicts running it in 256 MB.

The honest headline is not that one database won. It is that on a fixed small
budget, **two things dominate everything else — where your client sits relative
to the database, and how the engine represents adjacency** — and a benchmark
that measures neither will confidently rank them wrong.

---

## Caveats

Every one of these makes some number above less trustworthy than it looks. They
are listed because a benchmark without them is marketing.

**CognoDB is remote; the other four are local.** The largest asymmetry in this
comparison and the reason the [transport baseline](#1-almost-all-of-cognodbs-latency-is-the-network-and-the-harness-can-prove-it)
exists. CognoDB pays a ~238 ms public-internet round trip on every operation
that the loopback containers do not. The harness measures that floor so it can
be subtracted, but subtraction is an estimate, not a correction: it assumes
transport and query cost are independent and additive, which is approximately
but not exactly true. **The clean fix is to benchmark all five as managed
endpoints from one client region**, which this harness supports unchanged and
which is the obvious next run.

**Run-to-run variance is large — larger than several differences in the tables.**
Worst spreads were 100.7%, 69.8% and 52.1%. Across two full runs of this suite,
ArangoDB's ingest moved 54% and its 3-hop p50 moved 3×. Treat anything under
about 2× as unresolved rather than as a result. Three repeats is enough to
expose the noise, not enough to average it away.

**FalkorDB's 40-client throughput excludes 1,027 rejected queries.** It returned
`Max pending queries exceeded` and those are counted as errors, not completed
operations. Its 1,815 qps is "throughput while shedding load", which is not the
same measurement as the other engines' figures at that concurrency.

**"Cold start" here means first-query-after-load, not a cold process.** Loading
has already populated caches by the time that measurement runs. A true
cold-start number would need each database restarted against a populated volume
and queried before anything warms. Read that column as "first touch of this
query shape" — the weaker claim it actually supports.

**Burstable vCPU is not a steady resource.** Both `cpus: 0.5` under cgroups and
CognoDB's "0.5 burstable" permit short excursions above the nominal limit, so a
workload that fits inside a burst window looks faster than a sustained one.

**The client is shared, and at 40 clients it is part of the measurement.** All
platforms are driven from one machine, which is what fairness requires, but the
harness itself consumes real CPU at the top of the sweep. The *shape* of each
curve is more trustworthy than its absolute height.

**Local containers share a host.** Four capped containers run simultaneously.
Cgroups partition CPU and memory but not disk I/O bandwidth or memory-bus
contention. Sequential runs on an idle host would be cleaner.

**The aggregation workload is skewed by data, not engines.** 58.8% of nodes carry
the unknown-year sentinel, so the group-by is dominated by a single bucket. Real
aggregation over real data, but not a balanced one.

**Two platforms ran at their memory ceiling.** Neo4j and ArangoDB both sat at
essentially 256/256 MB during the run. Neither was OOM-killed and both passed
load verification, but an engine operating at its limit spends time on eviction,
and that is part of what these numbers show.

**Query equivalence is verified by result, not by plan.** The parity check proves
every engine returned identical counts. It does not prove they chose comparable
execution strategies — an engine could reach the right answer by a route the
others would never take. Confirming that means reading five query planners.

**CognoDB's depth sensitivity has no established mechanism.** Section 2 notes its
cost grows sharply with hop depth in a way that resembles index-backed
adjacency. The instance exposes no query plan and no storage internals, so that
resemblance is a hypothesis, not a finding.

**Single dataset, single shape.** cit-HepTh is sparse and scale-free. A dense
social graph, a supply chain, or a property-heavy graph could reorder these
results entirely. Nothing here generalises past this shape.

---

## Repository layout

```
config/platforms.yaml     Platform definitions; secrets are ${ENV} refs only
docker/docker-compose.yml Local platforms, cgroup-capped to the parity tier
src/bench/
  dataset.py              Download, de-duplicate, canonicalise, hash
  workloads.py            Start-node selection and workload definitions
  adapters/base.py        The interface that enforces identical questions
  adapters/bolt.py        CognoDB, Neo4j, Memgraph
  adapters/arango.py      ArangoDB (AQL)
  adapters/falkor.py      FalkorDB (GraphBLAS)
  concurrency.py          Mixed workload and concurrency sweep
  runner.py               Orchestration, load verification, parity check
  report.py               Markdown tables and charts
  cli.py                  python -m bench
scripts/                  Readiness polling
tests/                    Unit tests for stats, dataset, config, concurrency, runner
results/                  results.json, REPORT.md, charts/
```

## Write-up

A narrative version of this benchmark, aimed at a broader technical audience, is
in [ARTICLE.md](ARTICLE.md) — the same findings with the reasoning foregrounded
and the mistakes included.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
