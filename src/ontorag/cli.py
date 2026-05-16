from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Literal, Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

console = Console()

app = typer.Typer(
    name="ontorag",
    help="Ontology-aware RAG framework.",
    no_args_is_help=True,
)

load_app = typer.Typer(help="RDF 파일을 그래프 스토어에 로드합니다.")
app.add_typer(load_app, name="load")


# ── load subcommands ─────────────────────────────────────────────────────────

def _run_load(file: Path, mode: Literal["schema", "data", "auto"]) -> None:
    """Parse + upload RDF file with a Rich spinner and result summary."""
    from ontorag.stores.fuseki import FusekiStore

    if not file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(1)

    store = FusekiStore.from_env()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"{file.name} 로딩 중...", total=None)
        try:
            result = asyncio.run(store.load_rdf(str(file), mode))
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Error:[/] Fuseki 연결 실패 — {exc}")
            console.print("[dim]Fuseki가 실행 중인지 확인하세요: docker compose up fuseki[/]")
            raise typer.Exit(1)

    mode_label = {"schema": "스키마(TBox)", "data": "데이터(ABox)"}.get(
        result.mode, result.mode
    )
    console.print(
        f"[green]✓[/] [bold]{result.triples_loaded:,}[/] 트리플을 "
        f"[bold]{mode_label}[/]로 로드했습니다 ← {file.name}"
    )


@load_app.callback(invoke_without_command=True)
def load_auto(
    ctx: typer.Context,
    file: Optional[Path] = typer.Argument(None, help="RDF 파일 경로 (TTL, JSON-LD, RDF/XML)."),
) -> None:
    """RDF 파일을 자동 감지 모드로 로드합니다 (TBox/ABox 자동 판별)."""
    if ctx.invoked_subcommand is not None:
        return
    if file is None:
        console.print("[red]Error:[/] 파일 경로를 지정하세요.")
        console.print("  사용법: ontorag load <FILE>")
        console.print("         ontorag load schema <FILE>")
        console.print("         ontorag load data <FILE>")
        raise typer.Exit(1)
    _run_load(file, "auto")


@load_app.command("schema")
def load_schema(
    file: Path = typer.Argument(..., help="스키마(TBox) RDF 파일 (클래스/속성 정의)."),
) -> None:
    """스키마(TBox) RDF 파일을 로드합니다. 기존 스키마를 교체합니다."""
    _run_load(file, "schema")


@load_app.command("data")
def load_data(
    file: Path = typer.Argument(..., help="인스턴스 데이터(ABox) RDF 파일."),
) -> None:
    """인스턴스 데이터(ABox) RDF 파일을 로드합니다. 기존 데이터에 추가됩니다."""
    _run_load(file, "data")


# ── status command ───────────────────────────────────────────────────────────

@app.command()
def status() -> None:
    """그래프 스토어 연결 및 데이터 로드 상태를 표시합니다."""
    from ontorag.stores.fuseki import FusekiStore

    store = FusekiStore.from_env()
    try:
        s = asyncio.run(store.status())
    except Exception as exc:
        console.print(f"[red]Error:[/] 상태 조회 실패 — {exc}")
        raise typer.Exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("Store type", s.store_type)
    table.add_row(
        "Connected",
        "[green]✓ connected[/]" if s.connected else "[red]✗ disconnected[/]",
    )
    if s.connected:
        schema_str = (
            f"[green]loaded[/] ({s.triple_count} total)"
            if s.schema_loaded
            else "[yellow]not loaded[/]"
        )
        data_str = (
            "[green]loaded[/]" if s.data_loaded else "[yellow]not loaded[/]"
        )
        table.add_row("Schema (TBox)", schema_str)
        table.add_row("Data (ABox)", data_str)

    console.print(table)
    if s.connected and not s.schema_loaded:
        console.print("\n[dim]힌트: ontorag load schema <FILE> 로 스키마를 먼저 로드하세요.[/]")


# ── serve command ────────────────────────────────────────────────────────────

@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="바인딩할 호스트."),
    port: int = typer.Option(8000, help="리스닝 포트."),
    reload: bool = typer.Option(False, help="개발 모드 (코드 변경 시 자동 재시작)."),
) -> None:
    """ontorag API 서버를 시작합니다."""
    import uvicorn

    uvicorn.run(
        "ontorag.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
