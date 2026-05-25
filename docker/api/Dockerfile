FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

# Install with the neo4j + vector extras so the api container can serve any
# backend (GRAPH_STORE=neo4j needs the neo4j driver; Fuseki find_similar needs
# qdrant-client). The fuseki-only default does not require them, but baking them
# in keeps a single image usable for every GRAPH_STORE / find_similar setup.
RUN uv pip install --system --no-cache '.[neo4j,vector]'

EXPOSE 8000

CMD ["uvicorn", "ontorag.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
