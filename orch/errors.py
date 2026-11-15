"""Expected, user-facing failures. Caught once in the CLI."""

from __future__ import annotations


class OrchError(Exception):
    """Base for failures shown as `error:` + `hint:` instead of a traceback."""

    exit_code = 1

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class ConfigError(OrchError):
    """A scenario, template or config file is missing or invalid."""

    exit_code = 2


class StepError(OrchError):
    """A session failed or returned output orch cannot use."""


class GitError(OrchError):
    """A git command failed."""
