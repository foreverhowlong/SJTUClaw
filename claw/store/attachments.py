"""Session-scoped attachment storage with an atomic metadata index."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import uuid4

from filelock import FileLock, Timeout

from claw.errors import AttachmentError
from claw.store.sessions import LOCK_TIMEOUT_SECONDS, SessionStore


DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_READ_CHARS = 64 * 1024
COPY_CHUNK_BYTES = 64 * 1024
ATTACHMENT_ID_PATTERN = re.compile(r"attachment_[0-9a-f]{12}")


@dataclass(frozen=True)
class AttachmentMetadata:
    attachment_id: str
    filename: str
    size: int
    content_type: str
    uploaded_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "attachmentId": self.attachment_id,
            "filename": self.filename,
            "size": self.size,
            "contentType": self.content_type,
            "uploadedAt": self.uploaded_at.isoformat(),
        }


class AttachmentStore:
    """Persist uploaded bytes below the owning session directory only."""

    def __init__(
        self,
        sessions: SessionStore,
        *,
        max_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须大于 0。")
        self._sessions = sessions
        self.max_bytes = max_bytes

    def list(self, session_id: str) -> list[AttachmentMetadata]:
        self._sessions.load(session_id)
        with self._locked(session_id):
            return self._read_index(session_id)

    def save(
        self,
        session_id: str,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> AttachmentMetadata:
        self._sessions.load(session_id)
        safe_name = _validate_filename(filename)
        normalized_type = (content_type or "application/octet-stream").strip()
        if not normalized_type:
            normalized_type = "application/octet-stream"

        with self._locked(session_id):
            attachment_dir = self._attachment_dir(session_id)
            try:
                attachment_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AttachmentError(f"创建附件目录失败: {exc}") from exc

            attachment_id = f"attachment_{uuid4().hex[:12]}"
            destination = attachment_dir / attachment_id
            temporary = attachment_dir / f".{attachment_id}.{uuid4().hex}.tmp"
            size = 0
            try:
                with temporary.open("xb") as output:
                    while True:
                        chunk = source.read(COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            raise AttachmentError("附件流必须返回 bytes。")
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise AttachmentError(
                                f"附件超过大小限制 {self.max_bytes} bytes。"
                            )
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)

                record = AttachmentMetadata(
                    attachment_id=attachment_id,
                    filename=safe_name,
                    size=size,
                    content_type=normalized_type,
                    uploaded_at=datetime.now(timezone.utc),
                )
                records = [*self._read_index(session_id), record]
                self._write_index(session_id, records)
                return record
            except AttachmentError:
                destination.unlink(missing_ok=True)
                raise
            except OSError as exc:
                destination.unlink(missing_ok=True)
                raise AttachmentError(f"保存附件失败: {exc}") from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def read_text(
        self,
        session_id: str,
        attachment_id: str,
        *,
        max_chars: int = MAX_READ_CHARS,
    ) -> dict[str, object]:
        """Read one UTF-8 attachment owned by the current session."""
        self._sessions.load(session_id)
        if max_chars <= 0:
            raise ValueError("max_chars 必须大于 0。")
        if not ATTACHMENT_ID_PATTERN.fullmatch(attachment_id):
            raise AttachmentError(f"无效的 attachmentId: {attachment_id!r}。")

        with self._locked(session_id):
            record = next(
                (
                    item
                    for item in self._read_index(session_id)
                    if item.attachment_id == attachment_id
                ),
                None,
            )
            if record is None:
                raise AttachmentError(
                    f"当前 session 不存在附件: {attachment_id}。"
                )

            path = self._attachment_dir(session_id) / attachment_id
            if path.is_symlink():
                raise AttachmentError("附件 blob 不能是符号链接。")
            if not path.is_file():
                raise AttachmentError(f"附件 blob 不存在: {attachment_id}。")
            try:
                with path.open("r", encoding="utf-8") as handle:
                    content = handle.read(max_chars + 1)
            except UnicodeDecodeError as exc:
                raise AttachmentError(
                    f"附件不是有效的 UTF-8 文本: {record.filename}。"
                ) from exc
            except OSError as exc:
                raise AttachmentError(f"读取附件失败: {exc}") from exc
            if "\x00" in content:
                raise AttachmentError(
                    f"附件包含二进制内容，无法作为文本读取: {record.filename}。"
                )

            truncated = len(content) > max_chars
            visible = content[:max_chars]
            return {
                "attachmentId": record.attachment_id,
                "filename": record.filename,
                "contentType": record.content_type,
                "content": visible,
                "truncated": truncated,
                "charactersRead": len(visible),
            }

    def _read_index(self, session_id: str) -> list[AttachmentMetadata]:
        path = self._attachment_dir(session_id) / "index.json"
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AttachmentError(f"读取附件索引失败 {path}: {exc}") from exc
        if not isinstance(value, list):
            raise AttachmentError(f"附件索引损坏: {path} 必须包含 JSON array。")

        records: list[AttachmentMetadata] = []
        for item in value:
            try:
                if not isinstance(item, dict):
                    raise TypeError("record is not an object")
                uploaded_at = datetime.fromisoformat(str(item["uploadedAt"]))
                record = AttachmentMetadata(
                    attachment_id=str(item["attachmentId"]),
                    filename=str(item["filename"]),
                    size=int(item["size"]),
                    content_type=str(item["contentType"]),
                    uploaded_at=uploaded_at,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AttachmentError(f"附件索引包含无效记录: {exc}") from exc
            if (
                not ATTACHMENT_ID_PATTERN.fullmatch(record.attachment_id)
                or record.size < 0
            ):
                raise AttachmentError("附件索引包含无效 attachmentId 或 size。")
            records.append(record)
        return sorted(records, key=lambda item: item.uploaded_at, reverse=True)

    def _write_index(
        self,
        session_id: str,
        records: list[AttachmentMetadata],
    ) -> None:
        path = self._attachment_dir(session_id) / "index.json"
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        content = json.dumps(
            [record.to_dict() for record in records],
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise AttachmentError(f"写入附件索引失败 {path}: {exc}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _locked(self, session_id: str) -> Iterator[None]:
        lock_dir = self._sessions.root / ".locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AttachmentError(f"创建附件锁目录失败: {exc}") from exc
        lock = FileLock(
            lock_dir / f"{session_id}.attachments.lock",
            timeout=LOCK_TIMEOUT_SECONDS,
        )
        try:
            with lock:
                yield
        except Timeout as exc:
            raise AttachmentError(f"等待 session {session_id} 附件锁超时。") from exc

    def _attachment_dir(self, session_id: str) -> Path:
        return self._sessions.root / session_id / "attachments"


def _validate_filename(filename: str) -> str:
    normalized = filename.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise AttachmentError("附件文件名无效。")
    return normalized
