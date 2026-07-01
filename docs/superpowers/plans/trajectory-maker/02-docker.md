# 02 · Docker — 生命周期封装

**Goal:** 封装 docker CLI 子进程调用：build/run/exec/exec_stream/cp_from/cp_to/stop/rm/rmi/exists。集成测试用真实 docker（标记 `integration`）。

**Files:**
- Create: `src/trajectory_maker/docker.py`
- Create: `tests/test_docker.py`
- Create: `tests/fixtures/docker/Dockerfile`
- Create: `tests/fixtures/docker/hello.sh`

**Depends on:** 00-bootstrap

> 注：本块为集成测试块，需真实 docker。`@pytest.mark.integration` 默认 skip，用 `uv run pytest --run-integration` 触发。

---

- [ ] **Step 1: 写 fixture Dockerfile（最小可 build 镜像）**

Create `tests/fixtures/docker/Dockerfile`：

```dockerfile
FROM alpine:3.20
RUN apk add --no-cache bash
WORKDIR /workspace
COPY hello.sh /workspace/hello.sh
RUN chmod +x /workspace/hello.sh
ENTRYPOINT ["tail", "-f", "/dev/null"]
```

Create `tests/fixtures/docker/hello.sh`：

```bash
#!/usr/bin/env bash
echo "hello"
```

- [ ] **Step 2: 写失败测试 test_docker.py**

Create `tests/test_docker.py`：

```python
import uuid
from pathlib import Path

import pytest

from trajectory_maker.docker import DockerClient, BuildError, ExecError

FIXTURES = Path(__file__).parent / "fixtures" / "docker"


@pytest.fixture
def client():
    return DockerClient()


@pytest.fixture
def image(client):
    tag = f"tm-test-{uuid.uuid4().hex[:8]}"
    client.build(FIXTURES, tag)
    yield tag
    try:
        client.rmi(tag)
    except Exception:
        pass


@pytest.mark.integration
def test_build_creates_image(client, image):
    assert client.image_exists(image)


@pytest.mark.integration
def test_build_failure_raises_build_error(client, tmp_path):
    bad = tmp_path / "Dockerfile"
    bad.write_text("FROM alpine:3.20\nRUN exit 1\n")
    with pytest.raises(BuildError):
        client.build(tmp_path, "tm-test-badbuild")


@pytest.mark.integration
def test_run_exec_cp_lifecycle(client, image):
    name = f"tm-test-run-{uuid.uuid4().hex[:8]}"
    client.run(image, name)
    try:
        assert client.exists(name)
        code, out, err = client.exec(name, ["bash", "-lc", "echo $((1+1))"])
        assert code == 0
        assert out.strip() == "2"
        # write a file then cp it out
        client.exec(name, ["bash", "-lc", "echo created > /workspace/done.txt"])
        dest_dir = client_run_tmp(client)
        client.cp_from(name, "/workspace", dest_dir)
        # docker cp of a dir puts it under dest_dir/workspace/
        copied = Path(dest_dir) / "workspace" / "done.txt"
        assert copied.exists()
        assert "created" in copied.read_text()
    finally:
        client.stop(name)
        client.rm(name)
    assert not client.exists(name)


@pytest.mark.integration
def test_exec_stream_returns_lines(client, image):
    name = f"tm-test-stream-{uuid.uuid4().hex[:8]}"
    client.run(image, name)
    try:
        lines = list(client.exec_stream(name, ["bash", "-lc", "for i in 1 2 3; do echo line$i; done"]))
        assert any("line1" in l for l in lines)
        assert any("line3" in l for l in lines)
    finally:
        client.stop(name)
        client.rm(name)


@pytest.mark.integration
def test_exec_nonzero_returns_exit_code(client, image):
    """exec does not raise on nonzero exit; it returns the code (grade needs this)."""
    name = f"tm-test-execerr-{uuid.uuid4().hex[:8]}"
    client.run(image, name)
    try:
        code, out, err = client.exec(name, ["bash", "-lc", "exit 7"])
        assert code == 7
    finally:
        client.stop(name)
        client.rm(name)


def client_run_tmp(client):
    import tempfile
    return tempfile.mkdtemp()
```

- [ ] **Step 3: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_docker.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4: 实现 docker.py**

Create `src/trajectory_maker/docker.py`：

