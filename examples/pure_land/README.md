# Pure Land Ontology — 서방정토(西方淨土)

> **Status**: v0.0.1 — Scaffolding. TBox/ABox/Goldset are placeholders.
> **Purpose**: Benchmark dataset for ontology-aware Retrieval-Augmented Generation (Phase B of ontorag post-v0.3.2 roadmap).

---

## About / 개요

This example models the cosmology of **Sukhāvatī** (Skt. *Sukhāvatī*; Ch. *極樂*; Kor. *극락*) — the "Western Pure Land" of Buddha Amitābha as described in the *Sukhāvatīvyūha* literature.

이 예제는 『無量壽經』(Larger *Sukhāvatīvyūha Sūtra*)에 기술된 아미타불(阿彌陀佛)의 서방정토(西方淨土) 우주관을 RDF/OWL 온톨로지로 모델링합니다.

### Why this domain?

Ontology-aware RAG should differentiate itself from vector RAG on three axes — and this domain stresses all three:

1. **Multilingual labels** — 한국어 · English · 漢文 share the same URI; vector embeddings do not.
2. **OWL inference** — `owl:TransitiveProperty` (a celestial bird located-in a jeweled tree located-in a pond located-in Sukhāvatī), `rdfs:subClassOf` (Buddha ⊑ Being), `owl:inverseOf` (teaches/taught-by, vows-to-save/saved-by).
3. **Hallucination traps** — LLMs *think* they know Pure Land Buddhism. Many do not know the exact vow count (48, not "around 40"), the precise number of jeweled tower stories (7), or that 釋迦牟尼佛 has no 本願 in this ontology (those belong to 法藏比丘 → 阿彌陀佛). This domain weaponises that asymmetry to demonstrate where ontology RAG beats pure LLM recall.

---

## Doctrinal Disclaimer / 교리적 면책 조항

