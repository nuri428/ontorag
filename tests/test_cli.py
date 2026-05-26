"""Tests for CLI commands: config set/show, status."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from ontorag.cli import app

runner = CliRunner()


# ── config set ───────────────────────────────────────────────────────────────


def test_config_set_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--provider", "anthropic"])
    assert result.exit_code == 0
    assert "LLM_PROVIDER=anthropic" in result.output
    assert (tmp_path / ".env").exists()


def test_config_set_invalid_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--provider", "invalid"])
    assert result.exit_code != 0
    assert "provider" in result.output.lower() or "Error" in result.output


def test_config_set_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--model", "gpt-4o"])
    assert result.exit_code == 0
    assert "LLM_MODEL=gpt-4o" in result.output


def test_config_set_fuseki_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--fuseki-url", "http://fuseki:3030"])
    assert result.exit_code == 0
    assert "FUSEKI_URL=http://fuseki:3030" in result.output


def test_config_set_no_options(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set"])
    assert result.exit_code == 0
    assert "변경 사항 없음" in result.output


def test_config_set_ollama_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["config", "set", "--ollama-url", "http://localhost:11434"]
    )
    assert result.exit_code == 0
    assert "OLLAMA_BASE_URL" in result.output


# ── config set: backend (Neo4j / GRAPH_STORE / Qdrant) ─────────────────────────


def test_config_set_graph_store_neo4j(tmp_path, monkeypatch):
    from dotenv import dotenv_values  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--graph-store", "neo4j"])
    assert result.exit_code == 0
    assert "GRAPH_STORE=neo4j" in result.output
    # set_key quotes values in .env — parse rather than substring-match.
    assert dotenv_values(tmp_path / ".env").get("GRAPH_STORE") == "neo4j"


def test_config_set_graph_store_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "set", "--graph-store", "mongodb"])
    assert result.exit_code != 0
    assert "graph-store" in result.output or "Error" in result.output


def test_config_set_neo4j_url_and_user(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "config", "set",
            "--neo4j-url", "bolt://db:7687",
            "--neo4j-user", "neo4j",
        ],
    )
    assert result.exit_code == 0
    assert "NEO4J_URI=bolt://db:7687" in result.output
    assert "NEO4J_USER=neo4j" in result.output
    from dotenv import dotenv_values  # noqa: PLC0415

    vals = dotenv_values(tmp_path / ".env")
    assert vals.get("NEO4J_URI") == "bolt://db:7687"
    assert vals.get("NEO4J_USER") == "neo4j"


def test_config_set_neo4j_password_masked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["config", "set", "--neo4j-password", "superSecret123"]
    )
    assert result.exit_code == 0
    assert "NEO4J_PASSWORD=***" in result.output
    assert "superSecret123" not in result.output  # never echoed
    assert "superSecret123" in (tmp_path / ".env").read_text()  # but persisted


def test_config_set_qdrant_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["config", "set", "--qdrant-url", "http://qdrant:6333"]
    )
    assert result.exit_code == 0
    assert "QDRANT_URL=http://qdrant:6333" in result.output


# ── config show ──────────────────────────────────────────────────────────────


def test_config_show_no_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert ".env" in result.output


def test_config_show_with_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=openai\nLLM_MODEL=gpt-4o\n")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "openai" in result.output
    assert "gpt-4o" in result.output


def test_config_show_masks_api_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-supersecretkey12345\n")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "supersecretkey12345" not in result.output
    assert "sk-ant-s" in result.output  # first 8 chars shown


def test_config_show_includes_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GRAPH_STORE=neo4j\nNEO4J_URI=bolt://db:7687\nNEO4J_USER=neo4j\n"
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "neo4j" in result.output
    assert "bolt://db:7687" in result.output


def test_config_show_masks_neo4j_password(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("NEO4J_PASSWORD=verysecretpw99999\n")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "verysecretpw99999" not in result.output  # masked
    assert "verysecr" in result.output  # first 8 chars shown


# ── status ───────────────────────────────────────────────────────────────────


def test_status_connected(monkeypatch):
    mock_status = MagicMock(
        store_type="fuseki",
        connected=True,
        schema_loaded=True,
        data_loaded=True,
        triple_count=100,
    )
    mock_store = AsyncMock()
    mock_store.status = AsyncMock(return_value=mock_status)

    with patch("ontorag.stores.fuseki.FusekiStore") as MockStore:
        MockStore.from_env.return_value = mock_store
        result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "connected" in result.output.lower() or "fuseki" in result.output.lower()


def test_status_disconnected(monkeypatch):
    mock_store = AsyncMock()
    mock_store.status = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("ontorag.stores.fuseki.FusekiStore") as MockStore:
        MockStore.from_env.return_value = mock_store
        result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "Error" in result.output or "실패" in result.output


# ── embed command (graph embeddings, Neo4j-only) ──────────────────────────────


def test_embed_on_non_neo4j_backend_exits_and_closes_store():
    """`ontorag embed` on a store without build_embeddings exits 1 AND still
    closes the store (regression: early-exit guard must not leak the store)."""

    class _FakeFusekiStore:
        def __init__(self) -> None:
            self.closed = False

        # No build_embeddings attribute → capability guard trips.
        async def aclose(self) -> None:
            self.closed = True

    fake = _FakeFusekiStore()
    with patch("ontorag.stores.factory.create_store", return_value=fake):
        result = runner.invoke(app, ["embed", "--mode", "structural"])

    assert result.exit_code == 1
    assert fake.closed is True  # aclose ran despite the early exit


def test_embed_rejects_invalid_mode():
    result = runner.invoke(app, ["embed", "--mode", "bogus"])
    assert result.exit_code == 1
