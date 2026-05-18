# Evaluation Report — goldset

## Summary

- **Goldset**: `examples/commerce/goldset.jsonl`
- **Schema**: `examples/commerce/schema.ttl`
- **Data**: `examples/commerce/data.ttl`
- **Graph triples**: 297
- **Total questions**: 20
- **Failures**: 0 ✓

## Difficulty Distribution

| Difficulty | Total | OK | Errors | Empty rows |
|---|---:|---:|---:|---:|
| easy | 5 | 5 | 0 | 0 |
| medium | 8 | 8 | 0 | 0 |
| hard | 4 | 4 | 0 | 0 |
| trap | 3 | 3 | 0 | 3 |

## Category Breakdown

| Category | Count |
|---|---:|
| single_entity | 5 |
| multi_hop | 4 |
| counting | 3 |
| filter_join | 3 |
| hallucination_trap | 3 |
| reverse_lookup | 1 |
| transitive_inference | 1 |

## Inference Usage

3 of 20 questions exercise OWL/RDFS inference (15%).

## Per-question Detail

| ID | Difficulty | Category | Status | Rows | Inference |
|---|---|---|:---:|---:|:---:|
| Q001 | easy | single_entity | ✓ | 1 |  |
| Q002 | easy | single_entity | ✓ | 1 |  |
| Q003 | easy | single_entity | ✓ | 1 |  |
| Q004 | easy | single_entity | ✓ | 1 |  |
| Q005 | easy | single_entity | ✓ | 1 |  |
| Q006 | medium | filter_join | ✓ | 3 |  |
| Q007 | medium | filter_join | ✓ | 1 |  |
| Q008 | medium | counting | ✓ | 1 |  |
| Q009 | medium | multi_hop | ✓ | 1 |  |
| Q010 | medium | filter_join | ✓ | 1 |  |
| Q011 | medium | multi_hop | ✓ | 2 |  |
| Q012 | medium | reverse_lookup | ✓ | 1 |  |
| Q013 | medium | counting | ✓ | 1 |  |
| Q014 | hard | transitive_inference | ✓ | 2 | ✓ |
| Q015 | hard | multi_hop | ✓ | 1 | ✓ |
| Q016 | hard | multi_hop | ✓ | 4 |  |
| Q017 | hard | counting | ✓ | 1 | ✓ |
| Q018 | trap | hallucination_trap | ✓ | 0 |  |
| Q019 | trap | hallucination_trap | ✓ | 0 |  |
| Q020 | trap | hallucination_trap | ✓ | 0 |  |
