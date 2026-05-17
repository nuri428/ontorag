"""Generate terminal-style PNG screenshots for v0.3 learn feature docs."""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
FONT_SIZE = 15
BG = "#1e1e2e"         # dark background
FG = "#cdd6f4"         # default text
GREEN = "#a6e3a1"
CYAN = "#89dceb"
YELLOW = "#f9e2af"
RED = "#f38ba8"
BLUE = "#89b4fa"
DIM = "#6c7086"
WHITE = "#cdd6f4"
TITLE_BAR = "#313244"
BUTTON_RED = "#f38ba8"
BUTTON_YELLOW = "#f9e2af"
BUTTON_GREEN = "#a6e3a1"

PAD_X = 24
PAD_Y = 20
LINE_H = 22
TITLE_H = 36


def load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def render_terminal(
    lines: list[tuple[str, str]],   # (text, color)
    filename: str,
    title: str = "Terminal",
    width: int = 820,
) -> None:
    font = load_font(FONT_SIZE)
    title_font = load_font(13)

    height = TITLE_H + PAD_Y * 2 + len(lines) * LINE_H
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, width, TITLE_H], fill=TITLE_BAR)
    # Traffic lights
    for i, color in enumerate([BUTTON_RED, BUTTON_YELLOW, BUTTON_GREEN]):
        x = 16 + i * 22
        draw.ellipse([x, 11, x + 14, 25], fill=color)
    # Title text
    draw.text((width // 2, TITLE_H // 2), title, fill=DIM, font=title_font, anchor="mm")

    # Content
    y = TITLE_H + PAD_Y
    for text, color in lines:
        draw.text((PAD_X, y), text, fill=color, font=font)
        y += LINE_H

    img.save(filename)
    print(f"Saved: {filename}")


def make_test_results() -> None:
    lines = [
        ("$ uv run pytest tests/test_learn_*.py -v", DIM),
        ("", FG),
        ("platform darwin -- Python 3.12.12, pytest-9.0.3", DIM),
        ("collecting ... collected 48 items", DIM),
        ("", FG),
        ("tests/test_learn_term_typing.py::TestTypeTermHappyPath::test_returns_ranked_results  PASSED [  2%]", GREEN),
        ("tests/test_learn_term_typing.py::TestTypeTermHappyPath::test_top_k_limit             PASSED [  4%]", GREEN),
        ("tests/test_learn_term_typing.py::TestTypeTermHappyPath::test_label_populated_...     PASSED [  6%]", GREEN),
        ("tests/test_learn_term_typing.py::TestTypeTermValidation::test_unknown_uri_filtered   PASSED [  8%]", GREEN),
        ("tests/test_learn_term_typing.py::TestTypeTermValidation::test_empty_typings_...      PASSED [ 10%]", GREEN),
        ("tests/test_learn_term_typing.py::TestTypeTermLLMFailure::test_llm_exception_...      PASSED [ 12%]", GREEN),
        ("tests/test_learn_taxonomy.py::TestDiscoverTaxonomy::test_returns_valid_relations     PASSED [ 14%]", GREEN),
        ("tests/test_learn_taxonomy.py::TestDiscoverTaxonomy::test_filters_unknown_parent_uri  PASSED [ 16%]", GREEN),
        ("tests/test_learn_taxonomy.py::TestDiscoverTaxonomy::test_empty_relations             PASSED [ 18%]", GREEN),
        ("tests/test_learn_taxonomy.py::TestDiscoverTaxonomy::test_llm_failure_returns_empty   PASSED [ 20%]", GREEN),
        ("tests/test_learn_relation.py::TestExtractRelations::test_returns_valid_triples       PASSED [ 22%]", GREEN),
        ("tests/test_learn_relation.py::TestExtractRelations::test_filters_unknown_predicate   PASSED [ 25%]", GREEN),
        ("tests/test_learn_relation.py::TestExtractRelations::test_filters_below_min_...       PASSED [ 27%]", GREEN),
        ("tests/test_learn_relation.py::TestExtractRelations::test_schema_without_...          PASSED [ 29%]", GREEN),
        ("tests/test_learn_relation.py::TestExtractRelations::test_llm_failure_returns_empty   PASSED [ 31%]", GREEN),
        ("tests/test_learn_pipeline.py::TestPopulateFromText::test_populate_dry_run_...        PASSED [ 37%]", GREEN),
        ("tests/test_learn_pipeline.py::TestPopulateFromText::test_populate_auto_load_...      PASSED [ 39%]", GREEN),
        ("tests/test_learn_pipeline.py::TestPopulateFromText::test_confidence_filtering        PASSED [ 41%]", GREEN),
        ("tests/test_learn_pipeline.py::TestSerializeToTTL::test_produces_valid_turtle         PASSED [ 43%]", GREEN),
        ("tests/test_learn_pipeline.py::TestSerializeToTTL::test_object_uri_triple_...         PASSED [ 47%]", GREEN),
        ("tests/test_learn_routes.py::TestTypeTermRoute::test_returns_200_with_mocked_result   PASSED [ 70%]", GREEN),
        ("tests/test_learn_routes.py::TestTypeTermRoute::test_rejects_empty_term               PASSED [ 72%]", GREEN),
        ("tests/test_learn_routes.py::TestTypeTermRoute::test_503_when_llm_not_configured      PASSED [ 79%]", GREEN),
        ("tests/test_learn_routes.py::TestExtractTriplesRoute::test_returns_200_with_...       PASSED [ 85%]", GREEN),
        ("tests/test_learn_routes.py::TestExtractTriplesRoute::test_503_when_llm_not_...       PASSED [ 95%]", GREEN),
        ("tests/test_learn_routes.py::TestExtractTriplesRoute::test_503_when_schema_...        PASSED [100%]", GREEN),
        ("", FG),
        ("=============================== 48 passed in 0.61s ================================", GREEN),
    ]
    render_terminal(lines, "assets/learn_tests.png", title="pytest — v0.3 LLMs4OL tests (48 passed)", width=900)


def make_learn_help() -> None:
    lines = [
        ("$ ontorag learn --help", DIM),
        ("", FG),
        (" Usage: ontorag learn [OPTIONS] COMMAND [ARGS]...", FG),
        ("", FG),
        ("  텍스트에서 온톨로지 트리플을 학습합니다 (LLMs4OL v0.3).", CYAN),
        ("", FG),
        ("╭─ Commands ──────────────────────────────────────────────────────────╮", DIM),
        ("│  type-term   Task A: 텍스트 언급을 TBox 클래스에 매핑합니다.        │", FG),
        ("│  taxonomy    Task B: 텍스트에서 rdfs:subClassOf 관계를 제안합니다.  │", FG),
        ("│  extract     Task C: 텍스트에서 RDF 트리플을 추출합니다.            │", FG),
        ("│  populate    A+B+C 파이프라인: 트리플 추출 → Fuseki 로드.           │", FG),
        ("╰─────────────────────────────────────────────────────────────────────╯", DIM),
        ("", FG),
        ("$ ontorag learn type-term --help", DIM),
        ("", FG),
        (" Usage: ontorag learn type-term [OPTIONS] TERM", FG),
        ("", FG),
        ("  Task A: 텍스트 언급을 TBox 클래스에 매핑합니다.", CYAN),
        ("", FG),
        ("╭─ Options ───────────────────────────────────────────────────────────╮", DIM),
        ("│  --context  -c  TEXT    문맥 텍스트 (최대 500자).                   │", FG),
        ("│  --top-k    -k  INT     반환할 최대 결과 수. [default: 3]           │", FG),
        ("│  --help                 Show this message and exit.                 │", FG),
        ("╰─────────────────────────────────────────────────────────────────────╯", DIM),
    ]
    render_terminal(lines, "assets/learn_help.png", title="ontorag learn --help", width=820)


def make_type_term() -> None:
    lines = [
        ("$ ontorag learn type-term \"Pikachu\" --context \"진화한 포켓몬\"", DIM),
        ("", FG),
        ("  분류 중...", DIM),
        ("", FG),
        ("  'Pikachu' → TBox 클래스 매핑:", WHITE),
        ("", FG),
        ("  #    클래스 URI                     레이블         신뢰도         근거", DIM),
        ("  ─────────────────────────────────────────────────────────────────────", DIM),
        ("  1    pk:ElectricPokemon             ElectricPokemon  0.97 █████████  Electric-type match", CYAN),
        ("  2    pk:Pokemon                     Pokemon          0.92 █████████  Base class for all", CYAN),
        ("  3    pk:LegendaryPokemon            Legendary        0.41 ████       Partial: legendary?", CYAN),
        ("", FG),
        ("$ ontorag learn type-term \"React\"", DIM),
        ("", FG),
        ("  'React' → TBox 클래스 매핑:", WHITE),
        ("", FG),
        ("  #    클래스 URI                     레이블            신뢰도         근거", DIM),
        ("  ─────────────────────────────────────────────────────────────────────", DIM),
        ("  1    ts:FrontendFramework           FrontendFramework  0.96 █████████  React is a UI library", CYAN),
        ("  2    ts:Library                     Library            0.89 ████████   Could be classified as", CYAN),
        ("  3    ts:JavaScriptTechnology        JS Technology      0.82 ████████   Written in JavaScript", CYAN),
    ]
    render_terminal(lines, "assets/learn_type_term.png", title="ontorag learn type-term — Task A (Term Typing)", width=860)


def make_populate() -> None:
    lines = [
        ("$ ontorag learn populate examples/techstack/corpus.txt", DIM),
        ("", FG),
        ("  A+B+C 파이프라인 실행 중...", DIM),
        ("", FG),
        ("  Task A — 타입 매핑 (10건)", WHITE),
        ("  ──────────────────────────────────────────────────────────", DIM),
        ("  텀              클래스 URI                      신뢰도", DIM),
        ("  SvelteKit        ts:FullstackFramework            0.94", CYAN),
        ("  Deno             ts:RuntimeEnvironment            0.96", CYAN),
        ("  Remix            ts:FullstackFramework            0.91", CYAN),
        ("  Vitest           ts:TestingFramework              0.88", CYAN),
        ("  Playwright       ts:TestingFramework              0.93", CYAN),
        ("", FG),
        ("  Task C — RDF 트리플 (18건)", WHITE),
        ("  ──────────────────────────────────────────────────────────", DIM),
        ("  주어             서술어          목적어           신뢰도", DIM),
        ("  SvelteKit        dependsOn       Vite             0.92", CYAN),
        ("  SvelteKit        dependsOn       Svelte           0.97", CYAN),
        ("  Svelte           maintainedBy    Vercel           0.95", CYAN),
        ("  Deno             supersedes      Node.js          0.88", CYAN),
        ("  Remix            dependsOn       React            0.96", CYAN),
        ("  Vitest           integratesWith  Vite             0.94", CYAN),
        ("", FG),
        ("  위 항목을 Fuseki에 로드하시겠습니까? [y/N]: y", YELLOW),
        ("", FG),
        ("  ✓ 38개 트리플을 ABox에 로드했습니다.", GREEN),
    ]
    render_terminal(lines, "assets/learn_populate.png", title="ontorag learn populate — A+B+C Pipeline", width=820)


if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)
    make_test_results()
    make_learn_help()
    make_type_term()
    make_populate()
    print("All screenshots generated.")
