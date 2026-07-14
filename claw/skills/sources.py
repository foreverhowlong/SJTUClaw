"""Filesystem-neutral discovery sources for skill package directories."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal, Protocol

from claw.errors import SkillError


logger = logging.getLogger(__name__)
SkillOrigin = Literal["builtin", "local"]


@dataclass(frozen=True)
class SkillLocation:
    """One unparsed skill directory discovered by a source."""

    root: Traversable
    origin: SkillOrigin


class SkillSource(Protocol):
    """Enumerate skill directories without parsing their contents."""

    def locations(self) -> tuple[SkillLocation, ...]: ...


@dataclass(frozen=True)
class PackageSkillSource:
    package: str = "claw"
    directory: str = "builtin_skills"

    def locations(self) -> tuple[SkillLocation, ...]:
        root = resources.files(self.package).joinpath(self.directory)
        return _locations(root, "builtin")


@dataclass(frozen=True)
class DirectorySkillSource:
    root: Path

    def locations(self) -> tuple[SkillLocation, ...]:
        return _locations(self.root, "local")


def _locations(root: Traversable, origin: SkillOrigin) -> tuple[SkillLocation, ...]:
    try:
        if not root.is_dir():
            return ()
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SkillError(f"扫描 {origin} skills 失败 {root}: {exc}") from exc

    found: list[SkillLocation] = []
    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        if isinstance(child, Path) and child.is_symlink():
            logger.warning("ignoring symlinked %s skill: %s", origin, child)
            continue
        found.append(SkillLocation(child, origin))
    return tuple(found)
