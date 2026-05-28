"""`ontorag bayes` CLI — store, inspect, query, and learn Bayesian networks (v0.7).

Commands:
    bayes load <network.ttl>     Store a full bn: network (structure + CPTs).
    bayes show                   Print the stored network.
    bayes posterior ...          P(query | evidence) over the stored network.
    bayes mpe ...                Most probable explanation given evidence.
    bayes clear                  Drop the stored network.
    bayes learn-cpt <struct.ttl> Estimate CPTs from ABox data (v0.7.4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rdflib import Graph
from rich.console import Console
from rich.table import Table

bayes_app = typer.Typer(help="베이지안 네트워크 저장·조회·추론·학습 (v0.7).")
console = Console()


def _get_store():
    from ontorag.stores.factory import create_store

    store = create_store()
    getter = getattr(store, "get_bayes_network", None)
    if getter is None:
        console.print(
            f"[red]Error:[/] 활성 그래프 스토어({type(store).__name__})는 "
            "베이지안 기능을 지원하지 않습니다."
        )
        raise typer.Exit(1)
    return store


def _parse_evidence(pairs: list[str]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            console.print(f"[red]Error:[/] --evidence 형식은 VAR=STATE 입니다: {pair!r}")
            raise typer.Exit(1)
        key, _, value = pair.partition("=")
        evidence[key.strip()] = value.strip()
    return evidence


@bayes_app.command("load")
def bayes_load(
    file: Path = typer.Argument(..., help="bn: 어휘로 작성된 네트워크 TTL 파일."),
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
) -> None:
    """Store a full Bayesian network (structure + CPTs) from a bn: TTL file."""
    from ontorag.core.bayes import graph_to_network

    g = Graph()
    g.parse(str(file), format="turtle")
    network = graph_to_network(g)
    if network is None:
        console.print(f"[red]Error:[/] {file} 에 bn:Variable 이 없습니다.")
        raise typer.Exit(1)

    store = _get_store()

    async def _go() -> int:
        try:
            return await store.put_bayes_network(network, ontology=ontology)
        finally:
            await store.aclose()

    written = asyncio.run(_go())
    console.print(
        f"[green]저장됨[/] — 변수 {len(network.variables)}개, CPD {len(network.cpds)}개 "
        f"({written} statements)."
    )


@bayes_app.command("show")
def bayes_show(
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
) -> None:
    """Print the stored Bayesian network."""
    store = _get_store()

    async def _go():
        try:
            return await store.get_bayes_network(ontology=ontology)
        finally:
            await store.aclose()

    network = asyncio.run(_go())
    if network is None:
        console.print("[yellow]저장된 베이지안 네트워크가 없습니다.[/]")
        return

    console.print(f"\n[bold]{network.name or '(이름 없음)'}[/]\n")
    var_table = Table(title="변수", show_header=True, header_style="bold", box=None)
    var_table.add_column("URI", style="cyan")
    var_table.add_column("상태")
    var_table.add_column("represents")
    for v in network.variables:
        var_table.add_row(v.uri, ", ".join(v.states), v.represents or "—")
    console.print(var_table)

    cpd_table = Table(title="CPD", show_header=True, header_style="bold", box=None)
    cpd_table.add_column("변수", style="cyan")
    cpd_table.add_column("조건(evidence)")
    cpd_table.add_column("열 수", justify="right")
    for c in network.cpds:
        cpd_table.add_row(
            c.variable, ", ".join(c.evidence) or "(prior)", str(len(c.values[0]))
        )
    console.print(cpd_table)


@bayes_app.command("posterior")
def bayes_posterior(
    query: list[str] = typer.Option(
        ..., "--query", "-q", help="사후확률을 구할 변수 (URI 또는 라벨). 반복 가능."
    ),
    evidence: list[str] = typer.Option(
        [], "--evidence", "-e", help="관측 증거 VAR=STATE. 반복 가능."
    ),
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
) -> None:
    """Compute P(query | evidence) over the stored network."""
    from ontorag.bayes.engine import BayesianEngine, BayesianEngineError

    store = _get_store()
    ev = _parse_evidence(evidence)

    async def _go():
        try:
            network = await store.get_bayes_network(ontology=ontology)
            if network is None:
                return None
            return await BayesianEngine(network).compute_posterior(ev, list(query))
        finally:
            await store.aclose()

    try:
        posterior = asyncio.run(_go())
    except BayesianEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    if posterior is None:
        console.print("[yellow]저장된 베이지안 네트워크가 없습니다.[/]")
        raise typer.Exit(1)

    for var, dist in posterior.items():
        table = Table(title=f"P({var} | evidence)", show_header=True, box=None)
        table.add_column("상태", style="cyan")
        table.add_column("확률", justify="right")
        for state, prob in sorted(dist.items(), key=lambda kv: kv[1], reverse=True):
            table.add_row(state, f"{prob:.4f}")
        console.print(table)


@bayes_app.command("mpe")
def bayes_mpe(
    evidence: list[str] = typer.Option(
        [], "--evidence", "-e", help="관측 증거 VAR=STATE. 반복 가능."
    ),
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
) -> None:
    """Most probable explanation (MPE) of all non-evidence variables."""
    from ontorag.bayes.engine import BayesianEngine, BayesianEngineError

    store = _get_store()
    ev = _parse_evidence(evidence)

    async def _go():
        try:
            network = await store.get_bayes_network(ontology=ontology)
            if network is None:
                return None
            return await BayesianEngine(network).mpe(ev)
        finally:
            await store.aclose()

    try:
        assignment = asyncio.run(_go())
    except BayesianEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    if assignment is None:
        console.print("[yellow]저장된 베이지안 네트워크가 없습니다.[/]")
        raise typer.Exit(1)

    table = Table(title="최대확률설명 (MPE)", show_header=True, box=None)
    table.add_column("변수", style="cyan")
    table.add_column("가장 가능성 높은 상태")
    for var, state in assignment.items():
        table.add_row(var, state)
    console.print(table)


@bayes_app.command("clear")
def bayes_clear(
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
) -> None:
    """Drop the stored Bayesian network for this scope."""
    store = _get_store()

    async def _go() -> int:
        try:
            return await store.clear_bayes_network(ontology=ontology)
        finally:
            await store.aclose()

    removed = asyncio.run(_go())
    console.print(f"[green]삭제됨[/] — {removed} statements/nodes 제거.")


@bayes_app.command("learn-cpt")
def bayes_learn_cpt(
    file: Path = typer.Argument(..., help="bn:dependsOn 으로 작성된 구조(DAG) TTL 파일."),
    target_class: str = typer.Option(
        ..., "--class", "-c", help="관측 행이 되는 인스턴스의 클래스 URI."
    ),
    ontology: Optional[str] = typer.Option(None, "--ontology", help="온톨로지 스코프."),
    estimator: str = typer.Option(
        "bayes", "--estimator", help="bayes(BDeu) 또는 mle."
    ),
    save: bool = typer.Option(
        False, "--save", help="학습된 네트워크를 probabilistic 그래프에 저장."
    ),
) -> None:
    """Estimate CPTs from ABox data and (optionally) store the learned network."""
    from ontorag.bayes.engine import BayesianEngineError
    from ontorag.bayes.learn import learn_cpts
    from ontorag.core.bayes import graph_to_structure

    if estimator not in ("bayes", "mle"):
        console.print("[red]Error:[/] --estimator 는 bayes 또는 mle 여야 합니다.")
        raise typer.Exit(1)

    g = Graph()
    g.parse(str(file), format="turtle")
    structure = graph_to_structure(g)
    if structure is None:
        console.print(f"[red]Error:[/] {file} 에 bn:Variable 이 없습니다.")
        raise typer.Exit(1)

    store = _get_store()

    async def _go():
        try:
            network, n = await learn_cpts(
                store,
                structure,
                target_class,
                ontology=ontology,
                estimator=estimator,  # type: ignore[arg-type]
            )
            if save:
                await store.put_bayes_network(network, ontology=ontology)
            return network, n
        finally:
            await store.aclose()

    try:
        network, n_obs = asyncio.run(_go())
    except BayesianEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print(
        f"[green]학습 완료[/] — 관측 {n_obs}건에서 CPD {len(network.cpds)}개 추정"
        + (" (저장됨)." if save else " (미저장 — --save 로 저장).")
    )
    for c in network.cpds:
        cond = f" | {', '.join(c.evidence)}" if c.evidence else ""
        console.print(f"  • P({c.variable}{cond}) — {len(c.values[0])} 열")
