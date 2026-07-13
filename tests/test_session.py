from claw.session import Session


def test_new_session_has_empty_history() -> None:
    assert Session().messages == []


def test_session_preserves_message_order() -> None:
    session = Session()

    session.append("user", "hello")
    session.append("assistant", "hi")

    assert session.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_messages_returns_a_defensive_copy() -> None:
    session = Session()
    session.append("user", "original")

    messages = session.messages
    messages[0]["content"] = "changed"
    messages.append({"role": "assistant", "content": "extra"})

    assert session.messages == [{"role": "user", "content": "original"}]
