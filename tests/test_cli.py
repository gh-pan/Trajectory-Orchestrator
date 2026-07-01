from typer.testing import CliRunner

from trajectory_maker.cli import app

runner = CliRunner()


def test_help_lists_no_subcommands_yet():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Trajectory Maker" in result.output
