from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import typer
from click.exceptions import UsageError
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from ontorag.cli_bayes import bayes_app
from ontorag.cli_eval import eval_app
from ontorag.cli_learn import learn_app

load_dotenv()  # load .env from cwd before any command runs (override=False keeps real env vars)

console = Console()

app = typer.Typer(
    name="ontorag",
    help="Ontology-aware RAG framework.",
    no_args_is_help=True,
)

class _LoadGroup(typer.core.TyperGroup):
    """Route an unknown first token to the hidden ``auto`` command.

    ``load`` is a group with ``schema``/``data`` sub-commands, so Click would
    reject ``ontorag load <PATH>`` ("No such command '<path>'") — it resolves
    the first positional as a sub-command name. Fall back to ``auto`` (which
    takes the path as its argument) when the token is neither a known
    sub-command nor an option. This makes ``load <FILE|DIR>`` work alongside
    ``load schema``/``load data``.
    """

    def resolve_command(self, ctx, args):  # type: ignore[override]
        try:
            return super().resolve_command(ctx, args)
        except UsageError:
            if not args or args[0].startswith("-"):
                raise  # no token, or an option (e.g. --help) — keep Click's error
            return super().resolve_command(ctx, ["auto", *args])


load_app = typer.Typer(
    cls=_LoadGroup,
    no_args_is_help=True,
    help="RDF 파일/디렉토리를 그래프 스토어에 로드합니다 (load <FILE|DIR>).",
)
app.add_typer(load_app, name="load")

clear_app = typer.Typer(help="그래프 스토어의 TBox/ABox 데이터를 삭제합니다.")
app.add_typer(clear_app, name="clear")

config_app = typer.Typer(help="LLM 및 스토어 설정을 관리합니다.")
app.add_typer(config_app, name="config")

history_app = typer.Typer(help="채팅 대화 기록을 조회/삭제합니다.")
app.add_typer(history_app, name="history")
app.add_typer(learn_app, name="learn")

dump_app = typer.Typer(help="그래프 스토어의 TBox/ABox를 파일로 덤프합니다.")
app.add_typer(dump_app, name="dump")

app.add_typer(eval_app, name="eval")

embed_app = typer.Typer(help="그래프 임베딩을 생성합니다 (Fuseki+Qdrant 또는 Neo4j).")
app.add_typer(embed_app, name="embed")

app.add_typer(bayes_app, name="bayes")


# ── load subcommands ─────────────────────────────────────────────────────────


def _run_load(
    file: Path,
    mode: Literal["schema", "data", "auto"],
    replace: bool = False,
    ontology: str | None = None,
) -> None:
    """Parse + upload RDF file with a Rich spinner and result summary."""
    from ontorag.stores.factory import create_store

    if not file.exists():
        console.print(f"[red]Error:[/] 파일을 찾을 수 없습니다: {file}")
        raise typer.Exit(1)

    store = create_store()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"{file.name} 로딩 중...", total=None)
        try:
            result = asyncio.run(
                store.load_rdf(str(file), mode, replace=replace, ontology=ontology)
            )
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Error:[/] Fuseki 연결 실패 — {exc}")
            console.print(
                "[dim]Fuseki가 실행 중인지 확인하세요: docker compose up fuseki[/]"
            )
            raise typer.Exit(1)

    mode_label = {"schema": "스키마(TBox)", "data": "데이터(ABox)"}.get(
        result.mode, result.mode
    )
    action = "교체했습니다" if replace and result.mode == "data" else "로드했습니다"
    console.print(
        f"[green]✓[/] [bold]{result.triples_loaded:,}[/] 트리플을 "
        f"[bold]{mode_label}[/]로 {action} ← {file.name}"
    )


