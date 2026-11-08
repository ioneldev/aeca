"""Stands in for the process being killed."""


class Killed(BaseException):
    """A BaseException, so no `except Exception` in AECA sees it: like a kill, it skips every
    recovery step and leaves the state exactly as the last completed write left it."""
