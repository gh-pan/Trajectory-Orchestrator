# 09 · 多轮注入 + API 调用层轨迹采集（run 阶段升级）

> 状态：设计草案，待评审。
> 前序：`04-run.md`（单 turn 采集）、`05-sanitize.md`（清洗）、`06-package.md`（打包）。
> 本次升级 run 阶段：单 turn → 长链路多轮注入；stream-json 事件层采集 → HTTP 拦截的 API 调用层采集（对齐预期样本 `req_*.json`）。

## 目标

1. **采集层对齐样本**：每次被测 claude 发往 `/v1/messages` 的 API 调用，产出一份 `req_<uuid>.json`，结构与预期样本完全一致（`request.*` = 提升 + 清洗后的 API body；`response.response_data` = SSE 解码拼装的完整 message；顶层 `session_id/request_id/timestamp/thinking_effort/is_garbled` 由采集系统补全）。
2. **多轮注入**：首轮 `initial_instruction` 激活被测 claude 后，由一个独立的常驻 **user-agent**（skill 激活的 agentic，带跨轮记忆）在被测 agent 每次"说人话"（end_turn）后程序化注入一句反应式 user turn，推进长链路任务。
3. **不露馅**：注入的 user turn 必须是标准 user message，痕迹仅限正常 user turn；采集产物里不得出现任何机器注入、外部注入的元数据线索。
4. **跑通**：一条真实 task（现有 `tasks/` 之一）能端到端跑通，产出对齐样本的 `req_*.json` 集合。

不动 synthesize / verify / clean，不扩展 task schema（沿用现有 task.yaml + `workspace/` 单目录）。

## 逆向结论（采集途径已证实）

对 claude 原生二进制（`@anthropic-ai/claude-code-darwin-arm64/claude`，224MB Mach-O）逐字段核对：

- 样本 `request.*` 顶层字段（model/messages/system/tools/metadata/max_tokens/thinking/output_config/stream/temperature/top_p）全部是 claude 真实发给 `/v1/messages` 的 API body，二进制里命中。
- 样本顶层元数据 `thinking_effort` / `is_garbled` / `response_data` 在二进制里 **0 命中** —— 这些是采集系统加的，claude 原生不产出。
- `raw_calls` / `.claude_raw_calls` 字面在二进制里 0 命中 —— 那是采集系统自建目录，非 claude 干的。
- 无原生 API 录制开关：`--debug api` 只落生命周期日志（不含 req body / SSE）；`ANTHROPIC_LOG` 在原生二进制无效；`CLAUDE_CODE_REMOTE_RAW_EVENTS_FILE` 是 Anthropic 自家容器运行时的内部机制，独立用不起来。
- SDK 路线走不通：claude-code 包 `main=None`，`sdk-tools.d.ts` 仅工具 schema，无可注入自定义 `fetch` 的 SDK 入口。

**结论**：采集途径 = HTTP 拦截（透明代理），与 claude-trace 同路线，再做两步转换。claude 二进制确认尊重 `ANTHROPIC_BASE_URL`，故采 B1：本地 plain-HTTP 代理。

## 总体架构

```
                          ┌─────────────────────────────────────────────────────┐
                          │  Trajectory-Maker 主进程 (Python)                    │
                          │                                                      │
                          │  orchestrator.run_multi()                            │
                          │   ├─ 起本地 HTTP 代理 (recording_proxy, 线程)         │
                          │   ├─ 起常驻 user-agent (Driver.local + skill)        │
                          │   ├─ docker build/run 容器                            │
                          │   ├─ 容器内起被测 claude (ANTHROPIC_BASE_URL→代理)    │
                          │   │      driver.send_user_message(initial_instruction)│
                          │   ├─ 事件循环: 读 stream-json 事件                    │
                          │   │      ├─ result/end_turn → 交 user-agent 接话     │
                          │   │      │      user-agent 产 1 句 → 注入被测 stdin  │
                          │   │      └─ timeout/max_turns → 停                    │
                          │   ├─ 代理产 raw_calls/*.jsonl (req body + SSE raw)   │
                          │   ├─ convert: raw_calls → req_<uuid>.json (两步转换) │
                          │   ├─ cp actual_final_env → grade → package          │
                          │   └─ 销毁                                              │
                          └─────────────────────────────────────────────────────┘
                                                    │
                          代理转发 (HTTPS) ─────────┼──────► 真 endpoint
```

