import pytest

from bench.workloads import (
    READ_WORKLOADS,
    _adjacency,
    _has_depth_three,
    build_plan,
    select_start_nodes,
)


@pytest.fixture
def chain_edges():
    """A 100-node chain: every early node has deep reach, the tail does not."""
    return [(i, i + 1) for i in range(1, 100)]


@pytest.fixture
def star_edges():
    """A star: the hub has 1-hop reach only, so no node has depth 3."""
    return [(0, i) for i in range(1, 50)]


class TestDepthThree:
    def test_chain_head_has_depth_three(self, chain_edges):
        adj = _adjacency(chain_edges)
        assert _has_depth_three(adj, 1) is True

    def test_chain_tail_lacks_depth_three(self, chain_edges):
        adj = _adjacency(chain_edges)
        assert _has_depth_three(adj, 98) is False

    def test_star_has_no_depth_three(self, star_edges):
        adj = _adjacency(star_edges)
        assert _has_depth_three(adj, 0) is False


class TestSelectStartNodes:
    def test_all_selected_have_depth_three(self):
        # Wide branching so min_out_degree=2 is satisfiable.
        edges = [(i, i * 2) for i in range(1, 200)] + [(i, i * 2 + 1) for i in range(1, 200)]
        adj = _adjacency(edges)
        chosen = select_start_nodes(edges, 10, seed=5)
        assert chosen
        assert all(_has_depth_three(adj, n) for n in chosen)

    def test_deterministic(self):
        edges = [(i, i * 2) for i in range(1, 200)] + [(i, i * 2 + 1) for i in range(1, 200)]
        assert select_start_nodes(edges, 10, seed=5) == select_start_nodes(edges, 10, seed=5)

    def test_different_seeds_differ(self):
        edges = [(i, i * 2) for i in range(1, 400)] + [(i, i * 2 + 1) for i in range(1, 400)]
        assert select_start_nodes(edges, 10, seed=1) != select_start_nodes(edges, 10, seed=2)

    def test_raises_when_graph_too_sparse(self, star_edges):
        with pytest.raises(ValueError, match="3-hop reachability"):
            select_start_nodes(star_edges, 5, seed=1)

    def test_count_exceeding_eligible_returns_all(self, chain_edges):
        chosen = select_start_nodes(chain_edges, 10_000, seed=1, min_out_degree=1)
        adj = _adjacency(chain_edges)
        assert len(chosen) == len([n for n in adj if _has_depth_three(adj, n)])


class TestBuildPlan:
    @pytest.fixture
    def nodes(self):
        return [
            {"paper_id": i, "year": 2000 + (i % 3) if i % 4 else 0, "degree": i % 30}
            for i in range(1, 400)
        ]

    @pytest.fixture
    def edges(self):
        return [(i, i * 2) for i in range(1, 200)] + [(i, i * 2 + 1) for i in range(1, 200)]

    def test_plan_fields_populated(self, nodes, edges):
        plan = build_plan(nodes, edges, start_node_count=10, seed=99)
        assert len(plan.start_nodes) == 10
        assert len(plan.point_lookup_ids) == 10
        assert plan.filtered_year > 0
        assert plan.filtered_min_degree >= 1
        assert len(plan.write_pairs) >= 1000

    def test_deterministic(self, nodes, edges):
        a = build_plan(nodes, edges, start_node_count=10, seed=99)
        b = build_plan(nodes, edges, start_node_count=10, seed=99)
        assert a.start_nodes == b.start_nodes
        assert a.point_lookup_ids == b.point_lookup_ids
        assert a.write_pairs == b.write_pairs

    def test_filtered_year_excludes_unknown_sentinel(self, nodes, edges):
        # year 0 means "no publication date"; filtering on it would measure the
        # sentinel bucket rather than a real predicate.
        plan = build_plan(nodes, edges, start_node_count=10, seed=99)
        assert plan.filtered_year != 0


class TestReadWorkloads:
    def test_all_six_required_categories_present(self):
        assert set(READ_WORKLOADS) == {
            "1-hop", "2-hop", "3-hop", "point-lookup", "filtered-lookup", "aggregation",
        }

    def test_workloads_dispatch_to_adapter(self):
        calls = []

        class FakeAdapter:
            def one_hop(self, n): calls.append(("one_hop", n)); return 1
            def two_hop(self, n): calls.append(("two_hop", n)); return 2
            def three_hop(self, n): calls.append(("three_hop", n)); return 3
            def point_lookup(self, n): calls.append(("point", n)); return 1
            def filtered_lookup(self, d, y): calls.append(("filtered", d, y)); return 5
            def aggregation(self): calls.append(("agg",)); return 7

        class FakePlan:
            start_nodes = [10, 20]
            point_lookup_ids = [30]
            filtered_min_degree = 4
            filtered_year = 2001

        a, p = FakeAdapter(), FakePlan()
        assert READ_WORKLOADS["1-hop"](a, p, 0) == 1
        assert READ_WORKLOADS["3-hop"](a, p, 1) == 3
        assert READ_WORKLOADS["aggregation"](a, p, 0) == 7
        assert ("one_hop", 10) in calls
        assert ("three_hop", 20) in calls

    def test_start_nodes_cycle_by_index(self):
        seen = []

        class FakeAdapter:
            def one_hop(self, n): seen.append(n); return 1

        class FakePlan:
            start_nodes = [7, 8, 9]

        for i in range(7):
            READ_WORKLOADS["1-hop"](FakeAdapter(), FakePlan(), i)
        assert seen == [7, 8, 9, 7, 8, 9, 7]
