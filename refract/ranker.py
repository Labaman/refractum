"""
Speed-based mirror ranker.

Downloads a small portion of a test file from each mirror and measures
throughput in bytes/second. Uses a thread pool so all mirrors are tested
concurrently — the total time is roughly equal to the slowest mirror's
test time rather than the sum of all times.

This is the approach used by rate-mirrors and eos-rankmirrors.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from collections.abc import Callable

import requests

from .distros import MirrorSet, fetch_mirrorlist, generate_mirrorlist


# ---------------------------------------------------------------------------
# Speed test for a single URL
# ---------------------------------------------------------------------------

# Bytes to download per test. 500 KB gives a stable measurement for fast
# mirrors (4+ MB/s) without wasting too much time on slow ones.
_TEST_BYTES = 500_000

# Chunk size for streaming. 64 KB reduces Python loop overhead vs 8 KB.
_CHUNK_SIZE = 65536

# Mimic pacman so mirrors that filter by User-Agent respond correctly.
_HEADERS = {"User-Agent": "pacman/6.1.0 libalpm/14.0.0"}

# Alternative database filenames to try when the primary test file returns 404.
# Pacman repos always ship at least one of these.
_FALLBACK_DB_NAMES = ("extra.db", "core.db")


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
            # Start timing after first chunk arrives — removes TTFB and
            # connection overhead, measures pure download throughput only.
            start: float | None = None
            downloaded = 0
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                if start is None:
                    start = time.monotonic()
                downloaded += len(chunk)
                if downloaded >= max_bytes:
                    break

        if start is None:
            return None
        elapsed = time.monotonic() - start
        if elapsed > 0 and downloaded > 1024:
            return downloaded / elapsed
        return 0.0  # server responded but file too small to measure reliably

    except (requests.RequestException, OSError):
        pass

    return None


def test_rsync_speed(url: str, timeout: float = 10.0) -> float | None:
    """
    Download a file via rsync subprocess and return bytes/second.

    Mirrors the approach used by reflector: runs rsync into a temp directory,
    then measures file size / elapsed time.
    Returns None if rsync is not installed or the mirror is unreachable.
    """
    connection_timeout = max(5, int(timeout / 2))
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.monotonic()
            result = subprocess.run(
                [
                    "rsync",
                    "-aL",
                    "--no-motd",
                    f"--contimeout={connection_timeout}",
                    url,
                    tmpdir,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start

            if result.returncode != 0:
                return None

            filename = os.path.basename(url.rstrip("/"))
            filepath = os.path.join(tmpdir, filename)
            if not os.path.exists(filepath):
                return None

            size = os.path.getsize(filepath)
            if elapsed > 0 and size > 1024:
                return size / elapsed
            return 0.0

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
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
                start: float | None = None
                downloaded = 0
                for chunk in r.iter_content(chunk_size=_CHUNK_SIZE):
                    if start is None:
                        start = time.monotonic()
                    downloaded += len(chunk)
                    if downloaded >= _TEST_BYTES:
                        break
            if start is not None:
                elapsed = time.monotonic() - start
                if elapsed > 0 and downloaded > 1024:
                    return downloaded / elapsed
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
    test_url: str  # the actual URL that was fetched
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

    # Build (template → test_url) mapping, deduplicated
    seen: set[str] = set()
    jobs: list[tuple[str, str]] = []
    for tmpl in templates:
        if tmpl not in seen:
            seen.add(tmpl)
            jobs.append((tmpl, ms.make_test_url(tmpl)))

    results: list[RankResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Dict maps Future → template so we can look it up when the future completes
        future_to_job = {pool.submit(test_mirror_speed, test_url, timeout): (tmpl, test_url) for tmpl, test_url in jobs}

        for future in as_completed(future_to_job):
            tmpl, test_url = future_to_job[future]
            try:
                speed = future.result()
            except Exception:
                speed = None

            r = RankResult(
                template=tmpl,
                test_url=test_url,
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
