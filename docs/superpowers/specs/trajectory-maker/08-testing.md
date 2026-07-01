# 08 · 测试策略

## 测试分层

| 层 | 范围 | 工具 | 速度 |
|---|---|---|---|
| 单元 | 纯函数：schema 校验、清洗规则、事件解析、完成判定、score 聚合 | pytest | 快 |
| 集成 | docker build/run/exec/cp 生命周期、driver stream-json 双向流、grade() 评分 | pytest + 真实 docker | 中 |
| 端到端 | synthesize→verify→run→package 全流程，含真实 claude code | pytest（标记 `e2e`）+ 真实端点 | 慢 |

## 单元测试

不依赖 docker / claude，纯逻辑：

- **schema 校验**：`TaskSpec` 从 yaml 加载，合法样本通过、缺字段/类型错样本拒绝。
- **sanitize**：构造含 key/path/session_id 的 fixture 事件流 → 清洗 → 断言凭证零命中、路径规范化、元数据字段处理、事件数不变、合法 jsonl。
- **事件解析**：stream-json 各 type 事件（system/assistant/user/result/error）解析为结构化对象。
- **完成判定**：给末轮 assistant 文本，含完成语义→completed；无→stopped_without_claim；timeout/max_turns 各自正确标记。
- **score 聚合**：required/preferred 加权、verdict 判定（pass/partial/fail）。
- **rubric pass_condition**：exit_zero/output_contains/output_matches 三种判定逻辑。
- **task_id 唯一性检查**：与已有目录冲突 → 报错。

## 集成测试

依赖真实 docker，但用**最小 fixture 任务**（不调真实端点）：

- **fixture 任务**：`tests/fixtures/echo_task/`——一个极简任务：workspace 里放一个 `hello.txt`，初始指令"在 /workspace 下创建 done.txt 内容为 hello"，rubric 为 script（`test -f /workspace/done.txt && grep -q hello`）。Dockerfile 装 claude code。
- **docker 生命周期**：build→run→exec→cp_from→stop→rm→rmi 全链路，含异常路径（build 失败、容器不存在）。
- **driver 流**：用一个 mock stdin/stdout 代替真实 claude（喂预录的 stream-json 事件流 fixture），验证 driver 正确解析事件、写 stdin 注入、检测结束。
- **grade()**：对 fixture 任务终末环境跑评分，script 类直接判，checklist 类用一个"假判定"（mock claude 返回固定 StructuredOutput）验证流程。

## 端到端测试（`e2e` 标记，默认不跑）

- 需要真实端点 + apikey + model。
- 跑 `trajectory-maker all tests/fixtures/echo_task_source/`，断言产物目录齐全、trajectory.jsonl 合法、final_score 合理、容器/镜像已销毁。
- CI 默认跳过，本地手动 `pytest -m e2e` 或专门脚本触发。

## fixture 与录制

- `tests/fixtures/`：echo_task（最小任务）、各种 stream-json 事件流录制（system_init.jsonl、tool_use_result.jsonl、result_complete.jsonl、error_auth.jsonl 等）。
- 录制文件作为 driver 单元/集成测试的"假 claude 输出"，避免测试依赖真实 API。

## 测试覆盖目标

- 核心纯逻辑（sanitize、解析、判定、score）单元测试覆盖率 > 90%。
- docker 生命周期与 driver 流有集成测试覆盖。
- e2e 至少一条 happy path。

## 不做的事（YAGNI）

- 不 mock docker（集成测试用真实 docker，保证可信）。
- 不做性能/负载测试。
- 不做多模型矩阵 e2e（第一版单模型 happy path）。
