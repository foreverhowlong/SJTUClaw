"""Build model input from stable context and conversation history."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from claw.errors import ConfigError
from claw.llm import Message
from claw.store.memory import MemoryRecord


DEFAULT_SYSTEM_PROMPT_RESOURCE = "prompts/system_prompt.md"
DEFAULT_SOUL_RESOURCE = "prompts/soul.md"


class ContextBuilder:
    """Assemble stable context before the current session's messages."""

    def __init__(self, system_prompt: str, soul: str) -> None:
        normalized_system_prompt = system_prompt.strip()
        normalized_soul = soul.strip()
        if not normalized_system_prompt:
            raise ValueError("system_prompt 不能为空。")
        if not normalized_soul:
            raise ValueError("soul 不能为空。")
        self._system_prompt = normalized_system_prompt
        self._soul = normalized_soul

    @classmethod
    def from_files(
        cls,
        system_prompt_path: str | Path | None = None,
        soul_path: str | Path | None = None,
    ) -> ContextBuilder:
        return cls(
            _load_context(
                system_prompt_path,
                DEFAULT_SYSTEM_PROMPT_RESOURCE,
                "system prompt",
            ),
            _load_context(soul_path, DEFAULT_SOUL_RESOURCE, "soul"),
        )

    def build(
        self,
        messages: Sequence[Message],
        memories: Sequence[MemoryRecord] = (),
    ) -> list[Message]:
        stable_sections = [
            f"[System Prompt]\n{self._system_prompt}",
            f"[Soul]\n{self._soul}",
        ]
        if memories:
            rendered_memories = "\n\n".join(
                f"[{memory.memory_id}]\n{memory.content}" for memory in memories
            )
            stable_sections.append(f"[Memory]\n{rendered_memories}")
        return [
            {"role": "system", "content": "\n\n".join(stable_sections)},
            *(message.copy() for message in messages),
        ]


def _load_context(
    path: str | Path | None,
    resource_name: str,
    label: str,
) -> str:
    if path is not None:
        return _load_context_file(Path(path), label)
    try:
        content = (
            resources.files("claw")
            .joinpath(resource_name)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise ConfigError(f"读取默认 {label} 资源失败 {resource_name}: {exc}") from exc
    normalized = content.strip()
    if not normalized:
        raise ConfigError(f"默认 {label} 资源不能为空: {resource_name}。")
    return normalized


def _load_context_file(path: Path, label: str) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ConfigError(f"缺少 {label} 配置文件: {path}。") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"读取 {label} 配置文件失败 {path}: {exc}") from exc
    if not content:
        raise ConfigError(f"{label} 配置文件不能为空: {path}。")
    return content
