from pathlib import Path

import pytest

from trajectory_maker.grade import (
    RubricResult,
    judge_pass_condition,
    aggregate,
    grade_script,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_judge_exit_zero_pass():
    assert judge_pass_condition("exit_zero", "", 0) is True


def test_judge_exit_zero_fail():
    assert judge_pass_condition("exit_zero", "", 1) is False


def test_judge_output_contains_pass():
    assert judge_pass_condition("output_contains:OK", "all OK here", 0) is True


def test_judge_output_contains_fail():
    assert judge_pass_condition("output_contains:OK", "no match", 0) is False


def test_judge_output_matches_pass():
    assert judge_pass_condition("output_matches:\\d+ files", "3 files checked", 0) is True


def test_judge_output_matches_fail():
    assert judge_pass_condition("output_matches:\\d+ files", "no digits here", 0) is False


def test_aggregate_partial_when_preferred_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="required", passed=True),
        RubricResult(id="r3", type="checklist", severity="preferred", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "partial"
    assert summary.required_pass == 2
    assert summary.required_total == 2
    assert summary.score == pytest.approx(0.8)  # (2*1.0 + 0*0.5)/(2*1.0+1*0.5)=2.0/2.5


def test_aggregate_fail_when_required_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="required", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "fail"
    assert summary.required_pass == 1


def test_aggregate_pass_when_all_pass():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="preferred", passed=True),
    ]
    summary = aggregate(results)
    assert summary.verdict == "pass"
    assert summary.required_total == 1
    assert summary.required_pass == 1


def test_aggregate_partial_when_only_preferred_fails():
    results = [
        RubricResult(id="r1", type="script", severity="required", passed=True),
        RubricResult(id="r2", type="checklist", severity="preferred", passed=False),
    ]
    summary = aggregate(results)
    assert summary.verdict == "partial"
    assert summary.required_total == 1
    assert summary.required_pass == 1


@pytest.mark.integration
def test_grade_script_runs_in_container(tmp_path):
    from trajectory_maker.docker import DockerClient

    client = DockerClient()
    image_tag = "tm-grade-test"
    dfdir = tmp_path / "ctx"
    dfdir.mkdir()
    (dfdir / "Dockerfile").write_text(
        "FROM alpine:3.20\nRUN apk add --no-cache bash\nWORKDIR /workspace\n"
        "ENTRYPOINT [\"tail\",\"-f\",\"/dev/null\"]\n"
    )
    client.build(dfdir, image_tag)
    container = "tm-grade-test-run"
    try:
        client.run(image_tag, container)
        # script: exit_zero on a true echo
        result = grade_script(
            container=container,
            docker=client,
            rubric_run_cmd=["bash", "-lc", "echo OK"],
            pass_condition="output_contains:OK",
            timeout_seconds=10,
        )
        assert result.passed is True
        assert result.exit_code == 0
    finally:
        client.stop(container)
        client.rm(container)
        client.rmi(image_tag)


def test_grade_checklist_uses_driver(monkeypatch):
    from trajectory_maker import grade

    captured = {}

    class FakeDriver:
        def __init__(self, **kw):
            pass

        def send_user_message(self, text):
            captured["prompt"] = text

        def events(self):
            yield {"type": "result", "result": '{"pass": true, "reason": "all good"}'}

        def close(self):
            pass

    monkeypatch.setattr(grade, "Driver", type("D", (), {"docker": staticmethod(lambda *a, **k: FakeDriver())}))
    # checklist judge is meta work — pin meta env/model so build_meta_env/meta_model don't raise/return None
    monkeypatch.setattr(grade, "build_meta_env", lambda: {"ANTHROPIC_BASE_URL": "https://meta.example.com"})
    monkeypatch.setattr(grade, "meta_model", lambda: "m")
    result = grade.grade_checklist(
        container="c",
        docker=object(),
        objective="obj",
        criterion="crit",
        rubric_id="r1",
        description="desc",
        target_files=["src/**"],
    )
    assert result.passed is True
    assert result.reason == "all good"
    assert "obj" in captured["prompt"]
    assert "crit" in captured["prompt"]


def test_normalize_pass_condition_uses_pass_value():
    from trajectory_maker.grade import _normalize_pass_condition
    assert _normalize_pass_condition("output_contains", "OK") == "output_contains:OK"
    assert _normalize_pass_condition("output_matches", r"\d+") == r"output_matches:\d+"
    # inline form kept as-is
    assert _normalize_pass_condition("output_contains:OK", "") == "output_contains:OK"
    # exit_zero unchanged
    assert _normalize_pass_condition("exit_zero", "") == "exit_zero"


def test_grade_end_to_end_with_fake_docker(monkeypatch):
    """grade() orchestrates script + checklist rubrics with a fake docker and task_spec."""
    from trajectory_maker import grade

    class FakeRubric:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeTaskSpec:
        def __init__(self, rubrics, objective="obj"):
            self.rubrics = rubrics
            self.objective = objective

    exec_calls = []

    class FakeDocker:
        def exec(self, container, cmd, timeout=None):
            exec_calls.append(cmd)
            # script rubric: echo OK -> output_contains:OK passes
            return 0, "OK\n", ""

    # checklist judge: monkeypatch Driver.docker to return a fake that yields a result event
    class FakeJudgeDriver:
        def send_user_message(self, text):
            pass

        def events(self):
            yield {"type": "result", "result": '{"pass": true, "reason": "ok"}'}

        def close(self):
            pass

    monkeypatch.setattr(grade, "Driver", type("D", (), {"docker": staticmethod(lambda *a, **k: FakeJudgeDriver())}))
    # grade_checklist is meta work — pin meta env/model
    monkeypatch.setattr(grade, "build_meta_env", lambda: {"ANTHROPIC_BASE_URL": "https://meta.example.com"})
    monkeypatch.setattr(grade, "meta_model", lambda: "m")

    task_spec = FakeTaskSpec(
        rubrics=[
            FakeRubric(id="r1", type="script", run="rubrics/check.sh", interpreter="bash",
                       pass_condition="output_contains", pass_value="OK", timeout_seconds=60, severity="required"),
            FakeRubric(id="r2", type="checklist", description="desc", criterion="crit",
                       target_files=["src/**"], severity="preferred"),
        ],
    )
    outcome = grade.grade("container-x", FakeDocker(), task_spec)
    assert len(outcome.results) == 2
    assert outcome.results[0].id == "r1"
    assert outcome.results[0].passed is True
    assert outcome.results[0].severity == "required"
    assert outcome.results[1].id == "r2"
    assert outcome.results[1].severity == "preferred"
    # required passes, preferred passes -> pass
    assert outcome.summary.verdict == "pass"
    # script ran via docker.exec with /workspace/rubrics/check.sh
    assert any("/workspace/rubrics/check.sh" in " ".join(c) for c in exec_calls)
