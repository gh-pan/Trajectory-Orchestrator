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