```python
"""Docker CLI subprocess wrapper for container lifecycle."""

import subprocess
from pathlib import Path


class BuildError(Exception):
    pass


class ExecError(Exception):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(f"exec failed (exit {exit_code}): {stderr}")


class DockerClient:
    def _run(self, args: list[str], check: bool = True, timeout=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def build(self, task_dir: Path, image_tag: str) -> None:
        result = self._run(
            ["build", "-t", image_tag, str(task_dir)], check=False
        )
        if result.returncode != 0:
            raise BuildError(result.stderr)

    def image_exists(self, image_tag: str) -> bool:
        r = self._run(["image", "inspect", image_tag], check=False)
        return r.returncode == 0

    def exists(self, container_name: str) -> bool:
        r = self._run(["inspect", container_name], check=False)
        return r.returncode == 0

    def run(
        self,
        image_tag: str,
        container_name: str,
        memory: str = "2g",
        cpus: str = "2",
        workspace_host_path: str | None = None,
        workspace_container_path: str = "/workspace",
    ) -> str:
        args = ["run", "-d", "--name", container_name]
        args += ["--memory", memory, "--cpus", cpus]
        if workspace_host_path:
            args += ["-v", f"{workspace_host_path}:{workspace_container_path}"]
        args.append(image_tag)
        self._run(args)
        return container_name

    def exec(self, container_name: str, cmd: list[str], timeout=None) -> tuple[int, str, str]:
        r = self._run(["exec", container_name, *cmd], check=False, timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def exec_stream(
        self,
        container_name: str,
        cmd: list[str],
        env: dict[str, str] | None = None,
        timeout=None,
    ) -> list[str]:
        """Run docker exec -i, capturing stdout line by line. Returns collected lines."""
        full_cmd = ["docker", "exec", "-i"]
        if env:
            for k, v in env.items():
                full_cmd += ["-e", f"{k}={v}"]
        full_cmd.append(container_name)
        full_cmd.extend(cmd)
        proc = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout
        )
        return proc.stdout.splitlines()

    def exec_pipes(
        self,
        container_name: str,
        cmd: list[str],
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        """Start docker exec -i with live stdin/stdout pipes (for stream-json driver)."""
        full_cmd = ["docker", "exec", "-i"]
        if env:
            for k, v in env.items():
                full_cmd += ["-e", f"{k}={v}"]
        full_cmd.append(container_name)
        full_cmd.extend(cmd)
        return subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line buffered
        )

    def cp_from(self, container_name: str, container_path: str, dest_dir: str) -> str:
        self._run(["cp", f"{container_name}:{container_path}", dest_dir])
        return dest_dir

    def cp_to(self, host_path: str, container_name: str, container_path: str) -> None:
        self._run(["cp", host_path, f"{container_name}:{container_path}"])

    def stop(self, container_name: str) -> None:
        self._run(["stop", container_name], check=False)

    def rm(self, container_name: str) -> None:
        self._run(["rm", "-f", container_name], check=False)

    def rmi(self, image_tag: str) -> None:
        self._run(["rmi", "-f", image_tag], check=False)

    def list_containers(self, prefix: str = "tm-") -> list[str]:
        r = self._run(
            ["ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
            check=False,
        )
        return [n for n in r.stdout.splitlines() if n.strip()]

    def list_images(self, prefix: str = "tm-") -> list[str]:
        r = self._run(
            ["images", "--filter", f"reference={prefix}*", "--format", "{{.Repository}}:{{.Tag}}"],
            check=False,
        )
        return [n for n in r.stdout.splitlines() if n.strip()]
```

- [ ] **Step 5: 运行单元测试（无 docker 的部分应能 import）**

Run:
```bash
uv run pytest tests/test_docker.py -v
```
Expected: 集成测试被 skip（默认不跑），无 import 错误。

- [ ] **Step 6: 运行集成测试（需 docker 运行中）**

Run:
```bash
uv run pytest tests/test_docker.py -v --run-integration
```
Expected: PASS（5 integration passed）。若 docker 未运行，先启动 Docker.app。

- [ ] **Step 7: 提交**

```bash
git add src/trajectory_maker/docker.py tests/test_docker.py tests/fixtures/docker/Dockerfile tests/fixtures/docker/hello.sh
git commit -m "feat: add docker lifecycle wrapper (build/run/exec/cp/rm) with integration tests"
```
