"""File-backed storage for cross-session, long-term memories."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from claw.errors import MemoryError


MEMORY_ID_PATTERN = re.compile(r"mem_[0-9a-f]{12}")


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    content: str


class MemoryStore:
    """Persist each manually managed memory as one readable Markdown file."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def add(self, content: str) -> MemoryRecord:
        normalized = content.strip()
        if not normalized:
            raise MemoryError("memory 内容不能为空。")

        memory_id = self._new_id()
        path = self._path(memory_id)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, normalized + "\n")
        except OSError as exc:
            raise MemoryError(f"保存 memory {memory_id} 失败: {exc}") from exc
        return MemoryRecord(memory_id=memory_id, content=normalized)

    def list(self) -> list[MemoryRecord]:
        if not self.root.exists():
            return []
        try:
            paths = sorted(
                path
                for path in self.root.iterdir()
                if path.is_file() and MEMORY_ID_PATTERN.fullmatch(path.stem)
            )
        except OSError as exc:
            raise MemoryError(f"无法列出 memory 目录 {self.root}: {exc}") from exc
        return [self._read(path) for path in paths]

    def delete(self, memory_id: str) -> None:
        path = self._path(memory_id)
        if path.is_symlink() or not path.is_file():
            raise MemoryError(f"Memory 不存在: {memory_id}。")
        try:
            path.unlink()
        except OSError as exc:
            raise MemoryError(f"删除 memory {memory_id} 失败: {exc}") from exc

    def _new_id(self) -> str:
        while True:
            memory_id = f"mem_{uuid4().hex[:12]}"
            if not self._path(memory_id).exists():
                return memory_id

    def _path(self, memory_id: str) -> Path:
        if not MEMORY_ID_PATTERN.fullmatch(memory_id):
            raise MemoryError(f"无效的 memoryId: {memory_id!r}。")
        return self.root / f"{memory_id}.md"

    def _read(self, path: Path) -> MemoryRecord:
        if path.is_symlink():
            raise MemoryError(f"Memory 数据不安全: {path} 不能是符号链接。")
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise MemoryError(f"读取 memory 失败 {path}: {exc}") from exc
        if not content:
            raise MemoryError(f"Memory 数据损坏: {path} 内容为空。")
        return MemoryRecord(memory_id=path.stem, content=content)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
