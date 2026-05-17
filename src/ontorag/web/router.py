from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import html as _html

from dotenv import set_key
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from rdflib import Graph

from ontorag.api.deps import get_store
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
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    }
)


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

    # Object properties with domain/range — draws cross-class edges
    try:
        prop_q = """
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?prop ?propLabel ?domain ?range
FROM <urn:ontorag:schema>
WHERE {
    ?prop a owl:ObjectProperty .
    OPTIONAL { ?prop rdfs:label ?propLabel }
    OPTIONAL { ?prop rdfs:domain ?domain }
    OPTIONAL { ?prop rdfs:range ?range }
    FILTER(BOUND(?domain) && BOUND(?range))
}
"""
        prop_result = await store._sparql_select(prop_q)
        seen: set[str] = set()
        for row in prop_result.get("results", {}).get("bindings", []):
            prop_uri = row.get("prop", {}).get("value", "")
            domain = row.get("domain", {}).get("value", "")
            range_ = row.get("range", {}).get("value", "")
            prop_label = row.get("propLabel", {}).get("value") or (
                prop_uri.split("#")[-1].split("/")[-1]
            )
            key = f"{domain}|{prop_uri}|{range_}"
            if key not in seen and domain and range_:
                seen.add(key)
                edges.append(
                    {
                        "data": {
                            "id": f"p_{abs(hash(key))}",
                            "source": domain,
                            "target": range_,
                            "label": prop_label,
                            "uri": prop_uri,
                            "edge_type": "property",
                        }
                    }
                )
    except Exception:
        pass

    return JSONResponse({"nodes": nodes, "edges": edges, "namespaces": schema.namespaces})


@router.post("/schema/check", response_class=HTMLResponse)
async def schema_check(
    request: Request,
    check_type: Annotated[str, Form()] = "syntax",
    ttl_content: Annotated[str, Form()] = "",
    shapes_content: Annotated[str, Form()] = "",
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
        request, "partials/validate_result.html", {"result": result, "check_type": check_type}
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
        return HTMLResponse(f"<p class='text-red-400 text-sm p-4'>오류: {_html.escape(str(exc))}</p>")
    return templates.TemplateResponse(
        request, "partials/instances_grid.html", {"entities": entities, "class_uri": class_uri}
    )


@router.get("/data/entity-graph")
async def entity_graph(
    uri: str, store: FusekiStore = Depends(get_store)
) -> JSONResponse:
    """Traversal graph data (nodes/edges) for a single entity."""
    try:
        result = await store.traverse(uri, max_depth=2, direction=TraversalDirection.both)
        return JSONResponse({"nodes": result.nodes, "edges": result.edges})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Playground tab ─────────────────────────────────────────────────────────────


@router.get("/playground", response_class=HTMLResponse)
async def ui_playground(request: Request) -> HTMLResponse:
    config = {k: os.environ.get(k, "") for k in sorted(_CONFIG_KEYS)}
    return templates.TemplateResponse(
        request, "playground.html", {"active_tab": "playground", "config": config}
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
