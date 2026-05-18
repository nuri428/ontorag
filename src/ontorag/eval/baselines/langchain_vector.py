"""LangChain + Chroma + OpenAI vector RAG baseline.

This baseline represents the "default" vector RAG stack — the one a
typical enterprise PoC would compare ontorag against.

Pipeline:
1. Parse the TBox+ABox TTL files with rdflib.
2. Render each entity as a natural-language chunk (label + class +
   properties), so a semantic search can find it.
3. Index chunks in Chroma with OpenAI ``text-embedding-3-small``.
4. At query time, retrieve top-k chunks and stuff them into a
   gpt-4o-mini prompt that returns the final answer.

Vector RAG has no notion of triples, so ``cited_triples`` in the
returned :class:`BaselineAnswer` is always empty. ``extra`` contains
the retrieved chunks for inspection.

Dependencies are **optional**: install via ``uv sync --extra bench``.
``OPENAI_API_KEY`` must be set in the environment at construction.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from ontorag.eval.baselines.protocol import (
    BaselineAnswer,
    BaselineError,
    MissingBaselineDependencyError,
)

logger = logging.getLogger(__name__)


_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_DEFAULT_CHAT_MODEL = "gpt-4o-mini"
_DEFAULT_TOP_K = 5


def _require_deps() -> dict[str, Any]:
    """Lazy-import LangChain/Chroma deps; raise a clear error if missing.

    LangChain 1.x moved ``chains`` into the ``langchain_classic`` package.
    We try both import paths so the baseline works against 0.3.x and 1.x.
    """
    try:
        try:
            from langchain.chains import RetrievalQA  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            from langchain_classic.chains import (  # type: ignore[import-untyped]  # noqa: PLC0415
                RetrievalQA,
            )
        from langchain_chroma import Chroma  # noqa: PLC0415
        from langchain_openai import (  # noqa: PLC0415
            ChatOpenAI,
            OpenAIEmbeddings,
        )
    except ImportError as e:
        raise MissingBaselineDependencyError(
            "LangChain vector baseline requires the `bench` extra. "
            "Install with: `uv sync --extra bench`. "
            f"Original error: {e}"
        ) from e
    return {
        "RetrievalQA": RetrievalQA,
        "Chroma": Chroma,
        "ChatOpenAI": ChatOpenAI,
        "OpenAIEmbeddings": OpenAIEmbeddings,
    }


def _entity_local_name(uri: URIRef) -> str:
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


def _render_entity(graph: Graph, subject: URIRef) -> str:
    """Render a single entity's outgoing triples as a natural-language chunk.

    Produces something like::

        Aurora Phone X1 (Smartphone). Manufactured by Aurora Tech.
        Sold under brand Aurora. Model year 2024.

    The goal is for OpenAI's embedding model to find this chunk when a
    user asks about "Aurora Phone X1" or "smartphones from Aurora Tech".
    """
    labels = [
        str(o)
        for o in graph.objects(subject, RDFS.label)
        if isinstance(o, Literal)
    ]
    primary_label = labels[0] if labels else _entity_local_name(subject)

    types = [
        _entity_local_name(o)
        for o in graph.objects(subject, RDF.type)
        if isinstance(o, URIRef)
    ]
    type_str = f" ({', '.join(types)})" if types else ""

    parts: list[str] = [f"{primary_label}{type_str}."]
    for pred, obj in graph.predicate_objects(subject):
        if pred in (RDF.type, RDFS.label):
            continue
        pred_local = _entity_local_name(pred) if isinstance(pred, URIRef) else str(pred)
        if isinstance(obj, URIRef):
            obj_labels = list(graph.objects(obj, RDFS.label))
            obj_str = str(obj_labels[0]) if obj_labels else _entity_local_name(obj)
        else:
            obj_str = str(obj)
        parts.append(f"{pred_local}: {obj_str}.")

    return " ".join(parts)


def render_graph_as_chunks(
    schema_path: str | Path, data_path: str | Path
) -> list[str]:
    """Pure function: produce one natural-language chunk per ABox entity.

    Exported for testability — tests can verify the chunking output
    without needing OpenAI or Chroma. The chunks are exactly what the
    vector index will see.
    """
    g = Graph()
    g.parse(schema_path, format="turtle")
    g.parse(data_path, format="turtle")

    # ABox entities are subjects that have ≥1 type assertion to a non-
    # OWL class. Filter out the ontology header and class/property defs.
    skip_ns = (
        "http://www.w3.org/2002/07/owl#",
        "http://www.w3.org/2000/01/rdf-schema#",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    )
    chunks: list[str] = []
    seen: set[URIRef] = set()
    for s in g.subjects():
        if not isinstance(s, URIRef) or s in seen:
            continue
        # Skip class/property definitions
        types = list(g.objects(s, RDF.type))
        if not types:
            continue
        if any(
            str(t).startswith("http://www.w3.org/2002/07/owl#") for t in types
        ):
            continue
        if any(str(s).startswith(ns) for ns in skip_ns):
            continue
        seen.add(s)
        chunks.append(_render_entity(g, s))
    return chunks


class LangChainVectorBaseline:
    """LangChain RetrievalQA + Chroma + OpenAI baseline.

    Construct with the same schema+data files used by ontorag, then call
    ``await baseline.answer(question)`` for each goldset row.
    """

    name = "langchain_vector"
    version = "0.1.0"

    def __init__(
        self,
        schema_path: str | Path,
        data_path: str | Path,
        *,
        embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
        chat_model: str = _DEFAULT_CHAT_MODEL,
        top_k: int = _DEFAULT_TOP_K,
        persist_dir: str | Path | None = None,
    ) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise BaselineError(
                "OPENAI_API_KEY not set. LangChain vector baseline "
                "requires an OpenAI API key."
            )

        deps = _require_deps()
        self._deps = deps
        self.top_k = top_k
        self.chat_model_name = chat_model
        self.embedding_model_name = embedding_model

        chunks = render_graph_as_chunks(schema_path, data_path)
        if not chunks:
            raise BaselineError(
                f"No ABox entities found in {data_path}. "
                "Vector baseline has nothing to index."
            )
        self._chunks = chunks

        embeddings = deps["OpenAIEmbeddings"](model=embedding_model)
        if persist_dir is not None:
            self._vectorstore = deps["Chroma"].from_texts(
                texts=chunks,
                embedding=embeddings,
                persist_directory=str(persist_dir),
            )
        else:
            self._vectorstore = deps["Chroma"].from_texts(
                texts=chunks, embedding=embeddings
            )

        llm = deps["ChatOpenAI"](model=chat_model, temperature=0)
        self._qa = deps["RetrievalQA"].from_chain_type(
            llm=llm,
            retriever=self._vectorstore.as_retriever(
                search_kwargs={"k": top_k}
            ),
            chain_type="stuff",
            return_source_documents=True,
        )

    async def answer(self, question: str) -> BaselineAnswer:
        start = time.perf_counter()
        # LangChain's RetrievalQA is sync; offload to default executor
        import asyncio  # noqa: PLC0415

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._qa.invoke, {"query": question})
        latency_ms = (time.perf_counter() - start) * 1000

        text = result.get("result", "") if isinstance(result, dict) else str(result)
        sources = (
            result.get("source_documents", [])
            if isinstance(result, dict)
            else []
        )

        return BaselineAnswer(
            text=text,
            cited_triples=[],  # vector RAG has no triples
            tool_calls=0,
            latency_ms=latency_ms,
            extra={
                "retrieved_chunks": [
                    doc.page_content if hasattr(doc, "page_content") else str(doc)
                    for doc in sources
                ],
                "embedding_model": self.embedding_model_name,
                "chat_model": self.chat_model_name,
                "top_k": self.top_k,
            },
        )

    async def close(self) -> None:
        # Chroma in-memory store has no explicit close
        return None
