"""CLI entry point for trajectory-maker."""

from enum import Enum
from pathlib import Path

import typer

from .docker import DockerClient

app = typer.Typer(help="Trajectory Maker — synthesize, verify, and record agent trajectories.", no_args_is_help=True)


class WorkflowRuntime(str, Enum):
    docker = "docker"
    local = "local"


class ClaudeEffort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


def clean_all_containers() -> dict:
    docker = DockerClient()
    n_c = 0
    for name in docker.list_containers("tm-"):
        docker.rm(name)
        n_c += 1
    n_i = 0
    for tag in docker.list_images("tm-"):
        docker.rmi(tag)
        n_i += 1
    return {"containers": n_c, "images": n_i}


@app.command()
def synthesize(
    input_ref: str = typer.Argument(..., help="github URL or local folder path"),
    output: Path = typer.Option(Path("./tasks"), "--output", "-o"),
    model: str | None = typer.Option(None, "--model", help="synthesize model"),
) -> None:
    """Stage 1: synthesize a TaskSpec from an input folder."""
    from .synthesize import synthesize as do_synth
    final = do_synth(input_ref, output, model=model)
    typer.echo(f"synthesized task -> {final}")


@app.command()
def verify(
    task_dir: Path = typer.Argument(...),
    endpoint: str | None = typer.Option(None, "--endpoint"),
    apikey: str | None = typer.Option(None, "--apikey"),
    model: str | None = typer.Option(None, "--model"),
    keep_on_fail: bool = typer.Option(False, "--keep-on-fail"),
) -> None:
    """Stage 2: verify a synthesized task is solvable."""
    from .verify import verify as do_verify, serialize_verify_result
    vr = do_verify(task_dir, endpoint=endpoint, apikey=apikey, model=model, keep_on_fail=keep_on_fail)
    typer.echo(serialize_verify_result(vr))
    if vr.verdict != "pass":
        raise typer.Exit(code=1)


@app.command()
def run(
    task_dir: Path = typer.Argument(...),
    endpoint: str = typer.Option(..., "--endpoint"),
    apikey: str = typer.Option(..., "--apikey"),
    model: str = typer.Option(..., "--model"),
    output: Path = typer.Option(Path("./dataset"), "--output", "-o"),
    max_turns: int = typer.Option(20, "--max-turns", help="max user-injected turns (long-chain budget)"),
    timeout: int = typer.Option(3600, "--timeout"),
    idle_timeout: int = typer.Option(300, "--idle-timeout", help="kill agent if no event for N seconds"),
    keep: bool = typer.Option(False, "--keep"),
) -> None:
    """Stage 3: run task in docker, record trajectory, grade, package."""
    from .run import run as do_run
    out = do_run(task_dir, endpoint=endpoint, apikey=apikey, model=model,
                 output=output, max_turns=max_turns, timeout_seconds=timeout,
                 idle_timeout_seconds=idle_timeout, keep=keep)
    typer.echo(f"packaged -> {out}")


@app.command(name="run-workflow")
def run_workflow_command(
    case_or_workflow: Path = typer.Argument(
        ..., help="case directory containing workflow.json, or the workflow file itself"
    ),
    runtime: WorkflowRuntime = typer.Option(
        WorkflowRuntime.docker,
        "--runtime",
        help="execution backend: Docker isolation or local Claude Code",
    ),
    endpoint: str | None = typer.Option(
        None, "--endpoint", help="provider base URL; defaults to subject/Anthropic env"
    ),
    apikey: str | None = typer.Option(
        None, "--apikey", help="provider key; prefer AIHUBMIX_API_KEY or ANTHROPIC_AUTH_TOKEN"
    ),
    model: str = typer.Option(
        "claude-opus-4-8", "--model", help="subject model (default: Claude Opus 4.8)"
    ),
    effort: ClaudeEffort = typer.Option(
        ClaudeEffort.xhigh, "--effort", help="Claude reasoning effort (local runtime)"
    ),
    output: Path = typer.Option(Path("./dataset"), "--output", "-o"),
    task_id: str | None = typer.Option(
        None, "--task-id", help="output task id (defaults to the case directory name)"
    ),
    timeout: int = typer.Option(3600, "--timeout", help="whole workflow wall-clock timeout"),
    idle_timeout: int = typer.Option(
        300, "--idle-timeout", help="kill the subject after N seconds without an event"
    ),
    keep: bool = typer.Option(False, "--keep"),
) -> None:
    """Run preset workflow.json turns in one persistent Claude session."""
    from .workflow import run_workflow as do_run_workflow

    out = do_run_workflow(
        case_or_workflow,
        endpoint=endpoint,
        apikey=apikey,
        model=model,
        output=output,
        task_id=task_id,
        timeout_seconds=timeout,
        idle_timeout_seconds=idle_timeout,
        keep=keep,
        runtime=runtime.value,
        effort=effort.value,
    )
    typer.echo(f"packaged -> {out}")


@app.command(name="all")
def all_stages(
    input_ref: str = typer.Argument(...),
    endpoint: str = typer.Option(..., "--endpoint"),
    apikey: str = typer.Option(..., "--apikey"),
    model: str = typer.Option(..., "--model"),
    tasks_root: Path = typer.Option(Path("./tasks"), "--tasks"),
    output: Path = typer.Option(Path("./dataset"), "--output", "-o"),
    keep: bool = typer.Option(False, "--keep"),
) -> None:
    """End-to-end: synthesize -> verify -> run."""
    from .synthesize import synthesize as do_synth
    from .verify import verify as do_verify
    from .run import run as do_run
    task_dir = do_synth(input_ref, tasks_root, model=model)
    typer.echo(f"synthesized -> {task_dir}")
    vr = do_verify(task_dir, endpoint=endpoint, apikey=apikey, model=model)
    if vr.verdict != "pass":
        typer.echo(f"verify failed: {vr.verdict}")
        raise typer.Exit(code=1)
    typer.echo("verify passed")
    out = do_run(task_dir, endpoint=endpoint, apikey=apikey, model=model,
                 output=output, keep=keep)
    typer.echo(f"packaged -> {out}")


@app.command()
def clean(
    all_flag: bool = typer.Option(False, "--all", help="remove all tm-* containers and images"),
    task_id: str | None = typer.Option(None, "--task", help="clean only a specific task_id"),
) -> None:
    """Remove leftover tm-* containers and images."""
    docker = DockerClient()
    if task_id:
        for name in docker.list_containers(f"tm-"):
            if task_id in name:
                docker.rm(name)
        for tag in docker.list_images("tm-"):
            if task_id in tag:
                docker.rmi(tag)
        typer.echo(f"cleaned task {task_id}")
        return
    if all_flag:
        result = clean_all_containers()
        typer.echo(f"removed {result['containers']} containers, {result['images']} images")
        return
    typer.echo("specify --all or --task <id>")


if __name__ == "__main__":
    app()
