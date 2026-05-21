"""
Distro-specific mirror set definitions.

Each MirrorSet describes one mirrorlist file: where to get the full list
of available mirrors, how to construct a speed-test URL, and where to save
the ranked result.

Distros: CachyOS (x86_64, v3, v4), EndeavourOS, Artix, BlackArch, RebornOS, ArcoLinux.
Third-party repos: Chaotic-AUR, Arch Linux CN, Arch4edu.
Arch Linux itself is handled by reflector (separate tab), not listed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# MirrorSet — one mirrorlist file
# ---------------------------------------------------------------------------

@dataclass
class MirrorSet:
    """
    Describes a complete mirrorlist configuration for one distro/variant.

    Attributes:
        id            Unique identifier, e.g. "cachyos-v4"
        display_name  Human-readable label for the GUI
        mirrorlist_path  Where to write the ranked result
        source_url    URL of the canonical (full) mirror list
        test_repo     Repo name to use in the speed-test URL
        test_arch     Architecture string to substitute for $arch (or $arch_v4 etc.)
        test_db       Filename to fetch for the speed test (e.g. "cachyos.db")
        arch_var      The template variable for architecture in Server URLs.
                      Default "$arch"; CachyOS-v3 uses "$arch_v3", v4 uses "$arch_v4"
        primary_id    If set, this set is derived from the named primary (no direct test)
    """
    id:               str
    display_name:     str
    mirrorlist_path:  Path
    source_url:       str
    test_repo:        str
    test_arch:        str
    test_db:          str
    arch_var:         str = "$arch"
    primary_id:       str = ""

    def make_test_url(self, server_template: str) -> str:
        """
        Build a concrete speed-test URL from a Server line template.

        Example:
            template = "https://cdn77.cachyos.org/repo/$arch/$repo"
            → "https://cdn77.cachyos.org/repo/x86_64/cachyos/cachyos.db"
        """
        url = (
            server_template
            .replace(self.arch_var, self.test_arch)
            .replace("$repo", self.test_repo)
        )
        return url.rstrip("/") + f"/{self.test_db}"

    @property
    def is_installed(self) -> bool:
        """True if this mirrorlist file exists on the system."""
        return self.mirrorlist_path.exists()


# ---------------------------------------------------------------------------
# Parse a mirrorlist file
# ---------------------------------------------------------------------------

def parse_mirrorlist(text: str, include_commented: bool = True) -> list[str]:
    """
    Extract Server URL templates from a pacman mirrorlist.

    With include_commented=True (default when fetching upstream), both
    active servers (Server = …) and commented-out ones (# Server = …)
    are returned — we want to test ALL available mirrors, not just the
    currently active ones.

    With include_commented=False, only active servers are returned —
    useful when reading the locally installed file.
    """
    servers: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Server = "):
            servers.append(line[len("Server = "):].strip())
        elif include_commented and re.match(r"^#\s*Server\s*=\s*", line):
            url = re.sub(r"^#\s*Server\s*=\s*", "", line).strip()
            servers.append(url)
    return servers


# Matches section headers: "## Germany" or "## USA Mirror much thanks to…" etc.
_SECTION_HEADER_RE = re.compile(r'^#{1,2}\s+(.{2,60})$')
# Words in headers that are never country names
_SKIP_WORDS = frozenset(("server", "generated", "mirrorlist", "disabled", "enabled",
                         "rerouted", "deprecated", "note", "todo", "cachyos"))


def _filter_servers_by_countries(
    text: str,
    country_names: set[str],
    include_commented: bool,
) -> list[str]:
    """
    Extract Server lines from sections whose header contains any of country_names
    as a case-insensitive substring.

    Mirrorlist section headers vary by distro:
      ## Germany              ← Arch-style (exact name)
      ## USA Mirror ...       ← CachyOS-style (name embedded in text)

    Returns an empty list if:
      - the file contains no section headers at all, OR
      - none of the headers match any of country_names
    In both cases the caller should fall back to returning all servers.
    """
    lower_names = {n.lower() for n in country_names}
    servers: list[str] = []
    in_match = False
    found_any_section = False

    for line in text.splitlines():
        s = line.strip()

        m = _SECTION_HEADER_RE.match(s)
        if m:
            header = m.group(1).lower()
            # Skip headers that are clearly not country names
            if any(w in header for w in _SKIP_WORDS):
                in_match = False
                continue
            found_any_section = True
            in_match = any(name in header for name in lower_names)
            continue

        if not in_match:
            continue

        if s.startswith("Server = "):
            servers.append(s[len("Server = "):].strip())
        elif include_commented and re.match(r'^#\s*Server\s*=\s*', s):
            servers.append(re.sub(r'^#\s*Server\s*=\s*', '', s).strip())

    # If no section headers were found, country filtering is not possible
    return servers if found_any_section else []


def fetch_mirrorlist(
    ms: MirrorSet,
    timeout: int = 15,
    country_names: set[str] | None = None,
) -> list[str]:
    """
    Fetch the upstream mirrorlist for a MirrorSet and return server templates.

    If country_names is given (and is not just {"Worldwide"}), only mirrors
    from matching country sections are returned. Falls back to all mirrors
    if no matching country sections are found.

    Falls back to the locally installed file if the upstream fetch fails.
    Returns an empty list if neither source is available.
    """
    # Fetch raw text
    text: str | None = None
    include_commented = True
    try:
        response = requests.get(ms.source_url, timeout=timeout)
        response.raise_for_status()
        text = response.text
    except requests.RequestException:
        pass

    if text is None:
        if ms.mirrorlist_path.exists():
            text = ms.mirrorlist_path.read_text()
            include_commented = False
        else:
            return []

    # Apply country filter if requested (skip "Worldwide" — means no filter)
    effective_countries = (country_names or set()) - {"Worldwide"}
    if effective_countries:
        filtered = _filter_servers_by_countries(text, effective_countries, include_commented)
        if filtered:
            return filtered
        # No matching country sections → fall back to all mirrors

    return parse_mirrorlist(text, include_commented=include_commented)


# ---------------------------------------------------------------------------
# Generate mirrorlist text from ranked results
# ---------------------------------------------------------------------------

def generate_mirrorlist(
    ms: MirrorSet,
    ranked: list[tuple[str, float]],
) -> str:
    """
    Generate a pacman mirrorlist from ranked (template, speed) pairs.

    Includes a header comment and active Server lines sorted fastest first.
    """
    lines = [
        f"# Generated by refract",
        f"# Mirror set: {ms.display_name}",
        f"# Sorted by download speed (fastest first)",
        "",
    ]
    for template, speed_bps in ranked:
        speed_mb = speed_bps / (1024 * 1024)
        lines.append(f"# {speed_mb:.2f} MB/s")
        lines.append(f"Server = {template}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Known distro mirror sets
# ---------------------------------------------------------------------------

_GITHUB_RAW = "https://raw.githubusercontent.com"

ALL_MIRROR_SETS: list[MirrorSet] = [
    # NOTE: Arch Linux is handled by reflector (Arch mirrors tab), not listed here.
    MirrorSet(
        id="cachyos",
        display_name="CachyOS (x86_64)",
        mirrorlist_path=Path("/etc/pacman.d/cachyos-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/CachyOS/CachyOS-PKGBUILDS/master/cachyos-mirrorlist/cachyos-mirrorlist",
        test_repo="cachyos",
        test_arch="x86_64",
        test_db="cachyos.db",
    ),
    MirrorSet(
        id="cachyos-v3",
        display_name="CachyOS (x86_64-v3)",
        mirrorlist_path=Path("/etc/pacman.d/cachyos-v3-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/CachyOS/CachyOS-PKGBUILDS/master/cachyos-v3-mirrorlist/cachyos-v3-mirrorlist",
        test_repo="cachyos",
        test_arch="x86_64_v3",
        test_db="cachyos.db",
        arch_var="$arch_v3",
        primary_id="cachyos",
    ),
    MirrorSet(
        id="cachyos-v4",
        display_name="CachyOS (x86_64-v4)",
        mirrorlist_path=Path("/etc/pacman.d/cachyos-v4-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/CachyOS/CachyOS-PKGBUILDS/master/cachyos-v4-mirrorlist/cachyos-v4-mirrorlist",
        test_repo="cachyos",
        test_arch="x86_64_v4",
        test_db="cachyos.db",
        arch_var="$arch_v4",
        primary_id="cachyos",
    ),
    MirrorSet(
        id="endeavouros",
        display_name="EndeavourOS",
        mirrorlist_path=Path("/etc/pacman.d/endeavouros-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/endeavouros-team/PKGBUILDS/master/endeavouros-mirrorlist/endeavouros-mirrorlist",
        test_repo="endeavouros",
        test_arch="x86_64",
        test_db="endeavouros.db",
    ),
    MirrorSet(
        id="artix",
        display_name="Artix Linux",
        mirrorlist_path=Path("/etc/pacman.d/artix-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/artix-linux/artix-mirrorlist/master/mirrorlist",
        test_repo="system",
        test_arch="x86_64",
        test_db="system.db",
    ),
    MirrorSet(
        id="blackarch",
        display_name="BlackArch Linux",
        mirrorlist_path=Path("/etc/pacman.d/blackarch-mirrorlist"),
        source_url="https://www.blackarch.org/blackarch-mirrorlist",
        test_repo="blackarch",
        test_arch="x86_64",
        test_db="blackarch.db",
    ),
    MirrorSet(
        id="chaotic-aur",
        display_name="Chaotic-AUR",
        mirrorlist_path=Path("/etc/pacman.d/chaotic-mirrorlist"),
        source_url="https://gitlab.com/chaotic-aur/pkgbuilds/-/raw/main/chaotic-mirrorlist/mirrorlist",
        test_repo="chaotic-aur",
        test_arch="x86_64",
        test_db="chaotic-aur.db",
    ),
    MirrorSet(
        id="archlinuxcn",
        display_name="Arch Linux CN",
        mirrorlist_path=Path("/etc/pacman.d/archlinuxcn-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/archlinuxcn/mirrorlist-repo/master/archlinuxcn-mirrorlist",
        test_repo="archlinuxcn",
        test_arch="x86_64",
        test_db="archlinuxcn.db",
    ),
    MirrorSet(
        id="rebornos",
        display_name="RebornOS",
        mirrorlist_path=Path("/etc/pacman.d/reborn-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/RebornOS-Team/rebornos-mirrorlist/main/reborn-mirrorlist",
        test_repo="",
        test_arch="x86_64",
        test_db="Reborn-OS.db",
    ),
    MirrorSet(
        id="arcolinux",
        display_name="ArcoLinux",
        mirrorlist_path=Path("/etc/pacman.d/arcolinux-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/arcolinux/arcolinux-mirrorlist/master/etc/pacman.d/arcolinux-mirrorlist",
        test_repo="arcolinux_repo_3party",
        test_arch="x86_64",
        test_db="arcolinux_repo_3party.db",
    ),
    MirrorSet(
        id="arch4edu",
        display_name="Arch4edu",
        mirrorlist_path=Path("/etc/pacman.d/arch4edu-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/arch4edu/mirrorlist/refs/heads/master/mirrorlist.arch4edu",
        test_repo="",
        test_arch="x86_64",
        test_db="arch4edu.db",
    ),
]


def installed_mirror_sets() -> list[MirrorSet]:
    """Return only the MirrorSets whose mirrorlist file exists on this system."""
    return [ms for ms in ALL_MIRROR_SETS if ms.is_installed]


def detect_distro_id() -> str:
    """
    Return the ID= value from /etc/os-release (lowercase, no quotes).

    Examples: "cachyos", "endeavouros", "artix", "arch".
    Returns "" if the file is missing or the field is absent.
    """
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                return line[3:].strip().strip('"').lower()
    except OSError:
        pass
    return ""
