"""Project-level exceptions with user-facing messages."""


class ClawError(Exception):
    """Base class for expected runtime errors."""


class ConfigError(ClawError):
    """Raised when required configuration is missing or invalid."""


class LLMError(ClawError):
    """Raised when the LLM provider request or response fails."""

