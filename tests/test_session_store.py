import json
import os

import pytest

from claw.errors import SessionConflictError, SessionError
from claw.store.sessions import SessionStore


TURN = [
    {"role": "user", "content": "graph question"},
    {"role": "assistant", "content": "graph answer"},
]

TOOL_TURN = [
    {"role": "user", "content": "read it"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "read_file",
        "content": '{"ok":true,"result":"hello"}',
    },
    {"role": "assistant", "content": "The file says hello."},
]


def test_store_commits_append_only_turns_and_persists_metadata(tmp_path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    first = store.create("Algorithms")
    committed = store.commit_turn(first.session_id, expected_revision=0, messages=TURN)
    second = store.create("Database")

    restored = SessionStore(root)
    assert {item.session_id for item in restored.list()} == {
        first.session_id,
        second.session_id,
    }
    assert restored.load(first.session_id).messages == TURN
    assert committed.revision == 1

    lines = (root / first.session_id / "messages.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "turn"
    assert record["revision"] == 1
    assert record["messages"] == TURN


def test_store_round_trips_complete_tool_turn_and_copies_nested_values(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()

    committed = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=TOOL_TURN,
    )
    loaded = store.load(session.session_id)
    committed.messages[1]["tool_calls"][0]["function"]["name"] = "changed"

    assert loaded.messages == TOOL_TURN
    assert store.load(session.session_id).messages == TOOL_TURN


def test_store_rejects_orphan_or_mismatched_tool_results(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    orphan = [TOOL_TURN[0], TOOL_TURN[2], TOOL_TURN[-1]]
    mismatched = [message.copy() for message in TOOL_TURN]
    mismatched[2] = {**mismatched[2], "tool_call_id": "other"}

    with pytest.raises(SessionError, match="中间消息"):
        store.commit_turn(session.session_id, expected_revision=0, messages=orphan)
    with pytest.raises(SessionError, match="一一对应"):
        store.commit_turn(session.session_id, expected_revision=0, messages=mismatched)


def test_store_reads_legacy_per_message_jsonl_and_appends_new_turn(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    path = store.root / session.session_id / "messages.jsonl"
    path.write_text(
        '\n'.join(json.dumps(message) for message in TURN) + "\n",
        encoding="utf-8",
    )

    legacy = store.load(session.session_id)
    assert legacy.messages == TURN
    assert legacy.revision == 2

    store.commit_turn(
        session.session_id,
        expected_revision=2,
        messages=[
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": "answer"},
        ],
    )
    assert store.load(session.session_id).message_count == 4


def test_stale_revision_is_rejected_without_overwriting_history(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first_snapshot = store.load(session.session_id)
    second_snapshot = store.load(session.session_id)

    store.commit_turn(
        session.session_id,
        expected_revision=first_snapshot.revision,
        messages=TURN,
    )
    with pytest.raises(SessionConflictError, match="expected revision"):
        store.commit_turn(
            session.session_id,
            expected_revision=second_snapshot.revision,
            messages=[
                {"role": "user", "content": "stale"},
                {"role": "assistant", "content": "stale answer"},
            ],
        )

    assert store.load(session.session_id).messages == TURN


def test_append_failure_truncates_uncommitted_record(tmp_path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    messages_path = store.root / session.session_id / "messages.jsonl"
    original_fsync = os.fsync
    calls = 0

    def fail_first_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated disk failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)
    with pytest.raises(SessionError, match="提交 session turn 失败"):
        store.commit_turn(session.session_id, expected_revision=0, messages=TURN)

    assert messages_path.read_text(encoding="utf-8") == ""
    assert store.load(session.session_id).messages == []


def test_create_failure_does_not_publish_partial_session(tmp_path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    original = store._atomic_write
    calls = 0

    def fail_second_write(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated failure")
        original(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_second_write)
    with pytest.raises(SessionError, match="创建 session 失败"):
        store.create()

    assert store.list() == []


def test_store_renames_without_rewriting_message_log_and_deletes(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    store.commit_turn(session.session_id, expected_revision=0, messages=TURN)
    messages_path = store.root / session.session_id / "messages.jsonl"
    before = messages_path.read_bytes()

    renamed = store.rename(session.session_id, "Course project")
    assert renamed.title == "Course project"
    assert messages_path.read_bytes() == before

    store.delete(session.session_id)
    with pytest.raises(SessionError, match="不存在"):
        store.load(session.session_id)


def test_store_reports_corrupt_json_without_overwriting_it(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    messages_path = store.root / session.session_id / "messages.jsonl"
    messages_path.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(SessionError, match="数据损坏"):
        store.load(session.session_id)

    assert messages_path.read_text(encoding="utf-8") == "not-json\n"


def test_store_rejects_invalid_ids_and_mismatched_metadata(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()

    with pytest.raises(SessionError, match="无效的 sessionId"):
        store.load("../outside")

    meta_path = store.root / session.session_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sessionId"] = "session_ffffffffffff"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(SessionError, match="sessionId"):
        store.load(session.session_id)


def test_lock_io_errors_are_wrapped_as_session_errors(tmp_path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()

    def fail_lock(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("claw.store.sessions.FileLock.acquire", fail_lock)
    with pytest.raises(SessionError, match="获取 session .* 锁失败"):
        store.load(session.session_id)


def test_compaction_record_persists_summary_and_only_exposes_recent_turns(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=TURN,
    )
    second_turn = [
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]
    second = store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=second_turn,
    )

    compacted = store.commit_compaction(
        session.session_id,
        expected_revision=second.revision,
        summary="当前任务：继续回答 recent question。",
        recent_messages=second_turn,
    )
    restored = SessionStore(store.root).load(session.session_id)

    assert compacted.revision == 3
    assert restored.summary == "当前任务：继续回答 recent question。"
    assert restored.messages == second_turn
    records = [
        json.loads(line)
        for line in (store.root / session.session_id / "messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[-1]["type"] == "compaction"
    assert records[-1]["oldMessageCount"] == 2
    assert records[-1]["recentMessages"] == second_turn


def test_turns_after_compaction_keep_summary_and_revision_order(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=TURN,
    )
    recent = [
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "answer"},
    ]
    second = store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=recent,
    )
    compacted = store.commit_compaction(
        session.session_id,
        expected_revision=second.revision,
        summary="saved summary",
        recent_messages=recent,
    )
    final_turn = [
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "done"},
    ]

    store.commit_turn(
        session.session_id,
        expected_revision=compacted.revision,
        messages=final_turn,
    )
    restored = store.load(session.session_id)

    assert restored.summary == "saved summary"
    assert restored.messages == [*recent, *final_turn]
    assert restored.revision == 4


def test_compaction_rejects_stale_revision_and_non_suffix_without_change(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    committed = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=TURN,
    )
    next_turn = [
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "next answer"},
    ]
    latest = store.commit_turn(
        session.session_id,
        expected_revision=committed.revision,
        messages=next_turn,
    )

    with pytest.raises(SessionConflictError, match="expected revision"):
        store.commit_compaction(
            session.session_id,
            expected_revision=committed.revision,
            summary="stale summary",
            recent_messages=next_turn,
        )
    with pytest.raises(SessionError, match="后缀"):
        store.commit_compaction(
            session.session_id,
            expected_revision=latest.revision,
            summary="invalid summary",
            recent_messages=TURN,
        )

    restored = store.load(session.session_id)
    assert restored.summary == ""
    assert restored.messages == [*TURN, *next_turn]


def test_compaction_append_failure_keeps_complete_active_history(
    tmp_path,
    monkeypatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=TURN,
    )
    recent = [
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "answer"},
    ]
    latest = store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=recent,
    )
    log_path = store.root / session.session_id / "messages.jsonl"
    before = log_path.read_bytes()
    original_fsync = os.fsync
    calls = 0

    def fail_first_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated disk failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)
    with pytest.raises(SessionError, match="提交 session turn 失败"):
        store.commit_compaction(
            session.session_id,
            expected_revision=latest.revision,
            summary="must not persist",
            recent_messages=recent,
        )

    assert log_path.read_bytes() == before
    assert store.load(session.session_id).messages == [*TURN, *recent]
