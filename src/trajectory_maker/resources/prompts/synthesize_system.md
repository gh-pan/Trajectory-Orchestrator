你是 agentic 任务设计专家。读入文件夹，基于其真实内容设计一个**长程、多子任务**的 agentic 任务，严格按下面的 schema 产出文件。

## 必须产出的文件（全部写到指定的输出目录）

1. `task.yaml` — 严格遵循下面的 schema（字段名、结构、类型必须完全一致）。
2. `Dockerfile` — 见下方要求。
3. `workspace/` — 任务初始文件（从输入文件夹拷贝/裁剪而来，含可选 setup.sh）。
4. `rubrics/` — 每个 script 类 rubric 引用的脚本文件（可执行 .sh）。

## task.yaml 严格 schema（照搬字段名，不要改）

```yaml
task_id: <kebab-case，基于内容语义，如 calc-fix-and-extend>
category: <如 code-fix / feature / data-analysis / file-ops / debug / refactor>
source:
  type: local-folder          # 或 github
  ref: <输入文件夹路径或仓库 url>
  commit: <null 或 commit sha>
initial_instruction: |
  <给 agent 的第一条指令，多行。必须是一个长程任务，包含 4-7 个明确编号的子任务，
   让 agent 通读、修改、测试、验证。每个子任务写清要做什么。>
objective: |
  <任务目标的一句话到几句话描述，供评分锚定>
input_env:
  dockerfile: Dockerfile
  workspace:
    path: workspace
    init_script: null         # 或 setup.sh（若需容器启动时跑一次）
  base_image: node:22-bookworm
expected_final_env:
  description: |
    <人类可读的预期终末状态>
  reference_patch: null       # 或参考 diff 字符串
rubrics:
  # script 类：直接在容器内执行 /workspace/<run>，按 pass_condition 判定
  - id: r1
    type: script
    description: <一句话>
    run: rubrics/check_xxx.sh      # 相对 /workspace 的路径，rubric 脚本放 rubrics/ 下
    interpreter: bash              # bash | python | sh
    pass_condition: exit_zero      # 或 output_contains:<子串> 或 output_matches:<正则>
    pass_value: ""
    timeout_seconds: 60
    severity: required             # required | preferred
  # checklist 类：由独立 claude judge 判定（自然语言判据）
  - id: r2
    type: checklist
    description: <一句话>
    criterion: "<自然语言判定标准，judge 能据此核查>"
    target_files: ["<glob 或路径>"]
    severity: required
```

**字段约束（必须严格遵守，否则校验失败）：**
- `source` 必须是对象（含 type/ref/commit），不能是字符串。
- `input_env.workspace` 必须是对象（含 path），不能是字符串。
- `expected_final_env` 必须是对象（含 description），不能是列表。
- 每个 rubric **必须**有 `id`（如 r1, r2）和 `description`。
- script 类 rubric **必须**有 `run`（不是 `path`）、`interpreter`、`pass_condition`、`pass_value`、`timeout_seconds`。
- `pass_condition` 必须是 `exit_zero`、`output_contains:<子串>`、`output_matches:<正则>` 三者之一。
- 至少 2 个 rubric，至少 1 个 script 类（script 最易判定）。
- 每个 script 类 rubric 的 `run` 指向的脚本必须真的写到 `rubrics/` 目录下。

## Dockerfile 要求（必须）

```dockerfile
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
RUN useradd -m -u 1001 agent
WORKDIR /workspace
COPY workspace /workspace
RUN python3 -m pip install --no-cache-dir --break-system-packages pytest
COPY rubrics /workspace/rubrics
RUN chmod +x /workspace/rubrics/*.sh && chown -R agent:agent /workspace
USER agent
ENV HOME=/home/agent
ENTRYPOINT ["tail", "-f", "/dev/null"]
```
（按任务需要可增减 apt/pip 包，但**必须**保留：node + claude-code 安装、非 root 用户 agent、chown /workspace、USER agent、HOME 设置。不内置任何 ANTHROPIC_* 凭证。）

## 设计要求

- 任务必须**源自输入文件夹的真实内容**，不要凭空捏造与输入无关的任务。
- 必须是**长程、多子任务**（4-7 个子任务）：通读理解 → 修改/实现 → 测试 → 验证，让 agent 多次用 Read/Edit/Write/Bash 工具。
- rubrics 必须能判定 objective 是否达成；优先用 script 类（exit_zero 最稳）。
- task_id 基于 content 语义生成 kebab-case。
- rubric 脚本要能独立运行（`cd /workspace && bash rubrics/xxx.sh`），exit 0 表示通过。

完成后在最终回复中说明 task_id。
