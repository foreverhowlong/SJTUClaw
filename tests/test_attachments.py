from io import BytesIO

import pytest

from claw.context import ContextBuilder
from claw.errors import AttachmentError, SessionError
from claw.store.attachments import MAX_READ_CHARS, AttachmentStore
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
    assert "read_attachment" in content
    assert "not persisted" in content


def test_read_text_returns_owned_utf8_content_and_truncates(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = AttachmentStore(sessions)
    saved = store.save(
        session.session_id,
        "long.md",
        "text/markdown",
        BytesIO(("你" * (MAX_READ_CHARS + 1)).encode()),
    )

    result = store.read_text(session.session_id, saved.attachment_id)

    assert result["filename"] == "long.md"
    assert result["content"] == "你" * MAX_READ_CHARS
    assert result["charactersRead"] == MAX_READ_CHARS
    assert result["truncated"] is True


def test_read_text_rejects_cross_session_binary_and_invalid_id(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    first = sessions.create()
    second = sessions.create()
    store = AttachmentStore(sessions)
    saved = store.save(
        first.session_id,
        "binary.dat",
        "application/octet-stream",
        BytesIO(b"hello\x00world"),
    )

    with pytest.raises(AttachmentError, match="当前 session 不存在附件"):
        store.read_text(second.session_id, saved.attachment_id)
    with pytest.raises(AttachmentError, match="二进制内容"):
        store.read_text(first.session_id, saved.attachment_id)
    with pytest.raises(AttachmentError, match="无效的 attachmentId"):
        store.read_text(first.session_id, "../binary.dat")


def test_read_text_rejects_invalid_utf8_missing_blob_and_symlink(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = AttachmentStore(sessions)

    invalid = store.save(
        session.session_id,
        "invalid.txt",
        "text/plain",
        BytesIO(b"\xff\xfe"),
    )
    with pytest.raises(AttachmentError, match="不是有效的 UTF-8"):
        store.read_text(session.session_id, invalid.attachment_id)

    missing = store.save(
        session.session_id,
        "missing.txt",
        "text/plain",
        BytesIO(b"missing"),
    )
    missing_path = (
        sessions.root / session.session_id / "attachments" / missing.attachment_id
    )
    missing_path.unlink()
    with pytest.raises(AttachmentError, match="blob 不存在"):
        store.read_text(session.session_id, missing.attachment_id)

    linked = store.save(
        session.session_id,
        "linked.txt",
        "text/plain",
        BytesIO(b"linked"),
    )
    linked_path = (
        sessions.root / session.session_id / "attachments" / linked.attachment_id
    )
    linked_path.unlink()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    linked_path.symlink_to(target)
    with pytest.raises(AttachmentError, match="符号链接"):
        store.read_text(session.session_id, linked.attachment_id)
