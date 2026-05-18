"""`ontorag eval` CLI — goldset validation and execution.

Two commands:

* ``ontorag eval validate <goldset.jsonl>`` — Pydantic + SPARQL syntax
  check, plus a difficulty/category breakdown. Exits non-zero on any
  validation error.
* ``ontorag eval run <goldset.jsonl> --schema FILE --data FILE
  [--output FILE]`` — execute every question's gold_sparql against the
  combined RDF graph, report per-question result counts and the
  difficulty-tier rollup. Optional JSON report file.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rdflib import Graph
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from ontorag.eval.goldset import Difficulty, Goldset, GoldsetValidationError
from ontorag.eval.orchestrator import BenchRunner
from ontorag.eval.report import compare_reports, generate_markdown_report

console = Console()
err_console = Console(stderr=True)

eval_app = typer.Typer(
    help="평가 하네스 (RAGAS + goldset 기반 RAG 벤치마크).",
    no_args_is_help=True,
)


# ── validate ──────────────────────────────────────────────────────────────────


@eval_app.command("validate")
def eval_validate(
    goldset_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="검증할 goldset JSONL 파일 경로.",
    ),
) -> None:
    """Goldset JSONL 파일의 구조와 SPARQL 문법을 검증합니다."""
    try:
        gs = Goldset.load(goldset_path)
    except GoldsetValidationError as e:
        err_console.print(f"[red]✗ Validation failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"[green]✓ Loaded:[/green] {len(gs)} questions")
    _print_distribution(gs)


# ── run ───────────────────────────────────────────────────────────────────────


@eval_app.command("run")
def eval_run(
    goldset_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="실행할 goldset JSONL 파일 경로.",
    ),
    schema: Path = typer.Option(
        ...,
        "--schema",
        "-s",
        exists=True,
        dir_okay=False,
        readable=True,
        help="TBox TTL 파일 경로.",
    ),
    data: Path = typer.Option(
        ...,
        "--data",
        "-d",
        exists=True,
        dir_okay=False,
        readable=True,
        help="ABox TTL 파일 경로.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        dir_okay=False,
        writable=True,
        help="실행 결과를 저장할 JSON 파일 (생략 시 stdout 요약만).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="질문별 결과 행을 자세히 출력합니다.",
    ),
) -> None:
    """Goldset의 모든 질문을 schema+data 그래프에 실행하여 결과를 보고합니다."""
    try:
        gs = Goldset.load(goldset_path)
    except GoldsetValidationError as e:
        err_console.print(f"[red]✗ Goldset validation failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    graph = Graph()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Loading TBox + ABox…", total=None)
        graph.parse(schema, format="turtle")
        graph.parse(data, format="turtle")
        progress.update(task, description=f"Loaded {len(graph)} triples")

    console.print(f"[dim]Combined graph: {len(graph)} triples[/dim]")

    results: list[dict] = []
    failures: list[tuple[str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"Executing {len(gs)} queries…", total=len(gs)
        )
        for q in gs:
            entry: dict = {
                "id": q.id,
                "difficulty": q.difficulty.value,
                "category": q.category,
                "uses_inference": q.uses_inference,
            }
            try:
                rows = list(graph.query(q.gold_sparql))
                entry["row_count"] = len(rows)
                entry["status"] = "ok"
                if verbose:
                    entry["rows_preview"] = [
                        [str(v) for v in r] for r in rows[:5]
                    ]
            except Exception as e:  # noqa: BLE001
                entry["row_count"] = 0
                entry["status"] = "error"
                entry["error"] = str(e)
                failures.append((q.id, str(e)))
            results.append(entry)
            progress.advance(task)

    _print_run_summary(gs, results, failures, verbose)

    if output:
        report = {
            "goldset": str(goldset_path),
            "schema": str(schema),
            "data": str(data),
            "graph_triples": len(graph),
            "total_questions": len(gs),
            "failures": len(failures),
            "results": results,
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        console.print(f"\n[green]✓ Report written:[/green] {output}")

    if failures:
        raise typer.Exit(code=1)


# ── report ────────────────────────────────────────────────────────────────────


@eval_app.command("report")
def eval_report(
    json_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="`ontorag eval run --output`이 생성한 JSON 리포트 파일.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        dir_okay=False,
        writable=True,
        help="생성된 Markdown을 저장할 파일 (생략 시 stdout).",
    ),
) -> None:
    """`eval run` JSON 리포트를 사람이 읽기 좋은 Markdown으로 변환합니다."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    md = generate_markdown_report(data)
    if output:
        output.write_text(md, encoding="utf-8")
        console.print(f"[green]✓ Markdown report written:[/green] {output}")
    else:
        print(md)  # stdout, no rich formatting so pipes work


