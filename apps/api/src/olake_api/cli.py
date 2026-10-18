"""Command line for the API.

serve    run the HTTP server
ask      answer one question in the terminal (same pipeline as the API)
search   show what retrieval finds for a question, with scores
eval     run the golden-set evaluation (see eval/golden.yaml)
"""

from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from olake_api.config import get_settings

app = typer.Typer(help="Ask OLake API.", no_args_is_help=True)
console = Console()


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: Annotated[int, typer.Option(help="Uvicorn worker processes.")] = 1,
) -> None:
    """Run the HTTP server."""
    import uvicorn

    uvicorn.run("olake_api.main:app", host=host, port=port, workers=workers, proxy_headers=True)


@app.command()
def ask(question: str) -> None:
    """Answer one question in the terminal."""
    from olake_api.main import get_answerer

    sources: list[dict] = []
    for event in get_answerer().run(question, []):
        if event["event"] == "meta":
            for notice in event["data"]["notices"]:
                console.print(f"[yellow]{notice}[/yellow]")
        elif event["event"] == "sources":
            sources = event["data"]["sources"]
        elif event["event"] == "final":
            data = event["data"]
            console.print(Markdown(data["answer"]))
            console.print(f"\n[bold]Status:[/bold] {data['status']}")
            for n in data["citations"]:
                console.print(f"  [{n}] {sources[n - 1]['title']} — {sources[n - 1]['url']}")
            for w in data["warnings"]:
                console.print(f"[yellow]{w}[/yellow]")
        elif event["event"] == "error":
            console.print(f"[red]{event['data']['message']}[/red]")
            raise typer.Exit(code=1)


@app.command()
def search(question: str, k: int = 8) -> None:
    """Show retrieval results and the answer gate's decision."""
    from olake_api.main import get_answerer
    from olake_api.query import analyze
    from olake_api.retrieval import search as run_search

    answerer = get_answerer()
    analysis = analyze(question)
    vector = answerer.embedder.embed([analysis.search_text])[0]
    with answerer.pool.connection() as conn:
        result = run_search(conn, analysis, vector, top_k=k, candidates=get_settings().candidates)
    table = Table("#", "Score", "Sim", "Matched by", "Title", "URL")
    for n, h in enumerate(result.hits, start=1):
        table.add_row(
            str(n),
            f"{h.score:.4f}",
            f"{h.similarity:.2f}",
            ",".join(h.matched_by),
            h.title[:50],
            h.url,
        )
    console.print(table)
    console.print(
        f"best similarity {result.best_similarity:.2f}, keyword coverage {result.coverage:.2f}, "
        f"symbol match {result.symbol_match} → "
        + (
            "[green]would answer[/green]"
            if result.answerable(get_settings().similarity_floor)
            else "[yellow]would abstain[/yellow]"
        )
    )


@app.command(name="eval")
def run_eval(
    answers: Annotated[
        bool, typer.Option(help="Also generate and grade answers (uses the LLM).")
    ] = False,
    golden: Annotated[str | None, typer.Option(help="Path to the golden set YAML.")] = None,
    version: Annotated[
        str, typer.Option(help="Index version: 'active', 'latest-ready' or a version id.")
    ] = "active",
    activate_on_pass: Annotated[
        bool, typer.Option(help="Make the evaluated version live if every threshold passes.")
    ] = False,
) -> None:
    """Measure retrieval (and optionally answers) against the golden set."""
    from olake_api.evaluate import main as eval_main

    if not eval_main(
        answers=answers,
        golden_path=golden,
        console=console,
        version=version,
        activate_on_pass=activate_on_pass,
    ):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