def _run_load_directory(
    root: Path,
    ontology: str | None = None,
    replace: bool = False,
    recursive: bool = True,
) -> None:
    """Scan a directory and load its RDF files, with a progress bar + summary.

    Maps sub-directories to ontology scopes (or flat-merges under --ontology),
    loads schema-before-data per scope, and prints a Rich summary table. Exits
    with code 1 if any file failed. See docs/design/directory-loader.md.
    """
    from ontorag.core.batch_loader import load_directory
    from ontorag.stores.base import FileLoadOutcome
    from ontorag.stores.factory import create_store

    store = create_store()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        # Total is unknown until the scan runs inside load_directory; start
        # indeterminate and set the total on the first callback.
        task = progress.add_task(f"{root.name}/ 스캔 중...", total=None)

        def _on_file(outcome: FileLoadOutcome) -> None:
            icon = {"loaded": "✓", "skipped": "−", "failed": "✗"}[outcome.status]
            progress.advance(task)
            progress.console.log(
                f"  [dim]{icon}[/] {outcome.source}"
                + (f" [dim]({outcome.reason})[/]" if outcome.reason else "")
            )

        try:
            result = asyncio.run(
                load_directory(
                    store,
                    root,
                    ontology=ontology,
                    replace=replace,
                    recursive=recursive,
                    on_file=_on_file,
                )
            )
        except (ValueError, NotADirectoryError) as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Error:[/] 그래프 스토어 연결 실패 — {exc}")
            raise typer.Exit(1)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("결과", style="cyan")
    table.add_column("수", justify="right")
    table.add_row("로드됨 (loaded)", str(result.loaded))
    table.add_row("건너뜀 (skipped)", str(result.skipped))
    table.add_row("실패 (failed)", str(result.failed))
    table.add_row("총 트리플", f"{result.total_triples:,}")
    console.print(table)

    if result.failed or result.skipped:
        for o in result.outcomes:
            if o.status in ("failed", "skipped"):
                console.print(
                    f"  [yellow]{o.status}[/] {o.source}"
                    + (f" — {o.reason}" if o.reason else "")
                )

    summary = (
        f"[green]✓[/] {result.loaded}개 파일, "
        f"[bold]{result.total_triples:,}[/] 트리플 로드 ← {root}/"
    )
    console.print(summary)
    if result.failed:
        raise typer.Exit(1)


@load_app.command("auto", hidden=True)
def load_auto(
    path: Path = typer.Argument(..., help="RDF 파일 또는 디렉토리 경로."),
    ontology: Optional[str] = typer.Option(
        None, "--ontology", help="로드할 온톨로지 id (미지정 시 기본 그래프)."
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="ABox 재로드 시 교체 (디렉토리: 스코프별 첫 data 파일에서만).",
    ),
    recursive: bool = typer.Option(
        True,
        "--recursive/--no-recursive",
        help="디렉토리를 하위까지 재귀 스캔 (기본 on).",
    ),
) -> None:
    """RDF 파일 또는 디렉토리를 자동 감지 모드로 로드합니다.

    파일이면 TBox/ABox를 자동 판별해 로드하고, 디렉토리면 하위 RDF 파일을
    스캔해 온톨로지별로 로드합니다 (서브디렉토리명 = ontology id;
    --ontology 지정 시 플랫 병합). `ontorag load <FILE|DIR>`로 호출됩니다.
    """
    if not path.exists():
        console.print(f"[red]Error:[/] 경로를 찾을 수 없습니다: {path}")
        raise typer.Exit(1)
    if path.is_dir():
        _run_load_directory(
            path, ontology=ontology, replace=replace, recursive=recursive
        )
    else:
        _run_load(path, "auto", replace=replace, ontology=ontology)


@load_app.command("schema")
def load_schema(
    file: Path = typer.Argument(..., help="스키마(TBox) RDF 파일 (클래스/속성 정의)."),
    ontology: Optional[str] = typer.Option(
        None, "--ontology", help="로드할 온톨로지 id (미지정 시 기본 그래프)."
    ),
) -> None:
    """스키마(TBox) RDF 파일을 로드합니다. 기존 스키마를 교체합니다."""
    _run_load(file, "schema", ontology=ontology)


