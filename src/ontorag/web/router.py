from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

import html as _html

from dotenv import dotenv_values, set_key
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from rdflib import Graph

from ontorag.api.deps import get_store
from ontorag.chat import store as chat_store
from ontorag.stores.base import TraversalDirection
from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ui", tags=["ui"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

try:
    from pyshacl import validate as _shacl_validate  # type: ignore[import-untyped]

    _HAS_SHACL = True
except ImportError:
    _HAS_SHACL = False

_CONFIG_KEYS = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
    }
)

# Known models per provider (static lists; Ollama fetches from live API)
_ANTHROPIC_MODELS = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]
_OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
]


@router.get("/", response_class=RedirectResponse)
async def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/schema")


# ── Schema tab ─────────────────────────────────────────────────────────────────


@router.get("/schema", response_class=HTMLResponse)
async def ui_schema(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "schema.html", {"active_tab": "schema"})


@router.get("/schema/graph-data")
async def schema_graph_data(store: FusekiStore = Depends(get_store)) -> JSONResponse:
    """Return Cytoscape.js node/edge data for the TBox class hierarchy."""
    try:
        schema = await store.get_schema()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    nodes = []
    edges = []
    for cls in schema.classes:
        local_name = cls.uri.split("#")[-1].split("/")[-1]
        nodes.append(
            {
                "data": {
                    "id": cls.uri,
                    "label": cls.label or local_name,
                    "uri": cls.uri,
                    "instance_count": cls.instance_count,
                    "property_count": cls.property_count,
                }
            }
        )
        if cls.parent_uri:
            edges.append(
                {
                    "data": {
                        "id": f"h_{abs(hash(cls.uri))}",
                        "source": cls.parent_uri,
                        "target": cls.uri,
                        "label": "subClassOf",
                        "edge_type": "hierarchy",
                    }
                }
            )

    return JSONResponse(
        {"nodes": nodes, "edges": edges, "namespaces": schema.namespaces}
    )


_MAX_TTL_BYTES = 500_000  # ~5k triples; prevents rdflib OOM on huge payloads


@router.post("/schema/check", response_class=HTMLResponse)
async def schema_check(
    request: Request,
    check_type: Annotated[str, Form()] = "syntax",
    ttl_content: Annotated[str, Form(max_length=_MAX_TTL_BYTES)] = "",
    shapes_content: Annotated[str, Form(max_length=_MAX_TTL_BYTES)] = "",
) -> HTMLResponse:
    """Syntax check or SHACL validation. Returns HTMX partial."""
    result: dict = {}
    if check_type == "syntax":
        g = Graph()
        try:
            g.parse(data=ttl_content, format="turtle")
            result = {
                "ok": True,
                "triple_count": len(g),
                "message": f"{len(g)}개 트리플 파싱 성공",
            }
        except Exception as exc:
            result = {"ok": False, "message": str(exc)}

    elif check_type == "shacl":
        if not _HAS_SHACL:
            result = {
                "ok": False,
                "message": "pyshacl 미설치. uv add pyshacl 후 서버를 재시작하세요.",
            }
        else:
            try:
                data_g = Graph()
                data_g.parse(data=ttl_content, format="turtle")
                shapes_g = None
                if shapes_content.strip():
                    shapes_g = Graph()
                    shapes_g.parse(data=shapes_content, format="turtle")
                conforms, _, results_text = _shacl_validate(
                    data_g,
                    shacl_graph=shapes_g,
                    inference="rdfs",
                    abort_early=False,
                )
                result = {"ok": conforms, "conforms": conforms, "message": results_text}
            except Exception as exc:
                result = {"ok": False, "message": str(exc)}

    return templates.TemplateResponse(
        request,
        "partials/validate_result.html",
        {"result": result, "check_type": check_type},
    )


# ── Data tab ───────────────────────────────────────────────────────────────────


@router.get("/data", response_class=HTMLResponse)
async def ui_data(
    request: Request, store: FusekiStore = Depends(get_store)
) -> HTMLResponse:
    try:
        schema = await store.get_schema()
        classes = schema.classes
    except Exception:
        classes = []
    return templates.TemplateResponse(
        request, "data.html", {"active_tab": "data", "classes": classes}
    )


@router.get("/data/instances", response_class=HTMLResponse)
async def data_instances(
    request: Request,
    class_uri: str = "",
    limit: int = 50,
    store: FusekiStore = Depends(get_store),
) -> HTMLResponse:
    """Instance grid HTML — target for HTMX swap on class selection."""
    if not class_uri:
        return HTMLResponse(
            "<p class='text-gray-600 text-sm p-6 text-center'>클래스를 선택하세요.</p>"
        )
    try:
        entities = await store.find_entities(class_uri, limit=limit)
    except Exception as exc:
        return HTMLResponse(
            f"<p class='text-red-400 text-sm p-4'>오류: {_html.escape(str(exc))}</p>"
        )
    return templates.TemplateResponse(
        request,
        "partials/instances_grid.html",
        {"entities": entities, "class_uri": class_uri},
    )


