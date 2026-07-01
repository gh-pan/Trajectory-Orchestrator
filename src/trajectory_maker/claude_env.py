"""Isolated Claude Code subprocess environment — bypasses global cc-switch.

Two layers:
- meta work (synthesize, checklist judge): pinned to a project-local endpoint
  read from .claude-config/settings.json or TM_SYNTH_* env vars.
- subject agent (run stage): uses caller-supplied endpoint/apikey/model, but
  still isolated from cc-switch via CLAUDE_CONFIG_DIR + ANTHROPIC_* stripping.

Mirrors docq's build_isolated_claude_env pattern.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / ".claude-config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def _strip_anthropic(env: dict[str, str]) -> dict[str, str]:
    """Remove every ANTHROPIC_* key so cc-switch's leaked vars cannot reach the subprocess."""
    return {k: v for k, v in env.items() if not k.startswith("ANTHROPIC_")}


def _load_settings() -> dict:
    """Load pinned endpoint from .claude-config/settings.json (env block)."""
    if not SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data.get("env", {}) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def build_subject_env(
    endpoint: str,
    apikey: str,
    model: str,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Env for the subject agent (run stage) — caller-supplied credentials, isolated from cc-switch.

    Strips leaked ANTHROPIC_*, pins caller values, sets CLAUDE_CONFIG_DIR to the project-local
    config dir so ~/.claude/settings.json is bypassed.
    """
    env = _strip_anthropic(base_env if base_env is not None else dict(os.environ))
    env["ANTHROPIC_BASE_URL"] = endpoint
    env["ANTHROPIC_API_KEY"] = apikey
    env["ANTHROPIC_AUTH_TOKEN"] = apikey
    env["ANTHROPIC_MODEL"] = model
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
    if CONFIG_DIR.is_dir():
        env["CLAUDE_CONFIG_DIR"] = str(CONFIG_DIR)
    return env


def build_meta_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Env for meta work (synthesize, checklist judge) — pinned project endpoint, isolated from cc-switch.

    Endpoint comes from (in priority order):
      1. TM_SYNTH_BASE_URL / TM_SYNTH_API_KEY / TM_SYNTH_MODEL env vars
      2. .claude-config/settings.json env block
    Raises RuntimeError if no endpoint is configured.
    """
    env = _strip_anthropic(base_env if base_env is not None else dict(os.environ))
    settings = _load_settings()
    base_url = os.environ.get("TM_SYNTH_BASE_URL") or settings.get("ANTHROPIC_BASE_URL")
    api_key = os.environ.get("TM_SYNTH_API_KEY") or settings.get("ANTHROPIC_AUTH_TOKEN") or settings.get("ANTHROPIC_API_KEY")
    model = os.environ.get("TM_SYNTH_MODEL") or settings.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or settings.get("ANTHROPIC_MODEL")
    if not base_url or not api_key or not model:
        raise RuntimeError(
            "meta claude endpoint not configured: set TM_SYNTH_BASE_URL/TM_SYNTH_API_KEY/TM_SYNTH_MODEL "
            "env vars or populate .claude-config/settings.json (env block: ANTHROPIC_BASE_URL, "
            "ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_SONNET_MODEL)"
        )
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_API_KEY"] = api_key
    env["ANTHROPIC_AUTH_TOKEN"] = api_key
    env["ANTHROPIC_MODEL"] = model
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
    if CONFIG_DIR.is_dir():
        env["CLAUDE_CONFIG_DIR"] = str(CONFIG_DIR)
    return env


def meta_model() -> str | None:
    """The model to use for meta work (for --model flag), or None to let claude default."""
    settings = _load_settings()
    return os.environ.get("TM_SYNTH_MODEL") or settings.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or settings.get("ANTHROPIC_MODEL")
