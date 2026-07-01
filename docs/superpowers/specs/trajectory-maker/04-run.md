# 04 · 阶段 3 — run（轨迹采集）

## 目标

用指定的端点/apikey/model，在干净任务容器里跑 claude code，用 `initial_instruction` 激活，采集原生 stream-json 轨迹。第一版：agent 停止输出即结束 → 跑 rubric+script 验证 → 复制终末环境 → 打包 → 销毁。多轮注入留接口不实现。

## 命令

```
trajectory-maker run <task-dir> \
  --endpoint <base_url> \
  --apikey <key> \
  --model <model_id> \
  [--output <dataset-root>] \
  [--max-turns N] [--timeout <seconds>] [--keep]
```

## 流程

```
1. 加载 TaskSpec
2. docker build <task-dir> → image: tm-run-<task_id>-<run_id>
3. docker run 后台启动容器：
   ├─ workspace 落到 /workspace
   ├─ 执行 init_script（若有）→ 得到干净初始环境
   └─ 不在此注入凭证（凭证在 step 5 的 exec 时注入，仅活于 claude 进程，不落容器环境）
4. 复制初始环境快照：docker cp <c>:/workspace → <run_workdir>/initial_env/
5. driver（docker 后端）驱动容器内 claude code，激活并采集轨迹：
   docker exec -i <c> \
     env ANTHROPIC_BASE_URL=<endpoint> \
         ANTHROPIC_API_KEY=<apikey> \
         ANTHROPIC_MODEL=<model> \
         ANTHROPIC_AUTH_TOKEN=<按端点类型，若需> \
     claude --print \
       --input-format stream-json --output-format stream-json \
       --dangerously-skip-permissions \
       --add-dir /workspace \
       --model "$ANTHROPIC_MODEL"
   ├─ stdin 第一个 user message = initial_instruction
   ├─ stdout 事件流逐行落盘 <run_workdir>/trajectory_raw.jsonl
   ├─ 编排器状态机实时消费事件（见下）
   └─ 第一版：不注入后续 prompt
6. 结束条件（任一触发）：
   ├─ agent 声明完成（见"完成判定"）
   ├─ 超时 --timeout
   └─ --max-turns 上限
7. 结束 → 停止记录 trajectory_raw.jsonl
8. 复制终末环境快照：docker cp <c>:/workspace → <run_workdir>/actual_final_env/
   （必须在 grade 之前，以免评分脚本副作用污染 agent 的真实终末状态）
9. 跑 rubric+script 验证（复用 verify 阶段的评分原语 grade()，在仍存活的容器内读 /workspace）
10. 清洗去敏 trajectory_raw.jsonl → trajectory.jsonl（见 05-sanitize.md）
11. 打包为最终数据目录（见 06-package.md）
12. 销毁：docker stop/rm 容器 + docker rmi 镜像（--keep 则保留）
```

## 完成判定（第一版）

stream-json 在 `--print` 模式下，agent 跑完一个完整 agentic turn（可含多次 tool_use/tool_result）后会发出 `result` 事件并结束当前 turn。第一版的"agent 声明完成"判定：

```
判定为完成，当且仅当：
  收到 type=result 事件（agent 结束当前 turn，停止输出）
  且 扫描最后一个 assistant 文本消息，含完成声明语义
      （"完成"/"已完成"/"finished"/"done"/"任务完成"/"all done"/"完成了" 等）
```

- **不约束 agent 用任何特定标记**——它自然怎么说都行，编排器扫最后输出的语义即可。
- 合成阶段的 `initial_instruction` 不加任何完成标记约束，保持指令自然。
- 收到 `result` + 末轮输出含完成语义 → 判定完成，停止采集，`termination=completed`。
- 收到 `result` 但末轮无完成语义 → 第一版也视为结束（agent 自主停止即结束），`termination=stopped_without_claim`，rubric 反映真实完成度。
- 超时 / max-turns → 强制结束，`termination=timeout|max_turns`。

> 这条规则把"agent 声明完成即结束"落到可执行的信号上，同时不强迫 agent——它自然停止也结束，只是评分时 rubric 会反映真实完成度。

## rubric+script 验证（复用 verify 评分原语）

run 阶段结束后的验证与 verify 阶段**同一套评分代码**（抽成 `grade(container, task_spec)` 公共函数）：

- script 类 → docker exec 在终末容器内执行
- checklist 类 → 独立 claude code 无头实例 + 只读工具 + 结构化输出
- 产出 `final_score.json`（schema 见 06-package.md）

**关键**：此验证轨迹**不混入**采集的 trajectory.jsonl——判定实例是独立 session，其事件流单独存到 `_judge_log/`（可选保留供调试），最终数据里的 trajectory.jsonl 只含被测 agent 的轨迹。

## 编排器状态机（含多轮注入预留接口）

第一版状态机简化为"采集 + 结束判定"，但接口设计好以支持未来多轮：

```
states: ACTIVATING → RUNNING → DONE
events: system_init / assistant / tool_use / tool_result / user(injected) / result / error / timeout

第一版转换：
  ACTIVATING --(发送 initial_instruction)--> RUNNING
  RUNNING    --(收到 result+完成声明)--> DONE
  RUNNING    --(timeout/max_turns)--> DONE

预留钩子（第一版不触发，但 driver 支持写 stdin）：
  RUNNING --(检测到"未完成/等待输入"信号)--> INJECTING
  INJECTING --(写 stdin user message)--> RUNNING
```

- `driver` 提供 `inject(user_text)` 方法（写一行 `{"type":"user","message":{"role":"user","content":[{"type":"text","text":...}]}}` 到 stdin）。
- 第一版不调用 `inject`，但保留该方法与状态机的 `INJECTING` 分支，未来多轮直接接上。
- 这保证未来扩展时，注入的 prompt 是**标准 user turn**写入 trajectory（driver 会把注入的 user message 也补记进 raw 轨迹，保证对话完整），不引入任何 hook 污染。

## 超时与资源

- `--timeout`：整个 run 的墙钟上限（默认 1800s）。
- `--max-turns`：agentic turn 上限（默认 1，即第一版只跑一个激活 turn；未来多轮可调大）。注：一个 turn 内可有多次 tool 调用。
- docker 容器设内存/CPU 上限（默认 `--memory=2g --cpus=2`，可配）。

## 错误处理

- build 失败 → 报错退出，不创建容器。
- 容器启动/init 失败 → 报错，清理容器。
- agent 进程崩溃 / claude code 退出非零 → 保留已采集的部分 trajectory_raw.jsonl，标记 `termination=crashed`，仍跑 rubric（能跑则跑）并打包（trajectory 为部分轨迹）。
- 端点不可达 / 鉴权失败（stream-json 早期 error 事件）→ 立即终止，不打包（因为轨迹无意义），报错。
- **任何路径都在 finally 里销毁容器与镜像**（除非 `--keep`）。

## 不做的事（YAGNI）

- 第一版不做多轮注入（留接口）。
- 不做并行多任务 run（单任务单容器）。
- 不做轨迹的实时改写/截断（原样采集，清洗在事后）。
- 不录制终端录像（只存 stream-json）。
