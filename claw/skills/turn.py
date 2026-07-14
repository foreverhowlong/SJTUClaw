"""Turn-scoped skill selection and the model-facing load_skill tool."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from claw.errors import SkillError
from claw.skills.models import (
    SkillContext,
    SkillOutcome,
    SkillRequest,
    SkillSelection,
    SkillSelectionSource,
    SkillUsage,
)
from claw.skills.registry import SkillCatalog
from claw.tools.registry import ToolDefinition


LOAD_SKILL_TOOL_NAME = "load_skill"


@dataclass
class SkillTurn:
    catalog: SkillCatalog
    task: str
    allow_auto: bool
    selection: SkillSelection | None = None
    _selection_pending_event: bool = False

    def apply_explicit(self, request: SkillRequest) -> SkillSelection:
        if not request.name:
            raise SkillError("Skill name 不能为空。")
        return self._select(
            request.name,
            "explicit",
            "用户显式选择了该 Skill。",
        )

    def context(self) -> SkillContext:
        return SkillContext(self.catalog.summaries, self.selection)

    def tool(self) -> ToolDefinition | None:
        if not self.allow_auto or not self.catalog.summaries:
            return None

        async def load_skill(arguments):
            selection = self._select(
                arguments["name"],
                "auto",
                arguments["reason"],
            )
            return {
                "skill": selection.package.summary.name,
                "source": selection.source,
                "reason": selection.reason,
                "usageId": selection.usage_id,
            }

        return ToolDefinition(
            LOAD_SKILL_TOOL_NAME,
            "Load exactly one available skill into this turn after explaining why it "
            "matches the user's task. Do not call this tool when no skill is useful.",
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [item.name for item in self.catalog.summaries],
                    },
                    "reason": {
                        "type": "string",
                        "description": "A concise user-facing reason for using this skill.",
                    },
                },
                "required": ["name", "reason"],
                "additionalProperties": False,
            },
            load_skill,
            safety_level="context_extension",
            requires_approval=True,
        )

    def consume_selection_event(self) -> dict[str, str] | None:
        if self.selection is None or not self._selection_pending_event:
            return None
        self._selection_pending_event = False
        return {
            "usageId": self.selection.usage_id,
            "name": self.selection.package.summary.name,
            "description": self.selection.package.summary.description,
            "source": self.selection.source,
            "reason": self.selection.reason,
        }

    def usage(
        self,
        session_id: str,
        outcome: SkillOutcome,
        final_output: str,
    ) -> SkillUsage | None:
        if self.selection is None:
            return None
        return SkillUsage(
            usage_id=self.selection.usage_id,
            turn_id="",
            skill_name=self.selection.package.summary.name,
            session_id=session_id,
            task=self.task,
            source=self.selection.source,
            reason=self.selection.reason,
            used_at=self.selection.selected_at,
            outcome=outcome,
            final_output=final_output,
        )

    def _select(
        self,
        name: str,
        source: SkillSelectionSource,
        reason: str,
    ) -> SkillSelection:
        if self.selection is not None:
            raise SkillError(
                f"本轮已经选择 Skill {self.selection.package.summary.name}，不能再次选择。"
            )
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise SkillError("选择 Skill 的 reason 不能为空。")
        package = self.catalog.get(name)
        selection = SkillSelection(
            usage_id=f"usage_{uuid4().hex}",
            package=package,
            source=source,
            reason=normalized_reason,
            selected_at=datetime.now(timezone.utc),
        )
        self.selection = selection
        self._selection_pending_event = True
        return selection
