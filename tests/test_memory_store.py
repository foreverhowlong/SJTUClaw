from pathlib import Path

import pytest

from claw.errors import MemoryError
from claw.store.memory import MemoryStore


def test_memory_store_persists_lists_and_deletes_markdown_files(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    store = MemoryStore(root)
    first = store.add("用户偏好中文回答。")
    second = store.add("用户正在实现 claw 项目。")

    restored = MemoryStore(root)
    assert {item.memory_id: item.content for item in restored.list()} == {
        first.memory_id: "用户偏好中文回答。",
        second.memory_id: "用户正在实现 claw 项目。",
    }
    assert (root / f"{first.memory_id}.md").read_text(encoding="utf-8") == (
        "用户偏好中文回答。\n"
    )

    restored.delete(first.memory_id)
    assert [item.memory_id for item in restored.list()] == [second.memory_id]


def test_memory_store_rejects_blank_content_and_invalid_ids(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory")

    with pytest.raises(MemoryError, match="不能为空"):
        store.add("  ")
    with pytest.raises(MemoryError, match="无效的 memoryId"):
        store.delete("../outside")
    with pytest.raises(MemoryError, match="不存在"):
        store.delete("mem_0123456789ab")


def test_memory_store_reports_empty_files_as_corrupt(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    path = root / "mem_0123456789ab.md"
    path.write_text("\n", encoding="utf-8")

    with pytest.raises(MemoryError, match="数据损坏"):
        MemoryStore(root).list()

    assert path.read_text(encoding="utf-8") == "\n"
