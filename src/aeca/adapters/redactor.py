import math
import re
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel

from aeca.domain.secret import SecretStr

REDACTED = "[REDACTED]"


class Redactor:
    """Replaces every secret, by exact match of its full value, in text and in nested values."""

    def __init__(self, secrets: tuple[SecretStr, ...]) -> None:
        self._pattern = _pattern_matching(secrets)

    def text(self, text: str) -> str:
        return self._pattern.sub(REDACTED, text)

    # Walks the values before they become JSON, because JSON escaping would change a secret that
    # holds a quote or a backslash, and the plain text would no longer match.
    def value(self, value: object) -> object:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, BaseModel):
            return self.value(value.model_dump(mode="json"))
        if isinstance(value, Mapping):
            return {self.text(str(key)): self.value(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        return self.text(str(value))


# Built in one expression and kept compiled, so no frame holds a plain secret for --showlocals.
# A key read from a file often ends in a newline that is never part of the key, so each is
# stripped. An empty secret is dropped, because it would match between every character. The
# longest goes first, so a secret that contains another is replaced whole.
def _pattern_matching(secrets: tuple[SecretStr, ...]) -> re.Pattern[str]:
    values = {secret.reveal().strip() for secret in secrets} - {""}
    if not values:
        raise ValueError("a log with nothing to redact is a wiring mistake: pass the secrets")
    return re.compile("|".join(map(re.escape, sorted(values, key=len, reverse=True))))
