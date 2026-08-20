import csv
import gzip
import json
from pathlib import Path

import pytest

from bench import dataset


@pytest.fixture
def edges_gz(tmp_path):
    p = tmp_path / "edges.txt.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("# comment line\n")
        fh.write("1\t2\n")
        fh.write("2\t3\n")
        fh.write("1\t2\n")  # duplicate
        fh.write("4\t4\n")  # self-loop
        fh.write("3\t1\n")
    return p


@pytest.fixture
def dates_gz(tmp_path):
    p = tmp_path / "dates.txt.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("# comment\n")
        fh.write("0000001\t1998-03-01\n")   # -> 1
        fh.write("119203060\t1992-03-21\n")  # cross-listed -> 9203060
        fh.write("2\t2001-07-04\n")
    return p


class TestParseEdges:
    def test_drops_duplicates_and_self_loops(self, edges_gz):
        edges = dataset._parse_edges(edges_gz)
        assert edges == [(1, 2), (2, 3), (3, 1)]

    def test_self_loop_excluded(self, edges_gz):
        assert not any(s == d for s, d in dataset._parse_edges(edges_gz))


class TestParseDates:
    def test_exact_and_stripped_forms(self, dates_gz):
        exact, stripped = dataset._parse_dates(dates_gz)
        assert exact[1] == 1998
        assert exact[2] == 2001
        assert exact[119203060] == 1992
        assert stripped[9203060] == 1992

    def test_exact_takes_precedence_over_stripped(self, tmp_path):
        # A cross-listed id that strips down onto a real id must not clobber it.
        p = tmp_path / "d.txt.gz"
        with gzip.open(p, "wt") as fh:
            fh.write("115\t1995-01-01\n")  # strips to 5
            fh.write("5\t2002-01-01\n")    # genuine id 5
        exact, stripped = dataset._parse_dates(p)
        resolved = exact.get(5) or stripped.get(5)
        assert resolved == 2002


class TestBfsSample:
    def test_returns_all_when_target_exceeds_size(self):
        edges = [(1, 2), (2, 3)]
        assert dataset._bfs_sample(edges, 100, seed=1) == edges

    def test_sample_is_connected_and_bounded(self):
        # Two disjoint components; a BFS sample must not span both.
        comp_a = [(i, i + 1) for i in range(1, 50)]
        comp_b = [(i, i + 1) for i in range(1000, 1050)]
        sample = dataset._bfs_sample(comp_a + comp_b, 10, seed=7)
        nodes = {n for e in sample for n in e}
        assert not (any(n < 100 for n in nodes) and any(n >= 1000 for n in nodes))

    def test_deterministic_for_seed(self):
        edges = [(i, i + 1) for i in range(1, 200)] + [(i, i + 5) for i in range(1, 190)]
        a = dataset._bfs_sample(edges, 40, seed=99)
        b = dataset._bfs_sample(edges, 40, seed=99)
        assert a == b

    def test_preserves_traversal_depth(self):
        # The point of BFS sampling: sampled nodes retain multi-hop reach.
        edges = [(i, i + 1) for i in range(1, 500)]
        sample = dataset._bfs_sample(edges, 50, seed=3)
        adjacency = {}
        for s, d in sample:
            adjacency.setdefault(s, []).append(d)
        # At least one node must still have a 3-hop path.
        def reach(node, depth):
            if depth == 0:
                return True
            return any(reach(n, depth - 1) for n in adjacency.get(node, []))
        assert any(reach(n, 3) for n in adjacency)


class TestBuildAndLoad:
    def test_build_writes_canonical_outputs(self, tmp_path, edges_gz, dates_gz, monkeypatch):
        monkeypatch.setattr(dataset, "_download", lambda url, dest: edges_gz if "dates" not in url else dates_gz)
        manifest = dataset.build(tmp_path)

        assert manifest.node_count == 3
        assert manifest.relationship_count == 3
        assert manifest.sampled is False
        assert (tmp_path / "nodes.csv").exists()
        assert (tmp_path / "edges.csv").exists()

        saved = json.loads((tmp_path / "manifest.json").read_text())
        assert saved["nodes_csv_sha256"] == manifest.nodes_csv_sha256

    def test_degree_and_year_columns(self, tmp_path, edges_gz, dates_gz, monkeypatch):
        monkeypatch.setattr(dataset, "_download", lambda url, dest: edges_gz if "dates" not in url else dates_gz)
        dataset.build(tmp_path)
        rows = {int(r["paper_id"]): r for r in csv.DictReader((tmp_path / "nodes.csv").open())}
        assert int(rows[1]["degree"]) == 1   # 1->2
        assert int(rows[1]["year"]) == 1998
        assert int(rows[3]["year"]) == 0     # absent from dates file -> sentinel

    def test_load_csvs_roundtrip(self, tmp_path, edges_gz, dates_gz, monkeypatch):
        monkeypatch.setattr(dataset, "_download", lambda url, dest: edges_gz if "dates" not in url else dates_gz)
        manifest = dataset.build(tmp_path)
        nodes, edges = dataset.load_csvs(tmp_path)
        assert len(nodes) == manifest.node_count
        assert len(edges) == manifest.relationship_count
        assert all(isinstance(n["paper_id"], int) for n in nodes)

    def test_sha256_is_stable(self, tmp_path, edges_gz, dates_gz, monkeypatch):
        monkeypatch.setattr(dataset, "_download", lambda url, dest: edges_gz if "dates" not in url else dates_gz)
        a = dataset.build(tmp_path)
        b = dataset.build(tmp_path)
        assert a.edges_csv_sha256 == b.edges_csv_sha256
