"""Provider-independent types for skill discovery, selection, and usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


SkillSelectionSource = Literal["explicit", "auto"]
SkillOutcome = Literal["completed", "failed", "interrupted"]


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    origin: Literal["builtin", "local"]

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class SkillResource:
    path: str
    content: str | None


@dataclass(frozen=True)
class SkillPackage:
    summary: SkillSummary
    instructions: str
    resources: tuple[SkillResource, ...] = ()


@dataclass(frozen=True)
class SkillRequest:
    name: str

    @classmethod
    def explicit(cls, name: str) -> SkillRequest:
        return cls(name.strip())


@dataclass(frozen=True)
class SkillSelection:
    usage_id: str
    package: SkillPackage
    source: SkillSelectionSource
    reason: str
    selected_at: datetime


@dataclass(frozen=True)
class SkillContext:
    available: tuple[SkillSummary, ...]
    selected: SkillSelection | None = None


@dataclass(frozen=True)
class SkillUsage:
    usage_id: str
    turn_id: str
    skill_name: str
    session_id: str
    task: str
    source: SkillSelectionSource
    reason: str
    used_at: datetime
    outcome: SkillOutcome
    final_output: str

    def to_dict(self) -> dict[str, str]:
        return {
            "usageId": self.usage_id,
            "turnId": self.turn_id,
            "skillName": self.skill_name,
            "sessionId": self.session_id,
            "task": self.task,
            "source": self.source,
            "reason": self.reason,
            "usedAt": self.used_at.isoformat(),
            "outcome": self.outcome,
            "finalOutput": self.final_output,
        }
