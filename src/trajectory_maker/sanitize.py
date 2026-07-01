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