@load_app.command("data")
def load_data(
    file: Path = typer.Argument(..., help="인스턴스 데이터(ABox) RDF 파일."),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="기존 데이터 그래프를 완전히 교체합니다 (기본값: 추가).",
    ),
    ontology: Optional[str] = typer.Option(
        None, "--ontology", help="로드할 온톨로지 id (미지정 시 기본 그래프)."
    ),
) -> None:
    """인스턴스 데이터(ABox) RDF 파일을 로드합니다.

    기본값은 기존 데이터에 추가(append)입니다.
    --replace 플래그를 사용하면 기존 ABox를 완전히 교체합니다.
    """
    _run_load(file, "data", replace=replace, ontology=ontology)


# ── clear subcommands ────────────────────────────────────────────────────────


def _run_clear(target: str) -> None:
    """Drop named graph(s) from the store with confirmation prompt."""
    from ontorag.stores.factory import create_store

    label = {
        "schema": "스키마(TBox)",
        "data": "데이터(ABox)",
        "all": "전체(TBox + ABox)",
    }[target]
    confirmed = typer.confirm(f"[경고] {label}을 삭제합니다. 계속하시겠습니까?")
    if not confirmed:
        console.print("[dim]취소했습니다.[/]")
        raise typer.Exit(0)

    store = create_store()
    try:
        removed = asyncio.run(store.clear_graph(target))  # type: ignore[arg-type]
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    for graph, count in removed.items():
        graph_label = "스키마(TBox)" if graph == "schema" else "데이터(ABox)"
        console.print(f"[green]✓[/] {graph_label} 삭제 완료 — {count:,} 트리플 제거")


@clear_app.command("schema")
def clear_schema() -> None:
    """스키마(TBox) 그래프를 삭제합니다."""
    _run_clear("schema")


@clear_app.command("data")
def clear_data() -> None:
    """인스턴스 데이터(ABox) 그래프를 삭제합니다."""
    _run_clear("data")


@clear_app.command("all")
def clear_all() -> None:
    """스키마(TBox)와 인스턴스 데이터(ABox)를 모두 삭제합니다."""
    _run_clear("all")


# ── dump subcommands ─────────────────────────────────────────────────────────

_DUMP_FORMATS = ["ttl", "json", "jsonl", "xlsx"]
_DUMP_EXT = {"ttl": "ttl", "json": "json", "jsonl": "jsonl", "xlsx": "xlsx"}


def _run_dump(target: str, fmt: str, output: Path | None) -> None:
    """Export a named graph to a local file with a Rich spinner."""
    from ontorag.stores.factory import create_store

    if fmt not in _DUMP_FORMATS:
        console.print(
            f"[red]Error:[/] 지원하지 않는 포맷입니다: {fmt!r}. "
            f"허용값: {', '.join(_DUMP_FORMATS)}"
        )
        raise typer.Exit(1)

    store = create_store()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"{target} 덤프 중 ({fmt})...", total=None)
        try:
            data = asyncio.run(store.dump_graph(target, fmt))  # type: ignore[arg-type]
        except ImportError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Error:[/] 덤프 실패 — {exc}")
            console.print(
                "[dim]Fuseki가 실행 중인지 확인하세요: docker compose up fuseki[/]"
            )
            raise typer.Exit(1)

    if output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(f"ontorag_{target}_{ts}.{_DUMP_EXT[fmt]}")

    output.write_bytes(data)
    console.print(f"[green]✓[/] [bold]{len(data):,}[/] 바이트 → [bold]{output}[/]")


@dump_app.command("schema")
def dump_schema(
    fmt: str = typer.Option(
        "ttl", "--format", "-f", help="출력 포맷: ttl | json | jsonl | xlsx"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="저장할 파일 경로 (기본값: 자동 생성)"
    ),
) -> None:
    """TBox(스키마) 그래프를 파일로 덤프합니다."""
    _run_dump("schema", fmt, output)


