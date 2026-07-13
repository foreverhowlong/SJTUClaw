import json

import pytest

from claw.errors import SessionError
from claw.store.sessions import SessionStore


def test_store_persists_independent_sessions_and_metadata(tmp_path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    first = store.create("Algorithms")
    first.append("user", "graph question")
    first.append("assistant", "graph answer")
    store.save(first)
    second = store.create("Database")

    restored = SessionStore(root)
    summaries = restored.list()

    assert {item.session_id for item in summaries} == {first.session_id, second.session_id}
    assert restored.load(first.session_id).messages == [
        {"role": "user", "content": "graph question"},
        {"role": "assistant", "content": "graph answer"},
    ]
    assert restored.load(second.session_id).messages == []
    assert (root / first.session_id / "meta.json").exists()
    assert (root / first.session_id / "messages.jsonl").exists()


def test_store_renames_and_deletes_by_stable_id(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()

    renamed = store.rename(session.session_id, "Course project")
    assert renamed.session_id == session.session_id
    assert store.load(session.session_id).title == "Course project"

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


def test_store_reports_mismatched_session_id(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    meta_path = store.root / session.session_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sessionId"] = "different"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SessionError, match="sessionId"):
        store.load(session.session_id)
