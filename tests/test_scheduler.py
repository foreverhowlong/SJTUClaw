import asyncio
from datetime import datetime, timedelta, timezone

from claw.agent import AgentService
from claw.context import ContextBuilder
from claw.events import AgentEvent
from claw.llm import LLMCompletion, LLMStreamEvent
from claw.scheduler import Scheduler
from claw.store.memory import MemoryStore
from claw.store.sessions import SessionStore
from claw.store.tasks import TaskStore
from claw.tasks import IntervalSchedule, OnceSchedule
from claw.tools import ToolRegistry


START = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str | None]] = []

    async def run_turn(self, session_id: str, user_input: str, *, source=None):
        self.calls.append((session_id, user_input, source))
        yield AgentEvent("turn_start", session_id)
        if self.fail:
            yield AgentEvent(
                "error",
                session_id,
                {"code": "llm_error", "message": "LLM unavailable"},
            )
            yield AgentEvent("turn_end", session_id, {"status": "failed"})
        else:
            yield AgentEvent("llm_message", session_id, {"content": "finished"})
            yield AgentEvent("turn_end", session_id, {"status": "completed"})


def test_scheduler_executes_once_and_records_agent_result(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = TaskStore(tmp_path / "tasks")
    clock = Clock(START)
    agent = FakeAgent()
    scheduler = Scheduler(store, sessions, agent, now=clock)
    task = scheduler.create_task(
        session.session_id,
        "prepare report",
        OnceSchedule(START + timedelta(minutes=1)),
    )
    clock.value = START + timedelta(minutes=1)

    asyncio.run(scheduler.run_due(now=clock.value))

    finished = store.get(task.task_id)
    assert agent.calls == [
        (session.session_id, "prepare report", "scheduled_task")
    ]
    assert finished.status == "completed"
    assert finished.history[0].assistant_reply == "finished"


def test_failed_periodic_task_is_scheduled_again(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = TaskStore(tmp_path / "tasks")
    clock = Clock(START)
    scheduler = Scheduler(store, sessions, FakeAgent(fail=True), now=clock)
    task = scheduler.create_task(
        session.session_id,
        "check",
        IntervalSchedule(START + timedelta(minutes=1), 60),
    )
    clock.value = START + timedelta(minutes=1)

    asyncio.run(scheduler.run_due(now=clock.value))

    failed = store.get(task.task_id)
    assert failed.status == "failed"
    assert failed.next_run_at == START + timedelta(minutes=2)
    assert failed.history[0].error_code == "llm_error"


class FinalLLM:
    async def stream_chat(self, messages, tools=()):
        del messages, tools
        yield LLMStreamEvent("completed", completion=LLMCompletion("scheduled reply"))


def test_scheduler_uses_real_agent_service_and_writes_session_history(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    agent = AgentService(
        FinalLLM(),
        sessions,
        ContextBuilder("system", "soul"),
        MemoryStore(tmp_path / "memory"),
        tool_registry=ToolRegistry(),
    )
    store = TaskStore(tmp_path / "tasks")
    clock = Clock(START)
    scheduler = Scheduler(store, sessions, agent, now=clock)
    scheduler.create_task(
        session.session_id,
        "scheduled input",
        OnceSchedule(START + timedelta(seconds=1)),
    )
    clock.value = START + timedelta(seconds=1)

    asyncio.run(scheduler.run_due(now=clock.value))

    assert sessions.load(session.session_id).messages == [
        {
            "role": "user",
            "content": "scheduled input",
            "source": "scheduled_task",
        },
        {"role": "assistant", "content": "scheduled reply"},
    ]


def test_scheduler_notifies_session_listeners_after_success(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create()
    store = TaskStore(tmp_path / "tasks")
    clock = Clock(START)
    scheduler = Scheduler(store, sessions, FakeAgent(), now=clock)
    notified: list[str] = []

    async def listener(session_id: str) -> None:
        notified.append(session_id)

    unsubscribe = scheduler.subscribe_session_updates(listener)
    scheduler.create_task(
        session.session_id,
        "notify",
        OnceSchedule(START + timedelta(seconds=1)),
    )

    asyncio.run(scheduler.run_due(now=START + timedelta(seconds=1)))
    unsubscribe()

    assert notified == [session.session_id]
