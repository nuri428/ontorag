from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

learn_app = typer.Typer(help="텍스트에서 온톨로지 트리플을 학습합니다 (LLMs4OL v0.3).")

console = Console()


def _get_learner():
    """Build an LLMOntologyLearner from the current environment."""
    from ontorag.learn.pipeline import LLMOntologyLearner
    from ontorag.llm.factory import get_llm_provider
    from ontorag.stores.fuseki import FusekiStore

    try:
        llm = get_llm_provider()
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        console.print(
            "[dim]ontorag config set --provider anthropic --api-key sk-... 로 설정하세요.[/]"
        )
        raise typer.Exit(1)

    store = FusekiStore.from_env()
    return LLMOntologyLearner(store, llm)


def _print_triples_table(triples) -> None:
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("주어", style="cyan")
    table.add_column("서술어")
    table.add_column("목적어")
    table.add_column("신뢰도", justify="right")

    for t in sorted(triples, key=lambda x: x.confidence, reverse=True):
        obj = t.object_uri or t.object_value or ""
        table.add_row(
            escape(t.subject_label),
            escape(t.predicate_uri.split("#")[-1].split("/")[-1]),
            escape(str(obj)[:50]),
            f"{t.confidence:.2f}",
        )

    console.print(f"\n추출된 RDF 트리플 ({len(triples)}건):\n")
    console.print(table)


@learn_app.command("type-term")
def learn_type_term(
    term: str = typer.Argument(..., help="분류할 텍스트 언급 (예: React)."),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="문맥 텍스트 (최대 500자)."
    ),
    top_k: int = typer.Option(3, "--top-k", "-k", help="반환할 최대 결과 수."),
) -> None:
    """Task A: 텍스트 언급을 TBox 클래스에 매핑합니다.

    예시:
        ontorag learn type-term "React"
        ontorag learn type-term "Pikachu" --context "진화한 포켓몬"
    """
    learner = _get_learner()

    async def _run() -> list:
        try:
            return await learner.type_term(term, context=context, top_k=top_k)
        finally:
            await learner._store.aclose()

    with console.status("[bold]분류 중..."):
        results = asyncio.run(_run())

    if not results:
        console.print("[yellow]결과 없음[/] — 스키마가 로드되어 있는지 확인하세요.")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("순위", style="dim", width=4)
    table.add_column("클래스 URI", style="cyan")
    table.add_column("레이블")
    table.add_column("신뢰도", justify="right")
    table.add_column("근거", style="dim")

    for i, r in enumerate(results, 1):
        bar = "█" * int(r.confidence * 10)
        table.add_row(
            str(i),
            r.class_uri,
            r.label,
            f"{r.confidence:.2f} {bar}",
            (r.reasoning or "")[:60],
        )

    console.print(f"\n[bold]{term!r}[/] → TBox 클래스 매핑:\n")
    console.print(table)


@learn_app.command("taxonomy")
def learn_taxonomy(
    text_file: Path = typer.Argument(..., help="분석할 텍스트 파일."),
    min_confidence: float = typer.Option(
        0.7, "--min-confidence", help="최소 신뢰도 임계값."
    ),
) -> None:
    """Task B: 텍스트에서 rdfs:subClassOf 관계를 제안합니다.

    예시:
        ontorag learn taxonomy corpus.txt
        ontorag learn taxonomy corpus.txt --min-confidence 0.8
    """
    if not text_file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {text_file}")
        raise typer.Exit(1)

    text = text_file.read_text(encoding="utf-8")
    learner = _get_learner()

    async def _run() -> list:
        try:
            return await learner.discover_taxonomy(text)
        finally:
            await learner._store.aclose()

    with console.status("[bold]분류 계층 탐색 중..."):
        results = asyncio.run(_run())

    filtered = [r for r in results if r.confidence >= min_confidence]

    if not filtered:
        console.print("[yellow]제안된 subClassOf 관계 없음[/]")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("하위 개념", style="cyan")
    table.add_column("상위 클래스 URI")
    table.add_column("신뢰도", justify="right")

    for r in sorted(filtered, key=lambda x: x.confidence, reverse=True):
        table.add_row(r.child_term, r.parent_uri, f"{r.confidence:.2f}")

    console.print(f"\nrdfs:subClassOf 제안 ({len(filtered)}건):\n")
    console.print(table)


