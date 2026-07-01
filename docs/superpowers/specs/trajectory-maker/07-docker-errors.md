# 07 · docker 生命周期、销毁与错误处理

## 镜像与容器命名

| 阶段 | 镜像 tag | 容器名 |
|---|---|---|
| synthesize | （无，宿主跑） | — |
| verify | `tm-verify-<task_id>` | `tm-verify-<task_id>-<rand>` |
| run | `tm-run-<task_id>-<run_id>` | `tm-run-<task_id>-<run_id>` |

- 镜像名带阶段前缀，便于 `docker images | grep tm-` 清理与排查。
- 容器名唯一，run 阶段用 run_id 保证可追溯。

## docker 模块职责（`docker.py`）

统一封装生命周期，三阶段调用：

```
build(task_dir, image_tag)         → build Dockerfile，失败抛 BuildError
run(image_tag, container_name,     → 后台启动容器（不注入凭证；凭证在 exec_stream 时注入），返回 container
    workspace_path, resource_limits, ...)
exec_stream(container, cmd, env,   → docker exec -i 启动 claude（env=凭证，注入到 exec 进程），返回 (stdout_pipe, stdin_pipe)
    stdin)                          → driver 用这对管道做 stream-json 双向流
exec(container, cmd, timeout)      → 同步 exec 跑脚本/命令，返回 (exit_code, stdout, stderr)
cp_from(container, path, dest)     → docker cp 容器内路径到宿主
cp_to(host_path, container, path)  → docker cp 宿主到容器
stop(container) / rm(container)    → 停止/删除容器
rmi(image_tag)                     → 删除镜像
exists(container) / image_exists   → 查询
```

- 用 `docker` CLI 子进程（`subprocess`）而非 docker SDK——CLI 与 `docker exec -i` 的流式 stdin/stdout 管道最直接，避免 SDK 的流处理坑。
- `exec_stream` 是核心：`docker exec -i <c> claude --input-format stream-json --output-format stream-json --print` 拿到长连接双向管道，driver 持有它整个 run 期间。

## 容器内 claude code 的启动约定

容器启动后保持运行（entrypoint 为 `sleep infinity` 或 `tail -f /dev/null`），编排器用 `docker exec -i` 在其内部拉起 claude code：

```
docker exec -i <container> \
  env ANTHROPIC_BASE_URL=$ENDPOINT \
      ANTHROPIC_API_KEY=$KEY \
      ANTHROPIC_MODEL=$MODEL \
  claude --print \
    --input-format stream-json --output-format stream-json \
    --dangerously-skip-permissions \
    --add-dir /workspace \
    --model $MODEL
```

- `--dangerously-skip-permissions`：任务容器是隔离沙箱，agent 需要自主跑 bash/edit，跳过权限询问避免阻塞（且不引入 permission 事件污染轨迹）。
- `--add-dir /workspace`：明确 workspace 可访问范围。
- 凭证通过 `env` 前缀注入到 exec 进程（容器环境变量），不写进镜像、不落盘。

## 销毁策略

**销毁对象**：容器 + 镜像（run/verify 各自的）。

| 场景 | 容器 | 镜像 |
|---|---|---|
| run 打包校验通过 | 销毁 | 销毁 |
| run `--keep` | 保留 | 保留 |
| run 异常/crash | 默认销毁；`--keep-on-error` 保留 | 同 |
| verify pass | 销毁 | 销毁 |
| verify fail | `--keep-on-fail` 保留，否则销毁 | 同 |
| 编排器被 Ctrl-C | 捕获信号，清理当前容器（镜像视策略） | — |

- **原子性**：每个阶段的销毁包在 `finally` 里，确保异常路径也清理。
- **孤儿清理命令**：提供 `trajectory-maker clean --all`，扫描所有 `tm-verify-*`/`tm-run-*` 容器与镜像并删除（应对崩溃残留）。`clean --task <task_id>` 只清该任务。

## 错误处理汇总（跨阶段）

| 错误类型 | 处理 |
|---|---|
| 输入文件夹不存在 / git clone 失败 | 前置报错，不启动 |
| synthesize 产物 schema 不符 | 保留产物 + claude 原始事件流，报错退出 |
| docker build 失败 | 报错，不创建容器 |
| 容器启动 / init_script 失败 | 报错，清理容器，不进入 agent |
| claude code 不可用（冒烟失败） | verify/run fail，保留容器 |
| 端点不可达 / 鉴权失败 | run 早期 error 事件 → 立即终止，不打包 |
| agent 进程崩溃 | 保留部分轨迹，仍跑 rubric（能跑则跑）并打包，`termination=crashed` |
| 超时 / max-turns | 强制结束，`termination=timeout|max_turns`，正常打包 |
| rubric script 超时 | 该 rubric 标 fail，继续其他 rubric |
| checklist 判定实例崩溃 | 该 rubric 标 `error`(=fail)，继续 |
| 打包校验失败 | 报错，保留目录 + raw 轨迹，**不销毁容器** |
| Ctrl-C | 捕获，清理当前容器，退出 |

**原则**：

- 采集到的轨迹尽量不丢——即使 agent 崩溃，部分轨迹也打包（除非端点/鉴权失败这种轨迹无意义的情况）。
- 凭证绝不落盘——任何错误路径都不把 key 写进日志/产物。
- 清理必在 finally——不留孤儿容器。

## 资源限制

- 容器默认 `--memory=2g --cpus=2`（可配）。
- 网络：默认允许（agent 可能需装依赖/git clone）；`--network=none` 可选（隔离更强但很多任务跑不了，第一版不默认）。

## 不做的事（YAGNI）

- 不做容器复用池（每阶段 fresh build）。
- 不做 GPU 支持（第一版纯 CPU 任务）。
- 不做分布式/多机调度（单机单任务）。
