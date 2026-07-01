# Trajectory Maker

LLM agent 运行轨迹生成器：输入一个文件夹（github 仓库或本地文件夹），合成 agentic 任务 → 验证 → 在 docker 任务环境里跑 claude code 采集原生 stream-json 轨迹 → 清洗去敏 → 打包为"一数据一目录"。

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

## 输出

```
dataset/<task_id>/<run_id>/
├── metadata.yaml          # 任务元数据 + run 信息（不含 apikey）
├── final_score.json       # 最后一步评分结果
├── initial_env/           # 初始环境快照
├── expected_final_env/    # 预期终末环境 + rubrics
├── actual_final_env/      # agent 跑完的终末环境快照
└── trajectory.jsonl       # 清洗去敏后的原生 stream-json 轨迹
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
- **元工作（synthesize、checklist 判定）**：用项目固定端点，配置方式（二选一）：
  - 复制 `.claude-config/settings.json.template` 为 `.claude-config/settings.json`，填入 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_DEFAULT_SONNET_MODEL`
  - 或设环境变量 `TM_SYNTH_BASE_URL`/`TM_SYNTH_API_KEY`/`TM_SYNTH_MODEL`

隔离机制：剥离宿主所有 `ANTHROPIC_*` 环境变量 + 设 `CLAUDE_CONFIG_DIR` 指向项目本地 `.claude-config/`，绕开 `~/.claude/settings.json`。
