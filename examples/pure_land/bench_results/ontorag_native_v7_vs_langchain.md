# Comparison — ontorag_v7 vs langchain

## Summary

- **ontorag_v7**: 50 questions, ? failures
- **langchain**: 50 questions, ? failures
- **Aligned by ID**: 50

## Per-difficulty rollup

| Difficulty | ontorag_v7 OK | ontorag_v7 err | ontorag_v7 empty | langchain OK | langchain err | langchain empty |
|---|---:|---:|---:|---:|---:|---:|
| easy | 15 | 0 | 5 | 15 | 0 | 15 |
| medium | 20 | 0 | 11 | 20 | 0 | 20 |
| hard | 10 | 0 | 7 | 10 | 0 | 10 |
| trap | 5 | 0 | 2 | 5 | 0 | 5 |

## Per-question side-by-side

| ID | Difficulty | Category | ontorag_v7 rows | langchain rows | Δ |
|---|---|---|---:|---:|:---:|
| Q001 | easy | single_entity | 20 | 0 | ▲ |
| Q002 | easy | single_entity | 15 | 0 | ▲ |
| Q003 | easy | single_entity | 0 | 0 | = |
| Q004 | medium | filter_join | 0 | 0 | = |
| Q005 | medium | multi_hop | 20 | 0 | ▲ |
| Q006 | medium | filter_join | 20 | 0 | ▲ |
| Q007 | medium | reverse_lookup | 10 | 0 | ▲ |
| Q008 | hard | transitive_inference | 20 | 0 | ▲ |
| Q009 | hard | counting | 0 | 0 | = |
| Q010 | trap | hallucination_trap | 20 | 0 | ▲ |
| Q011 | easy | single_entity | 20 | 0 | ▲ |
| Q012 | easy | single_entity | 0 | 0 | = |
| Q013 | easy | single_entity | 0 | 0 | = |
| Q014 | easy | single_entity | 15 | 0 | ▲ |
| Q015 | easy | single_entity | 10 | 0 | ▲ |
| Q016 | easy | single_entity | 20 | 0 | ▲ |
| Q017 | easy | single_entity | 15 | 0 | ▲ |
| Q018 | easy | single_entity | 10 | 0 | ▲ |
| Q019 | easy | single_entity | 15 | 0 | ▲ |
| Q020 | easy | single_entity | 0 | 0 | = |
| Q021 | easy | single_entity | 0 | 0 | = |
| Q022 | easy | counting | 20 | 0 | ▲ |
| Q023 | medium | filter_join | 0 | 0 | = |
| Q024 | medium | filter_join | 0 | 0 | = |
| Q025 | medium | filter_join | 0 | 0 | = |
| Q026 | medium | reverse_lookup | 10 | 0 | ▲ |
| Q027 | medium | counting | 0 | 0 | = |
| Q028 | medium | filter_join | 20 | 0 | ▲ |
| Q029 | medium | reverse_lookup | 0 | 0 | = |
| Q030 | medium | filter_join | 0 | 0 | = |
| Q031 | medium | multi_hop | 20 | 0 | ▲ |
| Q032 | medium | filter_join | 20 | 0 | ▲ |
| Q033 | medium | filter_join | 0 | 0 | = |
| Q034 | medium | reverse_lookup | 0 | 0 | = |
| Q035 | medium | counting | 0 | 0 | = |
| Q036 | medium | filter_join | 10 | 0 | ▲ |
| Q037 | medium | filter_join | 20 | 0 | ▲ |
| Q038 | medium | multi_hop | 0 | 0 | = |
| Q039 | hard | transitive_inference | 10 | 0 | ▲ |
| Q040 | hard | transitive_inference | 10 | 0 | ▲ |
| Q041 | hard | counting | 0 | 0 | = |
| Q042 | hard | multi_hop | 0 | 0 | = |
| Q043 | hard | counting | 0 | 0 | = |
| Q044 | hard | multi_hop | 0 | 0 | = |
| Q045 | hard | counting | 0 | 0 | = |
| Q046 | hard | counting | 0 | 0 | = |
| Q047 | trap | hallucination_trap | 0 | 0 | = |
| Q048 | trap | hallucination_trap | 20 | 0 | ▲ |
| Q049 | trap | hallucination_trap | 0 | 0 | = |
| Q050 | trap | hallucination_trap | 15 | 0 | ▲ |
