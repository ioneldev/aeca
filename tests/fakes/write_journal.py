"""One ordered record of every write the Google fakes take and every line a command prints, so a
test can tell what happened before what. Reads are never recorded: only a write changes the
account."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

STDOUT = "stdout"
PRINTED = "line"

# (api, method, target): ("gmail", "messages.trash", "r-int"), or ("stdout", "line", the text).
JournalEntry = tuple[str, str, str]


@dataclass
class WriteJournal:
    entries: list[JournalEntry] = field(default_factory=list)

    def write(self, api: str, method: str, target: str) -> None:
        self.entries.append((api, method, target))

    def say(self, line: str) -> None:
        self.entries.append((STDOUT, PRINTED, line))

    def mutations(self) -> list[JournalEntry]:
        return [entry for entry in self.entries if entry[0] != STDOUT]

    def mutations_of(self, api: str) -> list[JournalEntry]:
        return [entry for entry in self.mutations() if entry[0] == api]

    def stdout(self) -> str:
        return "\n".join(entry[2] for entry in self.entries if entry[0] == STDOUT)

    def clear(self) -> None:
        self.entries.clear()


@dataclass
class ScriptedFailures:
    """Raises a chosen error on the n-th call of a method, or on every call for one target."""

    on_call: dict[tuple[str, int], Exception] = field(default_factory=dict)
    on_target: dict[tuple[str, str], Exception] = field(default_factory=dict)
    _calls: Counter[str] = field(default_factory=Counter)

    def check(self, method: str, target: str) -> None:
        self._calls[method] += 1
        error = self.on_call.get((method, self._calls[method])) or self.on_target.get(
            (method, target)
        )
        if error is not None:
            raise error


@dataclass
class LazyRequest:
    """What a googleapiclient method returns: nothing happens until execute()."""

    answer: Callable[[], Any]

    def execute(self) -> Any:
        return self.answer()


@dataclass(frozen=True)
class CallSite:
    api: str
    area: str
    # The keyword argument that names what a call acts on, for the journal.
    target_argument: str


class WritesOnly:
    """A resource whose every method counts as a write, reads included: the code under test has
    no business calling it at all, so any call shows up among the mutations."""

    def __init__(self, journal: WriteJournal, site: CallSite) -> None:
        self._journal = journal
        self._site = site

    def __getattr__(self, name: str) -> Callable[..., LazyRequest]:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**arguments: Any) -> LazyRequest:
            target = str(arguments.get(self._site.target_argument) or "")
            self._journal.write(self._site.api, f"{self._site.area}.{name}", target)
            return LazyRequest(lambda: {})

        return call
