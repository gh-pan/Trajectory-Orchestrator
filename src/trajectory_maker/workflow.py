"""Run a fixed multi-turn workflow from ``workflow.json``.

The workflow file is a non-empty JSON array of TaskSpec-shaped objects.  Each
object contributes one ``initial_instruction`` user turn; all turns run in one
container, one workspace, and one persistent Claude session.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .claude_env import resolve_subject_credentials
from .models import (
    ExpectedFinalEnv,
    InputEnv,
    TaskSource,
    TaskSpec,
    WorkspaceSpec,
)
from .run import run_prepared_task, run_prepared_task_local


DEFAULT_BASE_IMAGE = "node:22-bookworm"
_BASE_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]*$")


class WorkflowValidationError(ValueError):
    """Raised before Docker side effects when a workflow is not runnable."""


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_path: Path
    case_dir: Path
    turns: tuple[TaskSpec, ...]
    workspace_dir: Path
    workspace_relative: Path
    dockerfile_path: Path
    base_image: str

    @property
    def instructions(self) -> list[str]:
        return [turn.initial_instruction for turn in self.turns]


def load_workflow(case_or_workflow: str | Path) -> WorkflowDefinition:
    """Load and validate a case directory or a direct workflow JSON path."""
    supplied = Path(case_or_workflow).expanduser()
    workflow_path = supplied / "workflow.json" if supplied.is_dir() else supplied
    workflow_path = workflow_path.resolve()
    if not workflow_path.is_file():
        raise WorkflowValidationError(f"workflow file not found: {workflow_path}")

    try:
        payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowValidationError(
            f"invalid workflow JSON at {workflow_path}: {exc}"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise WorkflowValidationError("workflow.json must contain a non-empty JSON array")

    turns: list[TaskSpec] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise WorkflowValidationError(f"workflow turn {index} must be a JSON object")
        try:
            turn = TaskSpec.model_validate(item)
        except ValidationError as exc:
            raise WorkflowValidationError(
                f"invalid workflow turn {index}: {exc}"
            ) from exc
        if not turn.initial_instruction.strip():
            raise WorkflowValidationError(
                f"workflow turn {index} has an empty initial_instruction"
            )
        turns.append(turn)

    case_dir = workflow_path.parent.resolve()
    first = turns[0]
    workspace_dir = _resolve_from_case(case_dir, first.input_env.workspace.path)
    if not workspace_dir.is_dir():
        raise WorkflowValidationError(f"workflow workspace not found: {workspace_dir}")
    try:
        workspace_relative = workspace_dir.relative_to(case_dir)
    except ValueError as exc:
        raise WorkflowValidationError(
            f"workflow workspace must be inside the case directory: {workspace_dir}"
        ) from exc

    dockerfile_path = _resolve_from_case(case_dir, first.input_env.dockerfile)
    base_image = first.input_env.base_image or DEFAULT_BASE_IMAGE
    expected_environment = (
        workspace_dir,
        dockerfile_path,
        first.input_env.workspace.init_script,
        base_image,
    )
    for index, turn in enumerate(turns[1:], start=2):
        environment = (
            _resolve_from_case(case_dir, turn.input_env.workspace.path),
            _resolve_from_case(case_dir, turn.input_env.dockerfile),
            turn.input_env.workspace.init_script,
            turn.input_env.base_image or DEFAULT_BASE_IMAGE,
        )
        if environment != expected_environment:
            raise WorkflowValidationError(
                f"workflow turn {index} declares a different workspace or Docker environment"
            )

    return WorkflowDefinition(
        workflow_path=workflow_path,
        case_dir=case_dir,
        turns=tuple(turns),
        workspace_dir=workspace_dir,
        workspace_relative=workspace_relative,
        dockerfile_path=dockerfile_path,
        base_image=base_image,
    )


def build_workflow_task_spec(
    workflow: WorkflowDefinition,
    task_id: str | None = None,
) -> TaskSpec:
    """Build the single aggregate TaskSpec used by existing grade/package code."""
    merged_rubrics = []
    for turn_index, turn in enumerate(workflow.turns, start=1):
        for rubric in turn.rubrics:
            merged_rubrics.append(
                rubric.model_copy(update={"id": f"turn-{turn_index}-{rubric.id}"})
            )

    logical_id = task_id or _slugify(workflow.case_dir.name)
    final_turn = workflow.turns[-1]
    objectives = "\n\n".join(
        f"第 {index} 轮（{turn.task_id}）：{turn.objective}"
        for index, turn in enumerate(workflow.turns, start=1)
    )
    expected = "\n\n".join(
        f"第 {index} 轮（{turn.task_id}）：{turn.expected_final_env.description}"
        for index, turn in enumerate(workflow.turns, start=1)
    )
    return TaskSpec(
        task_id=logical_id,
        category="scripted-workflow",
        source=TaskSource(type="local-folder", ref=str(workflow.case_dir)),
        initial_instruction=workflow.turns[0].initial_instruction,
        objective=objectives,
        input_env=InputEnv(
            dockerfile="Dockerfile",
            workspace=WorkspaceSpec(
                path=workflow.workspace_relative.as_posix(),
                init_script=workflow.turns[0].input_env.workspace.init_script,
            ),
            base_image=workflow.base_image,
        ),
        expected_final_env=ExpectedFinalEnv(
            description=expected,
            reference_patch=final_turn.expected_final_env.reference_patch,
        ),
        rubrics=merged_rubrics,
    )


def prepare_build_context(workflow: WorkflowDefinition, destination: Path) -> Path:
    """Copy the case into an isolated Docker context and supply a Dockerfile."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"build context already exists: {destination}")
    generated_dockerfile = None
    if not workflow.dockerfile_path.is_file():
        generated_dockerfile = _default_dockerfile(
            workflow.base_image,
            workflow.workspace_relative,
            has_rubrics=(workflow.case_dir / "rubrics").is_dir(),
        )
    shutil.copytree(workflow.case_dir, destination)

    target_dockerfile = destination / "Dockerfile"
    if workflow.dockerfile_path.is_file():
        try:
            declared_relative = workflow.dockerfile_path.relative_to(workflow.case_dir)
            copied_dockerfile = destination / declared_relative
        except ValueError:
            copied_dockerfile = workflow.dockerfile_path
        if copied_dockerfile.resolve() != target_dockerfile.resolve():
            shutil.copy2(copied_dockerfile, target_dockerfile)
    else:
        assert generated_dockerfile is not None
        target_dockerfile.write_text(
            generated_dockerfile,
            encoding="utf-8",
        )
    return destination


