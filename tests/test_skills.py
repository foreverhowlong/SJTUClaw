import asyncio
from datetime import datetime, timezone

import pytest

from claw.agent import AgentService
from claw.approval import ApprovalDecision
from claw.context import ContextBuilder
from claw.llm import LLMCompletion, LLMStreamEvent
from claw.errors import SkillError
from claw.skills import (
    SkillContext,
    SkillLocation,
    SkillRegistry,
    SkillRequest,
    SkillUsage,
)
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.tools import ToolCall, ToolRegistry


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def stream_chat(self, messages, tools=()):
        self.calls.append((messages, list(tools)))
        for event in next(self.responses):
            yield event


def final(text):
    return [LLMStreamEvent("completed", completion=LLMCompletion(text))]


def tool_call(name, arguments):
    return [
        LLMStreamEvent(
            "completed",
            completion=LLMCompletion("", (ToolCall("call_skill", name, arguments),)),
        )
    ]


class ApproveAll:
    def __init__(self):
        self.recorded = []

    def create(self, session_id, prepared, workspace):
        from claw.store.approvals import ApprovalRequest

        now = datetime.now(timezone.utc)
        return ApprovalRequest(
            "approval_skill",
            session_id,
            prepared.call.call_id,
            prepared.call.name,
            prepared.arguments,
            workspace,
            "pending",
            "",
            now,
            now,
        )

    async def wait(self, approval_id):
        return ApprovalDecision(True, "用户允许使用该 Skill。", approval_id)

    def record_execution_started(self, approval_id):
        self.recorded.append((approval_id, "started"))

    def record_execution_result(self, approval_id, result):
        self.recorded.append((approval_id, result.ok))


def make_agent(tmp_path, responses, *, approval=None):
    sessions = SessionStore(tmp_path / "sessions")
    llm = FakeLLM(responses)
    agent = AgentService(
        llm,
        sessions,
        ContextBuilder("rules", "style"),
        MemoryStore(tmp_path / "memory"),
        tool_registry=ToolRegistry(),
        approval_policy=approval,
        skill_registry=SkillRegistry(tmp_path / "skills"),
    )
    return agent, llm, sessions


def collect(agent, session_id, task, **kwargs):
    async def run():
        return [
            event
            async for event in agent.run_turn(session_id, task, **kwargs)
        ]

    return asyncio.run(run())


def test_registry_merges_builtin_and_local_override_without_index_leak(tmp_path) -> None:
    root = tmp_path / "skills" / "course-report"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: course-report\ndescription: Local report method.\n---\n"
        "LOCAL SECRET INSTRUCTIONS\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(tmp_path / "skills")
    catalog = registry.snapshot()

    assert [item.name for item in catalog.summaries] == [
        "course-report",
        "material-summary",
        "presentation-outline",
    ]
    selected = catalog.get("course-report")
    assert selected.summary.origin == "local"
    assert selected.instructions == "LOCAL SECRET INSTRUCTIONS"

    context = ContextBuilder("rules", "style").build(
        [], skills=SkillContext(catalog.summaries)
    )[0]["content"]
    assert "Local report method." in context
    assert "LOCAL SECRET INSTRUCTIONS" not in context


def test_registry_ignores_invalid_oversized_and_symlinked_local_skills(tmp_path) -> None:
    local = tmp_path / "skills"
    invalid = local / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    oversized = local / "oversized"
    oversized.mkdir()
    (oversized / "SKILL.md").write_text(
        "---\nname: oversized\ndescription: too large\n---\n" + "x" * (64 * 1024),
        encoding="utf-8",
    )
    target = local / "target"
    target.mkdir()
    (target / "SKILL.md").write_text(
        "---\nname: target\ndescription: target\n---\nbody",
        encoding="utf-8",
    )
    (local / "linked").symlink_to(target, target_is_directory=True)

    names = {item.name for item in SkillRegistry(local).list()}

    assert "invalid" not in names
    assert "oversized" not in names
    assert "linked" not in names
    assert "target" in names


def test_registry_rejects_duplicate_names_from_the_same_source(tmp_path) -> None:
    root = tmp_path / "duplicate"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: duplicate\ndescription: duplicate\n---\nbody",
        encoding="utf-8",
    )

    class DuplicateSource:
        def locations(self):
            location = SkillLocation(root, "local")
            return (location, location)

    with pytest.raises(SkillError, match="重复的 local Skill"):
        SkillRegistry(sources=(DuplicateSource(),)).snapshot()


def test_explicit_skill_is_injected_and_usage_commits_with_turn(tmp_path) -> None:
    agent, llm, sessions = make_agent(tmp_path, [final("报告草稿已生成。")])
    session = sessions.create()

    events = collect(
        agent,
        session.session_id,
        "写一份课程报告",
        skill_request=SkillRequest.explicit("course-report"),
    )

    assert [event.type for event in events] == [
        "turn_start",
        "skill_selected",
        "llm_message",
        "turn_end",
    ]
    assert "[Selected Skill]" in llm.calls[0][0][0]["content"]
    assert "课程报告模板" in llm.calls[0][0][0]["content"]
    restored = sessions.load(session.session_id)
    assert len(restored.skill_usages) == 1
    usage = restored.skill_usages[0]
    assert usage.skill_name == "course-report"
    assert usage.source == "explicit"
    assert usage.final_output == "报告草稿已生成。"


