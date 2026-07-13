"""Project-level exceptions with user-facing messages."""


class ClawError(Exception):
    """Base class for expected runtime errors."""


class ConfigError(ClawError):
    """Raised when required configuration is missing or invalid."""


class LLMError(ClawError):
    """Raised when the LLM provider request or response fails."""


class ToolError(ClawError):
    """Raised when a tool cannot be registered or executed safely."""


class SessionError(ClawError):
    """Raised when session state cannot be loaded or persisted safely."""


class SessionConflictError(SessionError):
    """Raised when a turn is based on a stale session revision."""


class AttachmentError(ClawError):
    """Raised when a session attachment cannot be stored or read safely."""


class MemoryError(ClawError):
    """Raised when long-term memory cannot be loaded or persisted safely."""


class CommandParseError(ClawError):
    """Raised when a local CLI command is unknown or malformed."""


class TaskError(ClawError):
    """Raised when a scheduled task is invalid or cannot be persisted."""


class TaskNotFoundError(TaskError):
    """Raised when a scheduled task does not exist."""


class TaskConflictError(TaskError):
    """Raised when a scheduled task transition is no longer valid."""
