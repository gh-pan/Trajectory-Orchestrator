# 04 · Sanitize — 清洗去敏

**Goal:** sanitize.py 对 stream-json jsonl 做凭证移除、路径规范化、元数据规范，不改事件结构、不增删事件；清洗后凭证零命中自检。规则配置化（sanitize_rules.yaml）。

**Files:**
- Create: `src/trajectory_maker/sanitize.py`
- Create: `src/trajectory_maker/resources/sanitize_rules.yaml`
- Create: `tests/test_sanitize.py`
- Create: `tests/fixtures/trajectory_dirty.jsonl`

**Depends on:** 00-bootstrap

---

- [ ] **Step 1: 写清洗规则配置**

Create `src/trajectory_maker/resources/sanitize_rules.yaml`：

```yaml
# 凭证：敏感环境变量名（匹配后值替换为 <redacted>）
secret_env_keys:
  - ANTHROPIC_API_KEY
  - ANTHROPIC_AUTH_TOKEN
  - ANTHROPIC_BASE_URL
  - API_KEY
  - SECRET
  - TOKEN
  - PASSWORD

# 凭证：正则模式（匹配即替换为 <redacted>）
secret_patterns:
  - 'sk-ant-[A-Za-z0-9_-]+'
  - 'sk-[A-Za-z0-9]{20,}'
  - 'Bearer\s+[A-Za-z0-9._-]+'
  - 'Authorization:\s*[^\s]+'

# 路径：宿主前缀 -> 规范化目标
path_replacements:
  - pattern: '/Users/[^/]+'
    replacement: '/home/user'
  - pattern: '/home/[^/]+'
    replacement: '/home/user'
  - pattern: '/tmp/tm-clone-[A-Za-z0-9_-]+'
    replacement: '/workspace'
  - pattern: '/Volumes/[^ ]*Trajectory-Maker'
    replacement: '/workspace'

# 元数据：需要移除的字段（在 system/result 事件顶层）
remove_metadata_fields:
  - session_id
  - conversation_id
  - transcript_path
  - hostname
  - machine_id

# 元数据：需要规范化的字段
normalize_metadata:
  cwd: '/workspace'
```

- [ ] **Step 2: 写脏轨迹 fixture**

Create `tests/fixtures/trajectory_dirty.jsonl`：

```jsonl
{"type":"system","subtype":"init","session_id":"sess-secret123","cwd":"/Users/larr/tasks","version":"2.1.175","hostname":"larr-mac"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"我用 ANTHROPIC_API_KEY=sk-ant-abc123secret 来调用。"}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"cloned to /tmp/tm-clone-x7f/repo"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"修改 /Volumes/Files/EntropyOrder/Trajectory-Maker/src/app.py 完成。"}]}}
{"type":"result","subtype":"success","result":"done","session_id":"sess-secret123","cwd":"/Users/larr/tasks"}
```

- [ ] **Step 3: 写失败测试 test_sanitize.py**

Create `tests/test_sanitize.py`：

```python
import json
from pathlib import Path

import pytest

from trajectory_maker.sanitize import (
    SanitizeRules,
    sanitize_event,
    sanitize_jsonl,
    scan_secrets,
    load_rules,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rules():
    return load_rules()


def test_load_rules_has_entries(rules):
    assert "ANTHROPIC_API_KEY" in rules.secret_env_keys
    assert rules.path_replacements


def test_secret_env_key_value_redacted(rules):
    ev = {"type": "assistant", "message": {"content": [{"type": "text", "text": "key=ANTHROPIC_API_KEY=sk-ant-abc123"}]}}
    out = sanitize_event(ev, rules)
    text = out["message"]["content"][0]["text"]
    assert "sk-ant-abc123" not in text
    assert "<redacted>" in text


def test_secret_pattern_redacted(rules):
    ev = {"type": "user", "message": {"content": [{"type": "text", "text": "Bearer xyz123token"}]}}
    out = sanitize_event(ev, rules)
    assert "xyz123token" not in json.dumps(out)


def test_path_normalized(rules):
    ev = {"type": "assistant", "message": {"content": [{"type": "text", "text": "edit /Users/larr/src/app.py"}]}}
    out = sanitize_event(ev, rules)
    assert "/home/user/src/app.py" in out["message"]["content"][0]["text"]
    assert "/Users/larr" not in json.dumps(out)


def test_tmp_clone_path_normalized(rules):
    ev = {"type": "user", "message": {"content": [{"type": "text", "text": "cloned /tmp/tm-clone-x7f/repo"}]}}
    out = sanitize_event(ev, rules)
    assert "/tmp/tm-clone-" not in json.dumps(out)
    assert "/workspace/repo" in out["message"]["content"][0]["text"]


def test_metadata_session_id_removed(rules):
    ev = {"type": "system", "subtype": "init", "session_id": "s1", "cwd": "/Users/larr/x", "version": "2.1.175", "hostname": "h"}
    out = sanitize_event(ev, rules)
    assert "session_id" not in out
    assert "hostname" not in out
    assert out["cwd"] == "/workspace"
    assert out["version"] == "2.1.175"


def test_event_structure_preserved(rules):
    ev = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}}
    out = sanitize_event(ev, rules)
    assert out["type"] == "assistant"
    assert out["message"]["content"][0]["text"] == "hello"


def test_sanitize_jsonl_keeps_event_count(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    report = sanitize_jsonl(inp, outp, rules)
    raw_count = sum(1 for _ in inp.open())
    clean_count = sum(1 for _ in outp.open())
    assert clean_count == raw_count
    assert report.events_in == raw_count
    assert report.events_out == raw_count


def test_sanitize_jsonl_zero_secrets_after(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    sanitize_jsonl(inp, outp, rules)
    text = outp.read_text()
    matches = scan_secrets(text, rules)
    assert matches == [], f"remaining secrets: {matches}"


def test_sanitize_jsonl_valid_jsonl(tmp_path, rules):
    inp = FIXTURES / "trajectory_dirty.jsonl"
    outp = tmp_path / "clean.jsonl"
    sanitize_jsonl(inp, outp, rules)
    for line in outp.read_text().splitlines():
        if line.strip():
            json.loads(line)
```

