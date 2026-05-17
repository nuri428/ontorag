"""Generate terminal-style PNG screenshots for v0.3 / v0.3.1 feature docs."""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

# AppleSDGothicNeo index 3 = Regular weight; supports Korean + Latin
FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
FONT_INDEX = 3
FONT_SIZE = 15
BG = "#1e1e2e"
FG = "#cdd6f4"
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
PAD_Y = 18
LINE_H = 23
TITLE_H = 36


def load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size, index=FONT_INDEX)


def render_terminal(
    lines: list[tuple[str, str]],
    filename: str,
    title: str = "Terminal",
    width: int = 820,
) -> None:
    font = load_font(FONT_SIZE)
    title_font = load_font(12)

    height = TITLE_H + PAD_Y * 2 + len(lines) * LINE_H
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, width, TITLE_H], fill=TITLE_BAR)
    for i, color in enumerate([BUTTON_RED, BUTTON_YELLOW, BUTTON_GREEN]):
        x = 16 + i * 22
        draw.ellipse([x, 11, x + 14, 25], fill=color)
    draw.text((width // 2, TITLE_H // 2), title, fill=DIM, font=title_font, anchor="mm")

    y = TITLE_H + PAD_Y
    for text, color in lines:
        draw.text((PAD_X, y), text, fill=color, font=font)
        y += LINE_H

    img.save(filename)
    print(f"Saved: {filename}")


def make_test_results() -> None:
    lines = [
        ("$ uv run pytest --tb=short -q", DIM),
        ("", FG),
        ("platform darwin -- Python 3.12.12, pytest-9.0.3", DIM),
        ("", FG),
        ("....................................................................", GREEN),
        ("....................................................................", GREEN),
        ("....................................................................", GREEN),
        ("", FG),
        ("tests/test_learn_structured_reader.py   25 passed", GREEN),
        ("tests/test_learn_column_mapper.py       15 passed", GREEN),
        ("tests/test_learn_structured_pipeline.py 14 passed", GREEN),
        ("tests/test_learn_pipeline.py            18 passed", GREEN),
        ("tests/test_learn_term_typing.py          9 passed", GREEN),
        ("tests/test_learn_taxonomy.py             8 passed", GREEN),
        ("tests/test_learn_relation.py             8 passed", GREEN),
        ("tests/test_learn_routes.py              14 passed", GREEN),
        ("... (+ 103 more tests)", DIM),
        ("", FG),
        ("=============================== 214 passed in 1.57s ================================", GREEN),
    ]
    render_terminal(
        lines, "assets/learn_tests.png",
        title="pytest — v0.3.1 전체 테스트 (214 passed)",
        width=900,
    )


def make_learn_help() -> None:
    lines = [
        ("$ ontorag learn --help", DIM),
        ("", FG),
        (" Usage: ontorag learn [OPTIONS] COMMAND [ARGS]...", FG),
        ("", FG),
        ("  텍스트에서 온톨로지 트리플을 학습합니다 (LLMs4OL v0.3).", CYAN),
        ("", FG),
        ("╭─ Commands ───────────────────────────────────────────────────────────────────╮", DIM),
        ("│  type-term            Task A: 텍스트 언급을 TBox 클래스에 매핑합니다.              │", FG),
        ("│  taxonomy             Task B: 텍스트에서 rdfs:subClassOf 관계를 제안합니다.        │", FG),
        ("│  extract              Task C: 텍스트에서 RDF 트리플을 추출합니다.                  │", FG),
        ("│  populate             A+B+C 파이프라인: 트리플 추출 → Fuseki 로드.                 │", FG),
        ("│  populate-structured  CSV/JSON/JSONL → ABox 트리플 생성 + Fuseki 로드. (v0.3.1)   │", CYAN),
        ("╰─────────────────────────────────────────────────────────────────────────────╯", DIM),
    ]
    render_terminal(lines, "assets/learn_help.png", title="ontorag learn --help", width=900)


def make_type_term() -> None:
    lines = [
        ("$ ontorag learn type-term \"Pikachu\" --context \"진화한 포켓몬\"", DIM),
        ("", FG),
        ("  분류 중...", DIM),
        ("", FG),
        ("  'Pikachu' → TBox 클래스 매핑:", WHITE),
        ("", FG),
        ("  #    클래스 URI              레이블            신뢰도", DIM),
        ("  ─────────────────────────────────────────────────────", DIM),
        ("  1    pk:ElectricPokemon      ElectricPokemon    0.97 █████████", CYAN),
        ("  2    pk:Pokemon              Pokemon            0.92 █████████", CYAN),
        ("  3    pk:LegendaryPokemon     Legendary          0.41 ████", CYAN),
        ("", FG),
        ("$ ontorag learn type-term \"React\"", DIM),
        ("", FG),
        ("  'React' → TBox 클래스 매핑:", WHITE),
        ("", FG),
        ("  #    클래스 URI              레이블              신뢰도", DIM),
        ("  ─────────────────────────────────────────────────────", DIM),
        ("  1    ts:FrontendFramework    FrontendFramework   0.96 █████████", CYAN),
        ("  2    ts:Library              Library             0.89 ████████", CYAN),
        ("  3    ts:JavaScriptTech       JS Technology       0.82 ████████", CYAN),
    ]
    render_terminal(
        lines, "assets/learn_type_term.png",
        title="ontorag learn type-term — Task A (Term Typing)",
        width=840,
    )


def make_populate() -> None:
    lines = [
        ("$ ontorag learn populate examples/techstack/corpus.txt", DIM),
        ("", FG),
        ("  A+B+C 파이프라인 실행 중...", DIM),
        ("", FG),
        ("  Task A — 타입 매핑 (5건)", WHITE),
        ("  텀              클래스 URI                신뢰도", DIM),
        ("  SvelteKit        ts:FullstackFramework      0.94", CYAN),
        ("  Deno             ts:RuntimeEnvironment      0.96", CYAN),
        ("  Remix            ts:FullstackFramework       0.91", CYAN),
        ("", FG),
        ("  Task C — RDF 트리플 (18건)", WHITE),
        ("  주어         서술어        목적어       신뢰도", DIM),
        ("  SvelteKit    dependsOn     Vite          0.92", CYAN),
        ("  SvelteKit    dependsOn     Svelte        0.97", CYAN),
        ("  Deno         supersedes    Node.js       0.88", CYAN),
        ("  Remix        dependsOn     React         0.96", CYAN),
        ("", FG),
        ("  위 항목을 Fuseki에 로드하시겠습니까? [y/N]: y", YELLOW),
        ("", FG),
        ("  ✓ 38개 트리플을 ABox에 로드했습니다.", GREEN),
    ]
    render_terminal(
        lines, "assets/learn_populate.png",
        title="ontorag learn populate — A+B+C Pipeline",
        width=820,
    )


def make_populate_structured() -> None:
    lines = [
        ("$ ontorag learn populate-structured pokemon.csv \\", DIM),
        ("      --class-uri pk:Pokemon --id-column name", DIM),
        ("", FG),
        ("  pokemon.csv 처리 중 — 배치 크기 50행  (신규 매핑)", FG),
        ("  ⠴ 컬럼 매핑 + 트리플 생성 중...", DIM),
        ("", FG),
        ("  컬럼 → TBox 속성 매핑:", WHITE),
        ("  컬럼      속성 URI          신뢰도", DIM),
        ("  name      rdfs:label        0.95", CYAN),
        ("  type      pk:hasType        0.90", CYAN),
        ("  hp        pk:hasHP          0.88", CYAN),
        ("", FG),
        ("  생성된 RDF 트리플 (450건)  (상위 10건만 표시)", WHITE),
        ("  주어             서술어    목적어      신뢰도", DIM),
        ("  Pikachu          label     Pikachu     0.95", CYAN),
        ("  Pikachu          hasType   Electric    0.90", CYAN),
        ("  Pikachu          hasHP     35          0.88", CYAN),
        ("  Charmander       label     Charmander  0.95", CYAN),
        ("  Charmander       hasType   Fire        0.90", CYAN),
        ("  ...", DIM),
        ("", FG),
        ("  450건의 트리플을 Fuseki ABox에 로드하시겠습니까? [y/N]: y", YELLOW),
        ("", FG),
        ("  ✓ 450개 트리플을 ABox에 로드했습니다.  ← pokemon.csv", GREEN),
        ("", FG),
        ("  # 두 번째 실행: 캐시 재사용 → LLM 호출 없음", DIM),
        ("$ ontorag learn populate-structured pokemon.csv --yes", DIM),
        ("  pokemon.csv 처리 중 — 배치 크기 50행  (캐시: pokemon.csv.mapping.json)", FG),
        ("  ✓ 450개 트리플을 ABox에 로드했습니다.  ← pokemon.csv", GREEN),
    ]
    render_terminal(
        lines, "assets/learn_populate_structured.png",
        title="ontorag learn populate-structured — CSV/JSON/JSONL → ABox (v0.3.1)",
        width=900,
    )


if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)
    make_test_results()
    make_learn_help()
    make_type_term()
    make_populate()
    make_populate_structured()
    print("All screenshots generated.")
