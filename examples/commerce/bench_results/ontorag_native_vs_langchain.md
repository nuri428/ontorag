# Comparison — ontorag_native vs langchain

## Summary

- **ontorag_native**: 20 questions, ? failures
- **langchain**: 20 questions, ? failures
- **Aligned by ID**: 20

## Per-difficulty rollup

| Difficulty | ontorag_native OK | ontorag_native err | ontorag_native empty | langchain OK | langchain err | langchain empty |
|---|---:|---:|---:|---:|---:|---:|
| easy | 5 | 0 | 1 | 5 | 0 | 5 |
| medium | 8 | 0 | 4 | 8 | 0 | 8 |
| hard | 4 | 0 | 2 | 4 | 0 | 4 |
| trap | 3 | 0 | 2 | 3 | 0 | 3 |

## Per-question side-by-side

| ID | Difficulty | Category | ontorag_native rows | langchain rows | Δ |
|---|---|---|---:|---:|:---:|
| Q001 | easy | single_entity | 20 | 0 | ▲ |
| Q002 | easy | single_entity | 0 | 0 | = |
| Q003 | easy | single_entity | 9 | 0 | ▲ |
| Q004 | easy | single_entity | 13 | 0 | ▲ |
| Q005 | easy | single_entity | 20 | 0 | ▲ |
| Q006 | medium | filter_join | 20 | 0 | ▲ |
| Q007 | medium | filter_join | 0 | 0 | = |
| Q008 | medium | counting | 20 | 0 | ▲ |
| Q009 | medium | multi_hop | 0 | 0 | = |
| Q010 | medium | filter_join | 0 | 0 | = |
| Q011 | medium | multi_hop | 20 | 0 | ▲ |
| Q012 | medium | reverse_lookup | 0 | 0 | = |
| Q013 | medium | counting | 20 | 0 | ▲ |
| Q014 | hard | transitive_inference | 20 | 0 | ▲ |
| Q015 | hard | multi_hop | 20 | 0 | ▲ |
| Q016 | hard | multi_hop | 0 | 0 | = |
| Q017 | hard | counting | 0 | 0 | = |
| Q018 | trap | hallucination_trap | 5 | 0 | ▲ |
| Q019 | trap | hallucination_trap | 0 | 0 | = |
| Q020 | trap | hallucination_trap | 0 | 0 | = |