@learn_app.command("extract")
def learn_extract(
    text_file: Path = typer.Argument(..., help="트리플을 추출할 텍스트 파일."),
    min_confidence: float = typer.Option(
        0.7, "--min-confidence", help="최소 신뢰도 임계값."
    ),
) -> None:
    """Task C: 텍스트에서 RDF 트리플을 추출합니다.

    예시:
        ontorag learn extract corpus.txt
    """
    if not text_file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {text_file}")
        raise typer.Exit(1)

    text = text_file.read_text(encoding="utf-8")
    learner = _get_learner()

    async def _run() -> list:
        try:
            return await learner.extract_relations(text, min_confidence=min_confidence)
        finally:
            await learner._store.aclose()

    with console.status("[bold]트리플 추출 중..."):
        results = asyncio.run(_run())

    if not results:
        console.print("[yellow]추출된 트리플 없음[/]")
        return

    _print_triples_table(results)


@learn_app.command("populate")
def learn_populate(
    text_file: Path = typer.Argument(
        ..., help="A+B+C 파이프라인에 사용할 텍스트 파일."
    ),
    min_confidence: float = typer.Option(
        0.7, "--min-confidence", help="최소 신뢰도 임계값."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="확인 없이 즉시 Fuseki에 로드합니다."
    ),
) -> None:
    """A+B+C 파이프라인: 텍스트에서 ABox 트리플을 추출하고 Fuseki에 로드합니다.

    기본값으로 제안 트리플을 테이블로 표시한 후 y/n 로 확인합니다.
    --yes 플래그를 사용하면 확인 없이 즉시 로드합니다.

    예시:
        ontorag learn populate corpus.txt
        ontorag learn populate corpus.txt --min-confidence 0.8 --yes
    """
    if not text_file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {text_file}")
        raise typer.Exit(1)

    text = text_file.read_text(encoding="utf-8")
    learner = _get_learner()

    async def _run_pipeline() -> object:
        try:
            return await learner.populate_from_text(
                text, auto_load=False, min_confidence=min_confidence
            )
        finally:
            await learner._store.aclose()

    with console.status("[bold]A+B+C 파이프라인 실행 중..."):
        result = asyncio.run(_run_pipeline())

    total = (
        len(result.term_typings) + len(result.taxonomy_proposals) + len(result.triples)
    )
    if total == 0:
        console.print(
            "[yellow]추출된 항목 없음[/] — 텍스트나 min_confidence를 조정하세요."
        )
        return

    if result.term_typings:
        console.print(f"\n[bold]Task A — 타입 매핑 ({len(result.term_typings)}건)[/]")
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("텀", style="cyan")
        t.add_column("클래스 URI")
        t.add_column("신뢰도", justify="right")
        for r in result.term_typings:
            t.add_row(r.term, r.class_uri, f"{r.confidence:.2f}")
        console.print(t)

    if result.taxonomy_proposals:
        console.print(
            f"\n[bold]Task B — 분류 계층 ({len(result.taxonomy_proposals)}건)[/]"
        )
        t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        t.add_column("하위 개념", style="cyan")
        t.add_column("상위 클래스")
        t.add_column("신뢰도", justify="right")
        for r in result.taxonomy_proposals:
            t.add_row(r.child_term, r.parent_uri, f"{r.confidence:.2f}")
        console.print(t)

    if result.triples:
        console.print(f"\n[bold]Task C — RDF 트리플 ({len(result.triples)}건)[/]")
        _print_triples_table(result.triples)

    if not result.triples and not result.term_typings:
        console.print("\n[yellow]로드할 트리플이 없습니다.[/]")
        return

    if not yes:
        confirmed = typer.confirm(
            "\n위 항목을 Fuseki에 로드하시겠습니까? (--yes 플래그로 이 확인을 생략할 수 있습니다.)"
        )
        if not confirmed:
            console.print("[dim]취소했습니다.[/]")
            raise typer.Exit(0)

    async def _load() -> int:
        try:
            schema = await learner._store.get_schema()
            return await learner._load_triples(
                result.triples, result.term_typings, schema
            )
        finally:
            await learner._store.aclose()

    with console.status("[bold]Fuseki에 로드 중..."):
        loaded = asyncio.run(_load())

    console.print(f"\n[green]✓[/] [bold]{loaded:,}[/]개 트리플을 ABox에 로드했습니다.")


