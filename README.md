# CognoDB Graph Database Cloud Benchmark

A reproducible, honest benchmark comparing **CognoDB Cloud** against four other managed
graph database platforms on identical datasets, workloads and resource tiers.

> Status: scaffolding. Benchmark harness, results and analysis to follow.

## Planned scope

- **Databases:** CognoDB Cloud + 4 others (selection TBD), all on free/entry tiers with
  equivalent vCPU / RAM / storage.
- **Dataset:** public graph dataset, 100k–500k relationships, loaded identically everywhere.
- **Metrics:** ingest throughput, 1/2/3-hop traversal latency, point and indexed lookups,
  aggregations, concurrent read/write throughput, and observable resource footprint —
  reported as p50/p95 over >=100 iterations after warm-up.

## Repository layout

TBD.

## Reproducing

TBD.

## Credentials

Connection URIs and passwords are read from environment variables only and are never
committed. See `.env.example` once it exists.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
