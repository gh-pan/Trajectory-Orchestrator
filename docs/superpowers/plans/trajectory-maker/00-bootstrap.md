# 00 · Bootstrap — 项目脚手架

**Goal:** 初始化 uv 项目、目录结构、pytest 配置、一个能跑 `trajectory-maker --help` 的最小 CLI 入口。

**Files:**
- Create: `pyproject.toml`
- Create: `src/trajectory_maker/__init__.py`
- Create: `src/trajectory_maker/cli.py`
- Create: `tests/__init__.py`
- Create: `tests/test_cli.py`
- Create: `tests/conftest.py`
- Modify: `.gitignore`（已存在，确认无误即可）

---

- [ ] **Step 1: 用 uv 初始化项目**

Run:
```bash
cd /Volumes/Files/EntropyOrder/Trajectory-Maker
uv init --no-readme --python 3.13 --lib --name trajectory-maker .
```

如果 `uv init` 因目录非空报错，改为手动创建 `pyproject.toml`（见 Step 2）。初始化后检查 `pyproject.toml` 是否生成。

- [ ] **Step 2: 写 pyproject.toml（若 uv init 未生成或需覆盖）**

Create `pyproject.toml`：

```toml
[project]
name = "trajectory-maker"
version = "0.1.0"
description = "LLM agent trajectory generator: synthesize agentic tasks, verify, and record claude code trajectories in docker."
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "typer>=0.12",
]

[project.scripts]
trajectory-maker = "trajectory_maker.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/trajectory_maker"]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
markers = [
    "integration: tests requiring real docker",
    "e2e: tests requiring real claude endpoint + apikey",
]
```

- [ ] **Step 3: 安装依赖**

Run:
```bash
uv sync
```
Expected: 创建 `.venv/`，安装 pydantic/pyyaml/typer/pytest。

- [ ] **Step 4: 创建包与测试目录骨架**

Run:
```bash
mkdir -p src/trajectory_maker tests
```

Create `src/trajectory_maker/__init__.py`：

```python
"""Trajectory Maker: LLM agent trajectory generator."""

__version__ = "0.1.0"
```

Create `tests/__init__.py`：（空文件）

```python
```

- [ ] **Step 5: 写 cli.py 最小入口**

Create `src/trajectory_maker/cli.py`：

```python
"""CLI entry point for trajectory-maker."""

import typer

app = typer.Typer(help="LLM agent trajectory generator.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Trajectory Maker — synthesize, verify, and record agent trajectories."""


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: 写失败测试 test_cli.py**

Create `tests/test_cli.py`：

```python
from typer.testing import CliRunner

from trajectory_maker.cli import app

runner = CliRunner()


def test_help_lists_no_subcommands_yet():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Trajectory Maker" in result.output
```

- [ ] **Step 7: 写 conftest.py（标记注册）**

Create `tests/conftest.py`：

```python
import pytest


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="needs --run-integration")
    skip_e2e = pytest.mark.skip(reason="needs --run-e2e")
    for item in items:
        if "integration" in item.keywords and not config.getoption("--run-integration"):
            item.add_marker(skip_integration)
        if "e2e" in item.keywords and not config.getoption("--run-e2e"):
            item.add_marker(skip_e2e)


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False)
    parser.addoption("--run-e2e", action="store_true", default=False)
```

- [ ] **Step 8: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_cli.py -v
```
Expected: PASS（1 passed）。

- [ ] **Step 9: 验证 CLI 入口可执行**

Run:
```bash
uv run trajectory-maker --help
```
Expected: 输出含 "Trajectory Maker"，exit code 0。

- [ ] **Step 10: 提交**

```bash
git add pyproject.toml uv.lock src/trajectory_maker/__init__.py src/trajectory_maker/cli.py tests/__init__.py tests/test_cli.py tests/conftest.py
git commit -m "chore: bootstrap uv project with typer cli and pytest"
```
