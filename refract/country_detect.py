"""Detect the user's country code using several fallback methods."""

from __future__ import annotations

import locale
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from collections.abc import Callable

import requests


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CountryDetectionResult:
    """Holds the outcome of a country detection attempt."""

    code: str  # two-letter ISO code, e.g. "DE"


# ---------------------------------------------------------------------------
# Individual detection methods
# ---------------------------------------------------------------------------


def _detect_via_ipinfo(timeout: int = 10) -> str | None:
    """Ask https://ipinfo.io/country for the current country code."""
    try:
        response = requests.get("https://ipinfo.io/country", timeout=timeout)
        response.raise_for_status()
        code = response.text.strip()
        if re.fullmatch(r"[A-Z]{2}", code):
            return code
    except requests.RequestException:
        pass
    return None


def _detect_via_locale() -> str | None:
    """
    Extract country code from the system locale (LC_TIME).

    In Bash the original did:
        locale | grep ^LC_TIME | cut -d '"' -f 2 | sed 's|^.*_([A-Z]{2})\\..*$|\\1|'

    Here we use the stdlib `locale` module and a regex.
    """
    lc_time, _ = locale.getlocale(locale.LC_TIME)
    if not lc_time:
        return None
    match = re.search(r"_([A-Z]{2})", lc_time)
    if match:
        return match.group(1)
    return None


def _detect_via_geoiplookup() -> str | None:
    """
    Use geoiplookup (from geoip package) after fetching the public IPv4
    via a DNS query to Google's myaddr service.

    Bash equivalent:
        IP=$(dig -4 TXT +short o-o.myaddr.l.google.com @ns1.google.com | tr -d '"')
        geoiplookup "$IP" | sed 's|^.*: ([A-Z]{2}),.*$|\\1|'
    """
    ip = _get_public_ipv4()
    if ip is None:
        return None
    try:
        result = subprocess.run(
            ["geoiplookup", ip],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        match = re.search(r":\s+([A-Z]{2}),", result.stdout)
        if match:
            return match.group(1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _get_public_ipv4() -> str | None:
    """
    Resolve our public IPv4 address via a DNS TXT query to Google.

    Uses `dig -4 TXT +short o-o.myaddr.l.google.com @ns1.google.com`.
    Returns None if dig is not installed or returns no usable address.
    """
    try:
        result = subprocess.run(
            ["dig", "-4", "TXT", "+short", "o-o.myaddr.l.google.com", "@ns1.google.com"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ip = result.stdout.strip().strip('"')
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
            return ip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_METHODS: list[tuple[str, Callable[[], str | None]]] = [
    ("ipinfo", _detect_via_ipinfo),
    ("geoiplookup", _detect_via_geoiplookup),
    ("locale", _detect_via_locale),
]


def detect_country() -> CountryDetectionResult | None:
    """
    Run all detection methods concurrently and return the first success.

    Returns:
        CountryDetectionResult on success, None if all methods fail.
    """
    pool = ThreadPoolExecutor(max_workers=len(_METHODS))
    futures = [pool.submit(fn) for _, fn in _METHODS]
    try:
        for future in as_completed(futures):
            code = future.result()
            if code:
                return CountryDetectionResult(code=code)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return None
