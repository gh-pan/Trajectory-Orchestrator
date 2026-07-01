# 01 · 任务 yaml schema

对齐社区标准（SWE-bench 的 `instance_id`/`problem_statement`/`FAIL_TO_PASS`/`PASS_TO_PASS`，Cybench 的 `Dockerfile`+`setup.sh`+`eval.sh` 模式），同时覆盖全部要素。schema 由 synthesize 阶段生成，verify/run 阶段消费。

## 完整 schema

```yaml
# ===== 标识 =====
task_id: repo-cleanup-unused-imports        # 唯一 ID（合成时由 claude 按内容生成 kebab-case）
category: code-refactor                      # 任务类别（如 code-refactor/bugfix/test-writing/devops/debug）
source:                                      # 输入来源溯源
  type: github | local-folder
  ref: https://github.com/acme/widget  或  /path/to/folder
  commit: <sha>                              # 仅 github 类型，可空

# ===== 任务描述 =====
initial_instruction: |                       # 给 agent 的第一条 user prompt（激活用）
  请清理 src/ 下所有 Python 文件中未使用的 import，并移除因此产生的空行。
objective: |                                 # 任务目标（供 verify/编排器判定 + rubric 锚定）
  全部 src/*.py 文件中无未使用 import；不改变任何运行逻辑；py_compile 通过。

# ===== 输入环境（agent 启动前） =====
input_env:
  dockerfile: Dockerfile                     # 相对 task 目录的 Dockerfile 路径（装好 claude code + 依赖 + workspace）
  workspace:                                 # workspace 内容
    path: workspace                          # 相对 task 目录的目录
    init_script: setup.sh                    # 可选，容器启动后、agent 激活前跑一次（如 git checkout base_commit）
  base_image: node:22                        # 可选，供合成时参考；以 Dockerfile 为准

# ===== 预期终末环境 =====
expected_final_env:
  description: |                             # 人类可读的预期结果
    src/ 下所有 .py 经 lint 检查无 unused-import；py_compile 全通过；无逻辑改动。
  reference_patch: |                         # 可选，参考 diff（来自源仓库的 ground-truth，若有）
    diff --git ...

# ===== Rubrics =====
rubrics:
  # —— checklist 类（LLM/claude 判定）——
  - id: r1
    type: checklist
    description: 不存在未使用的 import
    criterion: "src/**/*.py 中任意文件经 pyflakes 检查无 'unused import' 报错"
    target_files: ["src/**/*.py"]
    severity: required                       # required | preferred

  # —— script 类（直接执行，退出码/输出判定）——
  - id: r2
    type: script
    description: py_compile 全通过
    run: py_compile_check.sh                 # 相对 task 目录的脚本路径
    interpreter: bash                        # bash | python | sh
    pass_condition: exit_zero                # exit_zero | output_contains:<substr> | output_matches:<regex>
    pass_value: ""                           # 配合 output_contains/matches
    timeout_seconds: 120
```

## 字段说明与设计取舍

- **`task_id` / `category` / `source`**：对齐 SWE-bench `instance_id`，并加 `source` 溯源输入文件夹，便于回查。
- **`initial_instruction` vs `objective`**：分离"给 agent 看的激活指令"和"给验证/编排器看的目标"。前者是 trajectory 第一个 user turn，后者不直接进 trajectory（仅用于判分与未来多轮注入的提示生成）。
- **`input_env`**：`dockerfile` 必须在镜像内装好 claude code（宿主编排器只 `docker exec` 进去调它）；`init_script` 对齐 SWE-bench 的 `environment_setup_commit`/`base_commit` checkout 概念。
- **`expected_final_env.reference_patch`**：对齐 SWE-bench `patch`（ground-truth）。可选，仅作参考，不作为唯一判据——真正的判据是 rubrics。
- **`rubrics` 数组，两种 type**：
  - `checklist`：自然语言判据，由 verify 阶段调 claude code 在终末环境里跑判定 prompt（可读文件、跑诊断命令），返回 pass/fail + 理由。
  - `script`：可直接执行的脚本，对齐 Cybench `eval.sh` 与 SWE-bench `FAIL_TO_PASS`（exit_zero / output_contains / output_matches 三种 pass_condition 覆盖常见判定）。
- **`severity`**：区分必须项（required）与加分项（preferred），便于 final_score 聚合。

## TaskSpec 内部模型

yaml 加载后转为 pydantic 模型 `TaskSpec`，三阶段统一操作该对象，避免到处解析裸 yaml。模型字段与上述 schema 一一对应。