@dump_app.command("data")
def dump_data(
    fmt: str = typer.Option(
        "ttl", "--format", "-f", help="출력 포맷: ttl | json | jsonl | xlsx"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="저장할 파일 경로 (기본값: 자동 생성)"
    ),
) -> None:
    """ABox(데이터) 그래프를 파일로 덤프합니다."""
    _run_dump("data", fmt, output)


@dump_app.command("all")
def dump_all(
    fmt: str = typer.Option(
        "ttl", "--format", "-f", help="출력 포맷: ttl | json | jsonl | xlsx"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="저장할 파일 경로 (기본값: 자동 생성)"
    ),
) -> None:
    """TBox + ABox 전체를 파일로 덤프합니다."""
    _run_dump("all", fmt, output)


# ── embed subcommand ─────────────────────────────────────────────────────────


@embed_app.callback(invoke_without_command=True)
def embed_run(
    ctx: typer.Context,
    mode: str = typer.Option(
        "both",
        "--mode",
        help="임베딩 모드: structural | textual | both (기본값: both)",
    ),
    ontology: Optional[str] = typer.Option(
        None,
        "--ontology",
        help="임베딩할 온톨로지 id (미지정 시 전체). 스코프 빌드는 다른 온톨로지 임베딩을 보존합니다.",
    ),
) -> None:
    """그래프 임베딩을 생성합니다.

    Fuseki 백엔드: FastRP(pure-Python) + Qdrant 벡터 스토어 사용.
    Neo4j 백엔드: GDS FastRP + 네이티브 벡터 인덱스 사용.

    --mode structural : 구조적 임베딩만 생성
    --mode textual    : EmbeddingProvider 의미적 임베딩만 생성
    --mode both       : 구조적 + 의미적 모두 생성 (기본값)

    실행 후 find_similar MCP 툴로 유사 엔티티를 검색할 수 있습니다.
    """
    if ctx.invoked_subcommand is not None:
        return

    from ontorag.stores.factory import create_store

    valid_modes = {"structural", "textual", "both"}
    if mode not in valid_modes:
        console.print(
            f"[red]Error:[/] --mode는 {valid_modes} 중 하나여야 합니다. "
            f"입력값: {mode!r}"
        )
        raise typer.Exit(1)

    store = create_store()
    # Wrap the whole store lifetime so every exit path (including the early
    # capability/provider guards below) releases the store's connection — a
    # bare guard `raise` would otherwise leak the Fuseki httpx client.
    try:
        # Verify the store supports embeddings.
        build_fn = getattr(store, "build_embeddings", None)
        if build_fn is None:
            console.print(
                "[red]Error:[/] 이 그래프 스토어는 임베딩을 지원하지 않습니다."
            )
            raise typer.Exit(1)

        # For textual mode, resolve the embedding provider eagerly so cred errors
        # surface before the long GDS structural step (when mode == "both").
        embedding_provider = None
        if mode in ("textual", "both"):
            try:
                from ontorag.llm.embedding import get_embedding_provider  # noqa: PLC0415

                embedding_provider = get_embedding_provider()
            except (ValueError, KeyError) as exc:
                console.print(
                    f"[red]Error:[/] 의미적 임베딩 제공자 초기화 실패 — {exc}"
                )
                console.print(
                    "[dim]EMBEDDING_PROVIDER 및 관련 API 키가 설정됐는지 확인하세요.[/]"
                )
                raise typer.Exit(1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task(f"임베딩 생성 중 (mode={mode})...", total=None)
            try:
                result = asyncio.run(
                    store.build_embeddings(  # type: ignore[union-attr]
                        mode, embedding_provider, ontology=ontology
                    )
                )
            except Exception as exc:
                console.print(f"[red]Error:[/] 임베딩 생성 실패 — {exc}")
                raise typer.Exit(1)
    finally:
        try:
            asyncio.run(store.aclose())
        except Exception:
            pass

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("모드", style="cyan")
    table.add_column("작성된 노드 수", style="green")
    for embed_mode, count in result.items():
        label = "구조적 (FastRP)" if embed_mode == "structural" else "의미적 (텍스트)"
        table.add_row(label, f"{count:,}")
    console.print(table)
    console.print(
        "\n[dim]ontorag serve 후 POST /tools/similar 로 유사 엔티티를 검색하세요.[/]"
    )


# ── status command ───────────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """그래프 스토어 연결 및 데이터 로드 상태를 표시합니다."""
    from ontorag.stores.factory import create_store

    store = create_store()
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
        schema_str = "[green]loaded[/]" if s.schema_loaded else "[yellow]not loaded[/]"
        data_str = "[green]loaded[/]" if s.data_loaded else "[yellow]not loaded[/]"
        table.add_row("Schema (TBox)", schema_str)
        table.add_row("Data (ABox)", data_str)
        if s.triple_count is not None:
            table.add_row("Total triples", str(s.triple_count))

    console.print(table)
    if s.connected and not s.schema_loaded:
        console.print(
            "\n[dim]힌트: ontorag load schema <FILE> 로 스키마를 먼저 로드하세요.[/]"
        )


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
    provider: Optional[str] = typer.Option(
        None, help="LLM 제공자 (anthropic | openai | ollama)."
    ),
    api_key: Optional[str] = typer.Option(None, help="LLM API 키."),
    model: Optional[str] = typer.Option(None, help="사용할 모델 이름."),
    fuseki_url: Optional[str] = typer.Option(
        None, help="Fuseki SPARQL 엔드포인트 URL."
    ),
    ollama_url: Optional[str] = typer.Option(None, help="Ollama base URL."),
    graph_store: Optional[str] = typer.Option(
        None, help="그래프 백엔드 (fuseki | neo4j)."
    ),
    neo4j_url: Optional[str] = typer.Option(
        None, help="Neo4j bolt URI (예: bolt://localhost:7687)."
    ),
    neo4j_user: Optional[str] = typer.Option(None, help="Neo4j 사용자명."),
    neo4j_password: Optional[str] = typer.Option(None, help="Neo4j 비밀번호."),
    neo4j_database: Optional[str] = typer.Option(None, help="Neo4j 데이터베이스명."),
    qdrant_url: Optional[str] = typer.Option(
        None, help="Qdrant URL (Fuseki 백엔드의 find_similar 벡터 스토어)."
    ),
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
        from dotenv import dotenv_values

        effective_provider = provider or dotenv_values(str(env_file)).get(
            "LLM_PROVIDER", "anthropic"
        )
        key_name = (
            "ANTHROPIC_API_KEY"
            if effective_provider == "anthropic"
            else "OPENAI_API_KEY"
        )
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

    if graph_store is not None:
        from ontorag.stores.factory import VALID_BACKENDS  # noqa: PLC0415

        if graph_store not in VALID_BACKENDS:
            console.print(
                f"[red]Error:[/] --graph-store는 {set(VALID_BACKENDS)} 중 "
                "하나여야 합니다."
            )
            raise typer.Exit(1)
        set_key(str(env_file), "GRAPH_STORE", graph_store)
        changes.append(f"GRAPH_STORE={graph_store}")

    if neo4j_url is not None:
        set_key(str(env_file), "NEO4J_URI", neo4j_url)
        changes.append(f"NEO4J_URI={neo4j_url}")

    if neo4j_user is not None:
        set_key(str(env_file), "NEO4J_USER", neo4j_user)
        changes.append(f"NEO4J_USER={neo4j_user}")

    if neo4j_password is not None:
        set_key(str(env_file), "NEO4J_PASSWORD", neo4j_password)
        changes.append("NEO4J_PASSWORD=***")

    if neo4j_database is not None:
        set_key(str(env_file), "NEO4J_DATABASE", neo4j_database)
        changes.append(f"NEO4J_DATABASE={neo4j_database}")

    if qdrant_url is not None:
        set_key(str(env_file), "QDRANT_URL", qdrant_url)
        changes.append(f"QDRANT_URL={qdrant_url}")

    if not changes:
        console.print(
            "[yellow]변경 사항 없음.[/] 옵션을 지정하세요. (예: --provider anthropic)"
        )
        return

    for change in changes:
        console.print(f"[green]✓[/] {change}")
    console.print(f"[dim].env 파일에 저장했습니다: {env_file.resolve()}[/]")


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
        ("GRAPH_STORE", "그래프 백엔드"),
        ("FUSEKI_URL", "Fuseki URL"),
        ("FUSEKI_DATASET", "Fuseki 데이터셋"),
        ("NEO4J_URI", "Neo4j URI"),
        ("NEO4J_USER", "Neo4j 사용자"),
        ("NEO4J_PASSWORD", "Neo4j 비밀번호"),
        ("NEO4J_DATABASE", "Neo4j DB"),
        ("QDRANT_URL", "Qdrant URL"),
        ("EMBEDDING_PROVIDER", "임베딩 제공자"),
    ]
    sensitive = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NEO4J_PASSWORD"}

    for env_key, label in keys:
        value = effective.get(env_key, "")
        if not value:
            table.add_row(label, "[dim](미설정)[/]", "")
            continue
        display = (
            f"{value[:8]}..." if env_key in sensitive and len(value) > 8 else value
        )
        source = ".env" if env_key in vals else "환경변수"
        table.add_row(label, display, source)

    console.print(table)
    if not env_file.exists():
        console.print(
            "\n[dim].env 파일 없음. ontorag config set --provider anthropic 으로 생성하세요.[/]"
        )


