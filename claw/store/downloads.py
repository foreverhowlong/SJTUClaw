"""Temporary download snapshots created by the core runtime."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from claw.errors import DownloadError


@dataclass(frozen=True)
class DownloadRecord:
    download_id: str
    session_id: str
    filename: str
    size: int
    created_at: datetime
    expires_at: datetime
    blob_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "downloadId": self.download_id,
            "sessionId": self.session_id,
            "filename": self.filename,
            "size": self.size,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "downloadUrl": f"/api/downloads/{self.download_id}",
        }


class DownloadStore:
    def __init__(self, root: str | Path, *, ttl_seconds: int = 900) -> None:
        if ttl_seconds <= 0:
            raise ValueError("download ttl_seconds 必须大于 0。")
        self.root = Path(root)
        self.ttl_seconds = ttl_seconds

    def create(self, session_id: str, source: Path) -> DownloadRecord:
        if source.is_symlink() or not source.is_file():
            raise DownloadError(f"下载源不是安全的普通文件: {source}。")
        download_id = f"download_{uuid4().hex}"
        directory = self.root / download_id
        now = datetime.now(timezone.utc)
        try:
            directory.mkdir(parents=True)
            blob = directory / "blob"
            with source.open("rb") as input_handle, blob.open("xb") as output:
                shutil.copyfileobj(input_handle, output)
                output.flush()
                os.fsync(output.fileno())
            record = DownloadRecord(
                download_id,
                session_id,
                source.name,
                blob.stat().st_size,
                now,
                now + timedelta(seconds=self.ttl_seconds),
                blob,
            )
            (directory / "meta.json").write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return record
        except OSError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise DownloadError(f"创建下载入口失败: {exc}") from exc

    def get(self, download_id: str) -> DownloadRecord:
        if not download_id.startswith("download_") or any(
            part in download_id for part in ("/", "\\", "..")
        ):
            raise DownloadError(f"无效的 downloadId: {download_id!r}。")
        directory = self.root / download_id
        try:
            value = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
            record = DownloadRecord(
                str(value["downloadId"]),
                str(value["sessionId"]),
                str(value["filename"]),
                int(value["size"]),
                datetime.fromisoformat(str(value["createdAt"])),
                datetime.fromisoformat(str(value["expiresAt"])),
                directory / "blob",
            )
        except FileNotFoundError as exc:
            raise DownloadError(f"下载入口不存在: {download_id}。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
            raise DownloadError(f"读取下载入口失败: {exc}") from exc
        if record.expires_at <= datetime.now(timezone.utc):
            shutil.rmtree(directory, ignore_errors=True)
            raise DownloadError(f"下载入口已过期: {download_id}。")
        if record.blob_path.is_symlink() or not record.blob_path.is_file():
            raise DownloadError(f"下载文件不存在: {download_id}。")
        return record

    def cleanup_expired(self) -> None:
        if not self.root.exists():
            return
        for directory in self.root.glob("download_*"):
            try:
                self.get(directory.name)
            except DownloadError:
                continue
