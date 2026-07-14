"""Persistent, session-scoped shell processes bounded by workspace cwd."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from claw.errors import ShellError
from claw.workspace import Workspace


MAX_SHELL_OUTPUT_CHARS = 64 * 1024


@dataclass
class ManagedShell:
    process: asyncio.subprocess.Process
    workspace: Workspace
    cwd: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ShellManager:
    def __init__(self, *, timeout_seconds: float = 60) -> None:
        if timeout_seconds <= 0:
            raise ValueError("shell timeout_seconds 必须大于 0。")
        self.timeout_seconds = timeout_seconds
        self._shells: dict[str, ManagedShell] = {}

    async def new_shell(
        self,
        session_id: str,
        workspace: Workspace,
        cwd: Path,
    ) -> dict[str, object]:
        await self.close(session_id)
        shell = os.environ.get("SHELL", "/bin/sh")
        try:
            process = await asyncio.create_subprocess_exec(
                shell,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except OSError as exc:
            raise ShellError(f"启动 shell 失败: {exc}") from exc
        self._shells[session_id] = ManagedShell(process, workspace, cwd)
        return {
            "success": True,
            "tool": "new_shell",
            "cwd": str(cwd),
            "message": "shell started",
        }

    async def run_command(
        self,
        session_id: str,
        workspace: Workspace,
        command: str,
    ) -> dict[str, object]:
        shell = self._shells.get(session_id)
        if shell is None or shell.process.returncode is not None:
            self._shells.pop(session_id, None)
            raise ShellError("当前 session 没有可用 shell，请先调用 new_shell。")
        if shell.workspace.root != workspace.root:
            await self.close(session_id)
            raise ShellError("session workspace 已改变，旧 shell 已终止，请调用 new_shell。")
        if not shell.cwd.is_relative_to(shell.workspace.root):
            await self.close(session_id)
            raise ShellError("shell 当前目录已离开 workspace，已终止。")

        async with shell.lock:
            marker = f"__CLAW_{uuid4().hex}__"
            script = (
                f"{command}\n"
                "__claw_status=$?\n"
                "__claw_cwd=$(pwd -P)\n"
                f"command printf '\\n{marker}:%s:%s\\n' \"$__claw_status\" "
                '"$__claw_cwd"\n'
                f"command printf '\\n{marker}\\n' >&2\n"
            )
            assert shell.process.stdin is not None
            shell.process.stdin.write(script.encode("utf-8"))
            await shell.process.stdin.drain()
            try:
                stdout_task = asyncio.create_task(
                    _read_until_stdout(shell.process.stdout, marker)
                )
                stderr_task = asyncio.create_task(
                    _read_until_stderr(shell.process.stderr, marker)
                )
                (stdout, status, cwd, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                await self.close(session_id)
                return {
                    "success": False,
                    "tool": "run_command",
                    "command": command,
                    "cwd": str(shell.cwd),
                    "exitCode": None,
                    "stdout": "",
                    "stderr": "",
                    "timedOut": True,
                    "truncated": False,
                    "error": "命令执行超时，shell 已终止。",
                }
            except (EOFError, UnicodeError, ValueError) as exc:
                await self.close(session_id)
                raise ShellError(f"shell 协议中断，已终止: {exc}") from exc

            resolved_cwd = Path(cwd).resolve()
            shell.cwd = resolved_cwd
            escaped = not resolved_cwd.is_relative_to(shell.workspace.root)
            if escaped:
                await self.close(session_id)
            truncated = stdout_truncated or stderr_truncated
            return {
                "success": status == 0 and not escaped,
                "tool": "run_command",
                "command": command,
                "cwd": str(resolved_cwd),
                "exitCode": status,
                "stdout": stdout,
                "stderr": stderr,
                "timedOut": False,
                "truncated": truncated,
                "error": (
                    "shell 离开 workspace，已终止。"
                    if escaped
                    else (f"命令退出码为 {status}。" if status != 0 else "")
                ),
            }

    async def close(self, session_id: str) -> None:
        shell = self._shells.pop(session_id, None)
        if shell is None or shell.process.returncode is not None:
            return
        shell.process.terminate()
        try:
            await asyncio.wait_for(shell.process.wait(), timeout=2)
        except TimeoutError:
            shell.process.kill()
            await shell.process.wait()

    async def close_all(self) -> None:
        await asyncio.gather(*(self.close(item) for item in tuple(self._shells)))


async def _read_until_stdout(
    stream: asyncio.StreamReader | None,
    marker: str,
) -> tuple[str, int, str, bool]:
    if stream is None:
        raise EOFError("stdout unavailable")
    chunks: list[str] = []
    characters = 0
    truncated = False
    while True:
        raw = await stream.readline()
        if not raw:
            raise EOFError("shell stdout closed")
        line = raw.decode("utf-8")
        stripped = line.rstrip("\r\n")
        if stripped.startswith(f"{marker}:"):
            _, status, cwd = stripped.split(":", 2)
            return "".join(chunks).rstrip("\n"), int(status), cwd, truncated
        remaining = MAX_SHELL_OUTPUT_CHARS - characters
        if remaining > 0:
            visible = line[:remaining]
            chunks.append(visible)
            characters += len(visible)
        if len(line) > remaining:
            truncated = True


async def _read_until_stderr(
    stream: asyncio.StreamReader | None,
    marker: str,
) -> tuple[str, bool]:
    if stream is None:
        raise EOFError("stderr unavailable")
    chunks: list[str] = []
    characters = 0
    truncated = False
    while True:
        raw = await stream.readline()
        if not raw:
            raise EOFError("shell stderr closed")
        line = raw.decode("utf-8")
        if line.rstrip("\r\n") == marker:
            return "".join(chunks).rstrip("\n"), truncated
        remaining = MAX_SHELL_OUTPUT_CHARS - characters
        if remaining > 0:
            visible = line[:remaining]
            chunks.append(visible)
            characters += len(visible)
        if len(line) > remaining:
            truncated = True