三件事叠加在 run 阶段：① 录制代理；② user-agent 多轮接话；③ raw→req 转换器。三者都新增模块，run.py 重构为多轮编排。

## 组件

### 1. `recording_proxy.py` — 录制用透明 HTTP 代理

本地 plain-HTTP 服务（Python 标准库 `http.server` + `ThreadingHTTPServer`，或 `aiohttp`；见选型）。监听 `127.0.0.1:<auto-port>`。

职责：
- 收到 `POST /v1/messages`（被测 claude 发来）→ 读完整 req body（JSON）→ 录制 → 转发到真 endpoint（`ANTHROPIC_BASE_URL` 真值）的 HTTPS。
- 真 endpoint 返回 SSE 流 → 边透传回被测 claude、边落 `response.body_raw`（原始 SSE 文本）。
- 每个 req/resp 对落一条到 `<run_workdir>/raw_calls/<req_uuid>.jsonl`（与 claude-trace `RawPair` 同构：`{request:{url,headers(脱敏),body}, response:{status_code,headers(脱敏),body_raw}}`）。**不在此做提升/解码**，那是转换器的事，保持代理只管"忠实录制"。
- 只拦 `/v1/messages`（与 claude-trace 默认过滤一致；其他路径透传不录）。
- 凭证：被测 claude 的 Authorization header 原样转发给真 endpoint（代理自己不持有 key，避免凭证落代理日志；代理日志只留脱敏 headers）。

**req_uuid**：代理为每对生成 `req_<32hex>`，与最终 `req_*.json` 文件名一致。

**端口/生命周期**：起一个线程跑 `serve_forever`，run 结束在 finally 里 `shutdown()`。绑定端口 0 让 OS 分配，回传给被测 env。

### 2. `user_agent.py` — 常驻 user-agent（skill 激活的 agentic）

一个常驻 `Driver.local` claude 进程，跑在宿主（非容器），用 meta endpoint（`.claude-config/` + `CLAUDE_CONFIG_DIR` 隔离，绕开 cc-switch）。带一个**真人互动语料 skill** 激活。

职责：
- 跨轮有记忆：自己的 stream-json 对话历史。
- 每次被测 agent end_turn，编排器把"被测这轮的可见输出"作为 user 消息喂进 user-agent，user-agent 回**一句**注入文本。
- 回的这句由编排器 `driver.send_user_message()` 注入被测 stdin —— 标准用户轮次，无 hook。
- user-agent 的 API 调用**绝不**经过录制代理（它用 meta endpoint，与被测 endpoint 不同；即使同 endpoint 也走直连，不进代理），保证 user-agent 轨迹不混入被测 `req_*.json`。

**隔离性**：user-agent 只"看"被测、不参与被测的对话上下文。它看到的是被测每轮的 assistant 文本（必要时含 tool_use 摘要），不是被测的完整 messages 数组。

**skill 语料**：`resources/skills/user-reactor/SKILL.md`（新建），定义"如何像真人一样接话/纠偏/追问/收尾"的语料与风格准则（casual、简洁、中英混合随任务、带任务推进感、不带机器腔）。skill 装载走 `--add-dir` 指向 skill 目录 + `CLAUDE_CODE_INVOKED_SKILLS` 或 skill 自动发现（实现时定）。

### 3. `convert.py` — raw → req 转换器（两步转换）

读 `raw_calls/<req_uuid>.jsonl` 的 RawPair，产出 `req_<uuid>.json`。两步：

**Step 1 — 提升**：`request.body.*` 提升一层 → `request.*`（model/messages/system/tools/metadata/max_tokens/thinking/output_config/stream/temperature/top_p）。丢弃 `request.url`/`request.headers`（样本顶层不含这些）。

**Step 2 — SSE 解码**：`response.body_raw`（`data: {...}\n` 流）→ 完整 Anthropic message，落到 `response.response_data.*`。解码逻辑参考 claude-trace `reconstructMessageFromSSE`：聚合 `message_start` / `content_block_start` / `content_block_delta`(text_delta/input_json_delta/thinking_delta/signature_delta) / `content_block_stop` / `message_delta`(stop_reason/usage) / `message_stop`。`tool_use.input` 的 `input_json_delta` 累积后 `JSON.parse`。

