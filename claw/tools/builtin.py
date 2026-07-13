"""The Step 5 read-only tool set."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from claw.tools.registry import ToolDefinition, ToolRegistry


MAX_READ_CHARS = 64 * 1024


def build_read_only_registry(base_dir: str | Path | None = None) -> ToolRegistry:
    root = Path.cwd() if base_dir is None else Path(base_dir)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="current_time",
            description="Return the current local date, time, and UTC offset.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: datetime.now().astimezone().isoformat(),
        )
    )
    registry.register(
        ToolDefinition(
            name="list_dir",
            description="List the direct children of a directory without changing it.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=lambda args: _list_dir(root, args),
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file without changing it.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=lambda args: _read_file(root, args),
        )
    )
    return registry


def _resolve(root: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else root / path


def _list_dir(root: Path, args: dict[str, Any]) -> list[dict[str, str]]:
    path = _resolve(root, args.get("path", "."))
    if not path.exists():
        raise FileNotFoundError(f"目录不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    entries: list[dict[str, str]] = []
    for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        kind = "directory" if child.is_dir() else "file"
        if child.is_symlink():
            kind = "symlink"
        entries.append({"name": child.name, "type": kind})
    return entries


def _read_file(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(root, args["path"])
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"不是文件: {path}")
    with path.open("r", encoding="utf-8") as handle:
        content = handle.read(MAX_READ_CHARS + 1)
    truncated = len(content) > MAX_READ_CHARS
    return {
        "path": str(path),
        "content": content[:MAX_READ_CHARS],
        "truncated": truncated,
        "charactersRead": min(len(content), MAX_READ_CHARS),
    }
