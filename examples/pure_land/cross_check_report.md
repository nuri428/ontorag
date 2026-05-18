# Cross-Check Report — examples/pure_land

> **Purpose**: Automated cross-check against external authoritative sources
> (Wikipedia, 84000 Toh 115, CBETA references). Compensates for the fact
> that the author of this benchmark is a Korean software engineer, not a
> Buddhist scholar — so checks that require Sanskrit/Chinese/doctrinal
> expertise are delegated to source comparison rather than human review.
>
> **Generated**: 2026-05-18 (eval-harness branch).
> **Scope**: schema.ttl + data.ttl as of commit `3dff1f8`.

---

## TL;DR

| Severity | Count | Required action |
|---|---|---|
| **CRITICAL** | 2 | Decide rename or remove (this session) |
| MEDIUM | 2 | Author decision (can defer) |
| LOW | 1 | Defer to GitHub issue (Buddhist scholar review) |
| INFO | 1 | No action — already correct |

---

## CRITICAL — Variable name ↔ label mismatch (6-direction buddhas)

These are **modelling errors**, not interpretive differences. The Python-style URI suggests one buddha, but the label names a different buddha. Two distinct entities have been conflated.

### C1. `pl:Ratnasambhava` rdfs:label "Ratnaketu"

**Problem**:
- `pl:Ratnasambhava` is the URI of *Ratnasambhava* (Skt. **रत्नसम्भव**), the southern Buddha of the Five Tathāgatas in Vajrayāna/金剛界 cosmology.
- Its rdfs:label is "Ratnaketu" (Skt. **रत्नकेतु**, 寶幢佛/寶相佛), an entirely different buddha — one of the *western-direction* Buddhas in the Display of Sukhāvatī.
- These are two distinct beings in two distinct doctrinal frameworks.

