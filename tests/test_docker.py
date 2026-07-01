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