**顶层补全**：
- `session_id`：本次 run 的固定 session id（run 启动时生成一个 uuid，所有 req 共用，对齐样本）。
- `request_id`：`req_<uuid>`（与文件名、代理生成的 req_uuid 一致）。
- `timestamp`：该 API 调用发起时刻（ISO 8601 Z，从代理录制的 request.timestamp 转）。
- `thinking_effort`：从 `request.output_config.effort` 推导（样本里 = `xhigh`）。
- `is_garbled`：默认 `false`（实现时可加 SSE 解码完整性自检：聚合后 content blocks 无破损则 false，否则 true）。

输出目录 = `<output_root>/<task_id>/<run_id>/<session_id>/req_<uuid>.json`（对齐样本"一 session 一目录、目录名 = session_id、里面一堆 req_*.json"）。

### 4. `orchestrator.py` 扩展 — 多轮状态机

现有 `detect_termination` 保留。新增 `run_multi()` 编排（run.py 调用）：

```
states: ACTIVATING → RUNNING → USER_TURN → RUNNING → ... → DONE
事件:
  ACTIVATING --send initial_instruction--> RUNNING
  RUNNING --收到 result 且末轮 end_turn/无 tool_use--> USER_TURN
  USER_TURN --user-agent 产出一句 + send_user_message--> RUNNING
  RUNNING --收到 result 且末轮 tool_use--> RUNNING (tool_result 自动回填，不接话)
  任一 --timeout/max_turns--> DONE
  任一 --被测 claude 退出且无新 result--> DONE (stopped_without_claim/crashed)
```

- **接话触发条件**：被测 agent 的 `result` 事件到来，且最后一个 assistant 文本块存在且**无后续 tool_use**（即 end_turn，agent 把话说完等用户）。tool_use 轮不接话（被测自己跑 tool_result 回填）。
- **结束条件（C 方案）**：被测自主停。即：被测的 `result` 末轮声明完成语义（复用现有 `COMPLETION_PHRASES`），**或** user-agent 判断"被测已无新进展/已收尾"而选择不再接话（user-agent 可回空/哨兵 → 编排器收尾），**或** timeout/max_turns 兜底。**不跑 rubric 判停**（用户明确否决，保持简单）。
- **轮次预算**：`--max-turns` 默认调大到支持长链路（如 20），`--timeout` 默认放宽。墙钟 + idle 看门狗保留。

## 数据流（一次 run 的时序）

```
1. run_multi 启动 → 生成 run_id, session_id, work_dir
2. 起 recording_proxy (127.0.0.1:P0) → 回传 P0
3. 起 user-agent (Driver.local, meta endpoint, skill) → 发 system+首轮喂入"任务上下文摘要"
4. docker build/run 容器 → cp initial_env 快照
5. 被测 driver: ANTHROPIC_BASE_URL=http://127.0.0.1:P0
   drv.send_user_message(spec.initial_instruction)
6. 事件循环:
   for ev in drv.events():
       代理同步落 raw_calls/<req_uuid>.jsonl (每个被测 API 调用)
       if ev.type==result and 末轮 end_turn:
           把被测末轮文本喂 user-agent → user-agent 回一句
           if 回句是收尾哨兵 or 完成语义: break
           else drv.send_user_message(回句)
       if timeout/max_turns: break
7. cp actual_final_env → grade → 转换 raw_calls → req_*.json → package → 销毁
```

## 清洗去敏（复用 + 扩展）

转换后的 `req_*.json` 仍要走 sanitize（复用 `sanitize.py` 的凭证/路径/元数据规则），但要适配新结构：

- 凭证：`request.metadata.user_id` 里的 `device_id`（样本里是个 64hex）需脱敏；Authorization header 代理已脱敏不进产物。
- 路径：被测在容器内 `/workspace`，相对干净；user-agent 在宿主，其路径不应进被测产物（本就隔离）。
- 元数据：`session_id` 是 run 生成的（非真实），保留即可；`request_id` 保留。
- **不露馅检查（新增 sanitize 规则）**：扫描 `request.messages`/`system`/`response.response_data.content`，剔除任何含"注入/agent/orchestrator/proxy/record"等机器痕迹的字面（user-agent 的 system prompt 自身不进被测产物，但要保证被测 messages 里不残留编排器元信息）。

`sanitize_rules.yaml` 扩展相应条目。

## 不露馅（重点约束）

