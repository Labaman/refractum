"""
Show up-to-date ranking information about mirrors in /etc/pacman.d/mirrorlist.

Can be run as: refract-rank [--age|--rate]
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests

MIRRORLIST_PATH = Path("/etc/pacman.d/mirrorlist")
LASTUPDATE_FILE = "lastupdate"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class MirrorInfo:
    url: str
    age_seconds: int
    fetch_time: float  # seconds


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def read_mirrors(path: Path = MIRRORLIST_PATH) -> list[str]:
    """
    Extract server base URLs from the mirrorlist.

    Strips the trailing `/$repo/os/$arch` path template so we can
    append `/lastupdate` to check mirror freshness.
    """
    servers = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"Cannot read {path}: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("Server = "):
            url = line[len("Server = ") :].strip()
            base = url.replace("/$repo/os/$arch", "").rstrip("/")
            servers.append(base)
    return servers


def check_mirror(url: str, now: float) -> MirrorInfo | None:
    """
    Fetch `url/lastupdate` and return age + fetch time.

    The `lastupdate` file contains a Unix timestamp (integer string).
    Age = now - timestamp.

    Returns None if the mirror is unreachable or returns bad data.
    """
    target = f"{url}/{LASTUPDATE_FILE}"
    try:
        response = requests.get(target, timeout=10)
        elapsed = response.elapsed.total_seconds()

        if response.status_code >= 400:
            return None

        timestamp_str = response.text.strip()
        timestamp = int(timestamp_str)
        age = int(now - timestamp)
        return MirrorInfo(url=f"{url}/$repo/os/$arch", age_seconds=age, fetch_time=elapsed)

    except (requests.RequestException, ValueError):
        return None


def rank_mirrors(
    mirrors: list[str],
    sort: str = "age",
    max_workers: int = 10,
) -> list[MirrorInfo]:
    """
    Check all mirrors concurrently and return a sorted list.

    Args:
        mirrors:     list of base URLs (without /$repo/os/$arch)
        sort:        "age" or "rate"
        max_workers: number of concurrent threads
    """
    now = time.time()
    results: list[MirrorInfo] = []
    failed: list[str] = []
    total = len(mirrors)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_url = {pool.submit(check_mirror, url, now): url for url in mirrors}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            done += 1
            print(f"\r  {done}/{total} checking…", end="", flush=True, file=sys.stderr)
            info = future.result()
            if info:
                results.append(info)
            else:
                failed.append(url)

    print(file=sys.stderr)
    for url in failed:
        print(f"  Warning: {url} failed", file=sys.stderr)

    if sort == "rate":
        results.sort(key=lambda m: (m.fetch_time, m.age_seconds))
    else:
        results.sort(key=lambda m: (m.age_seconds, m.fetch_time))

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_age(seconds: int) -> str:
    """Convert age in seconds to a human-readable string."""
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def print_table(results: list[MirrorInfo]) -> None:
    """Print results as a neatly aligned table."""
    if not results:
        print("No reachable mirrors found.")
        return

    # Determine column widths dynamically
    url_width = max(len(m.url) for m in results)
    url_width = max(url_width, len("Mirror"))

    header = f"{'Mirror':<{url_width}}  {'Age':>10}  {'Rate (s)':>10}"
    separator = f"{'~' * url_width}  {'~' * 10}  {'~' * 10}"

    print(header)
    print(separator)
    for m in results:
        age_str = format_age(m.age_seconds)
        print(f"{m.url:<{url_width}}  {age_str:>10}  {m.fetch_time:>10.3f}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """
    Usage: refract-rank [--age|--rate] [--help]
    """
    if argv is None:
        argv = sys.argv[1:]

    sort = "age"
    if "--rate" in argv:
        sort = "rate"
    if "--age" in argv:
        sort = "age"
    if "--help" in argv or "-h" in argv:
        print("Usage: refract-rank [--age|--rate]")
        print("  --age   Sort by mirror age (default)")
        print("  --rate  Sort by download speed")
        return

    if not MIRRORLIST_PATH.exists():
        print(f"Error: {MIRRORLIST_PATH} not found", file=sys.stderr)
        sys.exit(1)

    mirrors = read_mirrors()
    if not mirrors:
        print("No servers found in mirrorlist.", file=sys.stderr)
        sys.exit(1)

    print(f"Checking {len(mirrors)} mirrors from {MIRRORLIST_PATH}…", file=sys.stderr)
    results = rank_mirrors(mirrors, sort=sort)
    print(f"Sorted by {sort}.\n", file=sys.stderr)
    print_table(results)


if __name__ == "__main__":
    main()
