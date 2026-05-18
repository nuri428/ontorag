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
