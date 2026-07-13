from io import StringIO

import claw.cli
from claw.agent import TurnResult
from claw.cli import run_repl
from claw.compaction import CompactionResult
from claw.errors import LLMError
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


class FakeAgent:
    def __init__(self, responses, compact_responses=()) -> None:
        self.responses = iter(responses)
        self.compact_responses = iter(compact_responses)
        self.calls: list[tuple[str, str]] = []
        self.compact_calls: list[tuple[str, bool]] = []

    def run_turn(self, session_id: str, user_input: str) -> TurnResult:
        self.calls.append((session_id, user_input))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, TurnResult):
            return response
        return TurnResult(response)

    def compact_session(
        self,
        session_id: str,
        *,
        force: bool = True,
    ) -> CompactionResult:
        self.compact_calls.append((session_id, force))
        return next(self.compact_responses)


def input_from(values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def stores(tmp_path):
    return SessionStore(tmp_path / "sessions"), MemoryStore(tmp_path / "memory")


def test_repl_runs_multiple_turns_against_one_explicit_session(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    current = sessions.create()
    agent = FakeAgent(["reply one", "reply two"])
    stdout = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from(["first", "second", "/exit"]),
        stdout=stdout,
    ) == 0

    assert agent.calls == [
        (current.session_id, "first"),
        (current.session_id, "second"),
    ]
    assert "Assistant> reply one" in stdout.getvalue()
    assert stdout.getvalue().endswith("bye.\n")


def test_repl_skips_blank_and_continues_after_agent_error(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    agent = FakeAgent([LLMError("unavailable"), "recovered"])
    stdout = StringIO()
    stderr = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        input_fn=input_from(["  ", "first", "second", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    ) == 0

    assert [content for _, content in agent.calls] == ["first", "second"]
    assert "错误: unavailable" in stderr.getvalue()
    assert "Assistant> recovered" in stdout.getvalue()


def test_repl_treats_eof_and_interrupt_as_terminal_events(tmp_path) -> None:
    sessions, memories = stores(tmp_path)

    def raise_eof(_prompt: str) -> str:
        raise EOFError

    stdout = StringIO()
    assert run_repl(
        FakeAgent([]), sessions, memories, input_fn=raise_eof, stdout=stdout
    ) == 0
    assert stdout.getvalue().endswith("bye.\n")

    def raise_interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    stderr = StringIO()
    assert run_repl(
        FakeAgent([]), sessions, memories, input_fn=raise_interrupt, stderr=stderr
    ) == 130
    assert "已中断" in stderr.getvalue()


def test_repl_uses_unicode_terminal_reader_by_default(tmp_path, monkeypatch) -> None:
    sessions, memories = stores(tmp_path)
    values = iter(["", "/exit"])
    prompts: list[str] = []

    def fake_terminal_reader(prompt: str) -> str:
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(claw.cli, "_read_terminal_input", fake_terminal_reader)

    assert run_repl(FakeAgent([]), sessions, memories, stdout=StringIO()) == 0
    assert prompts == ["User> ", "User> "]


def test_session_commands_update_only_cli_current_session(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    first = sessions.create()
    agent = FakeAgent(["reply"])
    stdout = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        initial_session_id=first.session_id,
        input_fn=input_from(
            [
                f"/session rename {first.session_id} Course Project",
                "/session new",
                "hello",
                f"/session switch {first.session_id}",
                "/session list",
                "/exit",
            ]
        ),
        stdout=stdout,
    ) == 0

    assert len(agent.calls) == 1
    assert agent.calls[0][0] != first.session_id
    assert agent.calls[0][1] == "hello"
    assert sessions.load(first.session_id).title == "Course Project"
    assert f"Switched to session: {first.session_id}" in stdout.getvalue()


def test_deleting_current_session_selects_replacement_for_next_turn(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    first = sessions.create()
    second = sessions.create()
    agent = FakeAgent(["reply"])

    assert run_repl(
        agent,
        sessions,
        memories,
        initial_session_id=second.session_id,
        input_fn=input_from(
            [f"/session delete {second.session_id}", "hello", "/exit"]
        ),
        stdout=StringIO(),
    ) == 0

    assert agent.calls == [(first.session_id, "hello")]


def test_memory_commands_use_store_without_calling_agent(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    agent = FakeAgent([])
    stdout = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        input_fn=input_from(
            [
                "/memory add 用户偏好中文回答。",
                "/memory list",
                "/exit",
            ]
        ),
        stdout=stdout,
    ) == 0

    assert agent.calls == []
    assert [item.content for item in memories.list()] == ["用户偏好中文回答。"]
    assert "Added memory: mem_" in stdout.getvalue()


def test_unknown_command_is_not_sent_to_agent(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    agent = FakeAgent([])
    stderr = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        input_fn=input_from(["/sesion list", "/exit"]),
        stdout=StringIO(),
        stderr=stderr,
    ) == 0

    assert agent.calls == []
    assert "未知或格式错误" in stderr.getvalue()


def test_manual_compact_is_local_and_prints_summary(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    current = sessions.create()
    result = CompactionResult(
        session_id=current.session_id,
        status="compacted",
        old_message_count=6,
        recent_message_count=2,
        summary="当前任务：实现 compaction。",
    )
    agent = FakeAgent([], [result])
    stdout = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        initial_session_id=current.session_id,
        input_fn=input_from(["/compact", "/exit"]),
        stdout=stdout,
    ) == 0

    assert agent.calls == []
    assert agent.compact_calls == [(current.session_id, True)]
    assert "old_messages=6, recent_messages=2" in stdout.getvalue()
    assert "当前任务：实现 compaction。" in stdout.getvalue()


def test_auto_compaction_failure_is_visible_without_hiding_reply(tmp_path) -> None:
    sessions, memories = stores(tmp_path)
    failed = CompactionResult(
        session_id=sessions.create().session_id,
        status="failed",
        old_message_count=4,
        recent_message_count=2,
        detail="生成 summary 失败，旧消息未删除。",
    )
    agent = FakeAgent([TurnResult("reply", failed)])
    stdout = StringIO()
    stderr = StringIO()

    assert run_repl(
        agent,
        sessions,
        memories,
        input_fn=input_from(["hello", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    ) == 0

    assert "Assistant> reply" in stdout.getvalue()
    assert "旧消息未删除" in stderr.getvalue()
