# Design: Directory / Multi-file Loader

상태: 구현됨(Implemented, 매니페스트 §8 제외) · 대상 버전: v0.6 · 작성일: 2026-05-28

> 구현: `core/batch_loader.py` (오케스트레이션) + `stores/base.py`
> (`FileLoadOutcome`/`BatchLoadResult`) + `cli.py` (`load <FILE|DIR>` —
> default-command 그룹으로 위치인자 라우팅). 매니페스트(§8)는 후속 과제로 보류.
> 부수 수정: `load <PATH>` 위치인자가 Click 서브커맨드 해석과 충돌해
> 작동하지 않던 기존 버그를 default-command 폴백으로 해결.

## 1. 목표

현재 `ontorag load`는 **단일 RDF 파일**만 받는다. 이를 **디렉토리(서브디렉토리 포함)**를
한 번에 로드하도록 확장한다. 디렉토리 구조를 v0.5에서 이미 들어온 **multi-ontology 스코프**
(named graph / `_ontology` 태그)에 자연스럽게 매핑한다.

```bash
ontorag load ./ontologies/          # 디렉토리 전체 스캔 → 로드
```

핵심 사용 시나리오: "온톨로지 repo를 가리키면 알아서 TBox/ABox를 순서대로, 온톨로지별로 로드".

## 2. 설계 원칙 (반드시 지킬 것)

- **`GraphStore` Protocol을 건드리지 않는다.** `load_rdf`는 단일 파일 시그니처 그대로 유지.
  디렉토리 로직은 store 어댑터(Fuseki/Neo4j)에 들어가면 안 된다 — 중복 구현 + Protocol 비대화.
- **디렉토리 로딩 = 오케스트레이션 레이어.** `core/`에 새 모듈을 두고 내부적으로 기존
  `store.load_rdf(path, mode, replace, ontology)`를 파일마다 호출한다. 이러면 Fuseki·Neo4j
  둘 다 어댑터 수정 0으로 디렉토리 로딩을 지원한다.
- **새 개념을 만들지 않는다.** "디렉토리 단위"는 기존 multi-ontology 스코프에 파일을
  먹여주는 프런트엔드일 뿐이다. ontology id는 `core/ontology.py:validate_ontology_id` 규칙
  (`^[a-zA-Z0-9_-]+$`)을 그대로 따른다.
- **explicit over implicit.** 디렉토리→온톨로지 매핑 규칙은 예측 가능해야 하고, 모호하면
  매니페스트로 명시 가능해야 한다.

## 3. 디렉토리 → 온톨로지 매핑 규칙

우선순위 순서:

1. **`--ontology <id>` 명시 시 (플랫 병합):** 디렉토리 내 모든 RDF 파일을 그 단일 스코프에
   로드. 서브디렉토리는 의미 경계로 쓰지 않는다.
2. **매니페스트 존재 시 (`ontorag.yaml`, §8):** 매니페스트가 파일→온톨로지 매핑과 로드 순서를
   명시. 가장 우선(단, `--ontology`가 동시에 오면 에러로 거부 — 충돌).
3. **기본값 (서브디렉토리 = 온톨로지):**
   - 루트 바로 아래의 각 **서브디렉토리명**이 ontology id가 된다.
     `./ontologies/foaf/*.ttl` → ontology `foaf`, `./ontologies/pokemon/*.ttl` → `pokemon`.
   - 루트에 **직접** 놓인 파일(서브디렉토리에 속하지 않은)은 `ontology=None`
     (레거시 기본 그래프 쌍)으로 로드. 하위호환.
   - 서브디렉토리명이 slug 규칙 위반이면 즉시 검증 에러(로드 시작 전 fail-fast).
   - 2단계 이상 깊은 중첩(`foaf/sub/x.ttl`)은 가장 가까운 1-depth 디렉토리(`foaf`)에
     귀속. (단순·예측 가능 우선; 더 복잡한 매핑이 필요하면 매니페스트 사용.)

## 4. 새 결과 타입

`stores/base.py` — 기존 `LoadResult`는 그대로 두고 집계 타입을 추가한다.

```python
from pydantic import BaseModel, Field
from typing import Literal

class FileLoadOutcome(BaseModel):
    """디렉토리 로드 시 파일 1개의 결과."""
    source: str                         # 파일 경로 (루트 기준 상대)
    ontology: str | None                # 귀속된 ontology id (None = 기본 그래프)
    status: Literal["loaded", "skipped", "failed"]
    mode: Literal["schema", "data"] | None = None   # loaded일 때만
    triples_loaded: int = 0
    reason: str | None = None           # skipped/failed 사유

class BatchLoadResult(BaseModel):
    """디렉토리 로드 전체 집계."""
    root: str
    total_files: int
    loaded: int
    skipped: int
    failed: int
    total_triples: int
    outcomes: list[FileLoadOutcome] = Field(default_factory=list)
```

