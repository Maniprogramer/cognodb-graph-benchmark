# I gave four graph databases 256 MB of RAM and asked them the same question

*A benchmark about fairness, and what happens when you take a free tier literally.*

---

Most database benchmarks are useless, and they're useless for a boring reason:
the two systems weren't given the same machine.

You've seen the shape of it. A vendor benchmarks their managed service against a
competitor's free tier. Or someone runs Postgres on a laptop against a cloud
warehouse on 32 cores. The numbers are real, the graphs are pretty, and the
conclusion is worthless — because what got measured was the hardware bill, not
the engine.

I wanted to do the opposite. CognoDB's free tier is **0.5 burstable vCPU, 256 MB
of RAM, and 1 GB of disk**. That's small. So instead of giving CognoDB's little
instance a fight it couldn't win, I made that envelope the *rule*: every database
in this comparison gets exactly 0.5 vCPU and 256 MB, and not a byte more.

Then I asked all of them the same six questions, 100 times each, three times over, and watched.

Same hardware. Same data. Same questions, verified identical. The spread on
three-hop traversal was **71×**, and the engine that won it wasn't the one with
"graph" in its marketing.

One caveat before you read on, because it's a real one: **CognoDB isn't in these
numbers.** I built the harness around it, but no instance was provisioned before
the reporting run, so it shows up as *not configured* rather than as a number I
made up. What follows compares the four engines that did run.

---

## The setup

