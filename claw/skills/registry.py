"""Discover immutable skill catalogs from packaged and local sources."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

import yaml

from claw.errors import SkillError
from claw.skills.models import SkillPackage, SkillResource, SkillSummary
from claw.skills.sources import (
    DirectorySkillSource,
    PackageSkillSource,
    SkillLocation,
    SkillSource,
)


logger = logging.getLogger(__name__)
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MAX_SKILL_FILE_BYTES = 64 * 1024
MAX_SKILL_RESOURCE_FILES = 32
MAX_SKILL_PACKAGE_BYTES = 256 * 1024


@dataclass(frozen=True)
class SkillCatalog:
    """One immutable view used for the complete lifetime of an agent turn."""

    _packages: Mapping[str, SkillPackage]

    @property
    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(
            self._packages[name].summary for name in sorted(self._packages)
        )

    def get(self, name: str) -> SkillPackage:
        normalized = name.strip()
        package = self._packages.get(normalized)
        if package is None:
            raise SkillError(f"Skill 不存在: {normalized or name!r}。")
        return package


class SkillRegistry:
    """Parse and validate skills without selecting or executing them."""

    def __init__(
        self,
        local_root: str | Path | None = None,
        *,
        sources: Sequence[SkillSource] | None = None,
    ) -> None:
        self.local_root = Path(local_root) if local_root is not None else None
        if sources is not None and local_root is not None:
            raise ValueError("local_root 与 sources 不能同时提供。")
        self._sources: tuple[SkillSource, ...] = (
            tuple(sources) if sources is not None else self._default_sources()
        )

    def snapshot(self) -> SkillCatalog:
        packages: dict[str, SkillPackage] = {}
        for location in self._locations():
            try:
                package = _load_package(location)
            except SkillError as exc:
                logger.warning("ignoring invalid %s skill: %s", location.origin, exc)
                continue
            existing = packages.get(package.summary.name)
            if (
                existing is not None
                and existing.summary.origin == package.summary.origin
            ):
                raise SkillError(
                    f"重复的 {package.summary.origin} Skill: "
                    f"{package.summary.name}。"
                )
            # A valid local package wins regardless of source ordering.
            if existing is None or package.summary.origin == "local":
                packages[package.summary.name] = package
        return SkillCatalog(MappingProxyType(packages))

    def list(self) -> tuple[SkillSummary, ...]:
        return self.snapshot().summaries

    def get(self, name: str) -> SkillPackage:
        return self.snapshot().get(name)

    def _default_sources(self) -> tuple[SkillSource, ...]:
        sources: list[SkillSource] = [PackageSkillSource()]
        if self.local_root is not None:
            sources.append(DirectorySkillSource(self.local_root))
        return tuple(sources)

    def _locations(self) -> tuple[SkillLocation, ...]:
        return tuple(
            location
            for source in self._sources
            for location in source.locations()
        )


def _load_package(location: SkillLocation) -> SkillPackage:
    manifest = location.root.joinpath("SKILL.md")
    if not manifest.is_file():
        raise SkillError(f"{location.root} 缺少 SKILL.md。")
    raw = _read_limited(manifest, "SKILL.md")
    name, description, instructions = _parse_manifest(raw, location.root.name)
    summary = SkillSummary(name, description, location.origin)

    total_bytes = len(raw.encode("utf-8"))
    loaded_resources: list[SkillResource] = []
    for item, relative_path in _walk(location.root):
        if relative_path == "SKILL.md":
            continue
        if len(loaded_resources) >= MAX_SKILL_RESOURCE_FILES:
            raise SkillError(
                f"Skill {name} 资源超过 {MAX_SKILL_RESOURCE_FILES} 个。"
            )
        data = _read_bytes(item, relative_path)
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise SkillError(f"Skill {name} 资源过大: {relative_path}。")
        total_bytes += len(data)
        if total_bytes > MAX_SKILL_PACKAGE_BYTES:
            raise SkillError(f"Skill {name} 总内容超过 256 KiB。")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = None
        loaded_resources.append(SkillResource(relative_path, content))
    return SkillPackage(summary, instructions, tuple(loaded_resources))


def _parse_manifest(raw: str, directory_name: str) -> tuple[str, str, str]:
    if not raw.startswith("---\n"):
        raise SkillError(f"Skill {directory_name} 的 SKILL.md 缺少 YAML frontmatter。")
    marker = raw.find("\n---\n", 4)
    if marker < 0:
        raise SkillError(f"Skill {directory_name} 的 YAML frontmatter 未闭合。")
    try:
        metadata = yaml.safe_load(raw[4:marker])
    except yaml.YAMLError as exc:
        raise SkillError(f"Skill {directory_name} frontmatter 无效: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillError(f"Skill {directory_name} frontmatter 必须是 object。")
    name = metadata.get("name")
    description = metadata.get("description")
    instructions = raw[marker + 5 :].strip()
    if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillError(f"Skill {directory_name} name 无效。")
    if name != directory_name:
        raise SkillError(f"Skill name {name!r} 与目录 {directory_name!r} 不一致。")
    if not isinstance(description, str) or not description.strip():
        raise SkillError(f"Skill {name} description 不能为空。")
    if not instructions:
        raise SkillError(f"Skill {name} instructions 不能为空。")
    return name, description.strip(), instructions


def _walk(root: Traversable) -> list[tuple[Traversable, str]]:
    found: list[tuple[Traversable, str]] = []

    def visit(directory: Traversable, prefix: str) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise SkillError(f"读取 Skill 目录失败 {directory}: {exc}") from exc
        for child in children:
            if child.name.startswith("."):
                continue
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if isinstance(child, Path) and child.is_symlink():
                raise SkillError(f"Skill 资源不能是 symlink: {relative}。")
            if child.is_dir():
                visit(child, relative)
            elif child.is_file():
                found.append((child, relative))

    visit(root, "")
    return found


def _read_limited(item: Traversable, label: str) -> str:
    data = _read_bytes(item, label)
    if len(data) > MAX_SKILL_FILE_BYTES:
        raise SkillError(f"Skill 文件过大: {label}。")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"Skill 文件不是 UTF-8: {label}。") from exc


def _read_bytes(item: Traversable, label: str) -> bytes:
    try:
        return item.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise SkillError(f"读取 Skill 文件失败 {label}: {exc}") from exc
