# 03 · 阶段 2 — verify（任务验证）

## 目标

在干净任务容器里验证 synthesize 产出的任务**本身是否正确可解**——即一个合格的 agent 按初始指令执行后，rubrics 能判定通过。验证轨迹**不是采集目标**，只产出 pass/fail 与逐项结果。

## 命令

```
trajectory-maker verify <task-dir> [--endpoint ...] [--model ...] [--apikey ...] [--keep-on-fail]
```

## 验证两件事

1. **环境可构建可启动**：Dockerfile 能 build，`init_script` 能跑，容器内 claude code 可用。
2. **任务可解且 rubric 能判**：跑一遍 agent，跑 rubric，看是否能得到明确的 pass/fail。

## 流程

```
1. 加载 TaskSpec（task.yaml → TaskSpec）
2. docker build  <task-dir> → image: tm-verify-<task_id>
3. docker run    后台启动容器，执行 init_script（若有），保持运行
4. 环境冒烟：
   ├─ docker exec <c> claude --version   （claude code 可用？）
   └─ docker exec <c> <init_script 检查>   （workspace 就位？）
   └─ 任一失败 → verify 失败，保留容器日志
5. 跑"验证 agent"：
   ├─ 用 driver 驱动容器内 claude code（用 verify 专用 model，默认与轨迹 model 同或 --verify-model 覆盖）
   ├─ 注入 initial_instruction 作为第一个 user turn
   ├─ agent 自主跑到声明完成（第一版不注入多轮，与 run 阶段逻辑一致）
   └─ 这一程的轨迹只用于 verify，不落盘为最终数据
6. 跑 rubric 评分（核心，见下）：
   ├─ 对每个 rubric：
   │    ├─ script 类  → docker exec 在容器内执行脚本，按 pass_condition 判定
   │    └─ checklist 类 → 另起一个 claude code 无头实例，在终末环境里跑判定 prompt（见下）
   ├─ 汇总为 rubric_results[]
   └─ required 项全过 = verify pass；否则 fail
7. verify pass：
   ├─ 复制终末环境快照到 <task-dir>/_verify_snapshot/（可选，供 run 阶段参考）
   ├─ 写 <task-dir>/verify_result.json
   └─ 销毁验证容器与镜像（tm-verify-<task_id>）
8. verify fail：
   ├─ 写 verify_result.json（含失败 rubric + 理由 + agent 原始事件流到 _verify_log/）
   ├─ 保留容器与镜像（--keep-on-fail）便于调试
   └─ 退出非零码，不进入 run
```

## rubric 评分细节

### script 类（直接执行）

```
docker exec <container> bash -lc '<interpreter> /workspace/rubrics/<script>'
├─ exit_zero        → 退出码 == 0 即 pass
├─ output_contains  → stdout 含 pass_value 即 pass
└─ output_matches   → stdout 匹配 pass_value(regex) 即 pass
超时(timeout_seconds) → fail
```

### checklist 类（claude code 判定）

另起一个独立 claude code 无头实例（与验证 agent 不同实例，避免上下文污染），在**同一个终末容器**里跑判定 prompt：

```
system: 你是严格的任务验收裁判。只能读文件、跑只读诊断命令（如 cat/grep/pyflakes --check）。
        禁止修改任何文件。最终必须调用 StructuredOutput 返回判定。
user:   任务 objective：<objective>
        待判 rubric（id/description/criterion/target_files）：<...>
        请在容器 /workspace 内核查，给出 pass(bool)+reason(string)。
```

- 用 `--allowedTools "Read Glob Grep Bash(cat *) Bash(grep *) Bash(ls *) Bash(pyflakes *)"` 限制为只读。
- 强制结构化输出（pass/reason）。
- 这个判定实例的轨迹同样**不进最终数据**。

> checklist 判定用 claude code（能在终末环境里实际读文件、跑诊断），比纯 LLM judge 准确——这正是选定的"混合：script 直跑 + checklist 用 claude"。

## verify_result.json

```json
{
  "task_id": "repo-cleanup-unused-imports",
  "verdict": "pass | fail",
  "smoke": {"build": true, "init": true, "claude_ok": true},
  "rubric_results": [
    {"id":"r1","type":"checklist","pass":true,"reason":"src/*.py 经 pyflakes 无 unused import 报错"},
    {"id":"r2","type":"script","pass":true,"exit_code":0,"stdout_tail":"..."}
  ],
  "agent_event_log": "_verify_log/agent.jsonl",
  "judge_event_log": "_verify_log/judge_r1.jsonl",
  "timestamp": "2026-07-01T..."
}
```

## 与 run 阶段的关系

- verify 通过后，run 阶段会**重新 build 干净镜像**重新跑（不复用 verify 容器），保证轨迹采集从干净初始环境开始。
- verify 的价值：在烧 token 跑正式轨迹前，先确认任务与 rubric 自洽——避免"任务无解"或"rubric 判不出"白白浪费一次完整轨迹运行。

## 错误处理

- build/冒烟失败 → 立即 fail，不跑 agent。
- 验证 agent 跑飞/超时 → fail，保留日志。
- checklist 判定实例崩溃 → 该 rubric 标 `error`，视为 fail。
- 镜像销毁：pass 一定销毁；fail 默认保留（`--keep-on-fail`），`--no-keep` 强制销毁。

## 不做的事（YAGNI）

- 不做"多次验证取多数"（第一版单次）。
- 不做 verify 阶段的 rubric 自动修复建议。
- 不把 verify 轨迹纳入最终数据集。
