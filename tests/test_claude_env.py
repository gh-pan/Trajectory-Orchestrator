import json
from pathlib import Path

import pytest

from trajectory_maker import claude_env


def test_strip_anthropic_removes_all_anthropic_keys():
    env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_BASE_URL": "https://leak.example.com",
        "ANTHROPIC_API_KEY": "leak-key",
        "ANTHROPIC_AUTH_TOKEN": "leak-token",
        "ANTHROPIC_MODEL": "leak-model",
        "HOME": "/tmp",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "leak-sonnet",
    }
    stripped = claude_env._strip_anthropic(env)
    assert "PATH" in stripped
    assert "HOME" in stripped
    # all ANTHROPIC_* removed
    assert not any(k.startswith("ANTHROPIC_") for k in stripped)
    assert "ANTHROPIC_BASE_URL" not in stripped
    assert "ANTHROPIC_API_KEY" not in stripped


def test_build_subject_env_pins_caller_values(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    base = {
        "PATH": "/usr/bin",
        "ANTHROPIC_BASE_URL": "https://host-leak.example.com",
        "ANTHROPIC_API_KEY": "host-leak-key",
        "ANTHROPIC_MODEL": "host-leak-model",
        "HOME": "/tmp",
    }
    env = claude_env.build_subject_env(
        endpoint="https://subj.example.com",
        apikey="subj-key",
        model="subj-model",
        base_env=base,
    )
    # caller values pinned
    assert env["ANTHROPIC_BASE_URL"] == "https://subj.example.com"
    assert env["ANTHROPIC_API_KEY"] == "subj-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "subj-key"
    assert env["ANTHROPIC_MODEL"] == "subj-model"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "subj-model"
    # host leaked values stripped (then re-pinned to caller)
    assert env["HOME"] == "/tmp"
    assert env["PATH"] == "/usr/bin"
    # CLAUDE_CONFIG_DIR set to project-local config dir
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_build_subject_env_no_config_dir_when_missing(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(claude_env, "CONFIG_DIR", missing)
    env = claude_env.build_subject_env("ep", "key", "m", base_env={"PATH": "/x"})
    assert "CLAUDE_CONFIG_DIR" not in env
    assert env["ANTHROPIC_BASE_URL"] == "ep"


def test_build_meta_env_raises_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", tmp_path / "settings.json")
    # clear TM_SYNTH_* env vars
    for k in ("TM_SYNTH_BASE_URL", "TM_SYNTH_API_KEY", "TM_SYNTH_MODEL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="meta claude endpoint not configured"):
        claude_env.build_meta_env(base_env={"PATH": "/x"})


def test_build_meta_env_raises_when_empty_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
    }}))
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", settings)
    for k in ("TM_SYNTH_BASE_URL", "TM_SYNTH_API_KEY", "TM_SYNTH_MODEL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="meta claude endpoint not configured"):
        claude_env.build_meta_env(base_env={"PATH": "/x"})


def test_build_meta_env_reads_from_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://meta.example.com",
        "ANTHROPIC_AUTH_TOKEN": "meta-key",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "meta-model",
    }}))
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", settings)
    for k in ("TM_SYNTH_BASE_URL", "TM_SYNTH_API_KEY", "TM_SYNTH_MODEL"):
        monkeypatch.delenv(k, raising=False)
    env = claude_env.build_meta_env(base_env={
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "host-leak",  # should be stripped
    })
    assert env["ANTHROPIC_BASE_URL"] == "https://meta.example.com"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "meta-key"
    assert env["ANTHROPIC_API_KEY"] == "meta-key"
    assert env["ANTHROPIC_MODEL"] == "meta-model"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "meta-model"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)
    # host leak stripped
    assert env.get("ANTHROPIC_API_KEY") == "meta-key"  # not host-leak


def test_build_meta_env_env_vars_override_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://meta.example.com",
        "ANTHROPIC_AUTH_TOKEN": "meta-key",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "meta-model",
    }}))
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", settings)
    monkeypatch.setenv("TM_SYNTH_BASE_URL", "https://override.example.com")
    monkeypatch.setenv("TM_SYNTH_API_KEY", "override-key")
    monkeypatch.setenv("TM_SYNTH_MODEL", "override-model")
    env = claude_env.build_meta_env(base_env={"PATH": "/x"})
    assert env["ANTHROPIC_BASE_URL"] == "https://override.example.com"
    assert env["ANTHROPIC_API_KEY"] == "override-key"
    assert env["ANTHROPIC_MODEL"] == "override-model"


def test_meta_model_reads_from_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://meta.example.com",
        "ANTHROPIC_AUTH_TOKEN": "meta-key",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "meta-sonnet-model",
    }}))
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", settings)
    monkeypatch.delenv("TM_SYNTH_MODEL", raising=False)
    assert claude_env.meta_model() == "meta-sonnet-model"


def test_meta_model_env_overrides_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "settings-model",
    }}))
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", settings)
    monkeypatch.setenv("TM_SYNTH_MODEL", "env-model")
    assert claude_env.meta_model() == "env-model"


def test_meta_model_none_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.delenv("TM_SYNTH_MODEL", raising=False)
    assert claude_env.meta_model() is None


def test_load_settings_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", tmp_path / "nope.json")
    assert claude_env._load_settings() == {}


def test_load_settings_handles_invalid_json(tmp_path, monkeypatch):
    bad = tmp_path / "settings.json"
    bad.write_text("not json {{{")
    monkeypatch.setattr(claude_env, "SETTINGS_FILE", bad)
    assert claude_env._load_settings() == {}
