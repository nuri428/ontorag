from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.stores.base import EntityResult, SchemaResult, TraversalResult
from ontorag.stores.fuseki import FusekiStore

# ── Shared mock store fixture ──────────────────────────────────────────────────


@pytest.fixture
def mock_store():
    store = MagicMock(spec=FusekiStore)
    return store


@pytest.fixture
def client(mock_store):
    app.dependency_overrides[get_store] = lambda: mock_store
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ── Schema tab ─────────────────────────────────────────────────────────────────


def test_ui_root_redirects_to_schema(client):
    response = client.get("/ui/", follow_redirects=False)
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"].endswith("/ui/schema")


def test_schema_page_returns_html(client):
    response = client.get("/ui/schema")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"TBox" in response.content


def test_schema_graph_data_returns_nodes_and_edges(client, mock_store):
    from ontorag.stores.base import ClassSummary, PropertySummary

    mock_schema = SchemaResult(
        classes=[
            ClassSummary(
                uri="http://ex.org/Person",
                label="Person",
                parent_uri=None,
                instance_count=3,
                property_count=1,
            ),
            ClassSummary(
                uri="http://ex.org/Employee",
                label="Employee",
                parent_uri="http://ex.org/Person",
                instance_count=1,
                property_count=0,
            ),
        ],
        properties=[
            PropertySummary(
                uri="http://ex.org/name",
                label="name",
                domain_uri="http://ex.org/Person",
                range_uri=None,
                prop_type="datatype",
            )
        ],
        namespaces={"ex": "http://ex.org/"},
        total_classes=2,
        total_properties=1,
    )
    mock_store.get_schema = AsyncMock(return_value=mock_schema)

    response = client.get("/ui/schema/graph-data")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["data"]["edge_type"] == "hierarchy"


def test_schema_graph_data_503_when_store_fails(client, mock_store):
    mock_store.get_schema = AsyncMock(side_effect=RuntimeError("Fuseki down"))
    response = client.get("/ui/schema/graph-data")
    assert response.status_code == 503


def test_schema_check_syntax_valid_ttl(client):
    ttl = "@prefix ex: <http://ex.org/> .\nex:Alice a ex:Person ."
    response = client.post(
        "/ui/schema/check",
        data={"check_type": "syntax", "ttl_content": ttl},
    )
    assert response.status_code == 200
    assert (
        "triple_count" not in response.text or response.text
    )  # success partial rendered


def test_schema_check_syntax_invalid_ttl(client):
    response = client.post(
        "/ui/schema/check",
        data={"check_type": "syntax", "ttl_content": "this is not turtle !!!"},
    )
    assert response.status_code == 200
    assert "red" in response.text  # error styling rendered


def test_schema_check_ttl_size_limit_enforced(client):
    huge = "x" * 600_000
    response = client.post(
        "/ui/schema/check",
        data={"check_type": "syntax", "ttl_content": huge},
    )
    assert response.status_code == 422


# ── Data tab ───────────────────────────────────────────────────────────────────


def test_data_page_returns_html(client, mock_store):
    mock_store.get_schema = AsyncMock(
        return_value=SchemaResult(
            classes=[],
            properties=[],
            namespaces={},
            total_classes=0,
            total_properties=0,
        )
    )
    response = client.get("/ui/data")
    assert response.status_code == 200
    assert b"ABox" in response.content


def test_data_instances_no_class_returns_prompt(client):
    response = client.get("/ui/data/instances", params={"class_uri": ""})
    assert response.status_code == 200
    assert response.status_code == 200


def test_data_instances_returns_table(client, mock_store):
    mock_store.find_entities = AsyncMock(
        return_value=[
            EntityResult(
                uri="http://ex.org/alice",
                label="Alice",
                class_uri="http://ex.org/Person",
                properties={"name": "Alice"},
            )
        ]
    )
    response = client.get(
        "/ui/data/instances",
        params={"class_uri": "http://ex.org/Person"},
    )
    assert response.status_code == 200
    assert b"Alice" in response.content


def test_data_instances_store_error_returns_error_html(client, mock_store):
    mock_store.find_entities = AsyncMock(side_effect=RuntimeError("SPARQL error"))
    response = client.get(
        "/ui/data/instances",
        params={"class_uri": "http://ex.org/Person"},
    )
    assert response.status_code == 200
    assert b"SPARQL error" in response.content


def test_entity_detail_returns_json(client, mock_store):
    mock_store.describe_entity = AsyncMock(
        return_value=EntityResult(
            uri="http://ex.org/alice",
            label="Alice",
            class_uri="http://ex.org/Person",
            properties={"name": "Alice"},
        )
    )
    mock_store.traverse = AsyncMock(
        return_value=TraversalResult(
            start_uri="http://ex.org/alice",
            end_uri=None,
            nodes=[{"uri": "http://ex.org/alice", "label": "Alice", "depth": 0}],
            edges=[],
            depth_reached=0,
        )
    )
    response = client.get(
        "/ui/data/entity-detail", params={"uri": "http://ex.org/alice"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["label"] == "Alice"
    assert data["properties"] == {"name": "Alice"}
    assert len(data["graph"]["nodes"]) == 1


def test_entity_detail_500_on_store_error(client, mock_store):
    mock_store.describe_entity = AsyncMock(side_effect=RuntimeError("not found"))
    mock_store.traverse = AsyncMock(side_effect=RuntimeError("not found"))
    response = client.get(
        "/ui/data/entity-detail", params={"uri": "http://ex.org/ghost"}
    )
    assert response.status_code == 500


# ── Playground tab ─────────────────────────────────────────────────────────────


def test_playground_page_returns_html(client):
    response = client.get("/ui/playground")
    assert response.status_code == 200
    assert b"text/html" in response.headers["content-type"].encode()


def test_playground_config_save_rejects_unknown_keys(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/ui/playground/config",
        data={"UNKNOWN_KEY": "value", "LLM_PROVIDER": "anthropic"},
    )
    assert response.status_code == 200
    env_text = (tmp_path / ".env").read_text()
    assert "UNKNOWN_KEY" not in env_text
    assert "LLM_PROVIDER" in env_text


def test_playground_config_save_creates_env_file(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / ".env").exists()
    client.post(
        "/ui/playground/config",
        data={"LLM_PROVIDER": "openai"},
    )
    assert (tmp_path / ".env").exists()
