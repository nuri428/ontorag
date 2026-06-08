"""Unit tests for the v1.2 multi-agent complexity router.

The router is a pure function over ``(question, SchemaResult)``, so
these tests construct a minimal ``SchemaResult`` fixture and verify the
classification rules in isolation — no graph store, no LLM, no IO.
"""

from __future__ import annotations

import pytest

from ontorag.chat.multi_agent.messages import Complexity, RouteDecision
from ontorag.chat.multi_agent.router import route
from ontorag.stores.base import ClassSummary, SchemaResult


def _schema(*class_specs: tuple[str, str | None]) -> SchemaResult:
    """Build a SchemaResult from ``(uri, label)`` tuples."""
    classes = [
        ClassSummary(uri=uri, label=label, property_count=0)
        for uri, label in class_specs
    ]
    return SchemaResult(
        total_classes=len(classes),
        total_properties=0,
        namespaces={"ex": "http://example.org/"},
        classes=classes,
    )


class TestRouteSimple:
    """SIMPLE — no TBox match, no signal."""

    def test_no_match_no_signal(self) -> None:
        schema = _schema(("http://example.org/Pokemon", "Pokemon"))
        decision = route("Hi, what's the time?", schema)
        assert decision.complexity == Complexity.SIMPLE
        assert decision.matched_classes == ()
        assert decision.hop_signals == ()
        assert decision.reasoning_signals == ()


