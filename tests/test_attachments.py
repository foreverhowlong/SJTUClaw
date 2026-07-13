from io import BytesIO

import pytest

from claw.context import ContextBuilder
from claw.errors import AttachmentError, SessionError
from claw.store.attachments import AttachmentStore
from claw.store.sessions import SessionStore


def test_attachment_is_persisted_and_listed_only_for_owning_session(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    first = sessions.create()
    second = sessions.create()
    store = AttachmentStore(sessions)

    saved = store.save(
        first.session_id,
        "notes.txt",
        "text/plain",
        BytesIO(b"hello"),
    )

    assert saved.filename == "notes.txt"
    assert saved.size == 5
    assert store.list(first.session_id) == [saved]
    assert store.list(second.session_id) == []
    blob = sessions.root / first.session_id / "attachments" / saved.attachment_id
    assert blob.read_bytes() == b"hello"


def test_attachment_rejects_unsafe_filename_and_oversized_body(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = AttachmentStore(sessions, max_bytes=4)

    with pytest.raises(AttachmentError, match="文件名无效"):
        store.save(session.session_id, "../secret", None, BytesIO(b"x"))
    with pytest.raises(AttachmentError, match="大小限制"):
        store.save(session.session_id, "large.txt", None, BytesIO(b"12345"))

    attachment_dir = sessions.root / session.session_id / "attachments"
    assert not attachment_dir.exists() or list(attachment_dir.iterdir()) == []


def test_attachment_requires_existing_session(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    store = AttachmentStore(sessions)

    with pytest.raises(SessionError, match="不存在"):
        store.list("session_0123456789ab")


def test_context_lists_attachment_metadata_without_server_path(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = AttachmentStore(sessions)
    saved = store.save(
        session.session_id,
        "brief.md",
        "text/markdown",
        BytesIO(b"content"),
    )

    context = ContextBuilder("rules", "style").build(
        [],
        attachments=store.list(session.session_id),
    )

    content = context[0]["content"]
    assert "[Session Attachments]" in content
    assert "brief.md" in content
    assert saved.attachment_id in content
    assert str(sessions.root) not in content
