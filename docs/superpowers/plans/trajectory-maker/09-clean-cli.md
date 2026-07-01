# 09 · Clean + All + CLI 接线 + README + e2e fixture

**Goal:** 在 cli.py 接线全部子命令（synthesize/verify/run/all/clean）；clean 扫残留容器镜像；all 端到端串起三阶段；写项目 README；提供最小 e2e fixture 任务供手动验证。

**Files:**
- Modify: `src/trajectory_maker/cli.py`
- Create: `tests/test_cli_commands.py`
- Create: `tests/fixtures/echo_task/task.yaml`
- Create: `tests/fixtures/echo_task/Dockerfile`
- Create: `tests/fixtures/echo_task/workspace/hello.txt`
- Create: `tests/fixtures/echo_task/rubrics/check.sh`
- Create: `tests/test_e2e.py`
- Create: `README.md`（仓库根）

**Depends on:** 06-synthesize、07-verify、08-run-package

---

- [ ] **Step 1: 写 echo_task fixture（最小可跑任务）**

Create `tests/fixtures/echo_task/task.yaml`：

```yaml
task_id: echo-create-done
category: devops
source:
  type: local-folder
  ref: ./workspace
initial_instruction: |
  在 /workspace 下创建文件 done.txt，内容为 hello。
objective: |
  /workspace/done.txt 存在且内容为 hello。
input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
expected_final_env:
  description: |
    /workspace/done.txt 存在，内容为 hello。
rubrics:
  - id: r1
    type: script
    run: rubrics/check.sh
    interpreter: bash
    pass_condition: exit_zero
    pass_value: ""
    timeout_seconds: 30
```

Create `tests/fixtures/echo_task/Dockerfile`：

```dockerfile
FROM node:22
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /workspace
COPY workspace /workspace
COPY rubrics /workspace/rubrics
RUN chmod +x /workspace/rubrics/check.sh
ENTRYPOINT ["tail", "-f", "/dev/null"]
```

Create `tests/fixtures/echo_task/workspace/hello.txt`：

```text
hello
```

Create `tests/fixtures/echo_task/rubrics/check.sh`：

```bash
#!/usr/bin/env bash
test -f /workspace/done.txt && grep -q hello /workspace/done.txt
```

- [ ] **Step 2: 写 CLI 命令测试 test_cli_commands.py**

Create `tests/test_cli_commands.py`：

```python
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
```

- [ ] **Step 3: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_cli_commands.py -v
```
Expected: FAIL（子命令未注册）。

- [ ] **Step 4: 实现 cli.py 全部子命令**

Replace `src/trajectory_maker/cli.py` with：

```python
"""CLI entry point for trajectory-maker."""

from pathlib import Path

import typer

from .docker import DockerClient

app = typer.Typer(help="Trajectory Maker — synthesize, verify, and record agent trajectories.", no_args_is_help=True)


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
    max_turns: int = typer.Option(1, "--max-turns"),
    timeout: int = typer.Option(1800, "--timeout"),
    keep: bool = typer.Option(False, "--keep"),
) -> None:
    """Stage 3: run task in docker, record trajectory, grade, package."""
    from .run import run as do_run
    out = do_run(task_dir, endpoint=endpoint, apikey=apikey, model=model,
                 output=output, max_turns=max_turns, timeout_seconds=timeout, keep=keep)
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
```

- [ ] **Step 5: 运行 CLI 命令测试验证通过**

Run:
```bash
uv run pytest tests/test_cli_commands.py -v
```
Expected: PASS（4 passed）。

- [ ] **Step 6: 写 e2e 测试（默认 skip）**

Create `tests/test_e2e.py`：

```python
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
    import os, shutil

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
```

- [ ] **Step 7: 写项目 README**

Create `README.md`（仓库根）：

````markdown
# Trajectory Maker

LLM agent 运行轨迹生成器：输入一个文件夹（github 仓库或本地文件夹），合成 agentic 任务 → 验证 → 在 docker 任务环境里跑 claude code 采集原生 stream-json 轨迹 → 清洗去敏 → 打包为"一数据一目录"。

## 安装

```bash
uv sync
```

## 使用

```bash
# 端到端
trajectory-maker all <input-folder-or-github-url> \
  --endpoint <base_url> --apikey <key> --model <model_id> \
  --tasks ./tasks --output ./dataset

# 分阶段
trajectory-maker synthesize <input-folder> --output ./tasks
trajectory-maker verify ./tasks/<task_id> --endpoint ... --model ...
trajectory-maker run ./tasks/<task_id> --endpoint ... --apikey ... --model ... --output ./dataset

# 清理残留容器/镜像
trajectory-maker clean --all
trajectory-maker clean --task <task_id>
```

## 输出

```
dataset/<task_id>/<run_id>/
├── metadata.yaml          # 任务元数据 + run 信息（不含 apikey）
├── final_score.json       # 最后一步评分结果
├── initial_env/           # 初始环境快照
├── expected_final_env/    # 预期终末环境 + rubrics
├── actual_final_env/      # agent 跑完的终末环境快照
└── trajectory.jsonl       # 清洗去敏后的原生 stream-json 轨迹
dataset/index.jsonl        # 全局索引
```

## 设计文档

- 规格：`docs/superpowers/specs/trajectory-maker/`
- 实现计划：`docs/superpowers/plans/trajectory-maker/`

## 测试

```bash
uv run pytest                          # 单元测试
uv run pytest --run-integration        # + docker 集成测试
TM_E2E_ENDPOINT=... TM_E2E_APIKEY=... TM_E2E_MODEL=... \
  uv run pytest --run-e2e              # + 真实端点 e2e
```
````

- [ ] **Step 8: 运行全部单元测试确认无回归**

Run:
```bash
uv run pytest -v
```
Expected: 所有单元测试 PASS，e2e skip。

- [ ] **Step 9: 手动冒烟（可选，需真实端点）**

Run:
```bash
TM_E2E_ENDPOINT=<url> TM_E2E_APIKEY=<key> TM_E2E_MODEL=<model> \
  uv run pytest tests/test_e2e.py::test_echo_task_end_to_end_run -v --run-e2e
```
Expected: 生成 dataset/echo-create-done/<run_id>/，含 6 个条目，容器/镜像已销毁。

- [ ] **Step 10: 提交**

```bash
git add src/trajectory_maker/cli.py tests/test_cli_commands.py tests/test_e2e.py tests/fixtures/echo_task README.md
git commit -m "feat: wire cli subcommands (synthesize/verify/run/all/clean), add e2e fixture and readme"
```

---

## 完成标准

- `uv run pytest` 全绿（单元）。
- `uv run pytest --run-integration` 全绿（需 docker 运行中）。
- `uv run trajectory-maker --help` 列出全部 5 个子命令。
- e2e（手动）能在 echo_task 上跑通并产出完整数据目录、销毁容器镜像。
