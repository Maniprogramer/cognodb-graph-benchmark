# I gave five graph databases 256 MB of RAM and asked them the same question

*What happens when you take a free tier literally — and why the most important
number in my benchmark was the one that measured nothing at all.*

---

Most database benchmarks are useless, and for a boring reason: the two systems
weren't given the same machine.

You know the shape of it. A vendor benchmarks their managed service against a
competitor's free tier. Someone runs Postgres on a laptop against a cloud
warehouse on 32 cores. The numbers are real, the charts are pretty, and the
conclusion is worthless — what got measured was the hardware bill.

I wanted to do the opposite. CognoDB's free tier is **0.5 burstable vCPU, 256 MB
RAM, 1 GB disk**. That's small. So rather than hand it a fight it couldn't win, I
made that envelope the *rule*: every database gets 0.5 vCPU and 256 MB, not a
byte more. The four comparison engines run as containers with hard cgroup limits
at exactly those figures.

Then I asked all five the same six questions, 100 times each, three times over.

The headline result is that **one database appeared to be 600× slower than
another, and roughly 99% of that gap was the Atlantic Ocean.** Getting to that
required measuring something most benchmarks never do.

---

## The setup

**The data:** [SNAP cit-HepTh](https://snap.stanford.edu/data/cit-HepTh.html) —
the arXiv high-energy-physics citation network. 27,769 papers, 352,768 citations.
Real data with real skew: a few papers are cited hundreds of times, most barely
at all. Scale-free, which is what makes traversal interesting — hop counts
explode unevenly.

**The contenders:**

| | Engine | Language |
|---|---|---|
| **CognoDB** | Managed cloud, Bolt-compatible | Cypher |
| **Neo4j 5.26** | JVM, native graph store, page cache | Cypher |
| **Memgraph 2.22** | C++, in-memory | Cypher |
| **ArangoDB 3.11** | Multi-model, document store + edge index | AQL |
| **FalkorDB 4.2** | Sparse matrices + GraphBLAS, inside Redis | Cypher |

Picked deliberately, not for variety. Neo4j and Memgraph speak the *same
language* with completely different runtimes — that isolates "JVM vs. native."
Memgraph and FalkorDB are both in-memory but store the graph in different
*shapes* — that isolates data structure. ArangoDB doesn't speak Cypher at all.

Four Neo4j-compatible databases would have made a tidy table and taught me
nothing.

---

## The part everyone skips

Here's the question that kept me up: **how do I know they're answering the same
question?**

It's easy to write Cypher for "count distinct nodes exactly three hops out." It's
easy to write AQL that *looks* equivalent. It is not easy to be sure — AQL
traversals have uniqueness options that silently change what gets counted. Get
that wrong and you're comparing a hard question to an easy one and calling the
difference "performance."

So every query returns a **count**, and the harness sums those counts across all
100 iterations into a fingerprint. After every database runs, it compares
fingerprints. Divergence means the queries aren't equivalent and the run is
flagged `MISMATCH`.

They matched. Exactly, on all six workloads, across all five engines. That check
is why I trust anything below it.

---

## The number that measures nothing

CognoDB is a managed endpoint over the internet. The other four are containers on
loopback. That's not a difference resource caps can fix.

So I measured each platform's **floor**: the median round trip for `RETURN 1`, a
query that does no work whatsoever.

| | Transport floor |
|---|---|
| CognoDB | **238.08 ms** |
| Neo4j | 2.99 ms |
| ArangoDB | 2.28 ms |
| Memgraph | 0.45 ms |
| FalkorDB | 0.27 ms |

238 milliseconds before CognoDB does anything at all. Memgraph answers a real
1-hop traversal in 0.39 ms — *less than its own round trip.*

Subtract the floor and the picture inverts:

| p50 (ms) | 1-hop raw | **1-hop net** | 3-hop raw | **3-hop net** |
|---|---|---|---|---|
| CognoDB | 239.57 | **~1.5** | 256.30 | **~18.2** |
| Neo4j | 4.26 | **~1.3** | 4.63 | **~1.6** |
| ArangoDB | 3.11 | **~0.8** | 16.50 | **~14.2** |
| FalkorDB | 0.50 | **~0.2** | 0.94 | **~0.7** |
| Memgraph | 0.39 | *at floor* | 1.08 | **~0.6** |

**CognoDB's engine does a 1-hop traversal in about 1.5 ms — the same order as
Neo4j's 1.3 ms.** The 600× headline was almost entirely network.

Both halves matter, though. If your app talks to CognoDB across the internet,
240 ms per query is what you'll *actually* experience — the net column describes
the engine, the raw column describes the deployment. And subtracting one noisy
median from another amplifies noise, which is why Memgraph's 1-hop lands *below*
its own floor and gets reported as "at floor" rather than as a negative number.

---

## Depth is where engines diverge

Using net figures — the only fair way to compare a remote engine with local ones:

| 1-hop → 3-hop | Change |
|---|---|
| Neo4j | 1.3 → 1.6 ms — flat |
| FalkorDB | 0.2 → 0.7 ms — flat |
| Memgraph | floor → 0.6 ms — flat |
| **ArangoDB** | 0.8 → 14.2 ms — **~17×** |
| **CognoDB** | 1.5 → 18.2 ms — **~12×** |

Three engines barely notice the extra hops. Two fall off a cliff — and they're
the two whose one-hop numbers gave no warning. **ArangoDB is faster than Neo4j at
one hop and nine times slower at three.**

For ArangoDB the mechanism is clear. Edges are documents with an index on their
endpoints, so every hop costs an index probe *per frontier node*. The depth-3
frontier here is ~950 nodes — one query becomes roughly a thousand index lookups.
Memgraph follows pointers. FalkorDB multiplies a sparse matrix, where a third hop
is just a third multiply. Neither pays per node.

For CognoDB I genuinely don't know. The profile *resembles* index-backed
adjacency, but the managed tier exposes no query plan and no storage internals.
That's a hypothesis, and I'm labelling it as one rather than dressing it up.

The lesson that survives every caveat: **on a fixed small budget, adjacency
representation dominates — and one-hop benchmarks won't reveal it.**

---

## The JVM tells on itself

Neo4j's p95 runs **19×** its p50 on 1-hop. Nobody else exceeds 7×.

I didn't have to guess why. At 40 concurrent clients, Neo4j returned this:

```
Neo.TransientError.General.MemoryPoolOutOfMemoryError:
The allocation of an extra 2.0 MiB would use more than the limit 67.2 MiB
```

67.2 MB is the transaction pool it derived from the 96 MB heap I gave it. Neo4j
splits its box three ways — heap, page cache, JVM overhead — and at 256 MB all
three starve simultaneously. Its ingest was 5,421 rels/s against Memgraph's
42,246 on an identical load method: **8× slower**.

This doesn't say Neo4j is slow. It says Neo4j's architecture doesn't fit in
256 MB. For a benchmark about free tiers, that's exactly what's worth knowing.

---

## Scaling: five different shapes

| qps | 1 client | 10 | 40 |
|---|---|---|---|
| Memgraph | 2,780 | **3,265** | 3,262 |
| FalkorDB | 1,884 | 1,757 | 1,815 *(1,027 rejected)* |
| ArangoDB | 268 | 1,755 | 1,730 |
| Neo4j | 178 | 182 | 239 |
| CognoDB | 4.0 | 39.5 | **155.5** |

**CognoDB scales almost perfectly linearly** — 39× throughput for 40× clients,
p50 flat at ~250 ms throughout. That's the signature of a latency-bound system
with headroom to spare: every client spends its time waiting on the network, so
more clients cost almost nothing. Its 4 qps at one client isn't a capacity
ceiling, it's one query per round trip.

**FalkorDB starts at its ceiling** and gains nothing, because Redis runs commands
on one thread. At 40 clients it rejected 1,027 queries with `Max pending queries
exceeded` — backpressure, not a crash. It sheds load rather than degrading for
everyone, which is a defensible design choice and makes that cell mean
"throughput while shedding."

**Neo4j doesn't scale at all**: 178 → 182 qps from 1 to 10 clients.

---

## What I got wrong

Four things, all in the commit history, because I'd rather show the scar tissue.

**The benchmark loaded nothing and reported success.** CognoDB claimed 24,000
rels/sec and stored **zero relationships**. Nodes landed, edges didn't, nothing
raised an error. The edge load matches endpoints by an indexed property — and
while that index is still populating, the MATCH returns zero rows *and no
error*, so every CREATE was silently skipped. The procedure that should have
prevented this, `db.awaitIndexes`, doesn't exist on CognoDB; my code caught that
failure and slept two seconds instead, which is a guess, not a guarantee.

Only the load-verification step caught it. **A benchmark that timed queries
without checking what was stored would have published traversal latencies for an
empty graph — and they'd have looked fantastic.** That's the one that scares me.

**My reset procedure killed Neo4j.** Mid-run, it started refusing connections:
container up, JVM dead, `OutOfMemoryError` on every scheduler thread. My teardown
used `DETACH DELETE` in 10,000-node batches, which pulls each node's
relationships into the same transaction. Against a 96 MB heap, fatal.

**The harness could hang forever.** A run stalled during FalkorDB's 40-client
sweep and just sat there. No driver had a query timeout, so a worker blocked on a
response that never came waited indefinitely. A harness that can hang is worse
than one that records a timeout.

**I measured my own floor wrong.** First time round, the transport baseline came
out *higher* than Memgraph's 1-hop latency — 158% of a number it's supposed to
sit underneath. I'd measured the baseline cold and compared it against warm
percentiles. A floor that exceeds what it floors is a bug in the ruler.

---

## What this can't tell you

**Variance is large — larger than several differences in these tables.** Worst
run-to-run spread hit 100.7%. Between two full runs, ArangoDB's ingest moved 54%
and its 3-hop p50 moved 3×. **Anything under about 2× here isn't a finding**, and
I've said so where it isn't. The 8× ingest gap and the ~17× depth degradation
clear that bar; plenty else doesn't.

**One dataset, one shape.** cit-HepTh is sparse and scale-free. A dense social
graph could reorder all of this.

**Same answers ≠ same plans.** The parity check proves identical results, not
comparable execution strategies. Confirming that means reading five query
planners.

**The remote/local split is estimated, not eliminated.** Subtracting a transport
floor assumes transport and query cost are independent and additive —
approximately true, not exactly. The clean experiment benchmarks all five as
managed endpoints from one region. The harness supports it; I didn't run it.

---

## Run it yourself

No number in the README was typed by hand — they're injected from `results.json`
by the report generator, specifically so they can't drift from what was measured.

```bash
git clone https://github.com/Maniprogramer/cognodb-graph-benchmark.git
cd cognodb-graph-benchmark
make all
```

That builds a venv, starts four capped containers, downloads and canonicalises
the dataset, runs the suite, and writes the tables and charts. Add CognoDB
credentials to `.env` for the fifth.

If you disagree with a methodology choice — my warm-up, my start-node selection,
my read/write mix — the harness is small and the knobs are on the command line.
I'd rather be corrected with a pull request than agreed with quietly.

---

*Repo: [github.com/Maniprogramer/cognodb-graph-benchmark](https://github.com/Maniprogramer/cognodb-graph-benchmark)*
