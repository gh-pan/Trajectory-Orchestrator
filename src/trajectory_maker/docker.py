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
        add_hosts: list[str] | None = None,
    ) -> str:
        args = ["run", "-d", "--name", container_name]
        args += ["--memory", memory, "--cpus", cpus]
        for h in add_hosts or []:
            args += ["--add-host", h]
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
