"""Isolated Claude Code subprocess environment — bypasses global cc-switch.

Split by WHERE claude runs, not just by subject/meta:
- subject agent (run/verify) runs IN CONTAINER → minimal env (only ANTHROPIC_*).
  Must NOT copy host env: leaking host HOME/PATH into the container breaks claude
  (claude init writes to ~/.claude; a host HOME that doesn't exist in-container
  causes a silent early exit with empty stdout).
- meta work:
  - synthesize runs on HOST → full host env (stripped of ANTHROPIC_*) + meta creds
    + CLAUDE_CONFIG_DIR (bypass host ~/.claude/settings.json / cc-switch).
  - checklist judge runs IN CONTAINER → minimal env (only meta ANTHROPIC_*).

Mirrors docq's build_isolated_claude_env for the host case; the container case is
intentionally minimal because `docker exec -e` augments the container's own env.
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


def _anthropic_vars(endpoint: str, apikey: str, model: str) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": endpoint,
        "ANTHROPIC_API_KEY": apikey,
        "ANTHROPIC_AUTH_TOKEN": apikey,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
    }


def build_subject_env(endpoint: str, apikey: str, model: str) -> dict[str, str]:
    """Env for the subject agent IN CONTAINER (run/verify). Minimal — only ANTHROPIC_*.

    Does NOT copy host env (would leak host HOME/PATH into the container and break
    claude init). Does NOT set CLAUDE_CONFIG_DIR (the container has no cc-switch to
    bypass). `docker exec -e` augments the container's own PATH/HOME from the image.
    """
    return _anthropic_vars(endpoint, apikey, model)


def _meta_creds() -> tuple[str, str, str]:
    settings = _load_settings()
    base_url = os.environ.get("TM_SYNTH_BASE_URL") or settings.get("ANTHROPIC_BASE_URL")
    api_key = (
        os.environ.get("TM_SYNTH_API_KEY")
        or settings.get("ANTHROPIC_AUTH_TOKEN")
        or settings.get("ANTHROPIC_API_KEY")
    )
    model = (
        os.environ.get("TM_SYNTH_MODEL")
        or settings.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or settings.get("ANTHROPIC_MODEL")
    )
    if not base_url or not api_key or not model:
        raise RuntimeError(
            "meta claude endpoint not configured: set TM_SYNTH_BASE_URL/TM_SYNTH_API_KEY/TM_SYNTH_MODEL "
            "env vars or populate .claude-config/settings.json (env block: ANTHROPIC_BASE_URL, "
            "ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_SONNET_MODEL)"
        )
    return base_url, api_key, model


def build_meta_env(
    in_container: bool = False,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Env for meta work.

    in_container=False (synthesize on HOST): full host env (stripped of ANTHROPIC_*)
      + meta creds + CLAUDE_CONFIG_DIR (bypass host cc-switch).
    in_container=True (checklist judge IN CONTAINER): minimal — only meta ANTHROPIC_*.

    Endpoint comes from (priority): TM_SYNTH_* env vars > .claude-config/settings.json.
    Raises RuntimeError if no endpoint is configured.
    """
    base_url, api_key, model = _meta_creds()
    creds = _anthropic_vars(base_url, api_key, model)
    if in_container:
        return creds
    env = _strip_anthropic(base_env if base_env is not None else dict(os.environ))
    env.update(creds)
    if CONFIG_DIR.is_dir():
        env["CLAUDE_CONFIG_DIR"] = str(CONFIG_DIR)
    return env


def meta_model() -> str | None:
    """The model to use for meta work (for --model flag), or None to let claude default."""
    settings = _load_settings()
    return (
        os.environ.get("TM_SYNTH_MODEL")
        or settings.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or settings.get("ANTHROPIC_MODEL")
    )
