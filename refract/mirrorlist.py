"""
Mirrorlist post-processing and saving.

Responsibilities:
  - fetch the full Arch mirrorlist from archlinux.org
  - annotate each selected mirror with its country name
  - save the result to /etc/pacman.d/mirrorlist using pkexec
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

import requests

MIRRORLIST_PATH = Path("/etc/pacman.d/mirrorlist")
ARCH_MIRRORLIST_URL = "https://archlinux.org/mirrorlist/all"


# ---------------------------------------------------------------------------
# Fetch the full Arch mirrorlist
# ---------------------------------------------------------------------------

def fetch_full_mirrorlist(timeout: int = 15) -> str:
    """
    Download the complete Arch Linux mirrorlist from archlinux.org.

    Returns the raw text content.
    Raises requests.RequestException on network error.
    """
    response = requests.get(ARCH_MIRRORLIST_URL, timeout=timeout)
    response.raise_for_status()
    return response.text


# ---------------------------------------------------------------------------
# Annotate mirrorlist with country names
# ---------------------------------------------------------------------------

def annotate_with_countries(
    ranked_content: str,
    full_mirrorlist: str,
) -> str:
    """
    Add country header comments to the ranked mirrorlist.

    `reflector` produces a flat list of `Server = …` lines.
    This function inserts `## CountryName` headers above each server
    by looking up which country section that server appears in within
    the full Arch mirrorlist.

    Returns the annotated mirrorlist text.
    """
    # Collect server URLs from the ranked output
    ranked_servers = _extract_servers(ranked_content)

    # Parse the full mirrorlist into {country_name: [url, …]}
    country_map = _parse_full_mirrorlist(full_mirrorlist)

    # Build server -> country_name lookup
    server_to_country: dict[str, str] = {}
    for country_name, servers in country_map.items():
        for url in servers:
            server_to_country[url] = country_name

    # Rebuild the mirrorlist, inserting country headers
    lines: list[str] = []

    # Keep original header comments from the ranked output
    for line in ranked_content.splitlines():
        if line.startswith("#"):
            lines.append(line)
        else:
            break

    current_country: str | None = None

    for server_url in ranked_servers:
        country = server_to_country.get(server_url)

        if country and country != current_country:
            lines.append("")
            lines.append(f"## {country}")
            current_country = country

        lines.append(f"Server = {server_url}")

    return "\n".join(lines) + "\n"


def _extract_servers(content: str) -> list[str]:
    """Extract server URLs from mirrorlist text."""
    servers = []
    for line in content.splitlines():
        if line.startswith("Server = "):
            servers.append(line[len("Server = "):].strip())
    return servers


def _parse_full_mirrorlist(content: str) -> dict[str, list[str]]:
    """
    Parse the full Arch mirrorlist into {country_name: [server_url, …]}.

    The file format uses `## Country Name` as section headers and
    `#Server = url` (commented out) as entries.
    """
    result: dict[str, list[str]] = {}
    current: str | None = None

    for line in content.splitlines():
        if line.startswith("## ") and not line.startswith("## Generated"):
            current = line[3:].strip()
            result.setdefault(current, [])
        elif line.startswith("#Server = ") and current:
            url = line[len("#Server = "):].strip()
            result[current].append(url)

    return result


# ---------------------------------------------------------------------------
# Save mirrorlist
# ---------------------------------------------------------------------------

def save_mirrorlist(content: str, dest: Path = MIRRORLIST_PATH) -> None:
    """Save a single mirrorlist. Delegates to save_mirrorlist_batch."""
    save_mirrorlist_batch([(content, dest)])


def save_mirrorlist_batch(files: list[tuple[str, Path]]) -> None:
    """
    Save multiple mirrorlist files in a SINGLE pkexec invocation.

    Avoids repeated password prompts when saving several files at once
    (e.g. cachyos-mirrorlist + cachyos-v3-mirrorlist + cachyos-v4-mirrorlist).

    Strategy:
      1. Write every content string to its own temp file (no root needed).
      2. Build one bash script that backs up and overwrites all destinations.
      3. Run the script under a single pkexec call.

    Raises PermissionError if the user cancels pkexec.
    Raises subprocess.CalledProcessError on any other failure.
    """
    if not files:
        return

    tmp_pairs: list[tuple[Path, Path]] = []   # (tmp_path, dest_path)
    try:
        for content, dest in files:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".mirrorlist", delete=False
            ) as tmp:
                tmp.write(content)
                tmp_pairs.append((Path(tmp.name), dest))

        # One fragment per file: backup silently (may not exist on first run),
        # then install the ranked result.
        fragments = [
            f"(cp {shlex.quote(str(dest))} {shlex.quote(str(dest) + '.bak')} 2>/dev/null || true)"
            f" && cp {shlex.quote(str(tmp))} {shlex.quote(str(dest))}"
            for tmp, dest in tmp_pairs
        ]
        script = " && ".join(fragments)

        result = subprocess.run(
            ["pkexec", "bash", "-c", script],
            timeout=60, check=False,
        )
        if result.returncode == 126:
            raise PermissionError("User cancelled the pkexec authorisation dialog")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "pkexec")
    finally:
        for tmp_path, _ in tmp_pairs:
            tmp_path.unlink(missing_ok=True)
