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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

CONFIG_DIR = Path(__file__).resolve().parents[2] / ".claude-config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DEFAULT_SUBJECT_MODEL = "claude-opus-4-8"
LOCAL_CONNECT_TIMEOUT_MS = 600_000
_SAFE_LOCAL_HOST_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "TMPDIR", "TMP", "TEMP", "LANG", "LANGUAGE", "TZ",
    "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR",
    "NO_PROXY", "no_proxy",
    # Windows compatibility for local mode.
    "PATHEXT", "COMSPEC", "SYSTEMROOT", "SystemRoot", "WINDIR",
}


@dataclass(frozen=True)
class SubjectCredentials:
    """Resolved credentials for the subject Claude process.

    Values live only in memory and the child process environment; callers must
    never serialize this object into trajectory metadata.
    """

    endpoint: str
    apikey: str
    model: str


def _strip_anthropic(env: dict[str, str]) -> dict[str, str]:
    """Remove every ANTHROPIC_* key so cc-switch's leaked vars cannot reach the subprocess."""
    return {k: v for k, v in env.items() if not k.startswith("ANTHROPIC_")}


def _strip_claude_state(env: dict[str, str]) -> dict[str, str]:
    """Remove inherited Claude session/config state from a host environment."""
    clean = {
        k: v
        for k, v in _strip_anthropic(env).items()
        if not k.startswith("CLAUDE_CODE_")
    }
    clean.pop("CLAUDE_CONFIG_DIR", None)
    clean.pop("CLAUDE_EFFORT", None)
    return clean


def build_local_command_env(
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Safe host environment for local commands, without provider credentials."""
    clean_host = _strip_claude_state(
        dict(os.environ) if base_env is None else dict(base_env)
    )
    return {
        key: value
        for key, value in clean_host.items()
        if key in _SAFE_LOCAL_HOST_KEYS or key.startswith("LC_")
    }


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


def resolve_subject_credentials(
    endpoint: str | None = None,
    apikey: str | None = None,
    model: str | None = None,
    environ: dict[str, str] | None = None,
) -> SubjectCredentials:
    """Resolve subject credentials without persisting or displaying the key.

    Priority is explicit arguments, ``TM_SUBJECT_*``, Aihubmix-specific env,
    then the standard Anthropic env used by Claude Code.  The workflow entry
    deliberately defaults to Opus 4.8 instead of inheriting an unrelated
    global model selection.
    """
    env = os.environ if environ is None else environ
    resolved_endpoint = (
        endpoint
        or env.get("TM_SUBJECT_BASE_URL")
        or env.get("AIHUBMIX_BASE_URL")
        or env.get("ANTHROPIC_BASE_URL")
    )
    resolved_key = (
        apikey
        or env.get("TM_SUBJECT_API_KEY")
        or env.get("AIHUBMIX_API_KEY")
        or env.get("ANTHROPIC_AUTH_TOKEN")
        or env.get("ANTHROPIC_API_KEY")
    )
    resolved_model = model or env.get("TM_SUBJECT_MODEL") or DEFAULT_SUBJECT_MODEL
    missing: list[str] = []
    if not resolved_endpoint or not resolved_endpoint.strip():
        missing.append("endpoint")
    if not resolved_key or not resolved_key.strip():
        missing.append("API key")
    if not resolved_model or not resolved_model.strip():
        missing.append("model")
    if missing:
        raise RuntimeError(
            "subject Claude configuration missing "
            + ", ".join(missing)
            + "; pass --endpoint/--apikey/--model or set "
              "TM_SUBJECT_*, AIHUBMIX_API_KEY, and ANTHROPIC_BASE_URL"
        )
    parsed_endpoint = urlparse(resolved_endpoint.strip())
    if (
        parsed_endpoint.scheme not in {"http", "https"}
        or not parsed_endpoint.hostname
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
        or parsed_endpoint.query
        or parsed_endpoint.fragment
    ):
        raise RuntimeError(
            "subject Claude endpoint must be an http(s) base URL without embedded "
            "credentials, query, or fragment"
        )
    return SubjectCredentials(
        endpoint=resolved_endpoint.strip(),
        apikey=resolved_key.strip(),
        model=resolved_model.strip(),
    )


def build_local_subject_env(
    endpoint: str,
    apikey: str,
    model: str,
    config_dir: Path,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build an isolated HOST environment for the local subject process.

    Unlike the container environment, this retains PATH/HOME/locale so the
    locally installed ``claude`` binary and normal command-line tools work.
    Existing provider credentials and Claude session state are removed first.
    """
    env = build_local_command_env(base_env)
    env.update(_anthropic_vars(endpoint, apikey, model))
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
    # Claude Code 2.1.x aborts streaming requests when response headers take
    # longer than 60 seconds by default.  Large Aihubmix/Opus requests can
    # legitimately have a longer time-to-first-byte, so align the local
    # client's header wait with the recording proxy's 10-minute timeout.
    env["CLAUDE_CODE_CONNECT_TIMEOUT_MS"] = str(LOCAL_CONNECT_TIMEOUT_MS)
    # Keep the provider key in the Claude parent process but scrub it (and
    # other cloud credentials) from Bash/hooks/MCP child environments.
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"

    # Ensure the child connects directly to the loopback recording proxy even
    # when the host uses a corporate HTTP proxy.
    for key in ("NO_PROXY", "no_proxy"):
        existing = [part.strip() for part in env.get(key, "").split(",") if part.strip()]
        for host in ("127.0.0.1", "localhost"):
            if host not in existing:
                existing.append(host)
        env[key] = ",".join(existing)
    return env


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