**Source**:
- [84000 Toh 115 — Display of Sukhāvatī](https://84000.co/translation/toh115) lists for the **West**: *Amitāyus, Amitaskandha, Amitadhvaja, Mahāprabha, Illuminating Light Rays, **Ratnaketu**, Śuddha­raśmi­prabha*.
- Ratnasambhava does **not** appear in the six-direction list of this sūtra; he belongs to the later Vajrayāna 5-buddha mandala.

**Recommended action**:
- Rename URI `pl:Ratnasambhava` → `pl:Ratnaketu` and keep all current labels/relations.
- Update Chinese label `寶相佛` is acceptable as a 寶幢佛/寶相佛 variant; verify with CBETA T0366 if precise standard is needed.

### C2. `pl:Amoghasiddhi` rdfs:label "Bhīṣmagarjitaghoṣasvara"

**Problem**:
- `pl:Amoghasiddhi` is the URI of *Amoghasiddhi* (Skt. **अमोघसिद्धि**, 不空成就佛), the northern Buddha of the Five Tathāgatas in Vajrayāna.
- Its rdfs:label is "Bhīṣmagarjitaghoṣasvara" (Skt. **भीष्मगर्जितघोषस्वर**) — a *different* buddha. The 84000 translation does not list this name in any direction; the Chinese label "難沮佛" actually transliterates a different name found in T0366.
- Same problem as C1: two distinct buddhas conflated.

**Recommended action**:
- Either rename URI `pl:Amoghasiddhi` → `pl:NorthernBuddha_T0366` (or pick a specific name from 84000's North list: *Mahārciskandha, Vaiśvānara­nirghoṣa, Duṣpradharṣa, Āditya­saṃbhava, Jālinīprabha, Prabhākara*)
- Or **simplify**: replace `pl:Ratnasambhava` and `pl:Amoghasiddhi` with two clearly-T0366-attested buddhas (e.g. `pl:Aksobhya` for east — already correct — plus `pl:Ratnaketu` for west and one northern buddha).

---

## MEDIUM — Author decision (can defer)

### M1. `pl:Avalokitesvara assists` left vs right of Amitābha

**Problem**:
- Code comment says: *"Left attendant of Amitābha (西方三聖 left)"* for Avalokiteśvara.
- Wikipedia (English) states: *"Avalokiteśvara: **Right attendant** bodhisattva; Mahāsthāmaprāpta: **Left attendant** bodhisattva"*.

**Interpretation**:
- "Left" and "right" depend on **viewpoint** — from the buddha's perspective (traditional Buddhist iconography) versus from the viewer's perspective (looking at the buddha).
- 한국 전통은 觀者의 입장 vs 佛의 입장에서 표기가 종종 반대. Both conventions exist.

**Recommended action**:
- Either remove "left/right" from comments (rendering them position-agnostic), or explicitly state which viewpoint is used. Either is correct; the current text is just ambiguous.

### M2. `pl:Dharmakara owl:sameAs pl:Amitabha`

**Problem**:
- Doctrinally OK: Wikipedia confirms *"Amitābha was a bodhisattva monk named Dharmākara"*. They are the **same person at different temporal stages** (pre-enlightenment vs post-enlightenment).
- **But** in OWL semantics, `owl:sameAs` propagates *all* properties bidirectionally. A reasoner will conclude `pl:Dharmakara a pl:Buddha` because Amitābha is a Buddha, and `pl:Amitabha a pl:Bodhisattva` because Dharmākara is a Bodhisattva. This produces a **type inconsistency** (Buddha and Bodhisattva are sibling subclasses of Being, not disjoint but conceptually distinct).

**Recommended action — three options**:
1. **Remove `owl:sameAs`**, keep `rdfs:seeAlso` only. Loses the goldset benefit ("ask about Dharmākara → get Amitābha info") but is semantically clean.
2. **Replace with custom property** `pl:precedingStage` or `pl:becameAfterEnlightenment`. Doctrinally accurate, no reasoner contamination.
3. **Keep `owl:sameAs`** and accept that reasoners will collapse the two. May actually be desirable for goldset (single conceptual entity).

Option 2 is the safest for goldset quality.

---

## LOW — Defer to GitHub issue

### L1. 48 vows — precise canonical Chinese/Korean names

**Status**: Wikipedia "Forty-eight Vows of Amitabha" article returned 404 during automated fetch. Other authoritative sources (정토진종 표준, 學會 문헌) require domain expertise to evaluate.

**Recommendation**:
- Open a GitHub issue titled *"Verify canonical naming of 48 vows of Amitābha (T0360)"* tagged `help-wanted` and `domain-expertise`.
- README already invites *"Corrections from Buddhist scholars"* — this is exactly what that channel is for.
- The current 48 vow names in `data.ttl` are workable for benchmark purposes; perfect canonical naming is not blocking.

---

## INFO — Already correct (no action)

### I1. 十六觀法 (16 Contemplations) Chinese names

Wikipedia's English descriptions of T0365's sixteen contemplations align with the Chinese names in `data.ttl`:

| # | data.ttl | Wikipedia English |
|---|---|---|
| 1 | 日想觀 | Setting sun ✓ |
| 2 | 水想觀 | Water ✓ |
| 3 | 地想觀 | Beryl ground ✓ |
| 4 | 寶樹觀 | Jeweled trees ✓ |
| 5 | 寶池觀 | Golden ponds ✓ |
| 6 | 寶樓觀 | Various objects (jeweled tower) ✓ |
| 7 | 華座觀 | Lotus throne ✓ |
| 8 | 像觀 | Image of Amitābha ✓ |
| 9 | 阿彌陀佛觀 | Amitābha himself ✓ |
| 10 | 觀世音菩薩觀 | Avalokiteśvara ✓ |
| 11 | 大勢至菩薩觀 | Mahāsthāmaprāpta ✓ |
| 12 | 普觀 | Aspirants (general view) ✓ |
| 13 | 雜想觀 | Amitābha + 2 bodhisattvas (mixed) ✓ |
| 14 | 上輩生想觀 | Highest grades ✓ |
| 15 | 中輩生想觀 | Middle grades ✓ |
| 16 | 下輩生想觀 | Lowest grades ✓ |

No action needed.

---

## Sources consulted

- [Wikipedia — Amitābha](https://en.wikipedia.org/wiki/Amitabha) — Sanskrit names, attendants, Dharmākara relationship
- [Wikipedia — Amitāyurdhyāna Sūtra (T0365)](https://en.wikipedia.org/wiki/Amit%C4%81yurdhy%C4%81na_S%C5%ABtra) — 16 contemplations
- [84000 Toh 115 — Display of the Pure Land of Sukhāvatī](https://84000.co/translation/toh115) — 6-direction buddhas
- Wikipedia "Forty-eight Vows of Amitabha" — **404 / not available**

## What this report does NOT cover

- Sanskrit IAST diacritic precision (e.g. ā vs a) for every individual — assumed correct unless Wikipedia disagrees
- Korean Buddhist 종단별 표준 명칭 차이 — defer to L1's GitHub issue
- CBETA T0360 한문 본문과의 verbatim 대조 — would require fetching CBETA's structured text; not done in this pass
