"""`ontorag causal` CLI — causal DAG storage + interventional / counterfactual
queries + structure discovery (v0.8).

Over-claim guard: the causal DAG is user-supplied. ontorag computes do /
counterfactual queries assuming the DAG is correct; it does not validate causal
semantics. Structure discovery (`learn-dag`) produces a *proposal* only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rdflib import Graph
from rich.console import Console
from rich.table import Table

causal_app = typer.Typer(help="인과추론 — 인과 DAG 저장·개입·반사실·구조학습 (v0.8).")
console = Console()


def _store(require_bn: bool = False):
    from ontorag.stores.factory import create_store

    store = create_store()
    if getattr(store, "get_causal_model", None) is None:
        console.print(
            f"[red]Error:[/] 활성 스토어({type(store).__name__})는 인과 기능을 지원하지 않습니다."
        )
        raise typer.Exit(1)
    if require_bn and getattr(store, "get_bayes_network", None) is None:
        console.print("[red]Error:[/] 베이지안 네트워크 기능이 필요합니다.")
        raise typer.Exit(1)
    return store


def _kv(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            console.print(f"[red]Error:[/] VAR=STATE 형식이어야 합니다: {p!r}")
            raise typer.Exit(1)
        k, _, v = p.partition("=")
        out[k.strip()] = v.strip()
    return out


async def _build_engine(store, ontology):
    from ontorag.causal.engine import CausalEngine

    bn = await store.get_bayes_network(ontology=ontology)
    if bn is None:
        console.print(
            "[yellow]저장된 베이지안 네트워크가 없습니다 (인과 계산은 BN으로 정량화됩니다).[/]"
        )
        raise typer.Exit(1)
    causal = await store.get_causal_model(ontology=ontology)
    return CausalEngine(bn, causal)


def _dist_table(title: str, dist: dict[str, dict[str, float]]) -> None:
    for var, d in dist.items():
        t = Table(title=f"{title}: {var}", show_header=True, box=None)
        t.add_column("상태", style="cyan")
        t.add_column("확률", justify="right")
        for state, p in sorted(d.items(), key=lambda kv: kv[1], reverse=True):
            t.add_row(state, f"{p:.4f}")
        console.print(t)


@causal_app.command("load")
def causal_load(
    file: Path = typer.Argument(..., help="causal: 어휘로 작성된 인과 DAG TTL."),
    ontology: Optional[str] = typer.Option(None, "--ontology"),
) -> None:
    """Store a causal DAG (causal: TTL) into the causal named graph."""
    from ontorag.core.causal import graph_to_model

    g = Graph()
    g.parse(str(file), format="turtle")
    model = graph_to_model(g)
    if model is None:
        console.print(f"[red]Error:[/] {file} 에 causal:Variable 이 없습니다.")
        raise typer.Exit(1)
    store = _store()

    async def _go() -> int:
        try:
            return await store.put_causal_model(model, ontology=ontology)
        finally:
            await store.aclose()

    n = asyncio.run(_go())
    console.print(
        f"[green]저장됨[/] — 변수 {len(model.variables)}개, 엣지 {len(model.edges)}개 ({n} statements)."
    )
    console.print(
        "[dim]참고: 인과 DAG는 사용자 제공입니다. ontorag는 DAG가 옳다는 가정 하에 "
        "개입/반사실을 계산하며 인과 타당성을 검증하지 않습니다.[/]"
    )


@causal_app.command("show")
def causal_show(ontology: Optional[str] = typer.Option(None, "--ontology")) -> None:
    """Print the stored causal DAG."""
    store = _store()

    async def _go():
        try:
            return await store.get_causal_model(ontology=ontology)
        finally:
            await store.aclose()

    model = asyncio.run(_go())
    if model is None:
        console.print("[yellow]저장된 인과 모델이 없습니다.[/]")
        return
    console.print(f"\n[bold]{model.name or '(이름 없음)'}[/]  (basedOn: {model.based_on or '—'})\n")
    vt = Table(title="변수", show_header=True, box=None)
    vt.add_column("URI", style="cyan")
    vt.add_column("관측")
    for v in model.variables:
        vt.add_row(v.uri, "observed" if v.observed else "[magenta]latent[/]")
    console.print(vt)
    et = Table(title="인과 엣지 (cause → effect)", show_header=True, box=None)
    et.add_column("cause", style="cyan")
    et.add_column("effect")
    for c, e in model.edges:
        et.add_row(c, e)
    console.print(et)


@causal_app.command("do")
def causal_do(
    do: list[str] = typer.Option(..., "--do", "-d", help="개입 VAR=STATE. 반복 가능."),
    query: list[str] = typer.Option(..., "--query", "-q", help="질의 변수. 반복 가능."),
    evidence: list[str] = typer.Option([], "--evidence", "-e", help="관측 VAR=STATE."),
    ontology: Optional[str] = typer.Option(None, "--ontology"),
) -> None:
    """Interventional query P(query | do(intervention))."""
    from ontorag.causal.engine import CausalEngineError

    store = _store(require_bn=True)

    async def _go():
        try:
            engine = await _build_engine(store, ontology)
            return await engine.do_query(_kv(do), list(query), _kv(evidence))
        finally:
            await store.aclose()

    try:
        res = asyncio.run(_go())
    except CausalEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)
    _dist_table("P(· | do)", res)


@causal_app.command("identify")
def causal_identify(
    treatment: str = typer.Option(..., "--treatment", "-t"),
    outcome: str = typer.Option(..., "--outcome", "-o"),
    ontology: Optional[str] = typer.Option(None, "--ontology"),
) -> None:
    """Report back-door / front-door adjustment sets and identifiability."""
    from ontorag.causal.engine import CausalEngineError

    store = _store(require_bn=True)

    async def _go():
        try:
            engine = await _build_engine(store, ontology)
            return await engine.identify(treatment, outcome)
        finally:
            await store.aclose()

    try:
        info = asyncio.run(_go())
    except CausalEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"식별 가능: [bold]{info['identifiable']}[/]")
    console.print(f"backdoor 조정집합: {info['backdoor_adjustment_set'] or '—'}")
    console.print(f"frontdoor 조정집합: {info['frontdoor_adjustment_sets'] or '—'}")


@causal_app.command("counterfactual")
def causal_counterfactual(
    observed: list[str] = typer.Option([], "--observed", "-O", help="관측 VAR=STATE."),
    intervention: list[str] = typer.Option(..., "--intervention", "-i", help="반사실 전제 VAR=STATE."),
    query: list[str] = typer.Option(..., "--query", "-q", help="질의 변수."),
    ontology: Optional[str] = typer.Option(None, "--ontology"),
) -> None:
    """Counterfactual: P(query | observed, had intervention held)."""
    from ontorag.causal.engine import CausalEngineError

    store = _store(require_bn=True)

    async def _go():
        try:
            engine = await _build_engine(store, ontology)
            return await engine.counterfactual(_kv(observed), _kv(intervention), list(query))
        finally:
            await store.aclose()

    try:
        res = asyncio.run(_go())
    except CausalEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)
    _dist_table("P(· | obs, had)", res)


@causal_app.command("clear")
def causal_clear(ontology: Optional[str] = typer.Option(None, "--ontology")) -> None:
    """Drop the stored causal model for this scope."""
    store = _store()

    async def _go() -> int:
        try:
            return await store.clear_causal_model(ontology=ontology)
        finally:
            await store.aclose()

    console.print(f"[green]삭제됨[/] — {asyncio.run(_go())} statements/nodes 제거.")


@causal_app.command("learn-dag")
def causal_learn_dag(
    file: Path = typer.Argument(..., help="변수 명세 TTL (bn:represents + bn:states)."),
    target_class: str = typer.Option(..., "--class", "-c", help="관측 행이 되는 클래스 URI."),
    ontology: Optional[str] = typer.Option(None, "--ontology"),
    significance: float = typer.Option(0.01, "--significance", help="PC 유의수준."),
    save: bool = typer.Option(False, "--save", help="제안된 DAG를 저장 (검토 후)."),
) -> None:
    """Propose a causal DAG from ABox data via PC (a PROPOSAL — review before use)."""
    from ontorag.bayes.engine import BayesianEngineError
    from ontorag.causal.discovery import discover_dag
    from ontorag.core.bayes import graph_to_structure

    g = Graph()
    g.parse(str(file), format="turtle")
    structure = graph_to_structure(g)
    if structure is None:
        console.print(f"[red]Error:[/] {file} 에 변수가 없습니다.")
        raise typer.Exit(1)
    store = _store()

    async def _go():
        try:
            model, n = await discover_dag(
                store, structure, target_class,
                ontology=ontology, significance_level=significance,
            )
            if save:
                await store.put_causal_model(model, ontology=ontology)
            return model, n
        finally:
            await store.aclose()

    try:
        model, n_obs = asyncio.run(_go())
    except BayesianEngineError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    console.print(
        f"[green]구조 제안 완료[/] — 관측 {n_obs}건에서 엣지 {len(model.edges)}개 발견"
        + (" (저장됨)." if save else " (미저장).")
    )
    console.print(
        "[yellow]⚠ 제안일 뿐입니다.[/] PC는 Markov 등가류를 복원하므로 일부 방향은 "
        "데이터로 결정되지 않습니다. 인과 주장 전에 사람이 검토하세요."
    )
    for c, e in model.edges:
        console.print(f"  • {c} → {e}")
