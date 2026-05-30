# 온톨로지 학습 (LLMs4OL)

v0.3에서 **LLMs4OL 파이프라인**이 추가되었습니다 — LLM이 평문 텍스트를
읽고 살아 있는 온톨로지를 확장할 RDF 트리플을 제안합니다. 수동 작성
불필요. 모든 제안은 ABox에 들어가기 전 현재 TBox에 대해 검증됩니다.

## 세 가지 정전 작업 (EKAW 2023)

| 작업 | 입력 | 출력 | 새 트리플 유형 |
|---|---|---|---|
| **A — Term Typing** | 텍스트 멘션 + TBox 클래스 | 랭킹 `(class_uri, confidence)` | `rdf:type` |
| **B — Taxonomy Discovery** | 용어 쌍 + 기존 계층 | `is_subclass: bool + confidence` | `rdfs:subClassOf` |
| **C — Relation Extraction** | 텍스트 + 엔티티 쌍 | 예측 `predicate_uri + confidence` | `owl:ObjectProperty` 단언 |

파이프라인은 순서대로 실행: `text → A → B → C → 제안된 트리플 → 선택적 auto-load`.

## CLI

```bash
# Task A — 텍스트 멘션을 TBox 클래스로 매핑
ontorag learn type-term "Pikachu" --context "evolved Pokémon"
ontorag learn type-term "React"

# Task B — 코퍼스에서 subClassOf 제안
ontorag learn taxonomy --text corpus.txt

# Task C — 속성 트리플 제안
ontorag learn extract --text corpus.txt

# A+B+C 일괄 실행 + auto-load
ontorag learn populate examples/techstack/corpus.txt --auto-load
```

## 구조화된 ABox 채우기

`populate-structured`는 **CSV / JSON / JSONL**을 읽어 각 행을 RDF로
변환합니다. LLM이 컬럼을 TBox 속성 URI에 *한 번* 매핑하고, 매핑은 사이드카
`<file>.mapping.json`에 캐시되어 — 이후 실행은 LLM 호출 0회.

```bash
# 첫 실행 — LLM이 컬럼 매핑, pokemon.csv.mapping.json 저장
ontorag learn populate-structured pokemon.csv \
    --class-uri pk:Pokemon --id-column name

# 두 번째 실행 — 매핑 재사용
ontorag learn populate-structured pokemon.csv --yes

# JSON / JSONL — 중첩 키는 평탄화: {"stats":{"hp":35}} → "stats.hp"
ontorag learn populate-structured pokemon.jsonl --class-uri pk:Pokemon
```

제안된 트리플이 ABox에 들어가기 전 SHACL 검증 게이트가 실행됩니다 (v0.4
이후).

## MCP 툴

L1 툴 2개가 에이전트에 노출 — LLM은 *대화 중에* 자신의 온톨로지를 확장할
수 있습니다:

| 툴 | 역할 |
|---|---|
| `type_term(term, context?)` | Task A — 텍스트 멘션을 TBox 클래스로 매핑. |
| `extract_triples(text, entities?)` | Task C — 텍스트에서 RDF 트리플 제안, 스키마로 검증. |

## 설계 제약

- 모든 메서드는 호출 시점에 **현재 TBox**(`SchemaResult`)를 받음 — 스키마
  캐시 stale 문제 없음.
- 출력의 `predicate_uri`와 `class_uri`는 **현재 TBox에 존재**해야 함
  (반환 전 검증). 파이프라인은 URI를 발명하지 않음.
- 신뢰도 임계값(`min_confidence`, 기본 0.7)이 약한 제안을 거름.
- `auto_load=True`는 검증 *후* `store.load_rdf(...)`를 `mode="data"`로
  호출.

## 안 하는 것 (스코프 밖)

- **새 TBox 클래스를 자동 제안하지 않음.** 파이프라인은 *기존* 스키마를
  쓰는 ABox 트리플만 생성. TBox 진화는 의도적으로 사람의 검토 단계.
- **DL 임베딩 없음.** LLM 프롬프팅만, transformer fine-tune 없음.
- **완전 자율 스키마 진화 없음.** v0.3은 프롬프트 기반 유지 — 학습
  레이어(GNN / R-GCN)는 v1.1+로 연기.

## 코드 위치

- `src/ontorag/learn/term_typing.py` — Task A
- `src/ontorag/learn/taxonomy.py` — Task B
- `src/ontorag/learn/relation.py` — Task C
- `src/ontorag/learn/pipeline.py` — A+B+C 오케스트레이션
- `src/ontorag/api/routes/tools/learning.py` — MCP 라우트

## 더 읽기

- README §Ontology learning from text — 스크린샷이 포함된 서사형 예제.
- 설계 — `docs/design/directory-loader.md` (populate 후 배치 로드).
