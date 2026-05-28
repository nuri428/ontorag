from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from ontorag.api.routes import chat, dump, health, load, status
from ontorag.api.routes.tools import (
    _sparql,
    bayes,
    causal,
    entities,
    learning,
    pattern,
    schema,
    search,
    similar,
    traversal,
)
from ontorag.web.router import router as web_router

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    from ontorag.chat import store as chat_store

    await chat_store.init_db()
    logger.info("ontorag API starting")
    yield
    # Close the HTTP client held by the singleton store
    from ontorag.api.deps import get_store

    store = get_store()
    await store.aclose()
    logger.info("ontorag API stopped")


app = FastAPI(
    title="ontorag",
    description="Ontology-aware RAG framework — ontology as the source of truth.",
    version="0.1.0",
    lifespan=lifespan,
)

# System routes
app.include_router(health.router)
app.include_router(status.router)
app.include_router(load.router)
app.include_router(dump.router)
app.include_router(chat.router)

# Tool routes — Layer 1 + Layer 2 (exposed via MCP)
app.include_router(schema.router)
app.include_router(entities.router)
app.include_router(traversal.router)
app.include_router(pattern.router)

# v0.3 LLMs4OL learning tools (exposed via MCP)
app.include_router(learning.router)

# v0.5 BM25 full-text search (Neo4j capability; 501 on Fuseki)
app.include_router(search.router)

# v0.5 graph-embedding similarity search (Neo4j capability; 501 on Fuseki)
app.include_router(similar.router)

# v0.7 Bayesian inference (compute_posterior, mpe) — capability on both backends
app.include_router(bayes.router)

# v0.8 Causal inference (do_query, identify_effect, counterfactual) — both backends
app.include_router(causal.router)

# Debug route — Layer 3 (NOT exposed via MCP)
app.include_router(_sparql.router)

# Web UI — served at /ui/*
app.include_router(web_router)

# MCP server — mount after all routes are registered
# query_sparql_raw is excluded: internal/debug use only, never LLM-callable
# dump_graph excluded: file download endpoint, not an LLM-callable tool
mcp = FastApiMCP(app, exclude_operations=["query_sparql_raw", "dump_graph"])
mcp.mount_http()
