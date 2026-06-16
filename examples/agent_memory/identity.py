"""AgentIdentity — 사용자 + 워크스페이스 + 세션 식별자.

같은 서버에서 여러 Claude Code 인스턴스가 동시에 실행될 때
각 인스턴스의 메모리를 격리하는 핵심 식별자.

격리 구조:
  user + workspace → named graph (영구 격리)
  session_id       → 트리플 태그 (필터 가능, 격리 아님)
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _git_user_slug() -> str:
    """git config user.email → 로컬파트 slug."""
    try:
        email = subprocess.check_output(
            ["git", "config", "user.email"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        local = email.split("@")[0]
        return re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    except Exception:
        return re.sub(r"[^a-z0-9]+", "-", os.environ.get("USER", "unknown").lower())


def _workspace_slug(path: str | None = None) -> str:
    """현재 디렉토리 → 마지막 2단계 slug.

    /Users/nuri/dev/git/ws/ontorag  →  ws-ontorag
    /Users/nuri/projects/foo         →  projects-foo
    """
    cwd = Path(path or os.getcwd())
    parts = [p for p in cwd.parts if p not in ("", "/")]
    slug_parts = parts[-2:] if len(parts) >= 2 else parts
    return "-".join(re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-") for p in slug_parts)


def _session_id() -> str:
    """프로세스 기반 고유 세션 ID (재시작 시 갱신)."""
    raw = f"{os.getpid()}-{os.urandom(4).hex()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass
class AgentIdentity:
    """단일 Claude Code 인스턴스의 식별자."""

    user: str       # "greennuri"
    workspace: str  # "ws-ontorag"
    session_id: str = field(default_factory=_session_id)

    # ── 파생 속성 ──────────────────────────────────────────────────────────────

    @property
    def ontology_id(self) -> str:
        """ontorag의 assert_triple(ontology=...) 에 넣는 값.

        → named graph: urn:ontorag:{ontology_id}:data
        """
        return f"{self.user}_{self.workspace}"

    @property
    def uri_prefix(self) -> str:
        """이 인스턴스가 생성하는 엔티티 URI의 prefix."""
        return f"urn:ag:{self.user}:{self.workspace}"

    @property
    def session_uri(self) -> str:
        """현재 세션의 URI (트리플 태깅용)."""
        return f"{self.uri_prefix}:session:{self.session_id}"

    @property
    def graph_uri(self) -> str:
        """Fuseki named graph URI (읽기 전용 참고용)."""
        return f"urn:ontorag:{self.ontology_id}:data"

    def entity_uri(self, *path_parts: str) -> str:
        """이 인스턴스 소유의 엔티티 URI 생성.

        identity.entity_uri("decision", "2026-06-15", "mcp")
        → "urn:ag:greennuri:ws-ontorag:decision:2026-06-15:mcp"
        """
        slug = ":".join(
            re.sub(r"[^a-z0-9가-힣\-]+", "-", p.lower()).strip("-")
            for p in path_parts
        )
        return f"{self.uri_prefix}:{slug}"

    # ── 팩토리 ────────────────────────────────────────────────────────────────

    @classmethod
    def auto_detect(cls, cwd: str | None = None) -> "AgentIdentity":
        """현재 환경에서 자동 감지."""
        return cls(
            user=os.environ.get("ONTORAG_USER", _git_user_slug()),
            workspace=os.environ.get("ONTORAG_WORKSPACE", _workspace_slug(cwd)),
        )

    @classmethod
    def from_env(cls) -> "AgentIdentity":
        """환경 변수로 명시적 설정.

        ONTORAG_USER, ONTORAG_WORKSPACE, ONTORAG_SESSION_ID
        """
        return cls(
            user=os.environ.get("ONTORAG_USER", _git_user_slug()),
            workspace=os.environ.get("ONTORAG_WORKSPACE", _workspace_slug()),
            session_id=os.environ.get("ONTORAG_SESSION_ID", _session_id()),
        )

    def __str__(self) -> str:
        return f"{self.user}/{self.workspace} (session: {self.session_id})"


if __name__ == "__main__":
    identity = AgentIdentity.auto_detect()
    print(f"user:        {identity.user}")
    print(f"workspace:   {identity.workspace}")
    print(f"session_id:  {identity.session_id}")
    print(f"ontology_id: {identity.ontology_id}")
    print(f"graph_uri:   {identity.graph_uri}")
    print(f"session_uri: {identity.session_uri}")
    print(f"entity_uri:  {identity.entity_uri('decision', '2026-06-15', 'mcp')}")
