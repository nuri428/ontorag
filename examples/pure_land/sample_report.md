# Evaluation Report — goldset

## Summary

- **Goldset**: `examples/pure_land/goldset.jsonl`
- **Schema**: `examples/pure_land/schema.ttl`
- **Data**: `examples/pure_land/data.ttl`
- **Graph triples**: 948
- **Total questions**: 50
- **Failures**: 0 ✓

## Difficulty Distribution

| Difficulty | Total | OK | Errors | Empty rows |
|---|---:|---:|---:|---:|
| easy | 15 | 15 | 0 | 0 |
| medium | 20 | 20 | 0 | 0 |
| hard | 10 | 10 | 0 | 1 |
| trap | 5 | 5 | 0 | 2 |

## Category Breakdown

| Category | Count |
|---|---:|
| single_entity | 14 |
| filter_join | 11 |
| counting | 8 |
| hallucination_trap | 5 |
| multi_hop | 5 |
| reverse_lookup | 4 |
| transitive_inference | 3 |

## Inference Usage

3 of 50 questions exercise OWL/RDFS inference (6%).

## Per-question Detail

| ID | Difficulty | Category | Status | Rows | Inference |
|---|---|---|:---:|---:|:---:|
| Q001 | easy | single_entity | ✓ | 1 |  |
| Q002 | easy | single_entity | ✓ | 1 |  |
| Q003 | easy | single_entity | ✓ | 1 |  |
| Q004 | medium | filter_join | ✓ | 2 |  |
| Q005 | medium | multi_hop | ✓ | 5 |  |
| Q006 | medium | filter_join | ✓ | 4 |  |
| Q007 | medium | reverse_lookup | ✓ | 1 |  |
| Q008 | hard | transitive_inference | ✓ | 2 | ✓ |
| Q009 | hard | counting | ✓ | 1 |  |
| Q010 | trap | hallucination_trap | ✓ | 1 |  |
| Q011 | easy | single_entity | ✓ | 1 |  |
| Q012 | easy | single_entity | ✓ | 1 |  |
| Q013 | easy | single_entity | ✓ | 1 |  |
| Q014 | easy | single_entity | ✓ | 1 |  |
| Q015 | easy | single_entity | ✓ | 1 |  |
| Q016 | easy | single_entity | ✓ | 1 |  |
| Q017 | easy | single_entity | ✓ | 1 |  |
| Q018 | easy | single_entity | ✓ | 1 |  |
| Q019 | easy | single_entity | ✓ | 1 |  |
| Q020 | easy | single_entity | ✓ | 1 |  |
| Q021 | easy | single_entity | ✓ | 1 |  |
| Q022 | easy | counting | ✓ | 1 |  |
| Q023 | medium | filter_join | ✓ | 5 |  |
| Q024 | medium | filter_join | ✓ | 3 |  |
| Q025 | medium | filter_join | ✓ | 4 |  |
| Q026 | medium | reverse_lookup | ✓ | 1 |  |
| Q027 | medium | counting | ✓ | 1 |  |
| Q028 | medium | filter_join | ✓ | 7 |  |
| Q029 | medium | reverse_lookup | ✓ | 1 |  |
| Q030 | medium | filter_join | ✓ | 5 |  |
| Q031 | medium | multi_hop | ✓ | 1 |  |
| Q032 | medium | filter_join | ✓ | 4 |  |
| Q033 | medium | filter_join | ✓ | 1 |  |
| Q034 | medium | reverse_lookup | ✓ | 3 |  |
| Q035 | medium | counting | ✓ | 1 |  |
| Q036 | medium | filter_join | ✓ | 1 |  |
| Q037 | medium | filter_join | ✓ | 4 |  |
| Q038 | medium | multi_hop | ✓ | 2 |  |
| Q039 | hard | transitive_inference | ✓ | 2 | ✓ |
| Q040 | hard | transitive_inference | ✓ | 2 | ✓ |
| Q041 | hard | counting | ✓ | 1 |  |
| Q042 | hard | multi_hop | ✓ | 2 |  |
| Q043 | hard | counting | ✓ | 0 |  |
| Q044 | hard | multi_hop | ✓ | 1 |  |
| Q045 | hard | counting | ✓ | 1 |  |
| Q046 | hard | counting | ✓ | 1 |  |
| Q047 | trap | hallucination_trap | ✓ | 1 |  |
| Q048 | trap | hallucination_trap | ✓ | 0 |  |
| Q049 | trap | hallucination_trap | ✓ | 0 |  |
| Q050 | trap | hallucination_trap | ✓ | 1 |  |
