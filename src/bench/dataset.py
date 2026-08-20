"""Dataset acquisition and canonicalisation.

Source: SNAP cit-HepTh, the arXiv High Energy Physics (theory) citation
network -- 27,770 papers and 352,807 citations. It is used unmodified by
default because it already sits inside the 100k-500k relationship range the
assignment asks for, so no sampling step has to be justified.

Two node properties come from the data itself rather than being synthesised:

  year    parsed from the companion cit-HepTh-dates.txt file (real publication
          dates), used for the group-by aggregation workload
  degree  out-degree computed from the edge list, used for the filtered-lookup
          workload

Everything is written to a canonical pair of CSVs so that every platform loads
byte-identical input, and a manifest records counts plus a SHA-256 of each file
so a reviewer can prove the same data reached every database.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
import shutil
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

EDGES_URL = "https://snap.stanford.edu/data/cit-HepTh.txt.gz"
DATES_URL = "https://snap.stanford.edu/data/cit-HepTh-dates.txt.gz"

DATASET_NAME = "SNAP cit-HepTh (arXiv HEP-Th citation network)"
DATASET_URL = "https://snap.stanford.edu/data/cit-HepTh.html"


@dataclass
class DatasetManifest:
    name: str
    source_url: str
    node_count: int
    relationship_count: int
    sampled: bool
    sample_seed: int | None
    nodes_with_known_year: int
    year_coverage_pct: float
    nodes_csv_sha256: str
    edges_csv_sha256: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.rename(dest)
    return dest


def _parse_edges(gz_path: Path) -> list[tuple[int, int]]:
    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    with gzip.open(gz_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            src, dst = int(parts[0]), int(parts[1])
            if src == dst:
                continue  # drop self-citations; they distort hop counts
            key = (src, dst)
            if key in seen:
                continue
            seen.add(key)
            edges.append(key)
    return edges


def _parse_dates(gz_path: Path) -> tuple[dict[int, int], dict[int, int]]:
    """Parse publication years, returned as (exact, prefix_stripped).

    The dates file mixes two id formats. Most rows use the plain zero-padded
    arXiv number ("9203201"), but 5,906 rows for cross-listed papers carry a
    leading "11" ("119203001"). Both forms are indexed separately so that an
    exact match always wins over a prefix-stripped one -- stripping blindly
    would let a cross-listed paper overwrite a genuine id.

    Even with both forms resolved, only ~41% of the graph's nodes appear in the
    dates file: it covers the abstracts subset, not the full citation graph.
    That gap is real and is reported in the manifest rather than hidden.
    """
    exact: dict[int, int] = {}
    stripped: dict[int, int] = {}
    with gzip.open(gz_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                year = int(parts[1].split("-")[0])
                exact[int(parts[0])] = year
                if parts[0].startswith("11"):
                    stripped.setdefault(int(parts[0][2:]), year)
            except ValueError:
                continue
    return exact, stripped


def _bfs_sample(edges: list[tuple[int, int]], target_edges: int, seed: int) -> list[tuple[int, int]]:
    """Sample a connected subgraph by BFS.

    Random *edge* sampling would shatter the graph and make 2- and 3-hop
    traversals meaningless -- most start nodes would have no reachable
    neighbourhood. Growing a region by BFS preserves local structure, which is
    exactly what the traversal workloads measure.
    """
    if target_edges >= len(edges):
        return edges

    adjacency: dict[int, list[int]] = defaultdict(list)
    for src, dst in edges:
        adjacency[src].append(dst)

    rng = random.Random(seed)
    start = rng.choice(sorted(adjacency.keys()))

    kept: set[int] = set()
    queue = deque([start])
    edge_budget = 0
    while queue and edge_budget < target_edges:
        node = queue.popleft()
        if node in kept:
            continue
        kept.add(node)
        neighbours = adjacency.get(node, [])
        edge_budget += len(neighbours)
        for nbr in neighbours:
            if nbr not in kept:
                queue.append(nbr)

    return [(s, d) for s, d in edges if s in kept and d in kept]


def build(
    data_dir: Path,
    sample_edges: int | None = None,
    seed: int = 42,
) -> DatasetManifest:
    """Download, canonicalise and write nodes.csv + edges.csv. Idempotent."""
    data_dir = Path(data_dir)
    raw_dir = data_dir / "raw"
    edges_gz = _download(EDGES_URL, raw_dir / "cit-HepTh.txt.gz")
    dates_gz = _download(DATES_URL, raw_dir / "cit-HepTh-dates.txt.gz")

    edges = _parse_edges(edges_gz)
    years_exact, years_stripped = _parse_dates(dates_gz)

    sampled = sample_edges is not None and sample_edges < len(edges)
    if sampled:
        edges = _bfs_sample(edges, sample_edges, seed)

    out_degree: dict[int, int] = defaultdict(int)
    nodes: set[int] = set()
    for src, dst in edges:
        nodes.add(src)
        nodes.add(dst)
        out_degree[src] += 1

    nodes_csv = data_dir / "nodes.csv"
    edges_csv = data_dir / "edges.csv"

    # UNKNOWN_YEAR marks papers absent from the dates file. Using a sentinel
    # rather than dropping them keeps node counts identical across platforms,
    # and the group-by workload reports the bucket honestly.
    UNKNOWN_YEAR = 0

    years_known = 0
    with nodes_csv.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["paper_id", "year", "degree"])
        for node in sorted(nodes):
            year = years_exact.get(node) or years_stripped.get(node) or UNKNOWN_YEAR
            if year != UNKNOWN_YEAR:
                years_known += 1
            writer.writerow([node, year, out_degree[node]])

    with edges_csv.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["src", "dst"])
        for src, dst in edges:
            writer.writerow([src, dst])

    manifest = DatasetManifest(
        name=DATASET_NAME,
        source_url=DATASET_URL,
        node_count=len(nodes),
        relationship_count=len(edges),
        sampled=sampled,
        sample_seed=seed if sampled else None,
        nodes_with_known_year=years_known,
        year_coverage_pct=round(100 * years_known / len(nodes), 1) if nodes else 0.0,
        nodes_csv_sha256=_sha256(nodes_csv),
        edges_csv_sha256=_sha256(edges_csv),
    )
    (data_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2))
    return manifest


def load_csvs(data_dir: Path) -> tuple[list[dict], list[tuple[int, int]]]:
    """Read the canonical CSVs back for loading into a database."""
    data_dir = Path(data_dir)
    with (data_dir / "nodes.csv").open() as fh:
        nodes = [
            {"paper_id": int(r["paper_id"]), "year": int(r["year"]), "degree": int(r["degree"])}
            for r in csv.DictReader(fh)
        ]
    with (data_dir / "edges.csv").open() as fh:
        edges = [(int(r["src"]), int(r["dst"])) for r in csv.DictReader(fh)]
    return nodes, edges