> *This section addresses **doctrinal** matters specific to religious
> text modeling. For the **standard project disclaimer** (rights,
> takedown, no-affiliation), see the [Disclaimer](#disclaimer) section
> below.*

This is a **knowledge-graph modeling exercise** and a **benchmark dataset** for ontology-aware retrieval. It is **not** a doctrinal authority and does not represent the position of any Buddhist sangha or school.

Modeling decisions (class hierarchy, property names, cardinality constraints) reflect **ontology engineering pragmatics**, not religious teaching. Where the source texts admit multiple interpretations, this ontology selects one for the sake of consistency — that selection is editorial, not authoritative.

본 예제는 **온톨로지 모델링 실습 및 벤치마크 데이터셋**입니다. 어떠한 불교 종단·종파의 교리적 입장도 대표하지 않으며, 모델링 결정(클래스 위계, 속성 명명, 카디널리티)은 **온톨로지 공학적 편의**에 따른 것입니다.

Corrections from Buddhist scholars and practitioners are warmly welcomed via GitHub issues. 불교학자·수행자분들의 정정 제안을 GitHub issues로 환영합니다.

---

## Sources / 사료

The ontology is grounded in three canonical texts of the Pure Land tradition (淨土三部經), with the *Larger Sukhāvatīvyūha* as the primary source:

| Sūtra | Taishō | Translator | Role |
|---|---|---|---|
| **無量壽經** (Larger *Sukhāvatīvyūha*) | T0360 | 康僧鎧 | **Primary** — 48 vows, Sukhāvatī description |
| 阿彌陀經 (Smaller *Sukhāvatīvyūha*) | T0366 | 鳩摩羅什 | Supplementary — concise paradise imagery |
| 觀無量壽經 (*Amitāyurdhyāna Sūtra*) | T0365 | 畺良耶舍 | Supplementary — 16 contemplations |

### English translations referenced
- 84000 Toh 115 — *The Display of the Pure Land of Sukhāvatī* — https://84000.co/translation/toh115 (CC BY-NC-ND 3.0, cited as `dcterms:source`, not copied)

### Upper-level vocabulary
- **BDRC Buddhist Digital Ontology** — `http://purl.bdrc.io/ontology/core/` (CC0, imported as superclasses where applicable)
- **schema.org**, **FOAF**, **dcterms**, **PROV-O** — standard reuse per Linked Data 5-star

---

## Domain Scope / 도메인 범위

Target sizes (after B-1.4 ABox seeding):

| Layer | Count | Examples |
|---|---|---|
| Class hierarchy (TBox) | ~30 classes | Buddha, Bodhisattva, Śrāvaka, Sentient, Realm, JeweledStructure, Vow, Contemplation |
| Object/data properties | ~25 properties | `hasVow`, `locatedIn` (transitive), `teaches`/`taughtBy` (inverse), `colorOf` |
| Individuals (ABox) | ~150 entities | Amitābha, 48 Vows, 7 Towers, 8 Waters, 4 Lotus Colors, named bodhisattvas, śrāvakas, celestial birds |
| Triples (incl. multilingual labels) | ~800 | — |

This stays well under the v0.3.2 token-efficiency thresholds and exercises every L1 MCP tool.

---

## Multilingual Labels / 다국어 라벨

Every class and named individual carries labels in three languages:

```turtle
pl:Amitabha rdfs:label "Amitābha"@en ,
                       "阿彌陀佛"@zh-Hant ,
                       "아미타불"@ko .
```

Where canonical Sanskrit forms differ from English transliteration, both are kept. Where multiple Chinese renderings exist (e.g. 阿彌陀佛 vs 無量壽佛 vs 無量光佛), `skos:altLabel` is used.

---

## Files / 파일

```
examples/pure_land/
├── README.md           # this file
├── schema.ttl          # TBox — class hierarchy, properties (B-1.2)
├── data.ttl            # ABox — individuals (B-1.4)
├── goldset.jsonl       # benchmark questions (B-1.5)
└── domain_design.md    # inference pattern mapping (B-1.3)
```

---

## Disclaimer

**1. Rights / 권리 귀속.** The canonical sūtras themselves — *無量壽經*
(Larger Sukhāvatīvyūha, T0360), *阿彌陀經* (Smaller Sukhāvatīvyūha,
T0366), *觀無量壽經* (Amitāyurdhyāna Sūtra, T0365) — are **public
domain**, having been composed roughly 1500–2000 years ago. Modern
English translations (e.g. 84000 Toh 115) are under their own
licenses (CC BY-NC-ND 3.0 for 84000); this ontology **does not
reproduce translated prose** but only cites translation URLs via
`dcterms:source`.

**2. Nature of this work / 본 데이터의 성격.** This is an **original
ontology engineering work** modeling the cosmology and narrative
structure described in the canonical texts. The facts encoded
(48 vows, 7-storey jeweled towers, named bodhisattvas) are
**not copyrightable** as such — only the specific expression of those
facts in prose form is. This ontology encodes the facts as RDF
triples in its own original vocabulary (`pl:` namespace).

**3. No affiliation / 비제휴 선언.** This project is **not affiliated
with, endorsed by, or sponsored by** any Buddhist sangha, school,
publisher, or the 84000 translation project. Attribution to source
translations is academic citation, not institutional endorsement. See
also the [Doctrinal Disclaimer](#doctrinal-disclaimer--교리적-면책-조항)
above for matters of religious authority.

**4. Takedown commitment / 즉시 제거 약속.** If any translator or
publisher prefers their work not be referenced (even by URL only)
from this dataset, references will be **removed promptly** upon
request. Contact: GitHub issue on the ontorag repository.

## License / 라이선스

- **Ontology files** (`schema.ttl`, `data.ttl`, `goldset.jsonl`) — same MIT license as ontorag.
- **Source attribution** — Taishō references and 84000 URLs are cited via `dcterms:source`. Facts derived from canonical texts are not copyrightable; this ontology does not copy translated prose.
- **Doctrinal disclaimer** — see *Doctrinal Disclaimer / 교리적 면책 조항* near the top of this README.

---

## Benchmark Role / 벤치마크 역할

This domain is the **Fictional+Religious** half of ontorag's RAGAS+goldset evaluation harness (Phase B of post-v0.3.2 roadmap). The **Real** half is a FIBO subset (to be scaffolded separately).

Together they answer the Karpathy gate question:
> *"Pokemon이 아닌 도메인에서 LLMs4OL이 정말로 동작하는가?"*
> *"Does ontology-aware RAG measurably beat vector RAG on a domain LLMs partially know?"*

Comparison baseline: **LangChain** (`RetrievalQA + Chroma + OpenAI text-embedding-3-small + gpt-4o-mini`).

---

## RAGAS 벤치마크 결과 (2026-05, gpt-4o agent + gpt-4o judge)

50문항 한국어 goldset (`examples/pure_land/goldset.jsonl`). 이 도메인은 4개 측정 도메인 중 **contamination이 가장 낮고**, multilingual 라벨(`@en/@zh-Hant/@ko`)과 큰 ABox(717 triples)를 가집니다.

| 메트릭 | LangChain (vector RAG) | ontorag_native | Δ |
|---|---|---|---|
| RAGAS Faithfulness | 0.345 | **0.422** | ontorag +0.077 |
| RAGAS AnswerCorrectness | 0.260 | **0.381** | ontorag +0.121 |
| RAGAS AnswerRelevancy | 0.180 | **0.357** | ontorag +0.177 |
| Hallucination rate (det.) | — | **0.000** | ontorag |
| Citation 제공률 | 0% | **66%** | ontorag |
| Citation coverage | — | 0.009 | ontorag |
| 평균 응답 시간 | 1573 ms | 16435 ms | LangChain ↓ |

### 해석 — ontorag의 RAGAS 우위가 가장 큰 도메인

Pure Land는 두 가지 측면에서 vector RAG가 가장 약합니다:

1. **다국어 라벨이 같은 URI를 공유**: `阿彌陀佛` / `Amitābha` / `아미타불`이 모두 `pl:Amitabha`인데 vector embedding은 이를 분리된 chunk로 처리. SPARQL은 URI만 매칭하므로 언어와 무관.
2. **거짓 정답 함정**: LLM이 사전학습에서 본 적 있는 "약 40개의 본원" 같은 부정확한 사실이 chunk에 그대로 인용되며 점수를 깎음(법장비구의 vow=48이 정답). ontorag는 graph에 없는 사실은 생성하지 않음.

`AnswerRelevancy +0.177` (98% relative improvement)이 보여주는 것: 한국어 질문에 한국어/한자/영문 라벨을 자유롭게 결합한 답변을 만들 때 그래프 추론이 결정적이라는 점.

### Pure Land 도메인 결론

**Karpathy 게이트 통과**: "*Pokemon이 아닌 도메인에서 ontorag가 정말로 동작하는가?*" — 모든 RAGAS 메트릭에서 ontorag가 LangChain을 이긴 두 도메인(ODS, Pure Land) 중 하나이며, 가장 큰 격차를 보임. 다국어 + 큰 ABox + 낮은 contamination이 ontorag에 가장 유리한 조합.