# ── bench (run a baseline against the goldset + compute metrics) ─────────────


@eval_app.command("bench")
def eval_bench(
    goldset_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="실행할 goldset JSONL 파일 경로.",
    ),
    baseline_name: str = typer.Option(
        "ontorag_mock",
        "--baseline",
        "-b",
        help="실행할 baseline: ontorag_mock | vector_rag_mock | langchain (langchain은 OPENAI_API_KEY + bench extra 필요).",
    ),
    schema: Path = typer.Option(
        ..., "--schema", "-s", exists=True, dir_okay=False, readable=True,
        help="TBox TTL 파일.",
    ),
    data: Path = typer.Option(
        ..., "--data", "-d", exists=True, dir_okay=False, readable=True,
        help="ABox TTL 파일.",
    ),
    language: str = typer.Option(
        "en", "--lang", "-l", help="질문 언어: en | ko.",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", dir_okay=False, writable=True,
        help="bench result JSON 파일 (생략 시 stdout 요약만).",
    ),
) -> None:
    """Goldset × baseline × 메트릭을 일괄 실행하여 BenchResult를 생성합니다."""
    import asyncio  # noqa: PLC0415

    try:
        gs = Goldset.load(goldset_path)
    except GoldsetValidationError as e:
        err_console.print(f"[red]✗ Goldset validation failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    graph = Graph()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Loading TBox + ABox…", total=None)
        graph.parse(schema, format="turtle")
        graph.parse(data, format="turtle")
        progress.update(task, description=f"Loaded {len(graph)} triples")

    baseline = _build_baseline(baseline_name, gs, graph, language)

    async def _run():
        runner = BenchRunner(
            gs, baseline, graph,
            language=language, goldset_path=str(goldset_path),
        )
        return await runner.run()

    bench_result = asyncio.run(_run())

    _print_bench_summary(bench_result)

    if output:
        output.write_text(
            json.dumps(bench_result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"\n[green]✓ Bench result written:[/green] {output}")


def _build_baseline(name: str, goldset: Goldset, graph: Graph, language: str):
    """Construct a baseline instance by name. Mocks have no external deps."""
    if name == "ontorag_mock":
        from ontorag.eval.baselines.mocks import OntoragMockBaseline  # noqa: PLC0415

        return OntoragMockBaseline(goldset, graph, language=language)
    if name == "vector_rag_mock":
        from ontorag.eval.baselines.mocks import VectorRAGMockBaseline  # noqa: PLC0415

        return VectorRAGMockBaseline(goldset, language=language)
    if name == "langchain":
        # LangChain baseline path is not yet wired through this CLI helper
        # because it requires schema+data paths and OPENAI_API_KEY. See
        # `src/ontorag/eval/baselines/langchain_vector.py` and the test
        # `test_live_answer_on_commerce` for direct programmatic use.
        raise typer.BadParameter(
            "langchain baseline is not yet wired into the CLI. Use the "
            "mock baselines (ontorag_mock | vector_rag_mock) or call "
            "LangChainVectorBaseline directly with OPENAI_API_KEY set "
            "and `uv sync --extra bench` installed."
        )
    raise typer.BadParameter(
        f"Unknown baseline: {name!r}. "
        "Valid: ontorag_mock | vector_rag_mock | langchain"
    )


def _print_bench_summary(result) -> None:
    table = Table(title=f"Bench result — {result.baseline_name}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    agg = result.aggregate
    table.add_row("Total questions", str(result.total_questions))
    table.add_row("Avg latency (ms)", f"{agg.get('avg_latency_ms', 0):.1f}")
    table.add_row("Avg tool calls", f"{agg.get('avg_tool_calls') or 0:.2f}")
    table.add_row(
        "Avg hallucination rate",
        _fmt_opt(agg.get("avg_hallucination_rate")),
    )
    table.add_row(
        "Avg citation coverage",
        _fmt_opt(agg.get("avg_citation_coverage")),
    )
    table.add_row(
        "Citation provided (count / rate)",
        f"{agg.get('citation_provided_count', 0)} "
        f"({agg.get('citation_provided_rate', 0):.0%})",
    )
    table.add_row("Total prompt tokens", str(agg.get("total_prompt_tokens", 0)))
    table.add_row(
        "Total completion tokens", str(agg.get("total_completion_tokens", 0))
    )
    console.print(table)


def _fmt_opt(v) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "—"


# ── compare ───────────────────────────────────────────────────────────────────


@eval_app.command("compare")
def eval_compare(
    report_a: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="첫 번째 `eval run` JSON 리포트.",
    ),
    report_b: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="두 번째 `eval run` JSON 리포트.",
    ),
    name_a: str | None = typer.Option(
        None, "--name-a", help="A 측 표시 이름 (생략 시 파일명에서 추측)."
    ),
    name_b: str | None = typer.Option(
        None, "--name-b", help="B 측 표시 이름."
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        dir_okay=False,
        writable=True,
        help="비교 Markdown을 저장할 파일 (생략 시 stdout).",
    ),
) -> None:
    """두 `eval run` JSON 리포트를 나란히 비교하는 Markdown을 생성합니다."""
    data_a = json.loads(report_a.read_text(encoding="utf-8"))
    data_b = json.loads(report_b.read_text(encoding="utf-8"))
    md = compare_reports(data_a, data_b, name_a=name_a, name_b=name_b)
    if output:
        output.write_text(md, encoding="utf-8")
        console.print(f"[green]✓ Comparison written:[/green] {output}")
    else:
        print(md)