# ── chat command ─────────────────────────────────────────────────────────────


@app.command()
def chat(
    message: Optional[str] = typer.Argument(
        None, help="첫 메시지 (생략 시 REPL 프롬프트로 진입)"
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", "-r", help="이전 대화 ID로 이어서 시작."
    ),
) -> None:
    """온톨로지 Q&A 대화 세션을 시작합니다 (REPL).

    첫 메시지를 인자로 전달할 수도 있습니다: ontorag chat "질문"
    이전 대화를 이어가려면: ontorag chat --resume <대화ID>
    """
    from rich.markup import escape
    from rich.panel import Panel

    from ontorag.chat import store as chat_store
    from ontorag.llm.factory import get_llm_provider
    from ontorag.stores.factory import create_store

    try:
        llm = get_llm_provider()
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        console.print("[dim]ontorag config set 으로 설정하세요.[/]")
        raise typer.Exit(1)

    console.print(
        Panel(
            "[bold]ontorag chat[/]\n"
            "[dim]종료: Ctrl+C 또는 'exit'  |  대화 기록: ontorag history list[/]",
            border_style="blue",
        )
    )

    async def _repl(initial: Optional[str]) -> None:
        # store는 async 컨텍스트 안에서 생성 — httpx 클라이언트가 현재 루프에 바인딩됨
        store = create_store()

        from ontorag.chat.agent import AgentLoop, _format_schema_for_prompt

        # 세션 로드 또는 신규 생성
        initial_history: list = []
        if resume:
            loaded = await chat_store.get_history(resume)
            if loaded:
                initial_history = loaded
                session_id = resume
                display = chat_store.extract_display_messages(loaded)
                console.print(f"[dim]이전 대화 복원 — {len(display)}개 메시지[/]")
                # 마지막 3개 교환을 요약해 컨텍스트 제공
                for m in display[-3:]:
                    prefix = (
                        "[bold blue]>[/]"
                        if m["role"] == "user"
                        else "[bold green]AI[/]"
                    )
                    text = m["text"][:120] + ("…" if len(m["text"]) > 120 else "")
                    console.print(f"  {prefix} {escape(text)}")
                console.print("[dim]  ──────────────────────────────[/]")
            else:
                console.print(
                    f"[yellow]경고:[/] 대화 ID '{resume}'를 찾을 수 없습니다. 새 대화를 시작합니다."
                )
                session_id = await chat_store.create_session()
        else:
            session_id = await chat_store.create_session()

        console.print(f"[dim]세션: {session_id}[/]")

        # 세션 시작 시 schema를 한 번만 로드해 system prompt에 주입
        schema_ctx: str | None = None
        try:
            schema = await store.get_schema()
            schema_ctx = _format_schema_for_prompt(schema)
            console.print(f"[dim]스키마 로드됨 ({len(schema.classes)}개 클래스)[/]")
        except Exception as exc:
            console.print(f"[yellow]경고:[/] 스키마 로드 실패 — {exc}")

        agent = AgentLoop(
            store, llm, schema_context=schema_ctx, initial_history=initial_history
        )
        is_first = [
            len(initial_history) == 0
        ]  # 리스트로 감싸 클로저 내부에서 변경 가능

        async def run_turn(msg: str) -> None:
            async for event in agent.run(msg):
                _render_event(event)
            title = msg[:40] if is_first[0] else None
            await chat_store.save_session(session_id, agent._history, title=title)
            is_first[0] = False

        if initial:
            try:
                await run_turn(initial)
            except Exception as exc:
                console.print(f"[red]Error:[/] {escape(str(exc))}")

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
                await run_turn(user_input)
            except Exception as exc:
                console.print(f"[red]Error:[/] {escape(str(exc))}")

    asyncio.run(_repl(message))


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


