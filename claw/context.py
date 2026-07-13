"""Build model input from stable context and conversation history."""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from importlib import resources
from pathlib import Path

from claw.errors import ConfigError
from claw.messages import Message
from claw.store.memory import MemoryRecord


DEFAULT_SYSTEM_PROMPT_RESOURCE = "prompts/system_prompt.md"
DEFAULT_SOUL_RESOURCE = "prompts/soul.md"
TOOL_RESULT_PREVIEW_CHARS = 16_384
TOTAL_TOOL_RESULT_PREVIEW_CHARS = 32_768


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
        session_summary: str = "",
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
        normalized_summary = session_summary.strip()
        if normalized_summary:
            stable_sections.append(f"[Session Summary]\n{normalized_summary}")
        return [
            {"role": "system", "content": "\n\n".join(stable_sections)},
            *project_messages(messages),
        ]


def project_messages(
    messages: Sequence[Message],
    *,
    per_result_chars: int = TOOL_RESULT_PREVIEW_CHARS,
    total_result_chars: int = TOTAL_TOOL_RESULT_PREVIEW_CHARS,
) -> list[Message]:
    """Return an LLM-safe copy while preserving every tool protocol message.

    SessionStore remains the full-fidelity source of truth. Only this context
    projection is truncated, allocating raw preview characters newest-first.
    """
    if per_result_chars < 0 or total_result_chars < 0:
        raise ValueError("tool result preview budgets 不能为负数。")
    projected = [deepcopy(message) for message in messages]
    remaining = total_result_chars
    for message in reversed(projected):
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        allowance = min(per_result_chars, remaining)
        if len(content) <= allowance:
            remaining -= len(content)
            continue
        preview = content[:allowance]
        message["content"] = json.dumps(
            {
                "runtimeTruncated": True,
                "originalCharacters": len(content),
                "preview": preview,
            },
            ensure_ascii=False,
        )
        remaining -= len(preview)
    return projected


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
