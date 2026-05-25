from __future__ import annotations

import math

import pytest

from ontorag.core.fastrp import fastrp_embeddings


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# Two clusters joined by a single weak link.
_NODES = ["a", "b", "c", "x", "y", "z"]
_EDGES = [
    ("a", "b"), ("b", "c"), ("a", "c"),  # cluster 1 (triangle)
    ("x", "y"), ("y", "z"), ("x", "z"),  # cluster 2 (triangle)
    ("c", "x"),                          # weak bridge
]


def test_output_shape_and_keys():
    emb = fastrp_embeddings(_EDGES, _NODES, dim=32)
    assert set(emb) == set(_NODES)
    assert all(len(v) == 32 for v in emb.values())


def test_deterministic_for_fixed_seed():
    e1 = fastrp_embeddings(_EDGES, _NODES, dim=32, seed=7)
    e2 = fastrp_embeddings(_EDGES, _NODES, dim=32, seed=7)
    assert e1 == e2


def test_different_seed_changes_embeddings():
    e1 = fastrp_embeddings(_EDGES, _NODES, dim=32, seed=1)
    e2 = fastrp_embeddings(_EDGES, _NODES, dim=32, seed=2)
    assert e1 != e2


def test_same_cluster_more_similar_than_cross_cluster():
    """Structural signal: triangle-mates are closer than nodes across the bridge."""
    emb = fastrp_embeddings(_EDGES, _NODES, dim=128, seed=42)
    within = _cosine(emb["a"], emb["b"])
    across = _cosine(emb["a"], emb["z"])
    assert within > across


def test_isolated_node_keeps_unit_vector():
    emb = fastrp_embeddings(_EDGES, [*_NODES, "lonely"], dim=16, seed=42)
    lonely = emb["lonely"]
    assert len(lonely) == 16
    # Normalised non-zero seed vector → unit norm (or zero only if no entries hit).
    norm = math.sqrt(sum(x * x for x in lonely))
    assert norm == pytest.approx(1.0) or norm == 0.0


def test_empty_nodes_returns_empty():
    assert fastrp_embeddings(_EDGES, [], dim=8) == {}


def test_invalid_dim_raises():
    with pytest.raises(ValueError, match="dim"):
        fastrp_embeddings(_EDGES, _NODES, dim=0)


def test_edges_with_unknown_endpoints_ignored():
    # Edge to a node not in the list must not raise.
    emb = fastrp_embeddings([("a", "ghost"), ("a", "b")], ["a", "b"], dim=8)
    assert set(emb) == {"a", "b"}
