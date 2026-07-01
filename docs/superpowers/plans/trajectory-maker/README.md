# Trajectory Maker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 LLM agent 运行轨迹生成器：输入文件夹 → claude code 合成任务 → 验证 → 在 docker 内跑 claude code 采集原生 stream-json 轨迹 → 清洗去敏 → 打包为"一数据一目录"。

**Architecture:** Python(uv) 编排器在宿主，通过 stream-json 双向流驱动 claude code 无头实例（synthesize 用 local 子进程后端，verify/run 用 docker exec 后端）。driver 抽象双后端，上层统一。三阶段共用 `grade()` 评分原语。轨迹只靠 stdin 注入标准 user message，不挂 hook、不污染。

**Tech Stack:** Python 3.13、uv 0.11、pydantic v2、pytest、pyyaml、docker CLI（subprocess 调用）、claude code cli 2.1。

## 文档索引（分块）

本计划分块存放于本目录，按依赖顺序实现：

- [00-bootstrap.md](00-bootstrap.md) — 项目脚手架：uv 初始化、pyproject、目录结构、第一个能跑的 `trajectory-maker --help`
- [01-models.md](01-models.md) — TaskSpec pydantic 模型与 yaml 加载/校验
- [02-docker.md](02-docker.md) — docker.py 生命周期封装（build/run/exec/cp/rm/rmi）
- [03-driver.md](03-driver.md) — driver.py：stream-json 双向流（local/docker 两后端）+ 事件解析
- [04-sanitize.md](04-sanitize.md) — sanitize.py：凭证/路径/元数据清洗 + 自检
- [05-grade.md](05-grade.md) — grade.py：公共评分原语（script 直跑 + checklist 用 claude）
- [06-synthesize.md](06-synthesize.md) — 阶段1 synthesize 子命令
- [07-verify.md](07-verify.md) — 阶段2 verify 子命令
- [08-run-package.md](08-run-package.md) — 阶段3 run 子命令 + orchestrator + 打包
- [09-clean-cli.md](09-clean-cli.md) — clean 子命令 + all 端到端 + README

## 实现顺序与依赖

```
00 bootstrap ─┬─▶ 01 models ─┬─▶ 02 docker ─▶ 03 driver ─▶ 05 grade ─▶ 06 synthesize ─▶ 07 verify ─┐
              └──────────────┴─▶ 04 sanitize ──────────────────────────────────────────────────────┴─▶ 08 run+package ─▶ 09 clean+all
```

每块都是 TDD（先写失败测试 → 实现 → 通过 → 提交），产出可独立测试的软件。块内任务用 checkbox 标记，块间有明确依赖。

## 全局约定

- 所有 Python 包代码在 `src/trajectory_maker/` 下（src layout）。
- 测试在 `tests/` 下，镜像包结构（`tests/test_models.py` 对应 `src/trajectory_maker/models.py`）。
- 测试运行：`uv run pytest`。单元测试不依赖 docker/网络；集成测试用 `@pytest.mark.integration` 标记，需 docker；e2e 用 `@pytest.mark.e2e` 标记，需真实端点。
- 每个任务结束 `git commit`，提交信息用 conventional commits（feat/test/chore/docs/refactor）。
- 文件路径用绝对仓库相对路径，如 `src/trajectory_maker/models.py`。
