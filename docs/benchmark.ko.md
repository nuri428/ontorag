# 벤치마크

전체 v1.0 벤치마크 문서는 저장소의
[`docs/BENCHMARK_v1.md`](https://github.com/nuri428/ontorag/blob/main/docs/BENCHMARK_v1.md)에
있으며, 본 페이지에 그대로 포함됩니다. **키 불필요, 결정론적** 측정 두
가지가 v1.0 주장을 뒷받침합니다:

1. **Goldset 품질** — 모든 벤치마크 질문의 `gold_sparql`이 자신의 스키마
   + 데이터에 대해 깨끗하게 실행됨 (rdflib, 백엔드 무관). 5개 도메인 /
   130개 질문에서 실패 0건.
2. **백엔드 parity** — 동일한 프로토콜 툴이 Fuseki / Neo4j / FalkorDB에서
   *동일한* 결과 반환. 7/7 메트릭 일치 (`full_parity = True`) —
   ontorag의 핵심 차별점이 이제 단순 주장이 아닌 측정으로 뒷받침됨.

둘 다 클린 체크아웃에서 재현 가능 — 명령은 페이지 하단에 있습니다.

!!! info "추론 레이어 goldset (v1.1)"
    확률 / 인과 레이어에도 이제 병행 goldset이 있습니다 —
    `examples/smoking/reasoning_goldset.jsonl` (6개 손검증
    posterior / do / counterfactual / identify 체크). 어느 백엔드에서든
    저장된 BN + DAG에 대해 `ontorag eval reasoning <goldset>`으로 실행.
    6개 모두 통과.

!!! note "언어 안내"
    원본 BENCHMARK 문서는 영문입니다. 본 한국어 페이지는 동일한 영문
    본문을 그대로 보여줍니다 — 한국어 번역 트랙은 추후 점진적으로 보강할
    예정입니다.

---

--8<-- "docs/BENCHMARK_v1.md"
