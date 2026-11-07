"""Which failures stop the whole tick, and how any failure reads in the audit and the trace."""

from aeca.application.decide import BudgetExhausted
from aeca.application.ports import ProviderError
from aeca.domain.errors import AecaError, SystemWideError


def ends_the_tick(error: Exception) -> bool:
    """A failure every email would share, so no email is charged for it."""
    if isinstance(error, ProviderError):
        return error.system_wide
    return isinstance(error, SystemWideError | BudgetExhausted)


def describe(error: Exception) -> str:
    if isinstance(error, AecaError):
        return str(error)
    return f"{type(error).__name__}: {error}"