@learn_app.command("populate-structured")
def learn_populate_structured(
    file: Path = typer.Argument(
        ..., help="구조화 파일 경로 (.csv / .json / .jsonl)."
    ),
    class_uri: Optional[str] = typer.Option(
        None, "--class-uri", "-c", help="행에 매핑할 TBox 클래스 URI (예: pk:Pokemon)."
    ),
    id_column: Optional[str] = typer.Option(
        None, "--id-column", "-i", help="주어 URI의 슬러그로 쓸 컬럼명 (없으면 uuid5 자동 발급)."
    ),
    batch_size: int = typer.Option(
        50, "--batch-size", "-b", help="LLM 호출당 처리할 행 수."
    ),
    min_confidence: float = typer.Option(
        0.7, "--min-confidence", help="컬럼 매핑 포함 최소 신뢰도 임계값."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="확인 없이 즉시 Fuseki에 로드합니다."
    ),
) -> None:
    """구조화 파일(CSV/JSON/JSONL)에서 ABox 트리플을 생성하고 Fuseki에 로드합니다.

    LLM이 컬럼 이름을 TBox 속성 URI에 매핑하고, 각 행을 RDF 트리플로 변환합니다.
    컬럼 매핑 결과는 <파일명>.mapping.json 에 저장되어 재실행 시 LLM 호출 없이 재사용됩니다.

    \\b
    예시:
        ontorag learn populate-structured pokemon.csv --class-uri pk:Pokemon --id-column name
        ontorag learn populate-structured data.jsonl --batch-size 100 --yes
        ontorag learn populate-structured nested.json --min-confidence 0.8
    """
    if not file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(1)

    suffix = file.suffix.lower()
    if suffix not in {".csv", ".json", ".jsonl"}:
        console.print(
            f"[red]Error:[/] 지원하지 않는 형식입니다: [bold]{suffix}[/]\n"
            "[dim]지원 형식: .csv  .json  .jsonl[/]"
        )
        raise typer.Exit(1)

    learner = _get_learner()

    # --- 1단계: 파이프라인 실행 (자동 로드 없이) ---
    mapping_path = file.parent / (file.name + ".mapping.json")
    cache_label = (
        f"[dim](캐시: {mapping_path.name})[/]" if mapping_path.exists() else "[dim](신규 매핑)[/]"
    )
    console.print(
        f"\n[bold]{file.name}[/] 처리 중 — 배치 크기 {batch_size}행  {cache_label}"
    )

    async def _run() -> object:
        try:
            return await learner.populate_from_structured(
                file,
                class_uri=class_uri,
                id_column=id_column,
                batch_size=batch_size,
                min_confidence=min_confidence,
                auto_load=False,
            )
        finally:
            await learner._store.aclose()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("컬럼 매핑 + 트리플 생성 중...", total=None)
            result = asyncio.run(_run())
    except ValueError as exc:
        console.print(f"[yellow]⚠[/] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/] 파이프라인 실행 실패 — {exc}")
        raise typer.Exit(1)

    if not result.triples:
        console.print(
            "[yellow]생성된 트리플 없음[/] — 파일에 데이터가 있는지, "
            "스키마가 로드되어 있는지 확인하세요."
        )
        raise typer.Exit(0)

    # --- 2단계: 매핑 요약 표시 ---
    if mapping_path.exists():
        try:
            from ontorag.learn.column_mapper import load_mapping

            mf = load_mapping(mapping_path)
            console.print("\n[bold]컬럼 → TBox 속성 매핑:[/]")
            mt = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            mt.add_column("컬럼", style="cyan")
            mt.add_column("속성 URI")
            mt.add_column("신뢰도", justify="right")
            for cm in sorted(mf.columns, key=lambda c: c.confidence, reverse=True):
                pred = cm.predicate_uri or "[dim](매핑 없음)[/]"
                mt.add_row(cm.column_name, escape(pred), f"{cm.confidence:.2f}")
            console.print(mt)
        except Exception:
            pass

    # --- 3단계: 트리플 미리보기 (최대 10건) ---
    preview = result.triples[:10]
    console.print(f"\n[bold]생성된 RDF 트리플 ({len(result.triples):,}건)[/]")
    if len(result.triples) > 10:
        console.print(f"[dim](상위 10건만 표시, 총 {len(result.triples):,}건)[/]")
    _print_triples_table(preview)

    # --- 4단계: Fuseki 로드 확인 ---
    if not yes:
        confirmed = typer.confirm(
            f"\n{len(result.triples):,}건의 트리플을 Fuseki ABox에 로드하시겠습니까?"
        )
        if not confirmed:
            console.print("[dim]취소했습니다.[/]")
            raise typer.Exit(0)

    async def _load() -> int:
        try:
            schema = await learner._store.get_schema()
            return await learner._load_triples(result.triples, [], schema)
        finally:
            await learner._store.aclose()

    with console.status("[bold]Fuseki에 로드 중..."):
        loaded = asyncio.run(_load())

    console.print(
        f"\n[green]✓[/] [bold]{loaded:,}[/]개 트리플을 ABox에 로드했습니다. "
        f"[dim]← {file.name}[/]"
    )
