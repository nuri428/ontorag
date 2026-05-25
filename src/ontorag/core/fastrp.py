"""FastRP node embeddings — pure-Python (stdlib only).

A dependency-free implementation of Fast Random Projection (Chen et al., 2019)
node embeddings, used by the Fuseki backend where Neo4j's GDS `gds.fastRP` is
not available. Both backends therefore expose *structural* embeddings: Neo4j
via GDS, Fuseki via this module (vectors then land in Qdrant).

Algorithm: seed each node with a sparse random vector, propagate it over the
degree-normalised (undirected) adjacency K times — L2-normalising after each
hop — and return a weighted sum of the per-hop matrices. No NumPy: graphs at
ontology-ABox scale embed in well under a second as a one-off step.
"""

from __future__ import annotations

import math
import random

__all__ = ["fastrp_embeddings"]

# Achlioptas sparse random projection density: 1/s entries are non-zero.
_SPARSITY_S = 3.0
_DEFAULT_ITERATION_WEIGHTS: tuple[float, ...] = (0.0, 1.0, 1.0)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def fastrp_embeddings(
    edges: list[tuple[str, str]],
    nodes: list[str],
    dim: int = 256,
    iteration_weights: tuple[float, ...] = _DEFAULT_ITERATION_WEIGHTS,
    seed: int = 42,
) -> dict[str, list[float]]:
    """Compute FastRP structural embeddings for ``nodes``.

    Args:
        edges: Undirected (src, dst) pairs. Endpoints not in ``nodes`` are
            ignored; direction is irrelevant (each edge is symmetrised).
        nodes: Node identifiers to embed. The output preserves these keys.
        dim: Embedding dimension (must be > 0).
        iteration_weights: Weight per propagation hop. Index 0 is the hop-0
            (raw random) contribution; index k weights the k-hop matrix. The
            number of entries sets how many hops are propagated.
        seed: RNG seed — embeddings are deterministic for a fixed seed.

    Returns:
        Mapping of node identifier → embedding vector (length ``dim``). A node
        with no edges keeps its (normalised) random seed vector.

    Raises:
        ValueError: If ``dim`` <= 0 or ``iteration_weights`` is empty.
    """
    if dim <= 0:
        raise ValueError(f"dim must be a positive integer, got {dim}")
    if not iteration_weights:
        raise ValueError("iteration_weights must contain at least one weight")
    if not nodes:
        return {}

    index = {uri: i for i, uri in enumerate(nodes)}
    n = len(nodes)

    # Adjacency (undirected, de-duplicated), restricted to known nodes.
    neighbors: list[set[int]] = [set() for _ in range(n)]
    for src, dst in edges:
        i, j = index.get(src), index.get(dst)
        if i is None or j is None or i == j:
            continue
        neighbors[i].add(j)
        neighbors[j].add(i)

    # Seed matrix R: sparse random projection (Achlioptas). Each entry is
    # ±sqrt(s) with probability 1/(2s) each, else 0 — unit expected variance.
    rng = random.Random(seed)
    scale = math.sqrt(_SPARSITY_S)
    prob = 1.0 / (2.0 * _SPARSITY_S)
    current: list[list[float]] = []
    for _ in range(n):
        row = [0.0] * dim
        for d in range(dim):
            r = rng.random()
            if r < prob:
                row[d] = scale
            elif r < 2.0 * prob:
                row[d] = -scale
        current.append(row)

    # Accumulate the weighted sum across hops.
    embedding: list[list[float]] = [[0.0] * dim for _ in range(n)]
    for hop, weight in enumerate(iteration_weights):
        if hop > 0:
            # Degree-normalised propagation: row i = mean of neighbour rows.
            propagated: list[list[float]] = [[0.0] * dim for _ in range(n)]
            for i in range(n):
                nbrs = neighbors[i]
                if not nbrs:
                    continue
                acc = propagated[i]
                inv_deg = 1.0 / len(nbrs)
                for j in nbrs:
                    cur_j = current[j]
                    for d in range(dim):
                        acc[d] += cur_j[d] * inv_deg
            current = [_l2_normalize(row) for row in propagated]
        if weight != 0.0:
            for i in range(n):
                cur_i = current[i]
                emb_i = embedding[i]
                for d in range(dim):
                    emb_i[d] += cur_i[d] * weight

    return {uri: _l2_normalize(embedding[index[uri]]) for uri in nodes}
