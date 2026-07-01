"""CLI entry point for trajectory-maker."""

import typer

app = typer.Typer(help="Trajectory Maker — synthesize, verify, and record agent trajectories.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Trajectory Maker — synthesize, verify, and record agent trajectories."""


if __name__ == "__main__":
    app()
