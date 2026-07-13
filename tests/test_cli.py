from io import StringIO

from claw.cli import run_repl
from claw.errors import LLMError


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
