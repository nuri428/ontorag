# Pokemon — domain example

ontorag의 첫 시연용 도메인. 한국어 라벨을 가진 1세대 포켓몬(Gen-1) 13마리, 트레이너 3명, 기술 13개, 타입 18개를 RDF/OWL로 표현합니다.

## 왜 이 도메인인가

| 축 | Pokemon | 다른 도메인 |
|---|---|---|
| LLM contamination | **매우 높음 (100%)** — Gen-1 포켓몬은 어떤 frontier LLM이든 사전학습됨 | Pure Land(낮음), ODS(높음), Techstack(매우 높음) |
| OWL TransitiveProperty | `pk:evolvesFrom` 단일 체인 (3종) | Techstack의 `dependsOn`과 유사 |
| Class subclass | `LegendaryPokemon ⊑ Pokemon` | — |
| 라벨 다국어성 | 한국어 라벨만(영어 URI) | Pure Land와 다름 |
| 인스턴스 수 | 13 Pokemon + 18 Type + 13 Move + 3 Trainer = ~50 | 가장 작음 |

contamination이 가장 높은 도메인 → **goldset의 trap 비중을 20%로 높여** LLM 사전지식 누출(hallucination)을 측정합니다.

## TBox 요약

- **Classes**: `Pokemon`, `LegendaryPokemon ⊑ Pokemon`, `Type`, `Move`, `Trainer`, `Region`
- **Object properties**: `hasType`, `hasMove`, `moveType`, `evolvesFrom` (**TransitiveProperty**), `trainedBy`, `fromRegion`, `strongAgainst`, `weakAgainst`
- **Data properties**: `nationalDex`, `hp`, `attack`, `defense`, `spAttack`, `spDefense`, `speed`, `evolutionLevel`, `power`, `accuracy`, `category`, `hometown`

## ABox 진화 체인 (TransitiveProperty 시연용)

```
Bulbasaur (이상해씨)  →  Ivysaur (이상해풀)  →  Venusaur (이상해꽃)
Charmander (파이리)   →  Charmeleon (리자드)  →  Charizard (리자몽)
Squirtle (꼬부기)     →  Wartortle (어니부기) →  Blastoise (거북왕)
```
`evolvesFrom`이 TransitiveProperty이므로 Fuseki가 `Venusaur evolvesFrom Bulbasaur`를 추론합니다.

## Goldset (20문항)

| difficulty | count | 카테고리 |
|---|---|---|
| easy | 5 | 단순 lookup (dex 번호, HP, 타입) |
| medium | 6 | filter, subclass, count |
| hard | 5 | **transitive_inference** (`evolvesFrom+`) |
| trap | 4 | **Eevee / Mew / Pichu / Mega-Charizard-X** — 실제 포켓몬이지만 이 온톨로지에 없음 → "정보 없음"이 정답 |

## RAGAS 벤치마크 결과 (2026-05, gpt-4o agent + gpt-4o judge)

각 baseline은 동일한 schema.ttl + data.ttl에 대해 20문항 한국어 질문을 받습니다.

| 메트릭 | LangChain (vector RAG) | ontorag_native | Δ |
|---|---|---|---|
| RAGAS Faithfulness | **0.677** | 0.423 | LangChain +0.254 |
| RAGAS AnswerCorrectness | 0.448 | **0.466** | ontorag +0.018 |
| RAGAS AnswerRelevancy | 0.342 | **0.349** | ontorag +0.007 |
| Hallucination rate (det.) | — (n/a) | **0.000** | ontorag |
| Citation 제공률 | 0% | **65%** | ontorag |
| 평균 도구 호출 | 0 | 1.15 | — |
| 평균 응답 시간 | 1481 ms | 2636 ms | LangChain ↓ |

### 해석

- **Faithfulness 차이**: LangChain은 텍스트 chunk를 그대로 인용 → judge가 "원문 일치"로 점수를 높게 줌. ontorag_native는 SPARQL 결과를 자연어로 재구성하므로 judge가 "원문 텍스트와 표현 다름"을 페널티함 (전형적인 LLM-judge style bias).
- **Correctness/Relevancy는 ontorag 미세 우위**: 같은 정답을 더 정확하게 맞춤(0.018), 질문에 더 잘 정렬됨(0.007).
- **Citation 65%, Hallucination 0%**: ontorag만 자기 답변에 RDF 트리플을 근거로 첨부 → 결정론적 검증 가능. LangChain은 citation이 0%, hallucination 측정 자체 불가.

### Pokemon 도메인 결론

Pokemon은 LLM 사전지식이 100% 있는 도메인이라 **LangChain의 텍스트 매칭이 RAGAS judge에는 매력적**으로 보입니다. 하지만:
- 4개 trap 질문(Eevee/Mew/Pichu/Mega) 에서 LangChain이 hallucinate할 위험 → 검증 필요
- ontorag_native는 trap에서도 graph에 없는 사실은 절대 만들지 않음 (Hallucination=0%)

## 파일

| 파일 | 설명 |
|---|---|
| `schema.ttl` | TBox — 6 클래스, 8 object properties (1 transitive), 12 data properties |
| `data.ttl` | ABox — 13 Pokemon, 3 Trainer, 13 Move, 18 Type, 1 Region |
| `goldset.jsonl` | 20문항 (easy 5 / medium 6 / hard 5 / trap 4) |
| `bench_results/langchain_gpt4o.json` | LangChain RetrievalQA 벤치 결과 |
| `bench_results/ontorag_native_gpt4o.json` | ontorag agent 벤치 결과 |

## 재현 방법

```bash
# 1. Fuseki에 로드
docker compose up -d
uv run ontorag load schema examples/pokemon/schema.ttl
uv run ontorag load data examples/pokemon/data.ttl

# 2. 벤치마크 실행
export RAGAS_JUDGE_MODEL=gpt-4o
export LLM_MODEL=gpt-4o
uv run ontorag eval bench examples/pokemon/goldset.jsonl \
  --baseline ontorag_native \
  --schema examples/pokemon/schema.ttl \
  --data examples/pokemon/data.ttl \
  --lang ko --with-ragas \
  --output examples/pokemon/bench_results/ontorag_native_gpt4o.json
```
