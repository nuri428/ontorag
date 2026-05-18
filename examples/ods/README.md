# Open Data Structures (ODS) — domain example

A computer-science ontology covering the data structures and algorithms
taught in Pat Morin's *Open Data Structures* (Carleton University, CC BY 2.5,
[opendatastructures.org](https://opendatastructures.org)). Treats each
chapter's data structures as ABox instances of TBox classes that mirror
the book's class hierarchy (Array-based, Linked, Tree, Hash, Heap, Trie,
Sort algorithms).

**Why this domain**

- Third domain after Pure Land (Buddhism, multilingual, fictional) and
  Commerce (schema.org subset). Different vocabulary, different OWL
  feature usage profile.
- Rich `owl:TransitiveProperty` chains: a structure `uses` another
  structure that itself uses another (e.g. `XFastTrie uses BinaryTrie
  uses LinkedList`); a structure `specialises` another (e.g.
  `RedBlackTree specialises BinarySearchTree specialises BinaryTree`).
  These are exactly the patterns the v4–v8 fix set targets.
- `owl:inverseOf` pairs (`implements` ↔ `implementedBy`).
- LLM contamination is *high* — ODS is widely-available open text, so
  gpt-4o/Claude were almost certainly trained on it. We compensate
  with **trap questions about fictional structures** (e.g.
  `ods:AuroraTree`) and tight goldset answers that require *the
  ontology's own claims* rather than the book's wider context.

**Schema choices (deliberately compact)**

| count | what |
|---|---|
| 10 | classes (DataStructure root + 6 categories + Interface / Operation / Complexity / SortAlgorithm) |
| 5 | object properties (implements, supports, uses TRANSITIVE, specialises TRANSITIVE, hasWorstCaseComplexity) |
| 2 | data properties (chapter, bigO notation as string) |

This is small enough to compare like-for-like with Commerce (15 classes,
15 properties) while exercising the full v4–v8 fix surface.

---

## RAGAS 벤치마크 결과 (2026-05, gpt-4o agent + gpt-4o judge)

20문항 영어 goldset (`examples/ods/goldset.jsonl`) — easy 5 / medium 6 / hard 5
(transitive_inference: `ods:uses+`, `ods:specialises+`) / trap 4
(AuroraTree·SplayTree·Ch15·TimSort — 책엔 있으나 이 온톨로지에 없음).

| 메트릭 | LangChain (vector RAG) | ontorag_native | Δ |
|---|---|---|---|
| RAGAS Faithfulness | 0.521 | **0.551** | ontorag +0.030 |
| RAGAS AnswerCorrectness | 0.493 | **0.515** | ontorag +0.022 |
| RAGAS AnswerRelevancy | 0.641 | **0.749** | ontorag +0.108 |
| Hallucination rate (det.) | — | **0.000** | ontorag |
| Citation 제공률 | 0% | **65%** | ontorag |
| Citation coverage | — | **0.247** | ontorag |
| 평균 응답 시간 | 1243 ms | 5425 ms | LangChain ↓ |

### 해석 — ontorag가 모든 RAGAS 메트릭에서 LangChain을 이긴 첫 도메인

ODS는 Pokemon/Techstack과 다음 두 가지가 다릅니다:
1. **두 개의 TransitiveProperty 체인**(`uses`, `specialises`)이 동시에 존재 → hard 질문 5개 모두 graph 추론 필수. Vector RAG는 텍스트 chunk에서 전이 관계를 합성 못함.
2. **`implements`/`implementedBy` inverseOf 쌍** → "어떤 자료구조가 USet을 구현?" 같은 역방향 조회를 OWL 추론으로 처리.

이 두 OWL 기능이 결합하여 **AnswerRelevancy +0.108**의 큰 차이를 만들었습니다. 텍스트 chunk에서 "HeapSort uses BinaryHeap; BinaryHeap uses ArrayStack"을 합성해 "HeapSort transitively uses ArrayStack"으로 잇는 추론을 vector RAG는 못 합니다.

> **시사점**: TransitiveProperty/inverseOf가 많은 도메인일수록 ontorag의 우위가 커집니다. 단순 lookup 도메인은 LangChain의 chunk-quote 전략이 RAGAS judge에 유리.

---

## Disclaimer

**1. Rights / 권리 귀속.** *Open Data Structures* is an open-access
textbook authored by **Pat Morin (Carleton University, Canada)** and
published at [opendatastructures.org](https://opendatastructures.org/)
under the **Creative Commons Attribution 2.5 Canada (CC BY 2.5 CA)**
license. The class taxonomy (`ArrayStack`, `RedBlackTree`,
`SkiplistSSet`, ...), the chapter structure, and the complexity
classifications encoded in this dataset are derived from that book.

**2. Nature of this work / 본 데이터의 성격.** This RDF/OWL ontology
is a **derivative work under CC BY 2.5 attribution terms**: the
underlying ideas (class hierarchy, operations and their complexity
bounds) are sourced from *Open Data Structures*, but the encoding
(`ods:` namespace, `owl:TransitiveProperty` on `uses`/`specialises`,
`owl:inverseOf` on `implements`/`implementedBy`, `rdfs:label` strings
in English) is an original modeling layer. **No prose, figures, code
listings, or other expressive content from the textbook is reproduced
verbatim** — only factual classification.

**3. No affiliation / 비제휴 선언.** This project is **not affiliated
with or endorsed by** Pat Morin, Carleton University, or any publisher
of *Open Data Structures*. The attribution is academic citation, not
institutional endorsement.

**4. Takedown commitment / 즉시 제거 약속.** If the author prefers a
different encoding, removal, or additional attribution, the dataset
will be **revised or removed promptly** upon request. Contact: GitHub
issue on the ontorag repository.

## License

- **Ontology files** (`schema.ttl`, `data.ttl`, `goldset.jsonl`,
  README) — **CC BY 2.5** (matching the upstream license of *Open
  Data Structures*), with attribution to Pat Morin /
  opendatastructures.org. Both `schema.ttl` ontology header and this
  README carry the attribution.
