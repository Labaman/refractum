"""
Shared data models: Country, ReflectorOptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Country model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Country:
    """A single country entry."""

    name: str
    code: str   # two-letter ISO, e.g. "DE"
    count: int  # number of available mirrors

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


WORLDWIDE = Country(name="Worldwide", code="WW", count=0)


# ---------------------------------------------------------------------------
# Mirror options (shared between config and GUI)
# ---------------------------------------------------------------------------


@dataclass
class ReflectorOptions:
    """All options the user can configure in the GUI."""

    countries: list[str] = field(default_factory=list)   # ISO codes
    protocols: list[str] = field(default_factory=list)   # ["https", "http"]
    sort: str = "rate"
    number: int = 10
    use_latest: bool = False  # True = --latest N, False = --age N
    age: int | None = None    # hours; None = omit
    download_timeout: int = 10
    threads: int | None = None