1. 注入的 user turn = 纯文本标准 user message，经 `driver.send_user_message()` 写 stdin，与真人打字无异。**不挂 hook、不改 system prompt、不塞 system-reminder**。
2. user-agent 的存在对被测不可见：被测的 messages 里只有 user 文本，没有"user-agent 生成"的元数据。
3. 采集产物顶层元数据（session_id 等）是采集系统补的，与"是否机器注入"无关，符合样本格式本身。
4. 容器内跑被测时隔离宿主 plugin/hook（现状已靠容器隔离；本地 user-agent 跑在宿主会撞 superpowers hook，需 `--bare` 或隔离 `CLAUDE_CONFIG_DIR`，见错误处理）。

## 错误处理

- 代理起不来/端口占用 → 绑定端口 0 由 OS 分配，杜绝冲突；起不来则 run 失败退出。
- 代理转发失败（真 endpoint 不可达/鉴权失败）→ 透传错误响应给被测，被测收到 error 事件 → 编排器 `auth_error` 短路，不打包（与现有 run.py 一致）。
- SSE 解码失败（body_raw 截断/非 JSON）→ 该 req 的 `response.response_data` 尽力聚合，`is_garbled=true`，不中断 run。
- user-agent 卡住/不回 → 给 user-agent 单次调用加 idle 超时，超时则编排器用预设的兜底跟进（或判停收尾），不阻塞被测。
- 被测 claude 崩溃 → 保留已采集的 raw_calls + 部分转换产物，`termination=crashed`，仍 package（部分轨迹）。
- **本地 user-agent 撞宿主 hook**：用 `--bare` 启动 user-agent（跳过 hooks/LSP/plugin/auto-memory），或独立 `CLAUDE_CONFIG_DIR` 指向项目 `.claude-config/`（复用现有 meta 隔离）。被测在容器内不受影响。

## 测试

- **单元**：
  - `convert.py`：喂构造的 RawPair（含 SSE body_raw）→ 断言产出 `req_*.json` 字段与样本一致（提升正确、SSE 聚合出 content blocks、tool_use.input 已 parse、顶层元数据补全、is_garbled 判定）。用样本里真实的某个 req 作黄金样本反推。
  - `recording_proxy.py`：起代理 + 假 endpoint，发一个 mock `/v1/messages` SSE → 断言 raw_calls 落盘正确、透传不丢字节。
  - `user_agent.py`：mock 一个被测末轮文本 → 断言 user-agent 回一句非空、无机器痕迹文本。
  - `orchestrator.run_multi`：用 `tests/fixtures/fake_claude.py` 模拟被测事件流（含 end_turn + tool_use 轮）→ 断言接话时机正确、注入次数符合预期、结束条件触发。
- **集成（`--run-integration`，需 docker）**：用现有 `tasks/debug-textstats`（小、快、rubric 全 script）跑 `run_multi`，断言产出 `req_*.json` 集合、格式对齐样本、能被样本校验脚本接受。
- **e2e（`--run-e2e`，需真 endpoint）**：真 claude + 真 endpoint 跑一条长链路 task，人工抽查 `req_*.json` 与样本的格式一致性 + 注入自然度。

## 不做的事（YAGNI）

- 不做 rubric 判停（用户否决）。
- 不扩展 task schema / 不改 synthesize / verify。
- 不做并行多任务 run。
- 不复刻样本的 `repo/`+`workspace/` 双目录布局（沿用单 `workspace/`）。
- 不做 user-agent 的微调模型接入（本次用 skill 激活的 agentic；微调模型作为后续可插拔点，接口预留）。
- 代理不做请求改写/注入（纯忠实录制 + 转发）。

## 待定 / 实现时确认

- 代理实现选 `http.server` 还是 `aiohttp`（SSE 流式透传的简洁性 vs 依赖）——倾向标准库 + 线程，零新依赖。
- user-agent skill 的具体装载机制（`--add-dir` + 自动发现 vs `CLAUDE_CODE_INVOKED_SKILLS`）——实现时按 claude 2.1 实测。
- `thinking_effort` 取值映射（样本 `xhigh` ↔ `--effort xhigh`）——确认被测启动参数带 `--effort`。
- user-agent 看到的"被测末轮"是否含 tool_use 摘要（影响接话质量）——倾向只喂 assistant 文本块 + 简短 tool_use 名单摘要。
