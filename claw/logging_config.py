"""Runtime logging configuration shared by user-facing entry points."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from claw.errors import ConfigError


LOG_FILENAME = "claw.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3


def configure_logging(logs_dir: str | Path) -> Path:
    path = Path(logs_dir) / LOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        raise ConfigError(f"初始化运行日志失败: {exc}") from exc
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    resolved = str(path.resolve())
    for existing in root.handlers:
        if getattr(existing, "baseFilename", None) == resolved:
            handler.close()
            return path
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return path