`BatchLoadResult`는 store가 아니라 **오케스트레이터가 반환**한다 (Protocol 밖).

## 5. `core/batch_loader.py` (신규 모듈)

```python
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ontorag.core.loader import detect_mode, parse_rdf
from ontorag.core.ontology import validate_ontology_id
from ontorag.stores.base import BatchLoadResult, FileLoadOutcome, GraphStore

# 인식할 RDF 확장자
RDF_SUFFIXES: frozenset[str] = frozenset({".ttl", ".jsonld", ".rdf", ".owl", ".n3"})
# 항상 무시할 디렉토리/패턴
DEFAULT_IGNORE: frozenset[str] = frozenset({".git", "node_modules", "__pycache__", ".venv"})

async def load_directory(
    store: GraphStore,
    root: str | Path,
    *,
    ontology: str | None = None,        # 명시 시 플랫 병합 (§3-1)
    replace: bool = False,              # ABox 재로드 정책 (§7)
    recursive: bool = True,
    ignore: frozenset[str] = DEFAULT_IGNORE,
    on_file: Callable[[FileLoadOutcome], None] | None = None,  # 진행률 콜백
) -> BatchLoadResult:
    """디렉토리를 스캔해 RDF 파일들을 그래프 스토어에 로드한다.

    매핑·순서·부분실패 처리는 모듈 docstring(§3·§6·§7) 참조.
    """
    ...
```

### 알고리즘

1. **수집(collect):** `root`를 워킹하며 `RDF_SUFFIXES` 확장자 파일만 모은다.
   `ignore` 디렉토리/숨김파일 제외. `recursive=False`면 1-depth만.
2. **매핑 결정(resolve scope):** §3 규칙으로 각 파일의 `ontology` 귀속 결정.
   매니페스트 있으면 §8 로더로 매핑 + 순서를 가져온다.
   slug 위반 발견 시 **로드 시작 전** `ValueError`로 전체 중단(fail-fast on config).
3. **모드 분류 + 정렬(classify & order):** 파일마다 `parse_rdf` → `detect_mode`로
   schema/data 판별. **온톨로지 스코프별로** `schema` 파일을 전부 먼저, 그다음 `data` 파일
   순으로 정렬(§6). 파싱 단계 실패는 `failed` outcome으로 기록하고 계속 진행.
   - 최적화 노트: §2 호출은 파일을 두 번 파싱(여기 + `store.load_rdf` 내부)한다.
     1차 구현은 단순함 우선으로 두 번 파싱 허용. 추후 `load_rdf`가 파싱된 `Graph`를
     받는 오버로드를 추가해 중복 제거(별도 커밋, Protocol 영향 검토 필요).
4. **로드(load):** 정렬된 순서대로 `await store.load_rdf(path, mode, replace=?, ontology=scope)`.
   - `replace` 적용 규칙은 §7. 각 파일 결과를 `FileLoadOutcome`으로, `on_file` 콜백 호출.
   - 파일 단위 예외(`httpx.HTTPStatusError` 등)는 잡아서 `failed`로 기록, **계속 진행**
     (continue-and-report). 단, **schema 파일 실패 시** 같은 스코프의 후속 data 파일은
     `skipped`(reason="schema load failed in scope")로 처리.
5. **집계(aggregate):** `BatchLoadResult` 반환.

## 6. schema-before-data 순서 (필수)

ABox가 올라가기 전에 TBox가 존재해야 추론·검증이 정상 동작한다. 디렉토리는 파일명 순서를
보장하지 않으므로, **스코프별로 mode=schema를 전부 먼저, mode=data를 나중에** 호출한다.
`auto` 단일파일 경로처럼 파일별 개별 감지에 의존하지 말 것.

## 7. ABox 재로드 / 중복 정책 (함정)

- 스키마(TBox)는 `load_rdf` 내부에서 PUT(교체)라 재로드해도 누적되지 않음 — 안전.
- 데이터(ABox)는 POST(append)라 **디렉토리 로드를 두 번 돌리면 인스턴스 트리플이 중복**된다.
- 정책 (1차 구현):
  - `--replace` 플래그를 디렉토리 로드에도 노출. `replace=True`면 **각 ontology 스코프의
    data 그래프를 그 스코프 첫 data 파일 로드 시 1회 교체(PUT), 이후 파일은 append**.
    (스코프별 "첫 data 파일에서만 replace, 나머지는 append" 상태를 오케스트레이터가 관리.)
  - `replace=False`(기본)는 순수 append — 재실행 시 중복 책임은 사용자에게.
- 향후(범위 밖): content-hash 기반 파일 단위 스킵으로 증분 재로드. 별도 설계.

## 8. 매니페스트 포맷 (`ontorag.yaml`) — 선택 기능

