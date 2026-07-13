import asyncio
from io import StringIO

import claw.cli
from claw.cli import run_repl
from claw.compaction import CompactionResult
from claw.events import AgentEvent
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


class FakeAgent:
    def __init__(self, responses=(), compact_responses=()) -> None:
        self.responses = iter(responses)
        self.compact_responses = iter(compact_responses)
        self.calls: list[tuple[str, str]] = []
        self.compact_calls: list[tuple[str, bool]] = []

    async def run_turn(self, session_id: str, user_input: str):
        self.calls.append((session_id, user_input))
        for event in next(self.responses):
            yield event

    async def compact_session(self, session_id: str, *, force: bool = True):
        self.compact_calls.append((session_id, force))
        return next(self.compact_responses)


def turn_events(session_id: str, reply: str):
    return [
        AgentEvent("turn_start", session_id),
        AgentEvent("llm_delta", session_id, {"delta": reply[:2]}),
        AgentEvent("llm_delta", session_id, {"delta": reply[2:]}),
        AgentEvent("llm_message", session_id, {"content": reply}),
        AgentEvent("turn_end", session_id, {"status": "completed"}),
    ]


def input_from(values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def stores(tmp_path):
    return SessionStore(tmp_path / "sessions"), MemoryStore(tmp_path / "memory")


def run(agent, sessions, memories, **kwargs):
    return asyncio.run(run_repl(agent, sessions, memories, **kwargs))


def test_repl_streams_multiple_turns_against_explicit_session(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    current = sessions.create()
    agent = FakeAgent(
        [turn_events(current.session_id, "reply one"), turn_events(current.session_id, "reply two")]
    )
    stdout = StringIO()

    assert run(
        agent,
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from(["first", "second", "/exit"]),
        stdout=stdout,
    ) == 0

    assert agent.calls == [(current.session_id, "first"), (current.session_id, "second")]
    assert "Assistant> reply one\n" in stdout.getvalue()
    assert stdout.getvalue().endswith("bye.\n")


def test_repl_renders_tool_trace_and_error_event(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    session = sessions.create()
    events = [
        AgentEvent("turn_start", session.session_id),
        AgentEvent(
            "tool_call",
            session.session_id,
            {"callId": "1", "name": "list_dir", "arguments": '{"path":"."}'},
        ),
        AgentEvent(
            "tool_result",
            session.session_id,
            {"callId": "1", "name": "list_dir", "ok": True, "result": ["README.md"], "error": ""},
        ),
        AgentEvent(
            "approval_required",
            session.session_id,
            {"callId": "2", "name": "write_file"},
        ),
        AgentEvent(
            "approval_resolved",
            session.session_id,
            {
                "callId": "2",
                "name": "write_file",
                "approved": False,
                "reason": "未配置",
            },
        ),
        AgentEvent(
            "tool_result",
            session.session_id,
            {
                "callId": "2",
                "name": "write_file",
                "ok": False,
                "result": None,
                "error": "未配置",
            },
        ),
        AgentEvent("error", session.session_id, {"message": "stream failed"}),
        AgentEvent("turn_end", session.session_id, {"status": "failed"}),
    ]
    stdout = StringIO()
    stderr = StringIO()

    run(
        FakeAgent([events]),
        sessions,
        memories,
        input_fn=input_from(["inspect", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert "Tool> 查看目录 · . [RUNNING]" in stdout.getvalue()
    assert "Tool> 查看目录 · . [DONE] · 1 项" in stdout.getvalue()
    assert "Tool> 运行工具 write_file [APPROVAL REQUIRED]" in stdout.getvalue()
    assert "Tool> 运行工具 write_file [FAILED] · 未配置" in stdout.getvalue()
    assert "错误: stream failed" in stderr.getvalue()


def test_repl_skips_blank_and_treats_eof_as_exit(tmp_path) -> None:
    sessions, memories = stores(tmp_path)

    def raise_eof(_prompt: str) -> str:
        raise EOFError

    stdout = StringIO()
    assert run(FakeAgent(), sessions, memories, input_fn=raise_eof, stdout=stdout) == 0
    assert stdout.getvalue().endswith("bye.\n")


def test_session_commands_only_change_cli_current_session(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    first = sessions.create()
    agent = FakeAgent([])
    stdout = StringIO()

    run(
        agent,
        sessions,
        memories,
        initial_session_id=first.session_id,
        input_fn=input_from(
            [
                f"/session rename {first.session_id} Course Project",
                "/session new",
                f"/session switch {first.session_id}",
                "/session list",
                "/exit",
            ]
        ),
        stdout=stdout,
    )

    assert agent.calls == []
    assert sessions.load(first.session_id).title == "Course Project"
    assert f"Switched to session: {first.session_id}" in stdout.getvalue()


def test_session_history_reuses_timeline_and_keeps_tool_prelude(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    history = sessions.create("History")
    sessions.commit_turn(
        history.session_id,
        expected_revision=0,
        messages=[
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": "I will inspect README.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "read_file",
                "content": (
                    '{"ok":true,"result":{"path":"README.md",'
                    '"charactersRead":12,"truncated":false}}'
                ),
            },
            {"role": "assistant", "content": "Done."},
        ],
    )
    current = sessions.create("Current")
    stdout = StringIO()

    run(
        FakeAgent([]),
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from([f"/session switch {history.session_id}", "/exit"]),
        stdout=stdout,
    )

    rendered = stdout.getvalue()
    assert "Assistant [working]> I will inspect README." in rendered
    assert "Tool> 读取文件 · README.md [DONE] · 12 字符" in rendered
    assert "Assistant> Done." in rendered


def test_memory_commands_do_not_call_agent(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    agent = FakeAgent()
    stdout = StringIO()

    run(
        agent,
        sessions,
        memories,
        input_fn=input_from(["/memory add 用户偏好中文回答。", "/memory list", "/exit"]),
        stdout=stdout,
    )

    assert agent.calls == []
    assert [item.content for item in memories.list()] == ["用户偏好中文回答。"]


def test_manual_compact_is_awaited_and_printed(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    current = sessions.create()
    result = CompactionResult(
        session_id=current.session_id,
        status="compacted",
        old_message_count=6,
        recent_message_count=2,
        summary="当前任务：实现 compaction。",
    )
    agent = FakeAgent(compact_responses=[result])
    stdout = StringIO()

    run(
        agent,
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from(["/compact", "/exit"]),
        stdout=stdout,
    )

    assert agent.compact_calls == [(current.session_id, True)]
    assert "old_messages=6, recent_messages=2" in stdout.getvalue()
    assert "当前任务：实现 compaction。" in stdout.getvalue()


def test_unavailable_compaction_and_warning_are_rendered(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    current = sessions.create()
    unavailable = CompactionResult(
        current.session_id,
        "unavailable",
        0,
        0,
        detail="runtime 未配置 compactor。",
    )
    warning_events = [
        AgentEvent("turn_start", current.session_id),
        AgentEvent(
            "warning",
            current.session_id,
            {
                "code": "context_still_oversized",
                "message": "上下文压缩未能降到目标预算，将继续本轮。",
                "requestCharacters": 90_000,
            },
        ),
        AgentEvent("turn_end", current.session_id, {"status": "completed"}),
    ]
    agent = FakeAgent([warning_events], [unavailable])
    stderr = StringIO()

    run(
        agent,
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from(["hello", "/compact", "/exit"]),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert "[warning] 上下文压缩未能降到目标预算" in stderr.getvalue()
    assert "compaction unavailable" in stderr.getvalue()


def test_repl_uses_unicode_terminal_reader_by_default(tmp_path, monkeypatch) -> None:
    sessions, memories = stores(tmp_path)
    values = iter(["", "/exit"])
    prompts = []

    def fake_reader(prompt):
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(claw.cli, "_read_terminal_input", fake_reader)

    assert run(FakeAgent(), sessions, memories, stdout=StringIO()) == 0
    assert prompts == ["User> ", "User> "]


def test_terminal_reader_uses_prompt_async_inside_existing_event_loop(monkeypatch) -> None:
    class FakePromptSession:
        def __init__(self) -> None:
            self.prompts = []

        async def prompt_async(self, prompt):
            self.prompts.append(prompt)
            return "hello"

        def prompt(self, _prompt):
            raise AssertionError("synchronous prompt must not be used")

    session = FakePromptSession()
    monkeypatch.setattr(claw.cli, "_prompt_session", session)

    assert asyncio.run(claw.cli._read_terminal_input("User> ")) == "hello"
    assert session.prompts == ["User> "]
