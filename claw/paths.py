"""Stable runtime paths independent of the process working directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    env_file: Path
    sessions_dir: Path
    memory_dir: Path
    tasks_dir: Path
    approvals_dir: Path
    downloads_dir: Path
    executions_dir: Path
    logs_dir: Path
    skills_dir: Path | None = None
    system_prompt_file: Path | None = None
    soul_file: Path | None = None

    @classmethod
    def from_environment(cls) -> RuntimePaths:
        home = _runtime_home()
        return cls(
            home=home,
            env_file=home / ".env",
            sessions_dir=home / "data" / "sessions",
            memory_dir=home / "data" / "memory",
            tasks_dir=home / "data" / "tasks",
            approvals_dir=home / "data" / "approvals",
            downloads_dir=home / "data" / "downloads",
            executions_dir=home / "data" / "executions",
            logs_dir=home / "logs",
            skills_dir=home / "skills",
            system_prompt_file=_optional_path("CLAW_SYSTEM_PROMPT"),
            soul_file=_optional_path("CLAW_SOUL"),
        )


def _runtime_home() -> Path:
    configured = os.environ.get("CLAW_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    source_root = Path(__file__).resolve().parent.parent
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return (Path.home() / ".sjtuclaw").resolve()


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None