- [ ] **Step 4: 运行测试验证失败**

Run:
```bash
uv run pytest tests/test_sanitize.py -v
```
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 5: 实现 sanitize.py**

Create `src/trajectory_maker/sanitize.py`：

```python
"""Sanitize stream-json trajectories: redact secrets, normalize paths, scrub metadata."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SanitizeRules:
    secret_env_keys: list[str]
    secret_patterns: list[str]
    path_replacements: list[dict]
    remove_metadata_fields: list[str]
    normalize_metadata: dict[str, str]
    _compiled_secrets: list[re.Pattern] = field(default_factory=list, repr=False)
    _compiled_paths: list[tuple[re.Pattern, str]] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._compiled_secrets = [re.compile(p) for p in self.secret_patterns]
        # also treat secret env keys as assignment patterns: KEY=value
        for k in self.secret_env_keys:
            self._compiled_secrets.append(re.compile(rf"\b{re.escape(k)}=[^\s]+"))
        self._compiled_paths = [
            (re.compile(r["pattern"]), r["replacement"]) for r in self.path_replacements
        ]


@dataclass
class SanitizeReport:
    events_in: int = 0
    events_out: int = 0
    secrets_redacted: int = 0


def load_rules(path: Path | None = None) -> SanitizeRules:
    if path is None:
        path = Path(__file__).parent / "resources" / "sanitize_rules.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SanitizeRules(
        secret_env_keys=data["secret_env_keys"],
        secret_patterns=data["secret_patterns"],
        path_replacements=data["path_replacements"],
        remove_metadata_fields=data["remove_metadata_fields"],
        normalize_metadata=data.get("normalize_metadata", {}),
    )


def _redact_secrets(text: str, rules: SanitizeRules) -> str:
    for pat in rules._compiled_secrets:
        text = pat.sub("<redacted>", text)
    return text


def _normalize_paths(text: str, rules: SanitizeRules) -> str:
    for pat, repl in rules._compiled_paths:
        text = pat.sub(repl, text)
    return text


def _scrub(obj, rules: SanitizeRules):
    """Recursively redact secrets + normalize paths in all string values; scrub metadata fields."""
    if isinstance(obj, dict):
        return {k: v for k, v in (
            (k, _scrub(v, rules)) for k, v in obj.items()
        )}
    if isinstance(obj, list):
        return [_scrub(v, rules) for v in obj]
    if isinstance(obj, str):
        s = _redact_secrets(obj, rules)
        s = _normalize_paths(s, rules)
        return s
    return obj


def sanitize_event(event: dict, rules: SanitizeRules) -> dict:
    out = _scrub(event, rules)
    # scrub metadata fields on system/result events
    if out.get("type") in ("system", "result"):
        for f in rules.remove_metadata_fields:
            out.pop(f, None)
        for f, val in rules.normalize_metadata.items():
            if f in out:
                out[f] = val
    return out


def scan_secrets(text: str, rules: SanitizeRules) -> list[str]:
    """Return list of secret matches found in text (empty = clean)."""
    matches = []
    for pat in rules._compiled_secrets:
        for m in pat.findall(text):
            matches.append(m if isinstance(m, str) else str(m))
    return matches


def sanitize_jsonl(in_path: Path, out_path: Path, rules: SanitizeRules) -> SanitizeReport:
    report = SanitizeReport()
    with in_path.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            report.events_in += 1
            event = json.loads(line)
            clean = sanitize_event(event, rules)
            fout.write(json.dumps(clean, ensure_ascii=False) + "\n")
            report.events_out += 1
    # self-check: zero secrets in output
    remaining = scan_secrets(out_path.read_text(encoding="utf-8"), rules)
    if remaining:
        raise RuntimeError(f"sanitize self-check failed: {len(remaining)} secrets remain")
    return report
```

- [ ] **Step 6: 运行测试验证通过**

Run:
```bash
uv run pytest tests/test_sanitize.py -v
```
Expected: PASS（11 passed）。

- [ ] **Step 7: 提交**

```bash
git add src/trajectory_maker/sanitize.py src/trajectory_maker/resources/sanitize_rules.yaml tests/test_sanitize.py tests/fixtures/trajectory_dirty.jsonl
git commit -m "feat: add trajectory sanitizer with secret/path/metadata rules and zero-secret self-check"
```
