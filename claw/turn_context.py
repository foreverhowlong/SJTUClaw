"""Immutable stable inputs captured for the lifetime of one agent turn."""

from __future__ import annotations

from dataclasses import dataclass

from claw.skills.registry import SkillCatalog
from claw.store.attachments import AttachmentMetadata
from claw.store.memory import MemoryRecord


@dataclass(frozen=True)
class TurnContextSnapshot:
    memories: tuple[MemoryRecord, ...]
    attachments: tuple[AttachmentMetadata, ...]
    skills: SkillCatalog | None