def test_selected_skill_context_does_not_carry_into_next_turn(tmp_path) -> None:
    agent, llm, sessions = make_agent(
        tmp_path,
        [final("first"), final("second")],
    )
    session = sessions.create()

    collect(
        agent,
        session.session_id,
        "first task",
        skill_request=SkillRequest.explicit("course-report"),
    )
    collect(agent, session.session_id, "second task")

    assert "[Selected Skill]" in llm.calls[0][0][0]["content"]
    assert "[Selected Skill]" not in llm.calls[1][0][0]["content"]
    assert "[Available Skills]" in llm.calls[1][0][0]["content"]


def test_missing_explicit_skill_fails_without_committing_user_message(tmp_path) -> None:
    agent, llm, sessions = make_agent(tmp_path, [])
    session = sessions.create()

    events = collect(
        agent,
        session.session_id,
        "task",
        skill_request=SkillRequest.explicit("missing-skill"),
    )

    assert [event.type for event in events] == ["turn_start", "error", "turn_end"]
    assert events[1].payload["code"] == "skill_error"
    assert events[-1].payload["status"] == "failed"
    assert sessions.load(session.session_id).messages == []
    assert llm.calls == []


def test_auto_skill_uses_existing_approval_before_selection(tmp_path) -> None:
    approval = ApproveAll()
    agent, llm, sessions = make_agent(
        tmp_path,
        [
            tool_call(
                "load_skill",
                '{"name":"material-summary","reason":"需要跨材料汇总。"}',
            ),
            final("汇总完成。"),
        ],
        approval=approval,
    )
    session = sessions.create()

    events = collect(agent, session.session_id, "总结这些材料")
    types = [event.type for event in events]

    assert types.index("approval_required") < types.index("tool_result")
    assert types.index("tool_result") < types.index("skill_selected")
    assert "[Selected Skill]" not in llm.calls[0][0][0]["content"]
    assert "[Selected Skill]" in llm.calls[1][0][0]["content"]
    assert sessions.load(session.session_id).skill_usages[0].source == "auto"
    assert approval.recorded == [
        ("approval_skill", "started"),
        ("approval_skill", True),
    ]


def test_denied_auto_selection_continues_without_skill_or_usage(tmp_path) -> None:
    agent, llm, sessions = make_agent(
        tmp_path,
        [
            tool_call(
                "load_skill",
                '{"name":"course-report","reason":"需要报告方法。"}',
            ),
            final("未使用 Skill 继续回答。"),
        ],
    )
    session = sessions.create()

    events = collect(agent, session.session_id, "写报告")

    assert not any(event.type == "skill_selected" for event in events)
    assert "用户拒绝执行" in llm.calls[1][0][-1]["content"]
    assert "[Selected Skill]" not in llm.calls[1][0][0]["content"]
    assert sessions.load(session.session_id).skill_usages == ()


def test_second_skill_selection_fails_and_scheduled_turn_has_no_auto_tool(tmp_path) -> None:
    approval = ApproveAll()
    agent, llm, sessions = make_agent(
        tmp_path,
        [
            tool_call(
                "load_skill",
                '{"name":"material-summary","reason":"先汇总材料。"}',
            ),
            tool_call(
                "load_skill",
                '{"name":"presentation-outline","reason":"再生成展示。"}',
            ),
            final("done"),
            final("scheduled"),
        ],
        approval=approval,
    )
    session = sessions.create()

    events = collect(agent, session.session_id, "complex task")
    scheduled = collect(
        agent,
        session.session_id,
        "scheduled task",
        source="scheduled_task",
    )

    results = [event for event in events if event.type == "tool_result"]
    assert results[0].payload["ok"] is True
    assert results[1].payload["ok"] is False
    assert "已经选择" in results[1].payload["error"]
    assert len(sessions.load(session.session_id).skill_usages) == 1
    scheduled_call = llm.calls[-1]
    assert "load_skill" not in {
        item["function"]["name"] for item in scheduled_call[1]
    }
    assert scheduled[-1].type == "turn_end"


def test_skill_usage_survives_compaction_replay(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create()
    first_messages = [
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "first output"},
    ]
    usage = SkillUsage(
        "usage_123",
        "",
        "course-report",
        session.session_id,
        "first task",
        "explicit",
        "用户显式选择了该 Skill。",
        datetime.now(timezone.utc),
        "completed",
        "first output",
    )
    first = store.commit_turn(
        session.session_id,
        expected_revision=0,
        messages=first_messages,
        skill_usage=usage,
    )
    recent = [
        {"role": "user", "content": "recent task"},
        {"role": "assistant", "content": "recent output"},
    ]
    second = store.commit_turn(
        session.session_id,
        expected_revision=first.revision,
        messages=recent,
    )
    store.commit_compaction(
        session.session_id,
        expected_revision=second.revision,
        summary="first task used a course report method",
        recent_messages=recent,
    )

    restored = SessionStore(store.root).load(session.session_id)
    assert restored.messages == recent
    assert [item.skill_name for item in restored.skill_usages] == ["course-report"]