class TestRouteSingleStep:
    """SINGLE_STEP — exactly one class, no extra signal."""

    def test_one_class_by_local_name(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("List all Pokemon", schema)
        assert decision.complexity == Complexity.SINGLE_STEP
        assert "Pokemon" in decision.matched_classes

    def test_one_class_by_label(self) -> None:
        schema = _schema(("http://example.org/Pokemon", "포켓몬"))
        decision = route("포켓몬 목록을 보여줘", schema)
        assert decision.complexity == Complexity.SINGLE_STEP
        assert "Pokemon" in decision.matched_classes


class TestRouteMultiStep:
    """MULTI_STEP — multiple classes, hop signal, or reasoning signal."""

    def test_two_classes_trigger_multi(self) -> None:
        schema = _schema(
            ("http://example.org/Pokemon", None),
            ("http://example.org/Type", None),
        )
        decision = route("Show Pokemon and their Type", schema)
        assert decision.complexity == Complexity.MULTI_STEP
        assert len(decision.matched_classes) >= 2

    def test_hop_signal_korean_compare(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("피카츄와 라이츄를 비교해줘", schema)
        # "비교" should fire hop signal even though only ≤1 class matches
        assert decision.complexity == Complexity.MULTI_STEP
        assert any("비교" in s for s in decision.hop_signals)

    def test_hop_signal_english_top_n(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("Show top 5 Pokemon by HP", schema)
        assert decision.complexity == Complexity.MULTI_STEP
        assert any("top 5" in s for s in decision.hop_signals)

    def test_reasoning_signal_korean_if(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route(
            "만약 피카츄가 진화한다면 어떤 타입이 될까?", schema
        )
        assert decision.complexity == Complexity.MULTI_STEP
        assert any("만약" in s for s in decision.reasoning_signals)

    def test_reasoning_signal_english_probability(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route(
            "What is the probability that Pikachu wins?", schema
        )
        assert decision.complexity == Complexity.MULTI_STEP
        assert any("probability" in s for s in decision.reasoning_signals)

    def test_reasoning_beats_class_count(self) -> None:
        """Reasoning signal wins even with zero class match."""
        schema = _schema(("http://example.org/Unrelated", None))
        decision = route("What is the posterior of X given Y?", schema)
        assert decision.complexity == Complexity.MULTI_STEP
        assert decision.reasoning_signals  # at least one


class TestRouteMultiStepKoreanV121:
    """v1.2.1 — Korean hop patterns added from multi-hop goldset diagnostics.

    Each phrase is the actual surface form from
    examples/pokemon/goldset_multihop.jsonl that the v1.2 router missed.
    """

    @pytest.fixture
    def schema(self) -> SchemaResult:
        return _schema(("http://example.org/Pokemon", None))

    def test_per_type_grouping(self, schema: SchemaResult) -> None:
        d = route("타입별 포켓몬은 몇 마리인가?", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("타입별" in s for s in d.hop_signals)

    def test_distributive_each(self, schema: SchemaResult) -> None:
        d = route("각각 알려줘", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("각각" in s for s in d.hop_signals)

    def test_threshold_at_or_above(self, schema: SchemaResult) -> None:
        d = route("공격력 80 이상의 포켓몬", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("80" in s and "이상" in s for s in d.hop_signals)

    def test_threshold_below(self, schema: SchemaResult) -> None:
        d = route("HP 50 미만 포켓몬", schema)
        assert d.complexity == Complexity.MULTI_STEP

    def test_equality_join_wa(self, schema: SchemaResult) -> None:
        d = route("피카츄와 같은 타입의 포켓몬", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("같은" in s for s in d.hop_signals)

    def test_equality_join_kwa(self, schema: SchemaResult) -> None:
        d = route("자기 타입과 같은 타입의 기술", schema)
        assert d.complexity == Complexity.MULTI_STEP

    def test_superlative_most(self, schema: SchemaResult) -> None:
        d = route("가장 많은 포켓몬을 보유한 트레이너", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("가장" in s for s in d.hop_signals)

    def test_superlative_most_common(self, schema: SchemaResult) -> None:
        d = route("가장 흔한 타입", schema)
        assert d.complexity == Complexity.MULTI_STEP

    def test_completeness_show_all(self, schema: SchemaResult) -> None:
        d = route("진화 계통을 모두 알려줘", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("모두" in s for s in d.hop_signals)

    def test_existential_any(self, schema: SchemaResult) -> None:
        d = route("불꽃 타입 포켓몬을 한 마리라도 가진 트레이너", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("라도" in s for s in d.hop_signals)

    def test_inverse_direction(self, schema: SchemaResult) -> None:
        d = route("trainedBy 역방향으로 추적", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("역방향" in s for s in d.hop_signals)

    def test_set_inclusion(self, schema: SchemaResult) -> None:
        d = route("전설 포함 관동 출신 포켓몬 수", schema)
        assert d.complexity == Complexity.MULTI_STEP
        # Both "포함" and "출신" should fire
        sigs_joined = " ".join(d.hop_signals)
        assert "포함" in sigs_joined or "출신" in sigs_joined

    def test_two_stage_compound(self, schema: SchemaResult) -> None:
        d = route("물 타입이 강한 타입은 무엇이고, 그 타입의 포켓몬은 누구인가?", schema)
        assert d.complexity == Complexity.MULTI_STEP

    def test_origin_relation(self, schema: SchemaResult) -> None:
        d = route("관동 출신 포켓몬", schema)
        assert d.complexity == Complexity.MULTI_STEP
        assert any("출신" in s for s in d.hop_signals)


class TestRouteSimplePreserved:
    """Regression — new Korean patterns must not over-fire on simple queries."""

    @pytest.fixture
    def schema(self) -> SchemaResult:
        return _schema(("http://example.org/Pokemon", "Pokemon"))

    def test_plain_greeting(self, schema: SchemaResult) -> None:
        assert route("안녕", schema).complexity == Complexity.SIMPLE

    def test_simple_lookup_no_hop(self, schema: SchemaResult) -> None:
        # Only 'Pokemon' matches, no hop / reasoning signal — SINGLE_STEP
        d = route("Pokemon 목록", schema)
        assert d.complexity == Complexity.SINGLE_STEP

    def test_single_attribute_lookup(self, schema: SchemaResult) -> None:
        d = route("이 캐릭터의 정보를 알려줘", schema)
        # No TBox class match, no hop / reasoning signal — SIMPLE
        assert d.complexity == Complexity.SIMPLE


class TestRouteThreshold:
    """Threshold parameter changes promotion behaviour."""

    def test_threshold_three_keeps_two_as_single(self) -> None:
        schema = _schema(
            ("http://example.org/Pokemon", None),
            ("http://example.org/Type", None),
        )
        # Two class matches, no hop / reasoning signal in the phrasing.
        # With the default threshold of 2 this would be MULTI_STEP; with
        # threshold=3 it should fall back to SINGLE_STEP (still matched).
        decision = route(
            "Pokemon Type 상세 정보 알려줘", schema, multi_hop_threshold=3
        )
        assert decision.complexity == Complexity.SINGLE_STEP
        assert len(decision.matched_classes) == 2


class TestRouteMatcherSafety:
    """Defensive behaviour on the class matcher."""

    def test_short_class_names_ignored(self) -> None:
        # Class name shorter than _MIN_CLASS_NAME_LEN (3) is ignored to
        # prevent false positives on generic two-letter tokens.
        schema = _schema(("http://example.org/AB", "AB"))
        decision = route("Find AB items quickly", schema)
        assert decision.complexity == Complexity.SIMPLE
        assert decision.matched_classes == ()

    def test_case_insensitive_match(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("show all pokemon", schema)  # lowercase
        assert decision.complexity == Complexity.SINGLE_STEP
        assert "Pokemon" in decision.matched_classes

    def test_duplicate_class_match_dedup(self) -> None:
        """A class mentioned twice in the question counts once."""
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("Pokemon Pokemon Pokemon", schema)
        # Only one match recorded
        assert decision.matched_classes == ("Pokemon",)


class TestRouteDecisionShape:
    """Output structure invariants."""

    def test_decision_is_frozen(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        decision = route("Pokemon", schema)
        assert isinstance(decision, RouteDecision)
        with pytest.raises(Exception):
            decision.rationale = "tampered"  # type: ignore[misc]

    def test_rationale_is_non_empty(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        for q in ("", "Pokemon", "many Pokemon and Type", "확률은?"):
            d = route(q, schema)
            assert d.rationale  # always populated
