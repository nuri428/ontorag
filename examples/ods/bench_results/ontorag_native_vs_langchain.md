# Comparison — ontorag_native vs langchain

## Summary

- **ontorag_native**: 20 questions, ? failures
- **langchain**: 20 questions, ? failures
- **Aligned by ID**: 20

## Per-difficulty rollup

| Difficulty | ontorag_native OK | ontorag_native err | ontorag_native empty | langchain OK | langchain err | langchain empty |
|---|---:|---:|---:|---:|---:|---:|
| easy | 5 | 0 | 1 | 5 | 0 | 5 |
| medium | 6 | 0 | 4 | 6 | 0 | 6 |
| hard | 5 | 0 | 1 | 5 | 0 | 5 |
| trap | 4 | 0 | 4 | 4 | 0 | 4 |

## Per-question side-by-side

| ID | Difficulty | Category | ontorag_native rows | langchain rows | Δ |
|---|---|---|---:|---:|:---:|
| Q001 | easy | lookup | 13 | 0 | ▲ |
| Q002 | easy | lookup | 0 | 0 | = |
| Q003 | easy | lookup | 18 | 0 | ▲ |
| Q004 | easy | lookup | 3 | 0 | ▲ |
| Q005 | easy | lookup | 20 | 0 | ▲ |
| Q006 | medium | interface_search | 0 | 0 | = |
| Q007 | medium | relation_search | 9 | 0 | ▲ |
| Q008 | medium | count | 0 | 0 | = |
| Q009 | medium | interface_intersection | 0 | 0 | = |
| Q010 | medium | interface_search | 0 | 0 | = |
| Q011 | medium | chapter_filter | 20 | 0 | ▲ |
| Q012 | hard | transitive_inference | 13 | 0 | ▲ |
| Q013 | hard | transitive_inference | 15 | 0 | ▲ |
| Q014 | hard | transitive_inference | 15 | 0 | ▲ |
| Q015 | hard | transitive_inference | 20 | 0 | ▲ |
| Q016 | hard | transitive_inference | 0 | 0 | = |
| Q017 | trap | trap | 0 | 0 | = |
| Q018 | trap | trap | 0 | 0 | = |
| Q019 | trap | trap | 0 | 0 | = |
| Q020 | trap | trap | 0 | 0 | = |
