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

config_app = typer.Typer(help="LLM 및 스토어 설정을 관리합니다.")
app.add_typer(config_app, name="config")


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


# ── config subcommands ───────────────────────────────────────────────────────

def _env_path() -> Path:
    return Path(".env")


@config_app.command("set")
def config_set(
    provider: Optional[str] = typer.Option(None, help="LLM 제공자 (anthropic | openai | ollama)."),
    api_key: Optional[str] = typer.Option(None, help="LLM API 키."),
    model: Optional[str] = typer.Option(None, help="사용할 모델 이름."),
    fuseki_url: Optional[str] = typer.Option(None, help="Fuseki SPARQL 엔드포인트 URL."),
    ollama_url: Optional[str] = typer.Option(None, help="Ollama base URL."),
) -> None:
    """LLM 및 스토어 설정을 .env 파일에 저장합니다."""
    from dotenv import set_key

    env_file = _env_path()
    if not env_file.exists():
        env_file.touch()

    changes: list[str] = []

    if provider is not None:
        valid = {"anthropic", "openai", "ollama"}
        if provider not in valid:
            console.print(f"[red]Error:[/] provider는 {valid} 중 하나여야 합니다.")
            raise typer.Exit(1)
        set_key(str(env_file), "LLM_PROVIDER", provider)
        changes.append(f"LLM_PROVIDER={provider}")

    if api_key is not None:
        effective_provider = provider or _current_provider(env_file)
        key_name = "ANTHROPIC_API_KEY" if effective_provider == "anthropic" else "OPENAI_API_KEY"
        set_key(str(env_file), key_name, api_key)
        changes.append(f"{key_name}=***")

    if model is not None:
        set_key(str(env_file), "LLM_MODEL", model)
        changes.append(f"LLM_MODEL={model}")

    if fuseki_url is not None:
        set_key(str(env_file), "FUSEKI_URL", fuseki_url)
        changes.append(f"FUSEKI_URL={fuseki_url}")

    if ollama_url is not None:
        set_key(str(env_file), "OLLAMA_BASE_URL", ollama_url)
        changes.append(f"OLLAMA_BASE_URL={ollama_url}")

    if not changes:
        console.print("[yellow]변경 사항 없음.[/] 옵션을 지정하세요. (예: --provider anthropic)")
        return

    for change in changes:
        console.print(f"[green]✓[/] {change}")
    console.print(f"[dim].env 파일에 저장했습니다: {env_file.resolve()}[/]")


def _current_provider(env_file: Path) -> str:
    from dotenv import dotenv_values
    vals = dotenv_values(str(env_file))
    return vals.get("LLM_PROVIDER", "anthropic")


@config_app.command("show")
def config_show() -> None:
    """현재 설정을 표시합니다."""
    from dotenv import dotenv_values

    env_file = _env_path()
    vals = dotenv_values(str(env_file)) if env_file.exists() else {}

    import os
    effective = {**vals, **{k: v for k, v in os.environ.items() if v}}

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("설정", style="cyan")
    table.add_column("값")
    table.add_column("출처", style="dim")

    keys = [
        ("LLM_PROVIDER", "LLM 제공자"),
        ("LLM_MODEL", "모델"),
        ("ANTHROPIC_API_KEY", "Anthropic API 키"),
        ("OPENAI_API_KEY", "OpenAI API 키"),
        ("OLLAMA_BASE_URL", "Ollama URL"),
        ("FUSEKI_URL", "Fuseki URL"),
        ("FUSEKI_DATASET", "Fuseki 데이터셋"),
    ]
    sensitive = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}

    for env_key, label in keys:
        value = effective.get(env_key, "")
        if not value:
            table.add_row(label, "[dim](미설정)[/]", "")
            continue
        display = f"{value[:8]}..." if env_key in sensitive and len(value) > 8 else value
        source = ".env" if env_key in vals else "환경변수"
        table.add_row(label, display, source)

    console.print(table)
    if not env_file.exists():
        console.print(f"\n[dim].env 파일 없음. ontorag config set --provider anthropic 으로 생성하세요.[/]")


# ── chat command ─────────────────────────────────────────────────────────────

@app.command()
def chat() -> None:
    """온톨로지 Q&A 대화 세션을 시작합니다 (REPL)."""
    from rich.markup import escape
    from rich.panel import Panel

    from ontorag.llm.factory import get_llm_provider
    from ontorag.stores.fuseki import FusekiStore

    try:
        store = FusekiStore.from_env()
        llm = get_llm_provider()
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        console.print("[dim]ontorag config set 으로 설정하세요.[/]")
        raise typer.Exit(1)

    console.print(Panel(
        "[bold]ontorag chat[/]\n"
        "[dim]종료: Ctrl+C 또는 'exit'[/]",
        border_style="blue",
    ))

    async def run_turn(message: str) -> None:
        from ontorag.chat.agent import AgentLoop
        agent = AgentLoop(store, llm)
        async for event in agent.run(message):
            _render_event(event)

    while True:
        try:
            user_input = console.input("[bold blue]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]종료합니다.[/]")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "종료"}:
            console.print("[dim]종료합니다.[/]")
            break

        try:
            asyncio.run(run_turn(user_input))
        except Exception as exc:
            console.print(f"[red]Error:[/] {escape(str(exc))}")


def _render_event(event: dict) -> None:
    """SSE 이벤트를 터미널에 렌더링합니다."""
    from rich.markup import escape

    etype = event.get("type", "")

    if etype == "thinking":
        console.print(f"[dim]  ⟳ {escape(str(event.get('content', '')))}[/]")
    elif etype == "tool_call":
        tool = event.get("tool", "")
        content = event.get("content", {})
        console.print(f"[cyan]  → {tool}[/] [dim]{escape(str(content))}[/]")
    elif etype == "tool_result":
        tool = event.get("tool", "")
        console.print(f"[green]  ← {tool}[/] [dim]결과 수신[/]")
    elif etype == "text":
        console.print(event.get("content", ""))
    elif etype == "done":
        console.print()
    elif etype == "error":
        console.print(f"[red]Error:[/] {escape(str(event.get('content', '')))}")


if __name__ == "__main__":
    app()
