# Trajectory Maker 设计规格

> 状态：设计已逐节经用户确认，待实现规划
> 日期：2026-07-01
> 项目：大语言模型 agent 运行轨迹生成器

## 一句话目标

输入一个文件夹（github 仓库或本地文件夹），合成一个 agentic 任务（yaml + Dockerfile + workspace + rubrics），验证其正确可解，然后在指定 docker 任务环境里用指定端点/apikey/model 跑 claude code 无头实例、采集原生 stream-json 轨迹，清洗去敏后与任务元数据、评分、初始/预期/实际环境一起打包为"一数据一目录"，最后销毁 docker 镜像。

## 文档索引

本规格分节存放于本目录下，各节已逐节经用户确认：

- [00-overview.md](00-overview.md) — 总体架构与目录结构
- [01-task-schema.md](01-task-schema.md) — 任务 yaml schema
- [02-synthesize.md](02-synthesize.md) — 阶段 1：任务合成
- [03-verify.md](03-verify.md) — 阶段 2：任务验证
- [04-run.md](04-run.md) — 阶段 3：轨迹采集
- [05-sanitize.md](05-sanitize.md) — 清洗去敏
- [06-package.md](06-package.md) — 数据打包
- [07-docker-errors.md](07-docker-errors.md) — docker 生命周期与错误处理
- [08-testing.md](08-testing.md) — 测试策略

## 核心决策摘要

| 决策点 | 选择 |
|---|---|
| 编排器技术栈 | Python（uv） |
| agent 运行位置 | claude code 在任务容器内，编排器在宿主 |
| 交互机制 | stream-json 双向流，编排器只通过 stdin 注入标准 user message（不挂 hook、不污染轨迹） |
| 轨迹格式 | 原生 stream-json（jsonl） |
| 端点/凭证传递 | 环境变量注入容器（不写进镜像、不落盘） |
| 任务定义来源 | claude code 无头实例合成 |
| 验证机制 | 混合：script 直跑 + checklist 用独立 claude code 实例判定 |
| 完成判定 | agent 停止输出后扫描末轮 assistant 文本的完成语义（不约束标记） |
| 多轮注入 | 第一版不实现，driver 保留 inject() 与状态机 INJECTING 分支 |
| 数据布局 | 一数据一目录 + 轻量 index.jsonl 索引 |
| CLI 形态 | 子命令分阶段（synthesize / verify / run / all / clean） |

## 待后续阶段

- 多轮对话注入（第一版仅留接口）。
- 数据集版本化 / 远程上传 / train-test 划分（下游职责）。
- 并行多任务 run、GPU 支持、分布式调度（第一版单机单任务）。