# ── history subcommands ──────────────────────────────────────────────────────


@history_app.command("list")
def history_list() -> None:
    """저장된 대화 목록을 표시합니다."""
    from ontorag.chat import store as chat_store

    sessions = asyncio.run(chat_store.list_sessions())
    if not sessions:
        console.print("[dim]저장된 대화 없음[/]")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("제목")
    table.add_column("마지막 수정", style="dim", no_wrap=True)

    for s in sessions:
        updated = s["updated"][:16].replace("T", " ")
        table.add_row(s["id"], s["title"], updated)

    console.print(table)
    console.print(
        f"\n[dim]{len(sessions)}개 대화 | ontorag chat --resume <ID> 로 이어서 시작[/]"
    )


@history_app.command("show")
def history_show(
    session_id: str = typer.Argument(..., help="표시할 대화 ID"),
) -> None:
    """저장된 대화 내용을 출력합니다."""
    from rich.markup import escape

    from ontorag.chat import store as chat_store

    history = asyncio.run(chat_store.get_history(session_id))
    if not history:
        console.print(f"[red]Error:[/] 대화 ID '{session_id}'를 찾을 수 없습니다.")
        raise typer.Exit(1)

    messages = chat_store.extract_display_messages(history)
    console.print(f"\n[dim]대화 ID: {session_id} — {len(messages)}개 메시지[/]\n")

    for m in messages:
        if m["role"] == "user":
            console.print(f"[bold blue]>[/] {escape(m['text'])}")
        else:
            console.print(f"[bold green]AI[/] {escape(m['text'])}")
        console.print()


