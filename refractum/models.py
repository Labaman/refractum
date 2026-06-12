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
    code: str  # two-letter ISO, e.g. "DE"
    count: int  # number of available mirrors


WORLDWIDE = Country(name="Worldwide", code="WW", count=0)


# ---------------------------------------------------------------------------
# Mirror options (shared between config and GUI)
# ---------------------------------------------------------------------------


@dataclass
class ReflectorOptions:
    """All options the user can configure in the GUI."""

    countries: list[str] = field(default_factory=list)  # ISO codes
    protocols: list[str] = field(default_factory=list)  # ["https", "http"]
    sort: str = "rate"
    number: int = 10  # final result count (--number)
    use_latest: bool = False  # True = --latest N pool, False = --age N
    latest: int = 30  # pool size for speed test when use_latest=True (--latest)
    age: int | None = None  # hours; None = omit
    download_timeout: int = 10
    threads: int | None = None
    distro_sets: list[str] | None = None  # None = not yet saved; [] = user saved empty selection
    distro_ww_fallback: bool = False  # auto-use all mirrors when none match selected countries
