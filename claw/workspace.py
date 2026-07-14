"""Session-scoped workspace boundaries shared by every file capability."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw.errors import WorkspaceError
from claw.session import Session
from claw.store.sessions import SessionStore


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def from_path(cls, value: str | Path) -> "Workspace":
        path = Path(value).expanduser()
        try:
            root = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"workspace 不存在或无法解析: {path}。") from exc
        if not root.is_dir():
            raise WorkspaceError(f"workspace 不是目录: {root}。")
        return cls(root)

    def resolve(
        self,
        raw_path: str,
        *,
        must_exist: bool = False,
        kind: str | None = None,
    ) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise WorkspaceError("workspace 相对路径不能为空。")
        relative = Path(raw_path)
        if relative.is_absolute():
            raise WorkspaceError("tool path 必须是 workspace 内的相对路径。")
        try:
            candidate = (self.root / relative).resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise WorkspaceError(f"路径不存在: {raw_path}。") from exc
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"无法解析 workspace 路径 {raw_path}: {exc}") from exc
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError(f"路径越过 workspace 边界: {raw_path}。")
        if kind == "file" and must_exist and not candidate.is_file():
            raise WorkspaceError(f"不是文件: {raw_path}。")
        if kind == "directory" and must_exist and not candidate.is_dir():
            raise WorkspaceError(f"不是目录: {raw_path}。")
        if not must_exist:
            parent = candidate.parent
            try:
                resolved_parent = parent.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise WorkspaceError(f"目标父目录不存在或无法访问: {raw_path}。") from exc
            if not resolved_parent.is_relative_to(self.root):
                raise WorkspaceError(f"路径越过 workspace 边界: {raw_path}。")
        return candidate

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root)) or "."


class WorkspaceService:
    """Validate and persist workspace bindings owned by sessions."""

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    def set(self, session_id: str, path: str) -> Session:
        workspace = Workspace.from_path(path)
        return self._sessions.set_workspace(session_id, str(workspace.root))

    def clear(self, session_id: str) -> Session:
        return self._sessions.set_workspace(session_id, None)

    def get(self, session_id: str) -> Workspace | None:
        return workspace_from_session(self._sessions.load(session_id))


def workspace_from_session(session: Session) -> Workspace | None:
    # SessionStore only persists canonical values produced by WorkspaceService.
    # Do not fail the whole turn if the directory is removed later; individual
    # tools will return the more useful path-specific error.
    return Workspace(Path(session.workspace)) if session.workspace else None
