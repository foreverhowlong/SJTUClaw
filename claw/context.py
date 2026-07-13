"""Build model input from stable context and conversation history."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence

from claw.errors import ConfigError
from claw.llm import Message
from claw.session import Session
from claw.store.memory import MemoryRecord


DEFAULT_SYSTEM_PROMPT_PATH = Path("prompts/system_prompt.md")
DEFAULT_SOUL_PATH = Path("prompts/soul.md")


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
        system_prompt_path: str | Path = DEFAULT_SYSTEM_PROMPT_PATH,
        soul_path: str | Path = DEFAULT_SOUL_PATH,
    ) -> ContextBuilder:
        return cls(
            _load_context_file(Path(system_prompt_path), "system prompt"),
            _load_context_file(Path(soul_path), "soul"),
        )

    def build(
        self,
        session: Session,
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
            *session.messages,
        ]


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
