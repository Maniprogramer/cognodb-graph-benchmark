"""Tests for the Bolt adapter's load-safety machinery.

The node-visibility probe is the guard against a silent-data-loss bug seen on
CognoDB: the edge load matches endpoints by an indexed property, and while that
index is still populating the MATCH returns zero rows *and no error*, so every
CREATE is skipped and the load reports success having written nothing.
"""

import pytest

from bench.adapters.bolt import BoltAdapter


class StubBolt(BoltAdapter):
    """Bolt adapter with the network replaced by a scripted responder."""

    def __init__(self, visible_after_calls=0, never_visible=False):
        super().__init__({"name": "Stub", "uri": "bolt://x", "user": "u", "password": "p"})
        self.visible_after_calls = visible_after_calls
        self.never_visible = never_visible
        self.point_lookup_calls = 0
        self.executed = []

    def _run(self, query, **params):
        self.executed.append((query, params))
        if "count(p)" in query:  # point lookup
            self.point_lookup_calls += 1
            if self.never_visible:
                return [{"c": 0}]
            visible = self.point_lookup_calls > self.visible_after_calls
            return [{"c": 1 if visible else 0}]
        return [{"c": 0}]


class TestAwaitNodeVisibility:
    def test_returns_immediately_when_already_visible(self):
        a = StubBolt(visible_after_calls=0)
        waited = a._await_node_visibility([1, 2, 3], timeout=5)
        assert waited == 0.0
        assert a.point_lookup_calls == 3  # probes three sample ids

    def test_waits_until_nodes_appear(self):
        # First round of probes reports invisible, second reports visible.
        a = StubBolt(visible_after_calls=3)
        waited = a._await_node_visibility([1, 2, 3], timeout=10)
        assert waited >= 1.0
        assert a.point_lookup_calls > 3

    def test_raises_rather_than_loading_into_a_void(self):
        # The whole point: never proceed to the edge load silently.
        a = StubBolt(never_visible=True)
        with pytest.raises(RuntimeError, match="silently create nothing"):
            a._await_node_visibility([1, 2, 3], timeout=2)

    def test_probes_at_most_three_ids(self):
        a = StubBolt(visible_after_calls=0)
        a._await_node_visibility(list(range(1000)), timeout=5)
        assert a.point_lookup_calls == 3


class TestAwaitIndexes:
    def test_unsupported_procedure_is_not_fatal(self):
        """CognoDB has no db.awaitIndexes; that must not break the load."""

        class NoProcedure(StubBolt):
            def _run(self, query, **params):
                if "awaitIndexes" in query:
                    raise Exception("There is no procedure with the name `db.awaitIndexes`")
                return super()._run(query, **params)

        NoProcedure()._await_indexes()  # must not raise

    def test_memgraph_skips_await(self):
        a = StubBolt()
        a.flavor = "memgraph"
        a._await_indexes()
        assert not any("awaitIndexes" in q for q, _ in a.executed)


class TestResetIsBatched:
    def test_deletes_relationships_before_nodes(self):
        """Order matters: DETACH DELETE on dense nodes exhausted a 96 MB heap."""
        a = StubBolt()
        a.reset()
        queries = [q for q, _ in a.executed if "DELETE" in q]
        assert queries, "reset issued no deletes"
        first_delete = queries[0]
        assert "[r]" in first_delete, f"relationships must be deleted first, got: {first_delete}"

    def test_delete_batches_are_bounded(self):
        a = StubBolt()
        a.reset()
        limits = [p.get("limit") for q, p in a.executed if "limit" in p]
        assert limits and all(v <= 1000 for v in limits)
