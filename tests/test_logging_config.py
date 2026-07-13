import logging

from claw.logging_config import configure_logging


def test_configure_logging_persists_full_exception_trace(tmp_path) -> None:
    path = configure_logging(tmp_path / "logs")
    logger = logging.getLogger("claw.test")

    try:
        raise RuntimeError("full internal detail /private/path")
    except RuntimeError:
        logger.exception("agent failed")

    for handler in logging.getLogger().handlers:
        handler.flush()
    content = path.read_text(encoding="utf-8")
    assert "RuntimeError: full internal detail /private/path" in content
    assert "Traceback" in content

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "baseFilename", None) == str(path.resolve()):
            root.removeHandler(handler)
            handler.close()
