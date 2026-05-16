FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN uv pip install --system --no-cache .

EXPOSE 8000

CMD ["uvicorn", "ontorag.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
