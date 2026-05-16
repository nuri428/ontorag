from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontorag.stores.fuseki import DATA_GRAPH_URI, SCHEMA_GRAPH_URI, FusekiStore

_SCHEMA_TTL = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex:  <http://example.org/> .
ex:Person a owl:Class .
"""

_DATA_TTL = """\
@prefix ex: <http://example.org/> .
ex:alice a ex:Person .
"""


@pytest.fixture
def schema_file(tmp_path):
    f = tmp_path / "schema.ttl"
    f.write_text(_SCHEMA_TTL)
    return str(f)


@pytest.fixture
def data_file(tmp_path):
    f = tmp_path / "data.ttl"
    f.write_text(_DATA_TTL)
    return str(f)


@pytest.fixture
def store():
    return FusekiStore("http://localhost:3030", "test", "admin", "admin")


def _ok_response(status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_load_rdf_schema_uses_put(store, schema_file):
    mock_client = AsyncMock()
    mock_client.post.return_value = _ok_response(201)  # _ensure_dataset
    mock_client.put.return_value = _ok_response(200)   # GSP PUT

    with patch.object(store, "_http", return_value=mock_client):
        result = await store.load_rdf(schema_file, "schema")

    assert result.mode == "schema"
    assert result.triples_loaded > 0
    mock_client.put.assert_called_once()
    _, kwargs = mock_client.put.call_args
    assert kwargs["params"]["graph"] == SCHEMA_GRAPH_URI


@pytest.mark.asyncio
async def test_load_rdf_data_uses_post(store, data_file):
    mock_client = AsyncMock()
    # post is called twice: _ensure_dataset (201) + GSP data upload (200)
    mock_client.post.side_effect = [_ok_response(201), _ok_response(200)]

    with patch.object(store, "_http", return_value=mock_client):
        result = await store.load_rdf(data_file, "data")

    assert result.mode == "data"
    assert mock_client.post.call_count == 2
    # Second call is the actual GSP data upload
    _, kwargs = mock_client.post.call_args_list[1]
    assert kwargs["params"]["graph"] == DATA_GRAPH_URI


@pytest.mark.asyncio
async def test_load_rdf_auto_detects_schema(store, schema_file):
    mock_client = AsyncMock()
    mock_client.post.return_value = _ok_response(201)  # _ensure_dataset
    mock_client.put.return_value = _ok_response(200)   # GSP PUT

    with patch.object(store, "_http", return_value=mock_client):
        result = await store.load_rdf(schema_file, "auto")

    assert result.mode == "schema"


@pytest.mark.asyncio
async def test_load_rdf_raises_for_missing_file(store):
    with pytest.raises(FileNotFoundError):
        await store.load_rdf("/nonexistent/file.ttl")


@pytest.mark.asyncio
async def test_status_connected(store):
    ping_resp = _ok_response(200)

    sparql_resp = MagicMock()
    sparql_resp.status_code = 200
    sparql_resp.raise_for_status = MagicMock()
    sparql_resp.json.side_effect = [
        {"results": {"bindings": [{"n": {"value": "5"}}]}},   # schema count
        {"results": {"bindings": [{"n": {"value": "10"}}]}},  # data count
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = ping_resp
    # post: two SPARQL COUNT queries (no _ensure_dataset in status())
    mock_client.post.side_effect = [sparql_resp, sparql_resp]

    with patch.object(store, "_http", return_value=mock_client):
        s = await store.status()

    assert s.connected is True
    assert s.store_type == "fuseki"
    assert s.triple_count == 15
    assert s.schema_loaded is True
    assert s.data_loaded is True


@pytest.mark.asyncio
async def test_status_disconnected(store):
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("Connection refused")

    with patch.object(store, "_http", return_value=mock_client):
        s = await store.status()

    assert s.connected is False
    assert s.triple_count is None
    assert s.schema_loaded is False
    assert s.data_loaded is False
