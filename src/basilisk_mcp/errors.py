"""Structured errors returned to MCP clients."""

from __future__ import annotations

from typing import Any


class BskMcpError(Exception):
    """An error with a stable machine-readable code and an optional fix hint.

    Messages are written for the model: they say what was wrong and, where
    possible, what a valid call looks like, so the client can self-correct.
    """

    def __init__(self, code: str, message: str, hint: str | None = None, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" (hint: {self.hint})" if self.hint else "")


class ValidationError(BskMcpError):
    """Raised when a session spec or tool argument is invalid."""

    def __init__(self, message: str, hint: str | None = None, **details: Any):
        super().__init__("INVALID_ARGUMENT", message, hint, **details)


class SpecInvalid(BskMcpError):
    """Raised when a complete session spec fails validation; carries all problems."""

    def __init__(self, problems: list[str]):
        super().__init__(
            "SPEC_INVALID",
            f"The session has {len(problems)} problem(s): " + " | ".join(problems),
            "Fix the listed problems (validate_session lists them) and try again.",
            problems=problems,
        )
        self.problems = problems


class NotFound(BskMcpError):
    def __init__(self, what: str, name: str, available: list[str] | None = None):
        hint = f"Available: {', '.join(available)}" if available else None
        super().__init__("NOT_FOUND", f"{what} '{name}' does not exist.", hint)


class LimitExceeded(BskMcpError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__("LIMIT_EXCEEDED", message, hint)


class Conflict(BskMcpError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__("CONFLICT", message, hint)


class RunFailed(BskMcpError):
    def __init__(self, message: str, hint: str | None = None, **details: Any):
        super().__init__("RUN_FAILED", message, hint, **details)
