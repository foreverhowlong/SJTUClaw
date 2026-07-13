from io import StringIO

import claw.cli
from claw.cli import run_repl
from claw.agent import AgentService
from claw.context import ContextBuilder
from claw.errors import LLMError
from claw.llm import Message
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore


class FakeService:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = iter(responses)
        self.inputs: list[str] = []

    def send_message(self, user_input: str) -> str:
        self.inputs.append(user_input)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def input_from(values: list[str]):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def test_repl_runs_multiple_turns_and_exits() -> None:
    service = FakeService(["reply one", "reply two"])
    stdout = StringIO()

    exit_code = run_repl(
        service,
        input_fn=input_from(["first", "second", "/exit"]),
        stdout=stdout,
    )

    assert exit_code == 0
    assert service.inputs == ["first", "second"]
    assert "Assistant> reply one" in stdout.getvalue()
    assert "Assistant> reply two" in stdout.getvalue()
    assert stdout.getvalue().endswith("bye.\n")


def test_repl_skips_blank_input_and_continues_after_llm_error() -> None:
    service = FakeService([LLMError("unavailable"), "recovered"])
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_repl(
        service,
        input_fn=input_from(["  ", "first", "second", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert service.inputs == ["first", "second"]
    assert "错误: unavailable" in stderr.getvalue()
    assert "Assistant> recovered" in stdout.getvalue()


def test_repl_treats_eof_as_normal_exit() -> None:
    service = FakeService([])
    stdout = StringIO()

    def raise_eof(_prompt: str) -> str:
        raise EOFError

    assert run_repl(service, input_fn=raise_eof, stdout=stdout) == 0
    assert stdout.getvalue().endswith("bye.\n")


def test_repl_handles_keyboard_interrupt() -> None:
    service = FakeService([])
    stderr = StringIO()

    def raise_interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    assert run_repl(service, input_fn=raise_interrupt, stderr=stderr) == 130
    assert "已中断" in stderr.getvalue()


def test_repl_uses_unicode_terminal_reader_by_default(monkeypatch) -> None:
    service = FakeService([])
    values = iter(["", "/exit"])
    prompts: list[str] = []

    def fake_terminal_reader(prompt: str) -> str:
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(claw.cli, "_read_terminal_input", fake_terminal_reader)
    stdout = StringIO()

    assert run_repl(service, stdout=stdout) == 0
    assert prompts == ["User> ", "User> "]


class RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def chat(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        return "reply"


def persistent_service(llm: RecordingLLM, tmp_path) -> AgentService:
    return AgentService(
        llm,
        store=SessionStore(tmp_path / "sessions"),
        context_builder=ContextBuilder("rules", "style"),
        memory_store=MemoryStore(tmp_path / "memory"),
    )


def test_repl_handles_session_commands_without_sending_them_to_llm(tmp_path) -> None:
    llm = RecordingLLM()
    service = persistent_service(llm, tmp_path)
    first_id = service.session.session_id
    stdout = StringIO()

    exit_code = run_repl(
        service,
        input_fn=input_from(
            [
                "/session list",
                f"/session rename {first_id} Course Project",
                "/session new",
                "hello",
                f"/session switch {first_id}",
                "/exit",
            ]
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    assert len(llm.calls) == 1
    assert llm.calls[0][-1] == {"role": "user", "content": "hello"}
    rendered = stdout.getvalue()
    assert "Sessions:" in rendered
    assert "Course Project" in rendered
    assert f"Switched to session: {first_id}" in rendered
    assert "History:\n(empty)" in rendered


def test_deleting_current_session_selects_or_creates_a_replacement(tmp_path) -> None:
    service = persistent_service(RecordingLLM(), tmp_path)
    first_id = service.session.session_id
    second_id = service.create_session().session_id

    service.delete_session(second_id)
    assert service.session.session_id == first_id

    service.delete_session(first_id)
    assert service.session.session_id not in {first_id, second_id}
    assert len(service.list_sessions()) == 1


def test_repl_handles_memory_commands_without_sending_or_persisting_them(tmp_path) -> None:
    llm = RecordingLLM()
    service = persistent_service(llm, tmp_path)
    stdout = StringIO()

    assert run_repl(
        service,
        input_fn=input_from(
            [
                "/memory add 用户偏好中文回答。",
                "/memory list",
                "我偏好什么语言？",
                "/exit",
            ]
        ),
        stdout=stdout,
    ) == 0

    assert len(llm.calls) == 1
    assert "用户偏好中文回答。" in llm.calls[0][0]["content"]
    assert service.session.messages == [
        {"role": "user", "content": "我偏好什么语言？"},
        {"role": "assistant", "content": "reply"},
    ]
    rendered = stdout.getvalue()
    assert "Added memory: mem_" in rendered
    assert "Memories:" in rendered


def test_repl_deletes_memory_without_sending_command_to_llm(tmp_path) -> None:
    llm = RecordingLLM()
    service = persistent_service(llm, tmp_path)
    memory_id = service.add_memory("temporary").memory_id

    assert run_repl(
        service,
        input_fn=input_from([f"/memory delete {memory_id}", "/exit"]),
        stdout=StringIO(),
    ) == 0

    assert llm.calls == []
    assert service.list_memories() == []