@history_app.command("delete")
def history_delete(
    session_id: str = typer.Argument(..., help="삭제할 대화 ID"),
) -> None:
    """특정 대화를 삭제합니다."""
    from ontorag.chat import store as chat_store

    history = asyncio.run(chat_store.get_history(session_id))
    if not history:
        console.print(f"[red]Error:[/] 대화 ID '{session_id}'를 찾을 수 없습니다.")
        raise typer.Exit(1)

    confirmed = typer.confirm(f"대화 '{session_id}'를 삭제합니까?")
    if not confirmed:
        console.print("[dim]취소했습니다.[/]")
        raise typer.Exit(0)

    asyncio.run(chat_store.delete_session(session_id))
    console.print(f"[green]✓[/] 대화 '{session_id}' 삭제 완료")


@history_app.command("clear")
def history_clear() -> None:
    """저장된 모든 대화를 삭제합니다."""
    from ontorag.chat import store as chat_store

    sessions = asyncio.run(chat_store.list_sessions())
    if not sessions:
        console.print("[dim]삭제할 대화 없음[/]")
        return

    confirmed = typer.confirm(f"저장된 대화 {len(sessions)}개를 모두 삭제합니까?")
    if not confirmed:
        console.print("[dim]취소했습니다.[/]")
        raise typer.Exit(0)

    async def _delete_all() -> None:
        import asyncio as _asyncio

        await _asyncio.gather(*[chat_store.delete_session(s["id"]) for s in sessions])

    asyncio.run(_delete_all())
    console.print(f"[green]✓[/] {len(sessions)}개 대화를 삭제했습니다.")


