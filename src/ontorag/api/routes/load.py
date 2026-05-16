from __future__ import annotations

import logging
import os
import tempfile
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ontorag.api.deps import get_store
from ontorag.stores.base import LoadResult
from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ontology"])


@router.post(
    "/load",
    operation_id="load_rdf",
    summary="RDF 파일을 그래프 스토어에 로드",
    response_model=LoadResult,
)
async def load_rdf(
    file: Annotated[
        UploadFile,
        File(description="RDF 파일 (TTL, JSON-LD, RDF/XML)"),
    ],
    mode: Annotated[
        Literal["schema", "data", "auto"],
        Form(description="schema=TBox, data=ABox, auto=내용으로 자동 감지"),
    ] = "auto",
    store: FusekiStore = Depends(get_store),
) -> LoadResult:
    """Upload an RDF file and load it into the graph store.

    Args:
        file: RDF file to upload.
        mode: Load mode — auto-detects TBox vs ABox when set to "auto".

    Returns:
        Number of triples loaded and the resolved load mode.
    """
    content = await file.read()
    suffix = _file_suffix(file.filename)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        return await store.load_rdf(tmp_path, mode)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _file_suffix(filename: str | None) -> str:
    if filename and "." in filename:
        return f".{filename.rsplit('.', 1)[-1]}"
    return ".ttl"