def run_workflow(
    case_or_workflow: str | Path,
    endpoint: str | None = None,
    apikey: str | None = None,
    model: str | None = None,
    output: Path = Path("./dataset"),
    timeout_seconds: int = 3600,
    idle_timeout_seconds: int = 300,
    keep: bool = False,
    task_id: str | None = None,
    runtime: str = "docker",
    effort: str = "xhigh",
) -> Path:
    """Execute every workflow instruction in order and package the normal trajectory."""
    runtime = runtime.lower().strip()
    if runtime not in {"docker", "local"}:
        raise WorkflowValidationError("runtime must be 'docker' or 'local'")
    credentials = resolve_subject_credentials(endpoint, apikey, model)
    workflow = load_workflow(case_or_workflow)
    spec = build_workflow_task_spec(workflow, task_id=task_id)
    if runtime == "local":
        return run_prepared_task_local(
            task_spec=spec,
            task_dir=workflow.case_dir,
            workspace_dir=workflow.workspace_dir,
            endpoint=credentials.endpoint,
            apikey=credentials.apikey,
            model=credentials.model,
            output=output,
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            keep=keep,
            scripted_instructions=workflow.instructions,
            effort=effort,
        )
    with tempfile.TemporaryDirectory(prefix="tm-workflow-") as temp_dir:
        build_context = prepare_build_context(workflow, Path(temp_dir) / "task")
        return run_prepared_task(
            task_spec=spec,
            task_dir=build_context,
            endpoint=credentials.endpoint,
            apikey=credentials.apikey,
            model=credentials.model,
            output=output,
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            keep=keep,
            scripted_instructions=workflow.instructions,
        )


def _resolve_from_case(case_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else case_dir / path).resolve()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "workflow"


def _default_dockerfile(base_image: str, workspace_relative: Path, has_rubrics: bool) -> str:
    if not _BASE_IMAGE_RE.fullmatch(base_image):
        raise WorkflowValidationError(f"invalid Docker base image: {base_image!r}")
    image_name = base_image.rsplit("/", 1)[-1].lower()
    is_node_image = image_name == "node" or image_name.startswith(("node:", "node@"))
    if not is_node_image or "alpine" in image_name:
        raise WorkflowValidationError(
            "automatic Dockerfile generation requires a Debian-based node image; "
            "provide the declared Dockerfile for this base image"
        )
    copy_workspace = json.dumps(
        [workspace_relative.as_posix(), "/workspace"], ensure_ascii=False
    )
    rubric_lines = ""
    if has_rubrics:
        rubric_lines = (
            "COPY rubrics /workspace/rubrics\n"
            "RUN find /workspace/rubrics -type f -name '*.sh' -exec chmod +x {} +\n"
        )
    return (
        f"FROM {base_image}\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends "
        "python3 python3-pip git ca-certificates && rm -rf /var/lib/apt/lists/*\n"
        "RUN npm install -g @anthropic-ai/claude-code\n"
        "RUN useradd -m -u 1001 agent\n"
        "WORKDIR /workspace\n"
        f"COPY {copy_workspace}\n"
        f"{rubric_lines}"
        "RUN chown -R agent:agent /workspace\n"
        "USER agent\n"
        "ENV HOME=/home/agent\n"
        "ENV LANG=C.UTF-8\n"
        'ENTRYPOINT ["tail", "-f", "/dev/null"]\n'
    )
