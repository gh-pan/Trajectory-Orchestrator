# 05 · 清洗去敏（sanitize）

## 目标

把 run 阶段采集的 `trajectory_raw.jsonl`（原生 stream-json）清洗为 `trajectory.jsonl`：去除凭证、规范化路径、规范元数据，**不改动 agent 的实质输出与对话内容**。

## 处理对象

stream-json 的每行是一个事件，常见 type：`system`（init）、`assistant`（message，含 text/tool_use blocks）、`user`（含 tool_result blocks）、`result`、`error`。清洗针对事件内的文本与字段，不改变事件结构。

## 清洗规则（凭证 + 路径 + 元数据规范）

### 1. 凭证移除

扫描所有文本字段（assistant text、tool_use input、tool_result content、user content）：

| 模式 | 处理 |
|---|---|
| `ANTHROPIC_API_KEY=...` / `ANTHROPIC_AUTH_TOKEN=...` / `ANTHROPIC_BASE_URL=...` 及值 | 替换为 `ANTHROPIC_API_KEY=<redacted>` 等 |
| 形如 `sk-ant-...`、`sk-...` 的 API key 串 | 替换为 `<redacted>` |
| Bearer token、`Authorization: ...` 头 | 替换为 `<redacted>` |
| `.env` / 配置文件内容里的密钥键值 | 同上替换 |
| 其他匹配常见 secret 模式（高熵串、`*_KEY`/`*_TOKEN`/`*_SECRET` 变量赋值） | 替换为 `<redacted>` |

实现：用一组正则 + 敏感键名清单，匹配即替换为 `<redacted>`，**不删除整条事件**。

### 2. 路径规范化

| 原始 | 规范化为 |
|---|---|
| `/Users/<name>/...`、`/home/<name>/...` | `/home/user/...` |
| 宿主绝对路径（如 `/Volumes/Files/EntropyOrder/...`） | `/workspace/...` 或保留相对段 |
| 容器内 workspace 路径 `/workspace/...` | 保持（这是规范目标） |
| 临时 clone 路径 `/tmp/tm-clone-<hash>/...` | `/workspace/...`（剥离临时痕迹） |

实现：路径替换只动"路径字符串"本身，不重写文件内容。把出现过的宿主路径前缀收集起来统一替换。

### 3. 元数据规范

针对 `system`(init) 与 `result` 事件里的元数据字段：

| 字段 | 处理 |
|---|---|
| `session_id` / `conversation_id` / `transcript_path` | 移除或置空 |
| `cwd` | 规范为 `/workspace` |
| `version`（claude code 版本） | 保留（无害且利于复现） |
| `timestamp` / 时间戳类 | 保留（无害）；不删除 |
| `gitBranch` / `gitCommit`（init 事件里若有） | 保留 |
| `hostname` / `machine_id` 类 | 移除 |

### 4. 不做的事

- **不改写 agent 的文本内容**（除凭证/路径替换外，不润色、不截断、不删轮次）。
- **不做内容审查/敏感信息 LLM 判定**（第一版纯正则规则，够用且可控）。
- **不截断 tool_result 大文件**（保留完整，第一版）。
- **不剥离失败轮次**（第一版轨迹即原貌）。

## 清洗产物校验

清洗后做一次自检：

- 凭证扫描器对清洗后文件再跑一遍，**零命中**才算通过（命中说明规则漏了，报警并补规则）。
- 文件仍为合法 jsonl（每行可解析）。
- 事件数 = 原始事件数（只改字段值，不增删事件）。

## 配置化

清洗规则（敏感键名清单、路径前缀映射、secret 正则）放在 `src/trajectory_maker/resources/sanitize_rules.yaml`，便于扩展，不改代码。