# ── init command ─────────────────────────────────────────────────────────────


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."),
        help="초기화할 디렉터리 (기본값: 현재 디렉터리).",
    ),
    example: bool = typer.Option(True, help="포켓몬 예제 온톨로지를 포함할지 여부."),
) -> None:
    """새 ontorag 프로젝트를 초기화합니다.

    docker-compose.yml, .env.example 등 필요한 파일을 현재 디렉터리에 생성합니다.
    설치 후 다음 순서로 시작하세요:

    \b
    1. ontorag init
    2. cp .env.example .env  (API 키 설정)
    3. docker compose up -d  (Fuseki 시작)
    4. ontorag load schema examples/pokemon/schema.ttl
    5. ontorag load data   examples/pokemon/data.ttl
    6. ontorag serve        (API 서버 시작)
    7. ontorag chat
    """
    import importlib.resources

    target = directory.resolve()
    target.mkdir(parents=True, exist_ok=True)

    try:
        templates_ref = importlib.resources.files("ontorag._templates")
    except Exception as exc:
        console.print(f"[red]Error:[/] 템플릿 파일을 찾을 수 없습니다 — {exc}")
        raise typer.Exit(1)

    _copy_template(templates_ref, "docker-compose.yml", target / "docker-compose.yml")
    _copy_template(templates_ref, "env.example", target / ".env.example")

    # Fuseki Dockerfile (multi-arch: amd64 + arm64 native)
    fuseki_dir = target / "docker" / "fuseki"
    fuseki_dir.mkdir(parents=True, exist_ok=True)
    fuseki_ref = importlib.resources.files("ontorag._templates") / "docker" / "fuseki"
    _copy_template(fuseki_ref, "Dockerfile", fuseki_dir / "Dockerfile")

    if example:
        ex_dir = target / "examples" / "pokemon"
        ex_dir.mkdir(parents=True, exist_ok=True)
        ex_ref = (
            importlib.resources.files("ontorag._templates") / "examples" / "pokemon"
        )
        _copy_template(ex_ref, "schema.ttl", ex_dir / "schema.ttl")
        _copy_template(ex_ref, "data.ttl", ex_dir / "data.ttl")

    console.print(
        f"\n[green]✓[/] ontorag 프로젝트를 초기화했습니다: [bold]{target}[/]\n"
    )
    console.print("다음 단계:")
    console.print("  1. [cyan]cp .env.example .env[/]        ← API 키 설정")
    console.print("  2. [cyan]docker compose up -d[/]        ← Fuseki 시작 (포트 3030)")
    if example:
        console.print("  3. [cyan]ontorag load schema examples/pokemon/schema.ttl[/]")
        console.print("     [cyan]ontorag load data   examples/pokemon/data.ttl[/]")
    console.print(
        "  4. [cyan]ontorag serve[/]               ← API 서버 시작 (포트 8000)"
    )
    console.print("  5. [cyan]ontorag chat[/]                ← 대화 시작\n")


def _copy_template(ref, filename: str, dest: Path) -> None:
    """importlib.resources 레퍼런스에서 파일을 복사합니다."""
    if dest.exists():
        console.print(f"  [dim]skip[/] {dest.name} (이미 존재)")
        return
    try:
        src = ref / filename
        dest.write_bytes(src.read_bytes())
        console.print(f"  [green]create[/] {dest.relative_to(dest.parent.parent)}")
    except Exception as exc:
        console.print(f"  [yellow]warn[/] {filename} 복사 실패: {exc}")


if __name__ == "__main__":
    app()