@router.get("/data/entity-detail")
async def entity_detail(
    uri: str, store: FusekiStore = Depends(get_store)
) -> JSONResponse:
    """Entity properties (describe_entity) + depth-2 graph (traverse) in one response."""
    try:
        entity, traversal = await asyncio.gather(
            store.describe_entity(uri),
            store.traverse(uri, max_depth=2, direction=TraversalDirection.both),
        )
        return JSONResponse(
            {
                "label": entity.label,
                "class_uri": entity.class_uri,
                "properties": entity.properties,
                "graph": {"nodes": traversal.nodes, "edges": traversal.edges},
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _web_suffix(filename: str | None) -> str:
    if filename and "." in filename:
        return f".{filename.rsplit('.', 1)[-1]}"
    return ".ttl"


@router.post("/schema/upload", response_class=HTMLResponse)
async def schema_upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    store: FusekiStore = Depends(get_store),
) -> HTMLResponse:
    """Upload a TBox (schema) RDF file — always replaces the existing schema."""
    content = await file.read()
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=_web_suffix(file.filename), delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = await store.load_rdf(tmp_path, mode="schema")
        return templates.TemplateResponse(
            request,
            "partials/upload_result.html",
            {
                "ok": True,
                "triples": result.triples_loaded,
                "mode": "schema",
                "replaced": True,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/upload_result.html",
            {"ok": False, "error": str(exc), "mode": "schema"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/data/upload", response_class=HTMLResponse)
async def data_upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    replace: Annotated[str, Form()] = "false",
    store: FusekiStore = Depends(get_store),
) -> HTMLResponse:
    """Upload an ABox (data) RDF file — append by default, replace when replace=true."""
    should_replace = replace.lower() == "true"
    content = await file.read()
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=_web_suffix(file.filename), delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        result = await store.load_rdf(tmp_path, mode="data", replace=should_replace)
        return templates.TemplateResponse(
            request,
            "partials/upload_result.html",
            {
                "ok": True,
                "triples": result.triples_loaded,
                "mode": "data",
                "replaced": should_replace,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/upload_result.html",
            {"ok": False, "error": str(exc), "mode": "data"},
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Playground tab ─────────────────────────────────────────────────────────────


def _load_config() -> dict[str, str]:
    """Merge .env file values with os.environ (os.environ wins on conflict).

    Reads .env directly on every call so manual edits are reflected without
    a server restart. os.environ takes precedence because it includes both
    real system env vars and values set by save_config().
    """
    env_path = Path(".env")
    file_vals: dict[str, str] = (
        dict(dotenv_values(str(env_path))) if env_path.exists() else {}
    )
    config: dict[str, str] = {}
    for k in _CONFIG_KEYS:
        env_val = os.environ.get(k)
        config[k] = env_val if env_val is not None else file_vals.get(k, "")
    return config


@router.get("/playground", response_class=HTMLResponse)
async def ui_playground(request: Request) -> HTMLResponse:
    config = _load_config()
    sessions = await chat_store.list_sessions()
    return templates.TemplateResponse(
        request,
        "playground.html",
        {"active_tab": "playground", "config": config, "sessions": sessions},
    )


@router.post("/playground/config", response_class=HTMLResponse)
async def save_config(request: Request) -> HTMLResponse:
    """Write LLM config changes to .env and update os.environ."""
    form = await request.form()
    env_path = Path(".env")
    if not env_path.exists():
        env_path.touch()

    errors: list[str] = []
    for key, value in form.items():
        if key not in _CONFIG_KEYS:
            continue
        try:
            set_key(str(env_path), key, str(value))
            os.environ[key] = str(value)
        except Exception as exc:
            errors.append(f"{key}: {exc}")

    return templates.TemplateResponse(
        request, "partials/config_saved.html", {"errors": errors, "ok": not errors}
    )


@router.get("/playground/models")
async def playground_models(provider: str) -> dict:
    """Return available model IDs for the given LLM provider.

    Anthropic and OpenAI return static curated lists.
    Ollama fetches the list of installed models from the local API.
    """
    if provider == "anthropic":
        return {"models": _ANTHROPIC_MODELS}

    if provider == "openai":
        return {"models": _OPENAI_MODELS}

    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip(
            "/"
        )
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base_url}/api/tags", timeout=5.0)
                resp.raise_for_status()
                data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"models": models}
        except Exception as exc:
            logger.warning("Ollama model list failed: %s", exc)
            return {
                "models": [],
                "error": "Ollama에 연결할 수 없습니다 — URL을 먼저 저장하세요",
            }

    return {"models": []}


# ── Playground session management ──────────────────────────────────────────────


@router.get("/playground/sessions", response_class=HTMLResponse)
async def playground_session_list(request: Request) -> HTMLResponse:
    sessions = await chat_store.list_sessions()
    return templates.TemplateResponse(
        request, "partials/session_list.html", {"sessions": sessions}
    )


@router.post("/playground/sessions")
async def playground_session_create() -> dict:
    """Create a new chat session and return its ID."""
    session_id = await chat_store.create_session()
    return {"session_id": session_id}


@router.delete("/playground/sessions/{session_id}")
async def playground_session_delete(session_id: str) -> dict:
    await chat_store.delete_session(session_id)
    return {"deleted": session_id}


@router.get("/playground/sessions/{session_id}/messages")
async def playground_session_messages(session_id: str) -> dict:
    """Return user/assistant display messages for a session (JSON)."""
    history = await chat_store.get_history(session_id)
    messages = chat_store.extract_display_messages(history)
    return {"session_id": session_id, "messages": messages}
