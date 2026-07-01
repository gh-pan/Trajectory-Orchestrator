# 02 · 阶段 1 — synthesize（任务合成）

## 目标

输入一个文件夹（github 仓库或本地文件夹），调用一个 claude code 无头实例，产出一份完整 TaskSpec：`task.yaml` + `Dockerfile` + `workspace/`（含可选 `setup.sh`）+ `rubrics/`（checklist 项 + script 文件）。

## 命令

```
trajectory-maker synthesize <input-folder-or-url> [--output <task-dir>]
```

- `<input-folder-or-url>`：github URL（`https://github.com/owner/repo[.git]` 或 `git@...`）或本地目录绝对/相对路径。
- `--output`：任务产物目录，默认 `./tasks/<task_id>/`。

## 合成用的 claude code 实例

合成本身也是一个 claude code 无头实例，但和轨迹运行的实例职责不同：

- **不在任务 docker 里跑**——它的工作是"读输入文件夹 + 写任务产物"，宿主环境即可（用宿主装的 claude code，`--add-dir` 指向输入文件夹和输出目录）。
- 端点/key/model：用一套**合成专用配置**（默认复用宿主 claude code 的认证，也可 `--synthesize-model` 覆盖）。这是元工作，与轨迹运行用的"被测模型"解耦。
- 权限：`--allowedTools "Read Glob Grep Write Bash(git clone) Bash(git log)"`——允许读输入、写产物、必要时 clone 仓库；禁止其他写操作。

## 合成流程

```
1. 解析输入
   ├─ github URL → 临时 clone 到 /tmp/tm-clone-<hash>，记录 commit
   └─ 本地目录 → 直接用路径（不复制，只读）
2. 准备合成工作区  ./tasks/_synth_<rand>/   （task_id 尚未知，先用临时目录；合成后读出 yaml 里的 task_id 再 rename）
3. driver 驱动 claude code，注入合成 system+user prompt：
   ┌─────────────────────────────────────────────┐
   │ system: 你是 agentic 任务设计专家。          │
   │         严格按 resources/task_schema.yaml     │
   │         产出 task.yaml + 环境文件。           │
   │ user:   输入文件夹：<path>                   │
   │         先通读结构，构思一个真实、自洽、      │
   │         可被 rubric 验证的任务。              │
   │         要求：                               │
   │         1. 任务源自该文件夹的真实内容         │
   │         2. Dockerfile 内置 claude code        │
   │         3. rubrics 必须能判定 objective       │
   │         4. 写出 task.yaml / Dockerfile /      │
   │            workspace/ / rubrics/ 到 output    │
   │         5. 完成后返回 task_id 便于编排器 rename│
   └─────────────────────────────────────────────┘
4. driver 接收事件流：
   ├─ 记录 claude 的写文件操作（tool_use/Write）→ 落到 output 目录
   ├─ claude 声明完成 → 读取它写出的 task.yaml
   └─ 提取 task_id → rename 目录为 ./tasks/<task_id>/
5. 编排器做结构校验（非语义）：
   ├─ task.yaml 存在且符合 schema
   ├─ Dockerfile 存在
   ├─ workspace/ 存在
   ├─ 每个 script 类 rubric 的 run 路径文件存在且可执行
   └─ checklist 类 rubric 字段齐全
   └─ 失败 → 报错并保留 claude 的原始输出供调试
```

## 合成产物目录

```
tasks/<task_id>/
├── task.yaml
├── Dockerfile
├── workspace/
│   ├── setup.sh            # 可选
│   └── <源文件…>
└── rubrics/
    ├── r2_py_compile_check.sh   # script 类 rubric 引用的脚本
    └── ...
```

## Dockerfile 合成要求（约束写进合成 prompt）

合成出的 Dockerfile 必须满足轨迹运行的前置条件：

- 基础镜像含 node（装 claude code 用）+ 任务所需 runtime。
- `npm install -g @anthropic-ai/claude-code`（或宿主约定的版本/方式）。
- workspace 拷入容器工作目录（如 `/workspace`）。
- 不内置任何 ANTHROPIC_* 凭证（运行时由编排器在 `docker exec` 时通过 `env` 注入，仅活于 claude 进程）。
- 默认 entrypoint 留给编排器 `docker exec` 覆盖（如 `sleep infinity`）。

## 关于 task_id 的产生

- 让 claude 在 task.yaml 里填 `task_id`（kebab-case，基于内容语义，如 `repo-cleanup-unused-imports`）。
- 编排器读出后做唯一性检查（与 `tasks/` 下已有目录冲突则报错，要求重生成或人工介入）。
- 第一版不做自动去重，冲突即停。

## 错误处理

- claude 中途崩溃 / 没写出 task.yaml / schema 不符 → 保留 `./tasks/_synth_<rand>/` 全部产物 + claude 原始事件流日志，报错退出，不进入 verify。
- clone 失败 / 输入目录不存在 → 前置报错，不启动 claude。

## 不做的事（YAGNI）

- 不做"一次合成多个候选任务打分选最优"。
- 不做合成结果的人工审核 UI（产物落盘即可人工查看）。
- 不自动修复 schema 不符的 yaml（报错让人看 claude 原始输出）。
