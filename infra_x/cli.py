"""infra-x CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from infra_x import __version__
from infra_x.agent.planner import Planner
from infra_x.backend import BackendParseError, parse_backend_shorthand
from infra_x.blueprints.loader import list_blueprints, load_blueprint
from infra_x.llm import get_provider
from infra_x.render import render_stack

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Open-source AI Terraform generator with versioned blueprints. BYO LLM.",
)
console = Console()


@app.command("version")
def version_cmd() -> None:
    """Print the version."""
    console.print(f"infra-x [bold]{__version__}[/bold]")


@app.command("list-blueprints")
def list_cmd() -> None:
    """List all bundled blueprints."""
    bps = list_blueprints()
    if not bps:
        console.print("[yellow]No blueprints found.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"infra-x blueprints ({len(bps)})", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Cloud", style="magenta")
    table.add_column("Est. cost / mo", justify="right")
    table.add_column("Description")
    for b in bps:
        cost = (
            f"${b.estimated_cost_usd_monthly[0]:g}-${b.estimated_cost_usd_monthly[1]:g}"
            if b.estimated_cost_usd_monthly
            else "—"
        )
        table.add_row(b.id, b.name, b.provider, cost, b.description)
    console.print(table)


@app.command("show")
def show_cmd(blueprint_id: str = typer.Argument(..., help="Blueprint ID to show")) -> None:
    """Show details about a single blueprint."""
    bp = load_blueprint(blueprint_id)
    console.print(Panel(f"[bold]{bp.name}[/bold]\n{bp.description}", title=bp.id))
    console.print(f"  [magenta]Provider:[/magenta] {bp.provider}  ({bp.region or 'no region set'})")
    console.print(f"  [magenta]Version:[/magenta]  {bp.version}")
    console.print(f"  [magenta]Services:[/magenta] {len(bp.services)}")
    console.print(f"  [magenta]Variables:[/magenta] {len(bp.variables)}")
    if bp.estimated_cost_usd_monthly:
        lo, hi = bp.estimated_cost_usd_monthly
        console.print(f"  [magenta]Est. cost:[/magenta] ${lo:g}-${hi:g} / month")
    console.print()
    if bp.variables:
        t = Table(title="Variables")
        t.add_column("Name", style="cyan")
        t.add_column("Type")
        t.add_column("Default")
        t.add_column("Description")
        for v in bp.variables:
            t.add_row(v.name, v.type, str(v.default) if v.default is not None else "—", v.description or "")
        console.print(t)


@app.command("generate")
def generate_cmd(
    blueprint: str = typer.Option(..., "--blueprint", "-b", help="Blueprint ID"),
    out: Path = typer.Option(..., "--out", "-o", help="Output directory for .tf files"),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        "-p",
        help="Free-form requirements (skipped if --no-llm).",
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Stack name"),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip the LLM and emit blueprint defaults verbatim. Great for testing.",
    ),
    provider: str = typer.Option(
        "ollama",
        "--provider",
        help="LLM provider: ollama (local, default), anthropic, or openai. Hosted providers read their key from ANTHROPIC_API_KEY / OPENAI_API_KEY.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model name. Defaults: ollama=qwen2.5-coder:7b, anthropic=claude-sonnet-4-6, openai=gpt-4o-mini.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files in --out."),
    backend: str | None = typer.Option(
        None,
        "--backend",
        help=(
            "Remote-state backend in shorthand form. Examples: "
            "s3://BUCKET/path/state.tfstate?region=us-east-1&lock=tf-locks ; "
            "gcs://BUCKET/PREFIX ; tfc://ORG/WORKSPACE ; local ; local:./tf.tfstate. "
            "Omit for local state (default)."
        ),
    ),
) -> None:
    """Generate Terraform from a blueprint."""
    bp = load_blueprint(blueprint)
    out = out.expanduser().resolve()

    backend_cfg = None
    if backend:
        try:
            backend_cfg = parse_backend_shorthand(backend)
        except BackendParseError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(2) from e

    if out.exists() and any(out.iterdir()) and not overwrite:
        console.print(
            f"[red]✗[/red] {out} is not empty. Use --overwrite to clobber, or pick a fresh dir."
        )
        raise typer.Exit(2)

    # Build planner
    if no_llm:
        planner = Planner(llm=None)
        result = planner.deterministic(bp, stack_name=name or bp.id)
    else:
        if not prompt:
            console.print(
                "[red]✗[/red] --prompt is required unless you pass --no-llm. "
                "Try: infra-x generate -b aws-s3-static-site -p 'static site for acme.com prod' -o ./out"
            )
            raise typer.Exit(2)
        kwargs = {"model": model} if model else {}
        try:
            llm = get_provider(provider, **kwargs)
        except ValueError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(2) from e
        planner = Planner(llm=llm)
        with console.status(
            f"[cyan]Planning with {provider}/{getattr(llm, 'model', '?')}...[/cyan]",
            spinner="dots",
        ):
            try:
                result = planner.from_prompt(bp, prompt=prompt, stack_name=name)
            except RuntimeError as e:
                console.print(f"[red]✗[/red] {e}")
                raise typer.Exit(1) from e

    if backend_cfg is not None:
        result.stack.backend = backend_cfg

    paths = render_stack(result.stack, out)

    # Write any companion files the blueprint declared (e.g. placeholder Lambda
    # source, sample scripts). These live alongside the .tf output.
    companion_paths: list[Path] = []
    for rel_path, content in (bp.companion_files or {}).items():
        target = out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        companion_paths.append(target)
    backend_label = (
        f"[dim]Backend:[/dim] {backend_cfg.kind}" if backend_cfg else "[dim]Backend:[/dim] local (default)"
    )
    console.print(
        Panel(
            f"[green]✓[/green] Generated [bold]{result.stack.name}[/bold] "
            f"({bp.provider}, {len(result.stack.services)} resources)\n"
            f"{backend_label}\n"
            f"[dim]Notes:[/dim] {result.notes or '—'}",
            title="infra-x generate",
            border_style="green",
        )
    )
    console.print("\nFiles written:")
    for p in paths + companion_paths:
        try:
            rel = p.relative_to(Path.cwd())
        except ValueError:
            rel = p
        console.print(f"  • {rel}")
    console.print(
        f"\n[bold]Next:[/bold]  cd {out}  &&  terraform init  &&  terraform plan"
    )


@app.command("validate")
def validate_cmd(blueprint_id: str | None = typer.Argument(None)) -> None:
    """Validate one or all blueprints."""
    bps = [load_blueprint(blueprint_id)] if blueprint_id else list_blueprints()
    failed = 0
    for b in bps:
        try:
            stack = b.to_stack(name=b.id)
            # Render to dict to make sure HCL formation doesn't blow up.
            from infra_x.render import HCLRenderer

            HCLRenderer(stack).files()
            console.print(f"  [green]✓[/green] {b.id}  ({len(b.services)} resources)")
        except Exception as e:  # broad on purpose for CLI feedback
            failed += 1
            console.print(f"  [red]✗[/red] {b.id}: {e}")
    if failed:
        console.print(f"\n[red]{failed} blueprint(s) failed.[/red]")
        sys.exit(1)
    console.print(f"\n[green]All {len(bps)} blueprint(s) OK.[/green]")


if __name__ == "__main__":
    app()
