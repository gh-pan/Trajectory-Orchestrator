import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.e2e
def test_echo_task_end_to_end_run(tmp_path):
    """Run the echo_task fixture against a real endpoint. Requires --run-e2e + env vars."""
    from trajectory_maker.run import run as do_run

    endpoint = os.environ["TM_E2E_ENDPOINT"]
    apikey = os.environ["TM_E2E_APIKEY"]
    model = os.environ["TM_E2E_MODEL"]
    out = do_run(
        FIXTURES / "echo_task",
        endpoint=endpoint, apikey=apikey, model=model,
        output=tmp_path / "dataset", keep=False,
    )
    assert (out / "trajectory.jsonl").exists()
    assert (out / "final_score.json").exists()
    import json
    score = json.loads((out / "final_score.json").read_text())
    assert score["termination"] in ("completed", "stopped_without_claim")


@pytest.mark.e2e
def test_echo_task_end_to_end_all(tmp_path):
    """Full all pipeline on a tiny source folder."""
    from trajectory_maker.cli import app
    from typer.testing import CliRunner
    import os

    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# demo\n")
    runner = CliRunner()
    result = runner.invoke(app, [
        "all", str(src),
        "--endpoint", os.environ["TM_E2E_ENDPOINT"],
        "--apikey", os.environ["TM_E2E_APIKEY"],
        "--model", os.environ["TM_E2E_MODEL"],
        "--tasks", str(tmp_path / "tasks"),
        "--output", str(tmp_path / "dataset"),
    ])
    assert result.exit_code == 0, result.output
