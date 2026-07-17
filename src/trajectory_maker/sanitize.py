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
    leak_patterns: list[str] = field(default_factory=list)
    _compiled_leaks: list[re.Pattern] = field(default_factory=list, repr=False)
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
        self._compiled_leaks = [re.compile(p, re.IGNORECASE) for p in self.leak_patterns]


@dataclass
class SanitizeReport:
    events_in: int = 0
    events_out: int = 0
    secrets_redacted: int = 0


def load_rules(
    path: Path | None = None,
    secret_values: list[str] | None = None,
    path_mappings: dict[str, str] | None = None,
) -> SanitizeRules:
    """Load static rules plus per-run exact secrets and path mappings.

    Per-run values are kept in memory only.  Exact path mappings are inserted
    before broad host-path rules so a temporary local workspace normalizes all
    the way back to ``/workspace`` rather than merely ``/home/user/...``.
    """
    if path is None:
        path = Path(__file__).parent / "resources" / "sanitize_rules.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    dynamic_secrets = [
        re.escape(value)
        for value in (secret_values or [])
        if isinstance(value, str) and value
    ]
    dynamic_paths = [
        {"pattern": re.escape(source), "replacement": replacement}
        for source, replacement in sorted(
            (path_mappings or {}).items(), key=lambda item: len(item[0]), reverse=True
        )
        if source
    ]
    return SanitizeRules(
        secret_env_keys=data["secret_env_keys"],
        secret_patterns=[*dynamic_secrets, *data["secret_patterns"]],
        path_replacements=[*dynamic_paths, *data["path_replacements"]],
        remove_metadata_fields=data["remove_metadata_fields"],
        normalize_metadata=data.get("normalize_metadata", {}),
        leak_patterns=data.get("leak_patterns", []),
    )


def _redact_secrets(text: str, rules: SanitizeRules) -> str:
    for pat in rules._compiled_secrets:
        text = pat.sub("<redacted>", text)
    return text


def _normalize_paths(text: str, rules: SanitizeRules) -> str:
    for pat, repl in rules._compiled_paths:
        text = pat.sub(repl, text)
    return text


def _redact_leaks(text: str, rules: SanitizeRules) -> str:
    """Blank out machine/collector jargon that would betray programmatic injection."""
    for pat in rules._compiled_leaks:
        text = pat.sub("<redacted>", text)
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
        s = _redact_leaks(s, rules)
        return s
    return obj


def sanitize_event(event: dict, rules: SanitizeRules) -> dict:
    out = _scrub(event, rules)
    # scrub metadata fields (session_id, conversation_id, transcript_path, hostname,
    # machine_id) from EVERY event — claude code attaches session_id to assistant/user
    # events too, not just system/result.
    for f in rules.remove_metadata_fields:
        out.pop(f, None)
    # normalize metadata fields (e.g. cwd -> /workspace) only on system/result,
    # which is where they appear.
    if out.get("type") in ("system", "result"):
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


def sanitize_req_file(in_path: Path, out_path: Path, rules: SanitizeRules) -> None:
    """Sanitize one req_*.json record (spec 09 structure) in place."""
    rec = json.loads(in_path.read_text(encoding="utf-8"))
    rec = _scrub(rec, rules)
    # device_id inside metadata.user_id (a JSON-encoded string) — redact its value
    req = rec.get("request", {})
    meta = req.get("metadata")
    if isinstance(meta, dict) and isinstance(meta.get("user_id"), str):
        try:
            uid = json.loads(meta["user_id"])
            if isinstance(uid, dict) and "device_id" in uid:
                uid["device_id"] = "<redacted>"
            meta["user_id"] = json.dumps(uid, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            meta["user_id"] = "<redacted>"
    out_path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def sanitize_req_dir(req_dir: Path, rules: SanitizeRules) -> int:
    """Sanitize every req_*.json in a session directory (in place). Returns count."""
    n = 0
    for f in sorted(req_dir.glob("req_*.json")):
        sanitize_req_file(f, f, rules)
        n += 1
    return n


def sanitize_json_record_dir(record_dir: Path, rules: SanitizeRules) -> int:
    """Sanitize line-delimited JSON records in place (used for kept raw calls)."""
    count = 0
    if not record_dir.is_dir():
        return count
    for path in sorted(record_dir.glob("*.jsonl")):
        clean_lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            clean_lines.append(json.dumps(_scrub(json.loads(line), rules), ensure_ascii=False))
        path.write_text(
            "\n".join(clean_lines) + ("\n" if clean_lines else ""),
            encoding="utf-8",
        )
        count += 1
    return count
