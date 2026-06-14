"""
Arch Linux mirror data from archlinux.org/mirrors/status/json/.

Provides country list and mirror filtering directly from the upstream JSON.
Mirror status JSON is cached locally for CACHE_TTL seconds.
"""

from __future__ import annotations

import json
import platform
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

import requests

from .models import Country, WORLDWIDE

MIRRORS_JSON_URL = "https://archlinux.org/mirrors/status/json/"
_CACHE_PATH = Path.home() / ".cache" / "refractum" / "mirrorstatus.json"
_CACHE_TTL = 300  # seconds


@dataclass(frozen=True)
class ArchMirror:
    """One entry from the Arch Linux mirror status JSON."""

    url: str  # base URL, e.g. "https://mirror.de/archlinux/"
    country: str  # "Germany"
    country_code: str  # "DE"
    protocol: str  # "https" | "http" | "rsync" | "ftp"
    last_sync: float  # Unix timestamp; 0 if never synced
    completion_pct: float
    score: float | None
    delay: int | None  # seconds

    @property
    def server_template(self) -> str:
        """Server line template for pacman mirrorlist."""
        return self.url.rstrip("/") + "/$repo/os/$arch"

    def make_test_url(self) -> str:
        """URL used for speed measurement — extra.db, same target as reflector."""
        arch = platform.machine()
        return self.url.rstrip("/") + f"/extra/os/{arch}/extra.db"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sort_key(name: str) -> str:
    nfd = unicodedata.normalize("NFD", name)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch)).casefold()


def _parse_time(s: str | None) -> float:
    """Parse ISO 8601 timestamp to Unix timestamp. Returns 0.0 on failure."""
    if not s:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp()
        except ValueError:
            pass
    return 0.0


def _load_json(timeout: int = 15) -> dict:
    """Fetch mirror status JSON from archlinux.org, with 5-minute file cache."""
    if _CACHE_PATH.exists():
        try:
            if time.time() - _CACHE_PATH.stat().st_mtime < _CACHE_TTL:
                return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    response = requests.get(MIRRORS_JSON_URL, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass

    return data


def _parse_mirrors(data: dict) -> list[ArchMirror]:
    mirrors = []
    for m in data.get("urls", []):
        last_sync = _parse_time(m.get("last_sync"))
        if not last_sync:
            continue  # skip mirrors that have never synced
        mirrors.append(
            ArchMirror(
                url=m["url"],
                country=m.get("country", ""),
                country_code=m.get("country_code", ""),
                protocol=m.get("protocol", ""),
                last_sync=last_sync,
                completion_pct=float(m.get("completion_pct") or 0.0),
                score=m.get("score"),
                delay=m.get("delay"),
            )
        )
    return mirrors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_countries() -> list[Country]:
    """
    Return sorted Country list from Arch mirror status JSON.

    Returns [WORLDWIDE, ...sorted...] from the upstream JSON.
    Raises RuntimeError on network failure or empty list.
    """
    try:
        data = _load_json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Cannot fetch mirror list: {exc}") from exc

    seen: dict[str, str] = {}
    for m in data.get("urls", []):
        code = m.get("country_code", "").strip()
        name = m.get("country", "").strip()
        if code and name:
            seen.setdefault(code, name)

    if not seen:
        raise RuntimeError("Mirror status JSON returned no countries")

    countries = [Country(name=name, code=code) for code, name in seen.items()]
    return [WORLDWIDE] + sorted(countries, key=lambda c: _sort_key(c.name))


def fetch_mirrors(
    countries: list[str] | None = None,
    protocols: list[str] | None = None,
    age_hours: int | None = None,
    use_latest: int | None = None,
    timeout: int = 15,
) -> list[ArchMirror]:
    """
    Fetch and filter Arch mirrors from the status JSON.

    Args:
        countries:   ISO-2 codes; None or ["WW"] = no country filter.
        protocols:   e.g. ["https"]; None = all.
        age_hours:   Exclude mirrors not synced within N hours.
        use_latest:  Keep only the N most-recently-synced mirrors (applied last,
                     before speed testing — same as reflector's --latest).
        timeout:     HTTP request timeout in seconds.

    Returns only fully-synced mirrors (completion_pct == 1.0).
    """
    data = _load_json(timeout)
    mirrors = _parse_mirrors(data)

    # Only fully synced mirrors
    mirrors = [m for m in mirrors if m.completion_pct >= 1.0]

    # Protocol filter
    if protocols:
        proto_set = set(protocols)
        mirrors = [m for m in mirrors if m.protocol in proto_set]

    # Country filter
    if countries and "WW" not in countries:
        upper = {c.upper() for c in countries}
        mirrors = [m for m in mirrors if m.country_code.upper() in upper]

    # Age filter
    if age_hours and age_hours > 0:
        cutoff = time.time() - age_hours * 3600
        mirrors = [m for m in mirrors if m.last_sync >= cutoff]

    # --latest N: take N most recently synced before speed test
    if use_latest:
        mirrors.sort(key=lambda m: m.last_sync, reverse=True)
        mirrors = mirrors[:use_latest]

    return mirrors


def sort_no_test(mirrors: list[ArchMirror], sort_by: str) -> list[ArchMirror]:
    """
    Sort mirrors using JSON metadata only — no download required.
    Used for sort_by in ("score", "age", "delay", "country").
    """
    if sort_by == "score":
        return sorted(mirrors, key=lambda m: m.score if m.score is not None else float("inf"))
    if sort_by == "age":
        return sorted(mirrors, key=lambda m: m.last_sync, reverse=True)
    if sort_by == "delay":
        return sorted(mirrors, key=lambda m: m.delay if m.delay is not None else float("inf"))
    if sort_by == "country":
        return sorted(mirrors, key=lambda m: (_sort_key(m.country), m.url))
    return mirrors


def format_mirrorlist(entries: list[tuple[str, float, str]]) -> str:
    """
    Build a pacman mirrorlist string with country headers.

    Args:
        entries: list of (server_template, speed_bps, country_name).
                 speed_bps=0 means not tested (no annotation written).
    """
    lines = ["# Generated by refractum", ""]
    current_country = ""
    for template, speed_bps, country in entries:
        if country != current_country:
            if country:
                lines.append(f"## {country}")
            current_country = country
        if speed_bps > 0:
            speed_mb = speed_bps / (1024 * 1024)
            lines.append(f"# {speed_mb:.2f} MB/s")
        lines.append(f"Server = {template}")
    lines.append("")
    return "\n".join(lines)
