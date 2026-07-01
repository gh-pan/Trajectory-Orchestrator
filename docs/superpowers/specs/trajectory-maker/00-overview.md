# 00 · 总体架构与目录结构

## 三阶段流水线

整个系统是一条三阶段流水线，每阶段都复用同一个核心执行原语 **`driver`**（驱动 claude code 无头实例的 stream-json 双向流）：

```
输入文件夹 ──▶ [1. synthesize] ──▶ TaskSpec(yaml+dockerfile+workspace+rubrics)
                                    │
                                    ▼
                              [2. verify] ──▶ 验证通过？
                                    │            ├─ 否 → 退出/标记
                                    │            └─ 是
                                    ▼
                              [3. run] ──▶ 采集 trajectory.jsonl
                                    │
                                    ▼
                              [清洗去敏 + 打包] ──▶ 一数据一目录
                                    │
                                    ▼
                              销毁 docker 镜像
```

- **第 1 阶段 synthesize**：调一个 claude code 无头实例，读输入文件夹，生成任务 yaml + Dockerfile + workspace + rubrics。
- **第 2 阶段 verify**：在干净任务容器里验证任务本身是否正确可解。rubric 为 script 类→直接执行；rubric 为 checklist 类→另起一个 claude code 无头实例跑判定 prompt。验证轨迹不是采集目标，只产出 pass/fail。
- **第 3 阶段 run**：用指定端点/key/model 在任务容器里跑 claude code，初始指令激活，采集 stream-json。第一版：agent 停止输出即结束 → 跑 rubric+script 验证 → 复制终末环境 → 打包 → 销毁。多轮注入留接口，暂不实现。

## 核心原语 `driver`

三阶段统一通过 `driver` 驱动 claude code。`driver` 抽象的是"一个 stream-json 双向流的 claude 进程"，底层有两种启动后端，上层接口统一：

- **local 后端**（synthesize 用）：宿主直接 `subprocess` 拉起 `claude --input-format stream-json --output-format stream-json`，`--add-dir` 指向输入文件夹与产物目录。不经 docker。
- **docker 后端**（verify/run 用）：`docker exec -i <container> claude --input-format stream-json --output-format stream-json --print`，拿到容器内的 claude 双向管道。

两种后端对上层暴露同一接口：

- 读 stdout = 事件流（system/assistant/tool_use/tool_result/user/result/error），写 stdin = 注入 user message。
- 编排器**只通过 stdin 注入标准 user message**，不挂 hook、不改 agent 行为 → 轨迹干净，注入的 prompt 就是正常 user turn。
- `driver` 提供 `inject(user_text)` 方法：写一行 `{"type":"user","message":{"role":"user","content":[{"type":"text","text":...}]}}` 到 stdin。第一版不调用，但保留以支持未来多轮。

## 方案选型

采用 **方案 A：分层子进程编排 + 事件回调架构**（对比过纯流式流水线、Agent SDK 两方案，详见头脑风暴记录）。要点：编排器内部是事件驱动状态机，轨迹 = stream-json stdout 原样落盘，三阶段执行原语统一。

## 项目目录结构（仓库本身）

```
Trajectory-Maker/
├── pyproject.toml                    # uv 管理
├── README.md
├── src/trajectory_maker/
│   ├── cli.py                        # 子命令入口：synthesize / verify / run / all / clean
│   ├── config.py                     # 端点/key/model 配置
│   ├── driver.py                     # 核心原语：stream-json 双向流（local/docker 两后端）
│   ├── docker.py                     # 容器生命周期（build/run/exec/cp/rm）
│   ├── synthesize.py                 # 阶段1
│   ├── verify.py                     # 阶段2（script直跑 + checklist用claude）
│   ├── run.py                        # 阶段3
│   ├── grade.py                      # 公共评分原语 grade(container, task_spec)
│   ├── orchestrator.py               # 事件状态机（含多轮注入预留接口）
│   ├── sanitize.py                   # 清洗去敏
│   ├── package.py                    # 数据打包
│   ├── models.py                     # TaskSpec 等数据模型（pydantic）
│   └── resources/
│       ├── task_schema.yaml          # 任务 yaml schema 定义
│       ├── sanitize_rules.yaml       # 清洗规则配置
│       └── prompts/                  # synthesize/verify/system 等 prompt 模板
├── tests/
└── docs/superpowers/specs/
```

## 输出数据集布局（一数据一目录）

```
<output_root>/<task_id>/<run_id>/
├── metadata.yaml          # 任务元数据 + 本次 run 信息
├── final_score.json       # 最后一步评分结果
├── initial_env/           # 初始环境（Dockerfile + workspace 初始快照）
├── expected_final_env/    # 预期终末环境（rubric 期望/参考）
├── actual_final_env/      # trajectory 跑完从容器复制的终末环境
└── trajectory.jsonl       # 原生 stream-json 轨迹（清洗去敏后）

<output_root>/index.jsonl  # 全局轻量索引（一行一条数据汇总）
```

- `task_id` 来自 TaskSpec，`run_id` 由编排器生成，同任务可多次 run 不覆盖。
- `index.jsonl` 是一数据一目录基础上额外加的轻量汇总，便于程序化加载/统计，不改变主结构。
