from pathlib import Path

import pytest

from claw.config import load_env_file, load_llm_config
from claw.errors import ConfigError


def test_load_env_file_reads_key_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """
        # comment
        LLM_API_KEY='abc'
        LLM_BASE_URL="https://example.com/v1"
        LLM_MODEL=test-model
        """,
        encoding="utf-8",
    )

    values = load_env_file(env_file)

    assert values["LLM_API_KEY"] == "abc"
    assert values["LLM_BASE_URL"] == "https://example.com/v1"
    assert values["LLM_MODEL"] == "test-model"


def test_load_env_file_only_strips_matching_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOOD='abc'\nBAD=\"abc'\n", encoding="utf-8")

    values = load_env_file(env_file)

    assert values["GOOD"] == "abc"
    assert values["BAD"] == "\"abc'"


def test_load_llm_config_requires_api_key_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ConfigError, match="LLM_API_KEY, LLM_MODEL"):
        load_llm_config(tmp_path / ".env")


def test_environment_overrides_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_KEY=file-key\nLLM_MODEL=file-model\nLLM_BASE_URL=https://file.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_API_KEY", "env-key")

    config = load_llm_config(env_file)

    assert config.api_key == "env-key"
    assert config.model == "file-model"
    assert config.base_url == "https://file.example/v1"
