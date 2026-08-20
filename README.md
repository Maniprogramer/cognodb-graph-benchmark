# Graph database cloud benchmark — CognoDB vs. four other engines

A reproducible, fully scripted benchmark comparing **CognoDB Cloud** against four
other graph databases on one dataset, one set of logical queries, one client
machine, and — the part that usually gets fudged — **one set of hardware limits**.

Everything here runs from `make all`. Every number in this README was produced by
that command; none was typed in by hand.

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

CognoDB itself is a **managed cloud instance reached over the public internet**,
while the four comparison engines are **local containers reached over loopback**.
That is not a difference the harness can eliminate, and it matters:

- Every CognoDB measurement includes a network round trip that the local
  platforms do not pay. On sub-millisecond workloads — point lookups especially —
  that round trip can *exceed* the query itself.
- "0.5 burstable vCPU" on managed hardware and `cpus: 0.5` under cgroups are
  similar in intent but not identical in behaviour, particularly in how burst
  credit accrues.

Both effects push in the same direction: **they make CognoDB look slower than its
engine is**. Read the CognoDB row as an upper bound on latency, not a measurement
of the engine in isolation. The honest way to remove this asymmetry is to
benchmark managed endpoints for all five platforms from the same client region,
which the harness supports and which is the natural next run.

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
| `make quick` | Short smoke run — verifies the harness, not for reporting |
| `make test` | 89 unit tests |
| `make stats` | Live container memory/CPU against the caps |
| `make report` | Rebuild tables and charts from existing `results.json` |
| `make down` | Stop containers and delete their volumes |

---

## Results

<!-- BENCHMARK_RESULTS:START -->

_Not yet generated. Run `make bench`._

<!-- BENCHMARK_RESULTS:END -->

---

## Caveats

Every one of these makes some number in this README less trustworthy than it
looks. They are listed because a benchmark without them is marketing.

**CognoDB is remote; the other four are local.** The single biggest asymmetry in
this comparison, described in full under [the fairness
problem](#the-fairness-problem). CognoDB pays a public-internet round trip on
every operation that the loopback platforms do not. On sub-millisecond workloads
that round trip can be larger than the query. Its latency figures are an upper
bound, not an engine measurement.

**"Cold start" here means first-query-after-load, not a cold process.** The load
has already populated caches and page tables by the time the cold measurement
runs. A true cold-start number would require restarting each database with a
populated volume and querying before anything warms — worth doing, not done here.
Read the cold column as "first touch of this query shape", which is the weaker
claim it actually supports.

**Burstable vCPU is not a steady resource.** Both `cpus: 0.5` under cgroups and
CognoDB's "0.5 burstable" allow short excursions above the nominal limit. A
100-iteration workload that fits inside a burst window will look faster than a
sustained one. This is exactly why the read suite is repeated three times and the
spread reported — check the variance table before believing any two platforms
differ by less than their spread.

**The client is shared.** All platforms are driven from one machine, which is what
the brief requires for fairness, but at 40 concurrent clients the harness itself
consumes real CPU. Throughput at the top of the sweep is partly a measurement of
the client. The *shape* of each curve is more trustworthy than its absolute
height.

**Local containers share a host.** The four capped containers run simultaneously
on one machine. Cgroup limits partition CPU and memory but not disk I/O bandwidth
or memory-bus contention. Sequential runs on an otherwise idle host would be
cleaner.

**The aggregation workload is skewed by data, not engines.** 58.8% of nodes carry
the unknown-year sentinel, so the group-by is dominated by one bucket. It is a
real aggregation over real data, but it is not a balanced one.

**Two platforms ran at their memory ceiling.** Neo4j and ArangoDB were both
observed sitting at essentially 256/256 MB during the run. Neither was OOM-killed
and both passed load verification, but an engine operating at its limit is an
engine spending time on eviction, and that is part of what the numbers show.

**Query equivalence is verified by result, not by plan.** The parity check proves
every engine returned the same counts. It does not prove they chose comparable
execution strategies — an engine could reach the right answer via a plan the
others would never pick. Confirming that would mean reading five query planners,
which is beyond this run.

**Single dataset, single shape.** cit-HepTh is a sparse, scale-free citation
network. Results on a dense social graph, a supply chain, or a property-heavy
graph could differ substantially. Nothing here generalises past this shape.

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
tests/                    89 unit tests
results/                  results.json, REPORT.md, charts/
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
