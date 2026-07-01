# 06 · 数据打包（package）

## 目标

把一次 run 的全部产物按"一数据一目录"组织，每条数据 = 任务元数据 + 最后一步评分 + 初始/预期/实际环境 + 轨迹文件。

## 最终数据目录

```
<output_root>/<task_id>/<run_id>/        # task_id + run_id 双层，支持同任务多次 run
├── metadata.yaml
├── final_score.json
├── initial_env/
├── expected_final_env/
├── actual_final_env/
└── trajectory.jsonl

<output_root>/index.jsonl               # 全局轻量索引（一行一条数据汇总）
```

- `task_id` 来自 TaskSpec。
- `run_id` 由编排器生成（如 `20260701-1530a7`），同一任务可多次采集不同 model/端点的轨迹而不覆盖。
- `--output` 指定 `<output_root>`，默认 `./dataset/`。

## 各文件内容

### metadata.yaml

任务元数据 + 本次 run 的运行信息（TaskSpec 元数据拷贝 + 运行参数）：

```yaml
task_id: repo-cleanup-unused-imports
run_id: 20260701-1530a7
category: code-refactor
source:
  type: github
  ref: https://github.com/acme/widget
  commit: abc123
initial_instruction: |
  请清理 src/ 下所有 Python 文件中未使用的 import……
objective: |
  全部 src/*.py 无未使用 import；不改变运行逻辑；py_compile 通过。
run:
  endpoint: https://api.example.com        # 端点 host（不含 key）
  model: claude-sonnet-4-6
  started_at: 2026-07-01T15:30:00
  ended_at: 2026-07-01T15:42:11
  termination: completed                    # completed | stopped_without_claim | timeout | max_turns | crashed
  max_turns: 1
  timeout_seconds: 1800
toolchain:
  claude_code_version: 2.1.175
  docker_image_base: node:22
schema_version: 1
```

- **不含任何 apikey**（endpoint 只存 host，key 在清洗时已 `<redacted>`，metadata 里根本不出现）。

### final_score.json

run 结束后跑 rubric+script 验证的结果（复用 `grade()`）：

```json
{
  "task_id": "repo-cleanup-unused-imports",
  "run_id": "20260701-1530a7",
  "score": 0.75,
  "verdict": "partial",
  "termination": "completed",
  "rubric_results": [
    {"id":"r1","type":"checklist","severity":"required","pass":true,"reason":"src/*.py 经 pyflakes 无 unused import"},
    {"id":"r2","type":"script","severity":"required","pass":true,"exit_code":0,"stdout_tail":"OK"},
    {"id":"r3","type":"checklist","severity":"preferred","pass":false,"reason":"test_add.py 仍有一处空行残留"}
  ],
  "required_pass": 2,
  "required_total": 3,
  "timestamp": "2026-07-01T15:42:30"
}
```

- `score`：加权通过率（required 权重 1.0，preferred 权重 0.5，可配）。
- `verdict`：`pass`（required 全过）/ `partial` / `fail`（required 有不过）。
- **这就是"最后一步评分结果"**。

### initial_env/

run 开始后、agent 激活前，从容器 `docker cp` 出的 `/workspace` 快照——干净初始环境（含 setup.sh 执行后的状态）。同时包含本次 build 用的 `Dockerfile` 拷贝。

```
initial_env/
├── Dockerfile
└── workspace/        # = /workspace 快照
```

### expected_final_env/

来自 TaskSpec 的预期终末环境：

```
expected_final_env/
├── description.txt           # expected_final_env.description
├── reference_patch.diff      # expected_final_env.reference_patch（若有）
└── rubrics/                  # rubric 定义拷贝（checklist 文本 + script 文件）
```

### actual_final_env/

run 结束后、销毁容器前，从容器 `docker cp` 出的 `/workspace` 终末快照——agent 实际跑完的环境状态。

```
actual_final_env/
└── workspace/        # = /workspace 终末快照
```

### trajectory.jsonl

清洗去敏后的原生 stream-json 轨迹（见 05-sanitize.md）。

## 打包流程

```
1. run 结束、终末环境已 cp 出（actual_final_env）、rubric 评分已完成
2. 建 <output_root>/<task_id>/<run_id>/
3. 写 metadata.yaml（含 run 信息）
4. 写 final_score.json
5. 拷贝 initial_env/（run 开始时已 cp 的快照 + Dockerfile）
6. 拷贝 expected_final_env/（从 TaskSpec 抽取）
7. 拷贝 actual_final_env/（run 结束时 cp 的快照，评分前已取出）
8. 清洗 trajectory_raw.jsonl → trajectory.jsonl（见 05-sanitize.md）
9. 校验目录完整性（6 个条目齐全、trajectory 合法 jsonl、凭证零命中）
10. 写 _manifest 行到 <output_root>/index.jsonl（追加一行汇总：task_id/run_id/category/score/verdict/termination/路径）
```

- 第 9 步校验失败 → 报错，保留 `<run_id>/` 目录与原始 raw 轨迹供调试。
- `index.jsonl` 是唯一的全局索引（一数据一目录基础上额外加的轻量汇总，便于程序化加载/统计），不改变"一数据一目录"的主结构。

## 与销毁的顺序

打包完成且校验通过 → 才销毁容器与镜像。打包未完成不销毁（`--keep` 或异常时保留便于补救）。

## 不做的事（YAGNI）

- 不做数据集版本化/去重（第一版直接写目录）。
- 不做上传到 HF/远程（落盘即可）。
- 不做数据集分割/train-test 划分（下游的事）。
