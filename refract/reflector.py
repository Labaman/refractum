"""
Interface to the `reflector` command-line tool.

Responsibilities:
  - fetch the list of countries from `reflector --list-countries`
  - build a reflector CLI command from user selections
  - run reflector and stream its output line by line
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from collections.abc import Generator


# ---------------------------------------------------------------------------
# Country model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Country:
    """A single country entry from `reflector --list-countries`."""

    name: str
    code: str  # two-letter ISO, e.g. "DE"
    count: int  # number of available mirrors

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


# The "Worldwide" pseudo-entry is not returned by reflector itself.
WORLDWIDE = Country(name="Worldwide", code="WW", count=0)


# ---------------------------------------------------------------------------
# Fetch country list
# ---------------------------------------------------------------------------


def get_countries() -> list[Country]:
    """
    Run `reflector --list-countries` and parse its output.

    Output looks like:
        Country Name            Code  Mirror Count  Last Check
        ---------------------------------------------------
        Australia                AU               9  2024-01-01 …
        Austria                  AT               4  …

    Returns a list starting with the synthetic WORLDWIDE entry.
    Raises RuntimeError if reflector fails or returns no data.
    """
    try:
        result = subprocess.run(
            ["reflector", "--list-countries"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("reflector is not installed or not in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("reflector --list-countries timed out") from exc

    if result.returncode != 0:
        raise RuntimeError(f"reflector exited with code {result.returncode}")

    countries = _parse_country_list(result.stdout)
    if not countries:
        raise RuntimeError("reflector returned an empty country list")

    return [WORLDWIDE] + sorted(countries, key=lambda c: _sort_key(c.name))


def _sort_key(name: str) -> str:
    """Sort key that ignores accents: Réunion → reunion, Türkiye → turkiye."""
    nfd = unicodedata.normalize("NFD", name)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch)).casefold()


_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_ISO2_RE = re.compile(r"^[A-Z]{2}$")


def _parse_country_list(output: str) -> list[Country]:
    """
    Parse the tabular output of `reflector --list-countries`.

    Skips header lines (those containing '---' or 'Country Name').
    Each data line has the form:
        <Name padded>   <CODE>   <count>   <date> …
    """
    countries = []
    in_data = False

    for line in output.splitlines():
        if "-----" in line:
            in_data = True
            continue
        if not in_data or not line.strip():
            continue

        # Line example: "Germany                  DE              37  2024-05-15"
        # Split on 2+ spaces so country names with spaces stay intact.
        parts = _MULTI_SPACE_RE.split(line.strip())
        if len(parts) < 3:
            continue

        name = parts[0].strip()
        code = parts[1].strip()
        try:
            count = int(parts[2].strip())
        except ValueError:
            count = 0

        if _ISO2_RE.match(code):
            countries.append(Country(name=name, code=code, count=count))

    return countries


# ---------------------------------------------------------------------------
# Build reflector command
# ---------------------------------------------------------------------------


@dataclass
class ReflectorOptions:
    """All options the user can configure in the GUI."""

    countries: list[str] = field(default_factory=list)  # ISO codes
    protocols: list[str] = field(default_factory=list)  # ["https", "http"]
    sort: str = "rate"
    number: int = 10
    use_latest: bool = False  # True = --latest N (N most recent), False = --age N (freshness window)
    age: int | None = None  # --age N (hours), None = omit
    download_timeout: int = 5
    threads: int | None = None
    extra_args: list[str] = field(default_factory=list)


def build_command(opts: ReflectorOptions) -> list[str]:
    """
    Translate ReflectorOptions into a reflector CLI argument list.

    Example result:
        ["reflector", "--verbose", "-c", "DE", "-c", "FR",
         "--protocol", "https", "--sort", "rate", "--latest", "10",
         "--download-timeout", "5"]
    """
    cmd = ["reflector", "--verbose"]

    for code in opts.countries:
        if code and code != "WW":  # skip Worldwide — it means "no filter"
            cmd += ["-c", code]

    for proto in opts.protocols:
        if proto:
            cmd += ["--protocol", proto]

    if opts.age is not None and opts.age > 0:
        cmd += ["--age", str(opts.age)]

    cmd += ["--sort", opts.sort]

    if opts.use_latest:
        cmd += ["--latest", str(max(1, opts.number))]
    else:
        cmd += ["--number", str(max(1, opts.number))]

    cmd += ["--download-timeout", str(opts.download_timeout)]

    if opts.threads is not None and opts.threads > 1:
        cmd += ["--threads", str(opts.threads)]

    cmd += opts.extra_args

    return cmd


# ---------------------------------------------------------------------------
# Run reflector and stream output
# ---------------------------------------------------------------------------


def run_reflector(
    cmd: list[str],
    output_file: str,
) -> Generator[str, None, int]:
    """
    Run reflector, streaming each stderr line as it appears.

    Reflector writes progress to stderr and the mirrorlist to stdout.
    We redirect stdout to `output_file` and yield stderr lines one by one
    so the GUI can update its progress display in real time.

    Usage:
        gen = run_reflector(cmd, "/tmp/mirrorlist.tmp")
        for line in gen:
            update_progress_bar(line)
        return_code = gen.return_value   # after the loop ends

    This is a *generator function* — it uses `yield` to produce values
    one at a time without buffering the entire output.

    Returns (via StopIteration.value) the process exit code.
    """
    with open(output_file, "w", encoding="utf-8") as out_fh:
        process = subprocess.Popen(
            cmd,
            stdout=out_fh,  # mirrorlist goes directly to the file
            stderr=subprocess.PIPE,
            text=True,
        )

        # Read stderr line by line as they arrive
        if process.stderr is None:
            raise RuntimeError("subprocess.Popen failed to open stderr pipe")
        for line in process.stderr:
            yield line.rstrip("\n")

        process.wait()
        return process.returncode
