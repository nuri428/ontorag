# Commerce Ontology — Real-domain benchmark

> **Status**: v0.0.1 — Real-domain half of the Phase B evaluation
> harness. Companion to `examples/pure_land/` (fictional+religious half).
> **Purpose**: Demonstrate ontology-aware RAG on a domain LLMs partially
> know (commerce, products, organisations), using schema.org standard
> vocabulary with fictional company/product instances.

---

## Why this domain

The Phase B harness needs two domains:

| Domain | Role | LLM contamination |
|---|---|---|
| `pure_land/` | Fictional+religious | partial (concept names known) |
| `commerce/` | **Real-vocabulary + fictional instances** | partial (vocab known, instances unknown) |

This pair lets blog/paper benchmarks show:

* **Pure Land** — does ontology RAG beat vector RAG when *concepts* leak via training but *exact relations* require KG lookup?
* **Commerce** — does ontology RAG beat vector RAG when *vocabulary* is standard but *instances* are private (the realistic enterprise PoC setting)?

The latter is the use-case enterprise buyers care about most: "we have schema.org-ish data internally, but our actual products/people/customers are not on the public web."

---

## Sources / 어휘 출처

Reused standard vocabularies (no copying — just `rdfs:subClassOf` /
`owl:equivalentClass` references):

| Vocab | Used for | License |
|---|---|---|
| [schema.org](https://schema.org/) | `schema:Organization`, `schema:Product`, `schema:Person`, `schema:Brand`, `schema:Offer`, `schema:MonetaryAmount` | CC BY-SA 4.0 |
| [FOAF](http://xmlns.com/foaf/0.1/) | `foaf:Person`, `foaf:name` | CC BY 1.0 |
| [dcterms](http://purl.org/dc/terms/) | Metadata | open |
| [GoodRelations](http://www.heppnetz.de/projects/goodrelations/) (inspired) | Offer/price patterns | (concepts only) |

The instance data — companies named *Aurora Tech*, *Helios Robotics*,
*Nimbus Industries*, products like *Aurora Phone X1* — is **entirely
fictional**. No real-world company or product is modelled.

---

## What this domain exercises

| Feature | How |
|---|---|
| `owl:TransitiveProperty` | `pl:subsidiaryOf` — corporate group structure spans levels |
| `owl:inverseOf` | `employs` / `employedBy`, `manufactures` / `manufacturedBy` |
| `rdfs:subClassOf` | `Smartphone ⊑ ConsumerElectronics ⊑ Product` |
| Numeric filters | Price comparisons (`?price >= 500`) |
| Currency arithmetic | Product price × quantity in offers |
| Multi-hop joins | Find `Person` employed by `Organization` that owns `Brand` that manufactures `Product` |
| Multilingual labels | Korean + English (no zh-Hant — commerce domain doesn't share Pure Land's Sanskrit/Chinese demand) |

---

## Files

```
examples/commerce/
├── README.md           # this file
├── schema.ttl          # TBox — Organization/Product/Person/Offer hierarchy
├── data.ttl            # ABox — fictional companies, products, offers, people
└── goldset.jsonl       # Benchmark questions (20 seed; expand to 50 later)
```

## License

- **Ontology files** — MIT (same as ontorag).
- **Vocabulary references** — schema.org under CC BY-SA 4.0 (we only reference URIs; we do not copy schema.org's RDF/JSON-LD source).
- **Instance data** — fictional, CC0.

## Benchmark role

Together with `pure_land/`, this domain is the **Real** half of the
Phase B RAGAS+goldset evaluation harness. LangChain baseline (B-6)
will run on both domains; results compared via `ontorag eval report`.
