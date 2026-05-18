# Comparison — ontorag vs vector_rag

## Summary

- **ontorag**: 20 questions, ? failures
- **vector_rag**: 20 questions, ? failures
- **Aligned by ID**: 20

## Per-difficulty rollup

| Difficulty | ontorag OK | ontorag err | ontorag empty | vector_rag OK | vector_rag err | vector_rag empty |
|---|---:|---:|---:|---:|---:|---:|
| easy | 5 | 0 | 3 | 5 | 0 | 5 |
| medium | 8 | 0 | 2 | 8 | 0 | 8 |
| hard | 4 | 0 | 3 | 4 | 0 | 4 |
| trap | 3 | 0 | 3 | 3 | 0 | 3 |

## Per-question side-by-side

| ID | Difficulty | Category | ontorag rows | vector_rag rows | Δ |
|---|---|---|---:|---:|:---:|
| Q001 | easy | single_entity | 4 | 0 | ▲ |
| Q002 | easy | single_entity | 0 | 0 | = |
| Q003 | easy | single_entity | 0 | 0 | = |
| Q004 | easy | single_entity | 4 | 0 | ▲ |
| Q005 | easy | single_entity | 0 | 0 | = |
| Q006 | medium | filter_join | 18 | 0 | ▲ |
| Q007 | medium | filter_join | 6 | 0 | ▲ |
| Q008 | medium | counting | 0 | 0 | = |
| Q009 | medium | multi_hop | 9 | 0 | ▲ |
| Q010 | medium | filter_join | 5 | 0 | ▲ |
| Q011 | medium | multi_hop | 8 | 0 | ▲ |
| Q012 | medium | reverse_lookup | 4 | 0 | ▲ |
| Q013 | medium | counting | 0 | 0 | = |
| Q014 | hard | transitive_inference | 15 | 0 | ▲ |
| Q015 | hard | multi_hop | 0 | 0 | = |
| Q016 | hard | multi_hop | 0 | 0 | = |
| Q017 | hard | counting | 0 | 0 | = |
| Q018 | trap | hallucination_trap | 0 | 0 | = |
| Q019 | trap | hallucination_trap | 0 | 0 | = |
| Q020 | trap | hallucination_trap | 0 | 0 | = |
