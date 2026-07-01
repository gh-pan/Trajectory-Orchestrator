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
