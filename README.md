# Trajectory Maker

LLM agent 运行轨迹生成器：输入一个文件夹（github 仓库或本地文件夹），合成 agentic 任务 → 验证 → 在 docker 任务环境里跑 claude code，经 HTTP 拦截采集**原生 API 调用层**轨迹 → 多轮 user-agent 注入推进长链路 → 清洗去敏 → 打包为"一数据一目录"。

## 安装

```bash
uv sync
```

## 使用

```bash
# 端到端
trajectory-maker all <input-folder-or-github-url> \
  --endpoint <base_url> --apikey <key> --model <model_id> \
  --tasks ./tasks --output ./dataset

# 分阶段
trajectory-maker synthesize <input-folder> --output ./tasks
trajectory-maker verify ./tasks/<task_id> --endpoint ... --model ...
trajectory-maker run ./tasks/<task_id> --endpoint ... --apikey ... --model ... --output ./dataset

# 清理残留容器/镜像
trajectory-maker clean --all
trajectory-maker clean --task <task_id>
```

## run 阶段：多轮注入 + API 调用层采集

run 阶段做三件事（详见 `docs/superpowers/specs/trajectory-maker/09-multi-turn-capture-design.md`）：

1. **HTTP 拦截采集**：本地 plain-HTTP 录制代理拦截被测 claude 的每次 `/v1/messages`
   调用，落 `request body` + `SSE response body_raw`；再两步转换（提升 body + 解码 SSE）
   为 `req_<uuid>.json`，结构与采集规格一致（`request.*` = API body，
   `response.response_data` = 完整 message，顶层 `session_id/request_id/timestamp/
   thinking_effort/is_garbled`）。
2. **多轮 user-agent 注入**：一个常驻的、skill 激活的 user-agent，在被测 agent 每次
   end_turn 后程序化注入一句反应式 user 消息，推进长链路任务。注入即标准 user turn
   （写 stdin），不挂 hook、不改 system prompt、不留机器注入痕迹。被测自主停止即收尾
   （可加 `--max-turns`/`--timeout` 兜底）。
3. **清洗去敏**：对 `req_*.json` 做凭证/路径/元数据脱敏，并剔除任何暴露程序化注入的
   机器痕迹（`recording_proxy`/`host.docker.internal`/`user-agent` 等词）。

被测 claude 的 `ANTHROPIC_BASE_URL` 指向录制代理（容器经 `host.docker.internal` 网关
回宿主），凭证仍走 env、由代理原样转发给真实 endpoint（代理日志只留脱敏头）。

## 输出

```
dataset/<task_id>/<run_id>/
├── metadata.yaml          # 任务元数据 + run 信息（不含 apikey）
├── final_score.json       # rubric 评分结果
├── initial_env/           # 初始环境快照
├── expected_final_env/    # 预期终末环境
├── actual_final_env/      # agent 跑完的终末环境快照
├── rubrics/               # 评分脚本/清单
├── <session_id>/          # API 调用层轨迹
│   ├── req_<uuid>.json    #   每次调用一个文件（提升后的 request + 解码的 response）
│   └── ...
└── events.jsonl           # 原始 stream-json 事件流（审计用）
dataset/index.jsonl        # 全局索引
```

## 设计文档

- 规格：`docs/superpowers/specs/trajectory-maker/`
- 实现计划：`docs/superpowers/plans/trajectory-maker/`

## 测试

```bash
uv run pytest                          # 单元测试
uv run pytest --run-integration        # + docker 集成测试
TM_E2E_ENDPOINT=... TM_E2E_APIKEY=... TM_E2E_MODEL=... \
  uv run pytest --run-e2e              # + 真实端点 e2e
```

## Claude 配置隔离（摆脱 cc-switch）

项目自动隔离 Claude Code 子进程，不被全局 cc-switch 控制：

- **被测 agent（run）**：用 `--endpoint/--apikey/--model` 传入的凭证
- **元工作（synthesize、checklist 判定、user-agent）**：用项目固定端点，配置方式（二选一）：
  - 复制 `.claude-config/settings.json.template` 为 `.claude-config/settings.json`，填入 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_DEFAULT_SONNET_MODEL`
  - 或设环境变量 `TM_SYNTH_BASE_URL`/`TM_SYNTH_API_KEY`/`TM_SYNTH_MODEL`

隔离机制：剥离宿主所有 `ANTHROPIC_*` 环境变量 + 设 `CLAUDE_CONFIG_DIR` 指向项目本地 `.claude-config/`，绕开 `~/.claude/settings.json`。user-agent 额外用独立临时 config dir + 干净 cwd，且剥离宿主 `CLAUDE_CODE_*` session 状态，确保不被宿主 plugin/hook 污染。
