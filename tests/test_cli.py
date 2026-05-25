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
