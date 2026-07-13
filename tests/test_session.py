from claw.session import Session


def test_new_session_is_an_empty_revision_zero_snapshot() -> None:
    session = Session()

    assert session.messages == []
    assert session.revision == 0
    assert session.summary == ""


def test_snapshot_preserves_message_order_and_returns_defensive_copies() -> None:
    session = Session(
        revision=1,
        summary="  current task  ",
        _messages=(
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ),
    )

    messages = session.messages
    messages[0]["content"] = "changed"
    messages.append({"role": "assistant", "content": "extra"})

    assert session.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert session.message_count == 2
    assert session.summary == "current task"


def test_session_snapshot_has_no_mutating_conversation_methods() -> None:
    session = Session()

    assert not hasattr(session, "append")
    assert not hasattr(session, "restore")
    assert not hasattr(session, "discard_last_user_message")