루트에 있으면 §3-3 기본 매핑을 오버라이드. 1차 구현에서 필수는 아님 — **먼저 기본 매핑으로
구현하고, 매니페스트는 후속 커밋**으로 분리해도 됨.

```yaml
# ontorag.yaml — 디렉토리 로드 매니페스트
ontologies:
  - id: foaf
    schema: [foaf/foaf.ttl]
    data:   [foaf/people.ttl, foaf/orgs.ttl]   # 나열 순서대로 로드
  - id: pokemon
    schema: [pokemon/schema.ttl]
    data:   [pokemon/*.ttl]                     # glob 허용
ignore: ["drafts/**"]
```

검증: 모든 `id`는 `validate_ontology_id` 통과, 참조 파일은 존재해야 함(없으면 fail-fast).

## 9. CLI 변경 (`cli.py`)

`load_auto` 콜백에서 인자가 **디렉토리인지** 분기:

- `ontorag load ./dir/` → `core.batch_loader.load_directory(...)` 호출.
- `ontorag load ./file.ttl` → 기존 `_run_load` 단일 파일 경로 유지(하위호환).
- 새 옵션: `--ontology`(플랫 병합), `--replace`, `--no-recursive`.
- 진행률: 파일 다수이므로 Rich `Progress`에 **전체 파일 카운트 바**(`completed/total`)를 추가.
  `on_file` 콜백에서 advance + 파일별 한 줄 로그.
- 종료 요약: `BatchLoadResult`를 Rich `Table`로 출력 — loaded/skipped/failed 수 + 총 트리플,
  실패 파일은 사유와 함께. `failed > 0`이면 exit code 1.

`load schema` / `load data` 서브커맨드도 디렉토리 인자를 받게 확장 가능(같은 오케스트레이터에
`mode` 고정 전달). 1차 구현 범위에 포함할지는 구현자 판단 — 최소 `load <DIR>` auto는 필수.

## 10. API 라우트 (`api/routes/load.py`) — 범위 밖(이번 차수)

HTTP `/load`는 `UploadFile` 기반이라 "서버 측 디렉토리 스캔"과 의미가 다르다(서버가 클라이언트
디렉토리에 접근 불가). 이번 작업은 **CLI + `core.batch_loader`까지**로 한정. 멀티파일 업로드
엔드포인트는 별도 설계로 분리.

## 11. 테스트 계획 (`tests/core/test_batch_loader.py`)

store는 **fake/in-memory GraphStore**로 모킹(실제 Fuseki/Neo4j 불필요) — `load_rdf` 호출
인자(path, mode, replace, ontology)를 기록하는 스파이.

- `pytest.mark.unit`:
  - 서브디렉토리 → ontology id 매핑 (기본 규칙).
  - 루트 직속 파일 → `ontology=None`.
  - schema-before-data 순서 보장 (호출 순서 assert).
  - `--ontology` 플랫 병합 시 전 파일 동일 스코프.
  - ignore 패턴(`.git` 등) 제외.
  - slug 위반 서브디렉토리 → 로드 전 `ValueError`.
  - 파일 파싱 실패 → `failed` 기록 + 나머지 계속.
  - schema 실패 → 같은 스코프 data `skipped`.
  - `--replace`: 스코프별 첫 data만 replace, 이후 append (스파이 인자로 검증).
  - 빈 디렉토리 / RDF 없는 디렉토리 → `total_files=0` 정상 반환.
- 픽스처: `tmp_path`에 미니 TTL 트리(`foaf/`, `pokemon/`, 루트 파일) 생성.
- 매니페스트 구현 시: 매핑 오버라이드 + 파일 부재 fail-fast 테스트 추가.

## 12. 커밋 분할 (한 커밋 = 한 관심사)

1. `stores/base.py`: `FileLoadOutcome` + `BatchLoadResult` 타입 + 단위 테스트.
2. `core/batch_loader.py`: 수집·매핑·정렬·로드 오케스트레이션 + 테스트(매니페스트 제외).
3. `cli.py`: `load <DIR>` 분기 + Rich progress/summary + 옵션.
4. (선택) 매니페스트(`ontorag.yaml`) 로더 + 테스트.
5. 문서: README/CLAUDE.md의 load 섹션에 디렉토리 사용법 추가.

## 13. 안티패턴 (하지 말 것)

- `GraphStore.load_rdf`에 디렉토리/리스트 인자 추가 금지 — Protocol 오염.
- store 어댑터(Fuseki/Neo4j)에 디렉토리 워킹 로직 넣기 금지.
- 부분 실패 시 전체 롤백/예외 전파 금지 — continue-and-report가 기본(설정 에러만 fail-fast).
- 모듈 300줄 초과 시 분리. `print` 금지 — `logging` 사용(진행률은 CLI 레이어 Rich로).
- `auto` 단일파일 감지 로직을 디렉토리에 그대로 재사용해서 순서 보장을 깨지 말 것(§6).
```
