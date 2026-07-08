"""Configuration loading for OpenAI-compatible LLM providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from claw.errors import ConfigError


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def load_env_file(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE pairs from a dotenv file.

    This intentionally supports only the common dotenv subset needed by this
    project, avoiding an extra dependency for Step 0.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path} 第 {line_no} 行不是有效的 KEY=VALUE 配置。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_matching_quotes(value.strip())
        if not key:
            raise ConfigError(f"{path} 第 {line_no} 行配置名为空。")
        values[key] = value
    return values


def load_llm_config(env_path: Path | str = ".env") -> LLMConfig:
    env_file_values = load_env_file(Path(env_path))

    def get(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name) or env_file_values.get(name) or default

    api_key = get("LLM_API_KEY")
    model = get("LLM_MODEL")
    base_url = get("LLM_BASE_URL", DEFAULT_BASE_URL)
    timeout_raw = get("LLM_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))

    missing = [name for name, value in (("LLM_API_KEY", api_key), ("LLM_MODEL", model)) if not value]
    if missing:
        names = ", ".join(missing)
        raise ConfigError(f"缺少必要配置: {names}。请在 .env 或环境变量中设置。")

    api_key_value = cast(str, api_key)
    model_value = cast(str, model)

    try:
        timeout_seconds = float(timeout_raw or DEFAULT_TIMEOUT_SECONDS)
    except ValueError as exc:
        raise ConfigError("LLM_TIMEOUT 必须是数字秒数。") from exc

    if timeout_seconds <= 0:
        raise ConfigError("LLM_TIMEOUT 必须大于 0。")

    return LLMConfig(
        api_key=api_key_value.strip(),
        base_url=(base_url or DEFAULT_BASE_URL).strip().rstrip("/"),
        model=model_value.strip(),
        timeout_seconds=timeout_seconds,
    )


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
