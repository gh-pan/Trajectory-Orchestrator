from typer.testing import CliRunner

from trajectory_maker.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ["synthesize", "verify", "run", "all", "clean"]:
        assert sub in result.output


def test_clean_all_runs_without_error(monkeypatch):
    from trajectory_maker import cli
    monkeypatch.setattr(cli, "clean_all_containers", lambda: {"containers": 0, "images": 0})
    result = runner.invoke(app, ["clean", "--all"])
    assert result.exit_code == 0


def test_synthesize_requires_input_arg():
    result = runner.invoke(app, ["synthesize"])
    assert result.exit_code != 0


def test_run_requires_endpoint_and_model():
    result = runner.invoke(app, ["run", "some_task"])
    assert result.exit_code != 0
