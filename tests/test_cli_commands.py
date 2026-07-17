from typer.testing import CliRunner

from trajectory_maker.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ["synthesize", "verify", "run", "run-workflow", "all", "clean"]:
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


def test_run_workflow_missing_subject_credentials_fails(monkeypatch):
    for key in (
        "TM_SUBJECT_BASE_URL", "TM_SUBJECT_API_KEY", "AIHUBMIX_BASE_URL",
        "AIHUBMIX_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    result = runner.invoke(app, ["run-workflow", "some_case"])
    assert result.exit_code != 0


def test_run_workflow_forwards_cli_arguments(monkeypatch, tmp_path):
    from trajectory_maker import workflow

    captured = {}
    expected = tmp_path / "dataset" / "case-1" / "run"

    def fake_run_workflow(case_or_workflow, **kwargs):
        captured["case"] = case_or_workflow
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(workflow, "run_workflow", fake_run_workflow)
    result = runner.invoke(app, [
        "run-workflow", str(tmp_path / "case_1"),
        "--endpoint", "https://api.example.com",
        "--apikey", "secret",
        "--model", "model-x",
        "--output", str(tmp_path / "dataset"),
        "--task-id", "custom-case",
        "--timeout", "99",
        "--idle-timeout", "11",
        "--keep",
    ])

    assert result.exit_code == 0, result.output
    assert captured["case"] == tmp_path / "case_1"
    assert captured["task_id"] == "custom-case"
    assert captured["timeout_seconds"] == 99
    assert captured["idle_timeout_seconds"] == 11
    assert captured["keep"] is True
    assert captured["runtime"] == "docker"
    assert captured["effort"] == "xhigh"


def test_run_workflow_local_allows_credentials_from_environment(monkeypatch, tmp_path):
    from trajectory_maker import workflow

    captured = {}
    expected = tmp_path / "dataset" / "case-1" / "run"

    def fake_run_workflow(case_or_workflow, **kwargs):
        captured["case"] = case_or_workflow
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(workflow, "run_workflow", fake_run_workflow)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://aihubmix.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-secret")
    result = runner.invoke(app, [
        "run-workflow", str(tmp_path / "case_1"),
        "--runtime", "local",
        "--output", str(tmp_path / "dataset"),
    ])

    assert result.exit_code == 0, result.output
    assert captured["runtime"] == "local"
    assert captured["endpoint"] is None
    assert captured["apikey"] is None
    assert captured["model"] == "claude-opus-4-8"
    assert "env-secret" not in result.output
