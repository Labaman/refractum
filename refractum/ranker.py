"""
Speed-based mirror ranker.

Downloads a small portion of a test file from each mirror and measures
throughput in bytes/second. Mirrors are tested concurrently so the total
time is roughly equal to the slowest mirror's test time.

This is the approach used by rate-mirrors and eos-rankmirrors.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from collections.abc import Callable

import requests

from .distros import MirrorSet, fetch_mirrorlist, generate_mirrorlist


# ---------------------------------------------------------------------------
# Speed test for a single URL
# ---------------------------------------------------------------------------

# Bytes to download per test. 4 MB gets past the CDN burst zone —
# extra.db (the Arch test file) is ~8.5 MB, so 500 KB only covered 6% of it
# and measured burst speed rather than sustained throughput.
_TEST_BYTES = 4_000_000

# Chunk size for streaming. 64 KB reduces Python loop overhead vs 8 KB.
_CHUNK_SIZE = 65536

# Mimic pacman so mirrors that filter by User-Agent respond correctly.
_HEADERS = {"User-Agent": "pacman/6.1.0 libalpm/14.0.0"}

# Alternative database filenames to try when the primary test file returns 404.
# Pacman repos always ship at least one of these.
_FALLBACK_DB_NAMES = ("extra.db", "core.db")


def _measure_stream(resp: requests.Response, max_bytes: int, max_time: float | None = None) -> float | None:
    """
    Read up to `max_bytes` from an open streaming response and return its
    download speed in bytes/second.

    Timing starts only after the first chunk arrives, so TCP/TLS setup and
    time-to-first-byte don't count against throughput.

    `max_time` is a wall-clock cap on total streaming time. It stops mirrors
    that trickle data slowly — requests' socket timeout fires only when no
    bytes arrive, but a slow-but-steady mirror never triggers it.

    Returns:
        float > 0  — measured bytes/second
        0.0        — data arrived but too little to measure reliably
        None       — no data arrived at all
    """
    start: float | None = None
    wall_start = time.monotonic()
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
        if start is None:
            start = time.monotonic()
        downloaded += len(chunk)
        if downloaded >= max_bytes:
            break
        if max_time is not None and time.monotonic() - wall_start > max_time:
            break

    if start is None:
        return None
    elapsed = time.monotonic() - start
    if elapsed > 0 and downloaded > 1024:
        return downloaded / elapsed
    return 0.0


def test_mirror_speed(
    url: str,
    timeout: float = 8.0,
    max_bytes: int = _TEST_BYTES,
) -> float | None:
    """
    Download up to `max_bytes` from `url` and return bytes/second.

    Timing starts after the first response chunk arrives, excluding TCP setup,
    TLS handshake, and TTFB — measures pure download throughput.

    Returns None only when the mirror is genuinely unreachable or times out.
    Returns 0.0 when the mirror is up but the specific test file is missing
    (server responded 404 for primary file, but repo directory is reachable) —
    the mirror is still usable and sorts at the bottom of reachable results.

    Test strategy:
      1. GET primary URL (e.g. cachyos.db)  — measures real bandwidth
      2. If 404: try fallback .db names in the same directory
      3. If still 404: HEAD the repo directory — confirms server is up
    """
    try:
        with requests.get(url, timeout=timeout, stream=True, headers=_HEADERS) as resp:
            if resp.status_code == 404:
                return _check_fallback(url, timeout)
            resp.raise_for_status()
            return _measure_stream(resp, max_bytes, max_time=timeout)
    except (requests.RequestException, OSError):
        return None


def _check_fallback(primary_url: str, timeout: float) -> float | None:
    """
    Called when the primary test file returned 404.
    Try alternative filenames, then fall back to a HEAD on the repo directory.
    Returns 0.0 if the mirror is alive (speed unknown), None if unreachable.
    """
    base_dir = primary_url.rsplit("/", 1)[0]  # strip filename

    # Try alternative database names in the same repo directory
    for name in _FALLBACK_DB_NAMES:
        alt_url = f"{base_dir}/{name}"
        if alt_url == primary_url:
            continue
        try:
            with requests.get(alt_url, timeout=timeout, stream=True, headers=_HEADERS) as r:
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                speed = _measure_stream(r, _TEST_BYTES, max_time=timeout)
            if speed:  # not None and not 0.0 — a real measurement
                return speed
        except (requests.RequestException, OSError):
            pass

    # Last resort: HEAD the repo directory itself
    try:
        r = requests.head(base_dir + "/", timeout=min(3.0, timeout / 2), headers=_HEADERS, allow_redirects=True)
        if r.status_code < 500:
            return 0.0  # server is up, speed unknown
    except (requests.RequestException, OSError):
        pass

    return None


# ---------------------------------------------------------------------------
# Concurrent ranking of a full MirrorSet
# ---------------------------------------------------------------------------


@dataclass
class RankResult:
    """Result for one mirror."""

    template: str  # the Server = … URL template
    speed: float  # bytes/second, 0.0 if unreachable
    reachable: bool
    country: str = ""  # ISO-2 code or header text; populated by DistroProgressWindow


def rank_mirror_set(
    ms: MirrorSet,
    templates: list[str] | None = None,
    max_workers: int = 10,
    timeout: float = 8.0,
    protocols: list[str] | None = None,
    max_results: int | None = None,
    on_progress: Callable[[RankResult], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[RankResult]:
    """
    Test every mirror in `templates` concurrently and return results sorted
    fastest first.

    Args:
        ms:           The MirrorSet (used for make_test_url).
        templates:    Pre-fetched list of Server templates. If None, fetched
                      internally (no country filter — pass a pre-filtered list
                      from the caller when country filtering is needed).
        max_workers:  Number of concurrent download threads.
        timeout:      Per-mirror download timeout in seconds.
        protocols:    If given, only test mirrors matching these protocols.
        max_results:  If given, return only the top N reachable mirrors.
        on_progress:  Callback called after each mirror finishes.
    """
    if templates is None:
        templates = fetch_mirrorlist(ms)
    if not templates:
        return []

    # Filter by protocol before testing — no point benchmarking unwanted protocols
    if protocols:
        templates = [t for t in templates if any(t.startswith(p + "://") for p in protocols)]
    if not templates:
        return []

    results: list[RankResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Dict maps Future → template so we can look it up when the future completes
        future_to_tmpl = {
            pool.submit(test_mirror_speed, ms.make_test_url(tmpl), timeout): tmpl for tmpl in dict.fromkeys(templates)
        }

        for future in as_completed(future_to_tmpl):
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            tmpl = future_to_tmpl[future]
            try:
                speed = future.result()
            except Exception:
                speed = None

            r = RankResult(
                template=tmpl,
                speed=speed or 0.0,
                reachable=speed is not None,
            )
            results.append(r)

            if on_progress:
                on_progress(r)

    # Sort: reachable first, then by speed descending
    results.sort(key=lambda r: (not r.reachable, -r.speed))

    if max_results is not None and max_results > 0:
        reachable = [r for r in results if r.reachable]
        unreachable = [r for r in results if not r.reachable]
        results = reachable[:max_results] + unreachable

    return results


def ranked_to_mirrorlist(ms: MirrorSet, results: list[RankResult]) -> str:
    """Convert RankResult list to a pacman mirrorlist string."""
    reachable = [(r.template, r.speed) for r in results if r.reachable]
    return generate_mirrorlist(ms, reachable)