**The data:** [SNAP cit-HepTh](https://snap.stanford.edu/data/cit-HepTh.html) —
the arXiv high-energy-physics citation network. 27,769 papers, 352,768 citations
between them. Real data, real skew: a handful of papers are cited hundreds of
times and most are barely cited at all. It's a scale-free graph, which is what
makes traversal interesting — hop counts explode unevenly.

**The contenders:**

| | Engine | Language | In this run? |
|---|---|---|---|
| **Neo4j 5.26** | JVM, native graph store, page cache | Cypher | yes |
| **Memgraph 2.22** | C++, in-memory | Cypher | yes |
| **ArangoDB 3.11** | Multi-model, document store + edge index | AQL | yes |
| **FalkorDB 4.2** | Sparse matrices + GraphBLAS, inside Redis | Cypher | yes |
| **CognoDB** | Managed, Bolt-compatible | Cypher | **no — not provisioned** |

I picked these deliberately, not for variety's sake. Neo4j and Memgraph speak the
*same language* but have completely different runtimes — that isolates "JVM vs.
native". Memgraph and FalkorDB are both in-memory but store the graph in
completely different *shapes* — that isolates data structure. ArangoDB is the only
one that doesn't speak Cypher at all.

Four Neo4j-compatible databases would have made a tidy table and taught me
nothing.

---

## The part everyone skips

Here's the question that kept me up: **how do I know they're answering the same
question?**

It's easy to write Cypher for "count distinct nodes exactly three hops out." It's
easy to write AQL that looks equivalent. It is *not* easy to be sure they mean the
same thing — AQL traversals have uniqueness options that silently change what gets
counted, and if I got that wrong, I'd be comparing a hard question to an easy one
and calling the difference "performance."

So every query in this benchmark returns a **count**, and the harness sums those
counts across all 100 iterations into a fingerprint. After every database has run,
it compares fingerprints. If ArangoDB's AQL means something subtly different from
the Cypher it mirrors, the numbers diverge and the whole run is flagged
`MISMATCH`.

They matched. Exactly, on all six workloads, across every engine. That single
check is the reason I trust anything below it.

The harness also verifies the load: after ingesting, it compares stored node and
relationship counts against a SHA-256-stamped manifest of the source CSVs. A
database that quietly dropped 15% of the edges would post *excellent* traversal
times — on a graph that's simply smaller.

---

## The results

### Loading 352,768 relationships

Identical method everywhere — batched parameterised inserts, 10,000 rows a batch.

| | Rels/sec | Total time |
|---|---|---|
| Memgraph | 34,909 | 14.6 s |
| FalkorDB | 28,917 | 14.4 s |
| ArangoDB | 24,118 | 16.5 s |
| **Neo4j** | **2,518** | **190.2 s** |

Neo4j took **three minutes** to do what Memgraph did in fifteen seconds. Not
because Neo4j is a bad database — because inside a 256 MB box, its 64 MB page
cache can't hold the store files it's writing through, and it thrashes. The two
in-memory engines never touch disk on the write path at all.

### Traversal: where it gets interesting

This is the table I'd frame.

| p50 (ms) | 1-hop | 2-hop | 3-hop |
|---|---|---|---|
| FalkorDB | 0.61 | 0.47 | **0.93** |
| Memgraph | 0.88 | 0.57 | 1.44 |
| Neo4j | 5.06 | 4.55 | 6.82 |
| ArangoDB | 2.80 | 4.51 | **66.45** |

Look at ArangoDB. It **beats Neo4j at one hop** — then falls off a cliff at three,
landing 71× behind FalkorDB.

That cliff isn't a bug or a tuning miss. It's the data structure. ArangoDB keeps
edges as documents with an index on their endpoints, so every hop costs an index
probe *per node in the frontier*. On a scale-free citation graph the three-hop
frontier is around 950 nodes — so one query becomes roughly a thousand index
lookups. Memgraph follows pointers. FalkorDB multiplies a sparse matrix, where a
third hop is just a third multiply. Neither pays per-node.

The lesson generalises past these four products: **indexed adjacency degrades
exactly where graph databases are supposed to win.** One or two hops? ArangoDB is
genuinely competitive and hands you a document model for free. Three or more? No
amount of tuning closes a 71× gap that comes from the storage layout.

### The JVM tells on itself

Neo4j's p95 ran 10–20× its p50 on every single workload. Everyone else sat between
2× and 4×.

I didn't have to guess why. Under 40 concurrent clients, Neo4j returned this:

```
Neo.TransientError.General.MemoryPoolOutOfMemoryError:
The allocation of an extra 2.0 MiB would use more than the limit 67.2 MiB
```

67.2 MB is the transaction memory pool it derived from the 96 MB heap I gave it.
Neo4j splits its box three ways — heap, page cache, JVM overhead — and at 256 MB
all three are starved simultaneously. The tail latency is garbage collection,
competing with query execution for half a vCPU.

Again: this doesn't say Neo4j is slow. It says Neo4j's architecture doesn't fit in
256 MB. For a benchmark about free tiers, that's exactly the thing worth knowing.

### Concurrency: four completely different shapes

| qps | 1 client | 10 | 40 |
|---|---|---|---|
| FalkorDB | **1,457** | 1,656 | 1,627 *(465 rejected)* |
| Memgraph | 882 | **1,527** | 1,406 |
| ArangoDB | 308 | 1,432 | 1,358 |
| Neo4j | 115 | **79** | 149 |

**FalkorDB starts at the ceiling.** It's the fastest single-client engine here by
far and gains almost nothing from more of them, because Redis runs commands on one
thread. At 40 clients it rejected 465 queries outright — `Max pending queries
exceeded`. That's not a crash, it's backpressure: it sheds load instead of
degrading for everybody. Its 1,627 qps means "throughput while shedding," which
isn't quite the same measurement as the others'.

**ArangoDB is the best scaler** — 4.6× from 1 to 10 clients, from the lowest
starting point.

**Neo4j goes backwards.** From 115 qps at one client to 79 at ten. It was already
saturated by a single client; more concurrency bought contention, not throughput.

### Storage: you can see the index tax

| | Footprint |
|---|---|
| FalkorDB | 31.0 MB |
| ArangoDB | 73.3 MB — of which **39.6 MB is indexes** |
| Memgraph | 184.5 MB resident / 25.0 MB on disk |
| Neo4j | Not observable |

Indexes are **54% of ArangoDB's bytes**. The design choice that cost it deep
traversals costs it space too. FalkorDB holds the same graph in 31 MB.

### One number to distrust everything by

Run-to-run spread hit **157%** on FalkorDB's point lookups and **145%** on
ArangoDB's 3-hop. Burstable CPU is noisy, and I ran the whole suite three times
specifically so I could publish that.

Which means: **anything under about 2× in these tables isn't a finding.** The 14×
ingest gap and the 71× traversal gap clear that bar comfortably. Several other
differences in the full results don't, and I've said so where they don't.

---

## What I got wrong

Three things, all of which are in the repo's commit history because I'd rather
show the scar tissue.

**My reset procedure killed Neo4j.** Halfway through a reporting run, Neo4j
started refusing connections. The container was up; the JVM inside it was dead —
`OutOfMemoryError` on every scheduler thread. The culprit was my own teardown
code: `DETACH DELETE` in 10,000-node batches, which pulls every one of those
nodes' relationships into the same transaction. Against a 96 MB heap, that's
fatal. Deleting relationships first, in 1,000-row batches, fixed it.

Worth being precise about what this does and doesn't say: Neo4j had already
loaded all 352,768 relationships and served queries fine. The fragility was in my
harness, not in Neo4j's ability to hold the data.

**The harness could hang forever.** An earlier run stalled during FalkorDB's
40-client sweep and sat there. Not a single driver had a query timeout, so a
worker blocked on a response that never came just... waited. Forever. A benchmark
harness that can hang is worse than one that records a timeout, because a hang
gives you nothing. Every adapter now has a timeout, and thread joins are bounded.

**I was warming up the wrong things.** My warm-up loop touched 1-hop, 2-hop, and
point lookups — but not 3-hop traversal, the single most expensive workload. That
meant the first measured 3-hop iterations paid for cache misses the rest didn't.
That isn't just noise: it inflates p95 specifically on disk-backed engines, which
*biases the comparison toward in-memory ones*. I caught it before publishing, but
only just.

---

## What this benchmark can't tell you

**CognoDB was measured across the internet; the others were on loopback.** This
is the big one. CognoDB is a managed instance reached over the public network,
while the four comparison engines are local containers. On sub-millisecond
workloads, the network round trip can be *larger than the query*. Its latency
numbers are an upper bound on the engine, not a measurement of it.

**One dataset, one shape.** cit-HepTh is sparse and scale-free. A dense social
graph, or one with heavy properties on every node, could reorder everything here.

**Burstable CPU isn't a steady resource.** Both a cgroup quota and a "burstable
vCPU" allow short excursions above the nominal limit. That's why I ran the whole
read suite three times and published the spread — if two engines differ by less
than their own run-to-run variance, they don't actually differ.

**Same answers ≠ same plans.** The parity check proves every engine returned
identical results. It does not prove they got there via comparable execution
strategies. Confirming that means reading five query planners.

---

## Run it yourself

Everything is scripted. No numbers in the README were typed by hand — they're
injected from `results.json` by the report generator, precisely so they can't
drift from what was measured.

```bash
git clone https://github.com/Maniprogramer/cognodb-graph-benchmark.git
cd cognodb-graph-benchmark
make all
```

That sets up a venv, starts four resource-capped containers, downloads and
canonicalises the dataset, runs the whole suite, and writes the tables and charts.
Add CognoDB credentials to `.env` and it's five.

If you disagree with a methodology choice — my warm-up, my start-node selection,
my read/write mix — the harness is small and the knobs are on the command line.
I'd rather be corrected with a pull request than agreed with quietly.

---

*Repo: [github.com/Maniprogramer/cognodb-graph-benchmark](https://github.com/Maniprogramer/cognodb-graph-benchmark)*