# ── helpers ───────────────────────────────────────────────────────────────────


def _print_distribution(gs: Goldset) -> None:
    table = Table(title="Goldset distribution")
    table.add_column("Difficulty", style="cyan")
    table.add_column("Count", justify="right")
    dist = gs.distribution()
    for d in Difficulty:
        table.add_row(d.value, str(dist[d]))
    table.add_row("[bold]Total[/bold]", f"[bold]{len(gs)}[/bold]")
    console.print(table)


def _print_run_summary(
    gs: Goldset,
    results: list[dict],
    failures: list[tuple[str, str]],
    verbose: bool,
) -> None:
    table = Table(title=f"Execution summary ({len(results)} queries)")
    table.add_column("Difficulty", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("OK", justify="right", style="green")
    table.add_column("Error", justify="right", style="red")
    table.add_column("Empty rows", justify="right", style="yellow")

    for d in Difficulty:
        subset = [r for r in results if r["difficulty"] == d.value]
        if not subset:
            continue
        ok = sum(1 for r in subset if r["status"] == "ok")
        err = sum(1 for r in subset if r["status"] == "error")
        empty = sum(
            1 for r in subset if r["status"] == "ok" and r["row_count"] == 0
        )
        table.add_row(d.value, str(len(subset)), str(ok), str(err), str(empty))

    total = len(results)
    total_ok = sum(1 for r in results if r["status"] == "ok")
    total_err = sum(1 for r in results if r["status"] == "error")
    total_empty = sum(
        1 for r in results if r["status"] == "ok" and r["row_count"] == 0
    )
    table.add_row(
        "[bold]All[/bold]",
        f"[bold]{total}[/bold]",
        f"[bold]{total_ok}[/bold]",
        f"[bold]{total_err}[/bold]",
        f"[bold]{total_empty}[/bold]",
    )
    console.print(table)

    if failures:
        console.print("\n[red]Failed queries:[/red]")
        for qid, err in failures:
            console.print(f"  [red]✗[/red] {qid}: {err}")

    if verbose:
        for r in results:
            preview = r.get("rows_preview", [])
            status_color = "green" if r["status"] == "ok" else "red"
            console.print(
                f"  [{status_color}]{r['id']}[/{status_color}] "
                f"({r['difficulty']}, {r['category']}) → "
                f"{r['row_count']} row(s)"
            )
            for row in preview:
                console.print(f"      {row}")
