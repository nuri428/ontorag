"""Entity normalizer — free-text → canonical URI.

사용법:
    from normalizer import resolve, resolve_predicate

    resolve("patent board")   # → "urn:ag:proj:patent-board"
    resolve("OntoRAG")        # → "urn:ag:proj:ontorag"
    resolve("헤르메스")        # → "urn:ag:agent:hermes"
    resolve("unknown term")   # → "urn:ag:entity:unknown-term"  (auto-slug)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# ── Load registry ─────────────────────────────────────────────────────────────

_REGISTRY_PATH = Path(__file__).parent / "entity_registry.yaml"

_canonical: dict[str, dict] = {}   # uri → metadata
_alias_map: dict[str, str] = {}    # alias (lower) → canonical uri

def _load() -> None:
    data = yaml.safe_load(_REGISTRY_PATH.read_text())
    for entry in data["entities"]:
        uri = entry["uri"]
        _canonical[uri] = entry
        for alias in entry.get("aliases", []):
            _alias_map[alias.lower()] = uri
            _alias_map[alias] = uri          # exact-case도 등록

_load()


# ── Public API ────────────────────────────────────────────────────────────────

def resolve(text: str) -> str:
    """텍스트를 canonical URI로 변환. 미등록 용어는 자동 slug URI 생성."""
    if text in _alias_map:
        return _alias_map[text]
    lower = text.lower()
    if lower in _alias_map:
        return _alias_map[lower]
    slug = re.sub(r"[^a-z0-9가-힣]+", "-", lower).strip("-")
    return f"urn:ag:entity:{slug}"


def label_of(uri: str) -> str:
    """canonical URI → 사람이 읽을 수 있는 레이블."""
    meta = _canonical.get(uri)
    return meta["label"] if meta else uri.split(":")[-1]


def all_entities() -> list[dict]:
    """등록된 모든 엔티티 메타데이터 반환."""
    return list(_canonical.values())


# ── 관계 predicate 상수 ───────────────────────────────────────────────────────
# SPARQL injection을 막기 위해 predicate는 상수로만 사용.

class P:
    """Predicate URI 상수 모음."""
    LABEL        = "http://www.w3.org/2000/01/rdf-schema#label"
    TYPE         = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    DEPENDS_ON   = "urn:ag:rel:dependsOn"
    USES         = "urn:ag:rel:uses"
    INVOLVES     = "urn:ag:rel:involves"
    ENABLES      = "urn:ag:rel:enables"
    RELATED_TO   = "urn:ag:rel:relatedTo"
    LAYER        = "urn:ag:rel:layer"
    TARGET       = "urn:ag:rel:dogfoodTarget"
    RATIONALE    = "urn:ag:rel:rationale"
    MADE_AT      = "urn:ag:rel:madeAt"
    REJECTED     = "urn:ag:rel:rejectedAlternative"
    BECAUSE      = "urn:ag:rel:becauseOf"
    DESCRIPTION  = "urn:ag:rel:description"
    VERSION      = "urn:ag:rel:version"
    CONCEPT      = "urn:ag:rel:concept"
    # ── 메타 / 생명주기 ──────────────────────────────────────────────────────
    ASSERTED_AT  = "urn:ag:meta:assertedAt"    # xsd:dateTime 리터럴
    IN_SESSION   = "urn:ag:meta:inSession"     # session URI
    WORKSPACE    = "urn:ag:meta:workspace"     # workspace slug 리터럴
    EXPIRES_AT   = "urn:ag:meta:expiresAt"     # 명시적 만료일 (optional)


if __name__ == "__main__":
    # 노말라이즈 검증
    tests = [
        ("patent board",     "urn:ag:proj:patent-board"),
        ("patent_board",     "urn:ag:proj:patent-board"),
        ("OntoRAG",          "urn:ag:proj:ontorag"),
        ("헤르메스",          "urn:ag:agent:hermes"),
        ("hemes",            "urn:ag:agent:hermes"),
        ("Model Context Protocol", "urn:ag:tech:mcp"),
        ("MCP 서버",         "urn:ag:tech:mcp"),
        ("PROV-O write-back","urn:ag:concept:prov-o"),
    ]
    all_ok = True
    for text, expected in tests:
        got = resolve(text)
        status = "✓" if got == expected else "✗"
        if got != expected:
            all_ok = False
        print(f"  {status} resolve({text!r})\n      → {got}")
        if got != expected:
            print(f"      ≠ {expected}  ← 기대값")
    print("\n모든 테스트 통과" if all_ok else "\n일부 실패")
