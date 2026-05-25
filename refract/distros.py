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
from dataclasses import dataclass
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
        country_filter_supported  False for repos whose mirrorlist has no country sections
                      (institution names, CDN/location descriptions, single-server CDNs).
                      When False, country selection is ignored and all mirrors are returned.
    """

    id: str
    display_name: str
    mirrorlist_path: Path
    source_url: str
    test_repo: str
    test_arch: str
    test_db: str
    arch_var: str = "$arch"
    primary_id: str = ""
    is_repo: bool = False
    country_filter_supported: bool = True

    def make_test_url(self, server_template: str) -> str:
        """
        Build a concrete speed-test URL from a Server line template.

        Example:
            template = "https://cdn77.cachyos.org/repo/$arch/$repo"
            → "https://cdn77.cachyos.org/repo/x86_64/cachyos/cachyos.db"
        """
        url = server_template.replace(self.arch_var, self.test_arch).replace("$repo", self.test_repo)
        url = re.sub(r"(?<!:)//+", "/", url)  # collapse // left by empty test_repo
        return url.rstrip("/") + f"/{self.test_db}"

    @property
    def is_installed(self) -> bool:
        """True if this mirrorlist file exists on the system."""
        return self.mirrorlist_path.exists()


# ---------------------------------------------------------------------------
# Parse a mirrorlist file
# ---------------------------------------------------------------------------

_COMMENTED_SERVER_RE = re.compile(r"^#\s*Server\s*=\s*(.+)$")
# Matches section headers: "## Germany" or "## USA Mirror much thanks to…" etc.
_SECTION_HEADER_RE = re.compile(r"^#{1,2}\s+(.{2,60})$")
_CODE_RE = re.compile(r"\bcode=([A-Za-z]{2})\b")


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
            servers.append(line[9:])
        elif include_commented:
            m = _COMMENTED_SERVER_RE.match(line)
            if m:
                servers.append(m.group(1).strip())
    return servers


# Words in headers that are never country names
_SKIP_WORDS = frozenset(
    ("server", "generated", "mirrorlist", "disabled", "enabled", "rerouted", "deprecated", "note", "todo", "cachyos")
)


def _filter_servers_by_countries(
    text: str,
    country_names: set[str],
    include_commented: bool,
    country_codes: set[str] | None = None,
) -> list[str] | None:
    """
    Extract Server lines from sections whose header contains any of country_names
    as a case-insensitive substring, or a matching ISO-2 code marker (code=XX).

    Mirrorlist section headers vary by distro:
      ## Germany              ← EndeavourOS-style (exact name)
      ## USA Mirror ...       ← description with embedded name
      ## tier=1 code=FR       ← CachyOS-style metadata with ISO code

    For CachyOS, `code=XX` is the authoritative country marker — matched against
    country_codes so "code=US" works even when the name header says "USA Mirror"
    instead of "United States".

    Returns:
      None      — mirrorlist has no section headers; caller should use all mirrors.
      []        — sections found but none match the requested countries; caller
                  should return an empty list (no country mirrors available).
      [...]     — matching Server templates.
    """
    lower_names = {n.lower() for n in country_names}
    upper_codes = {c.upper() for c in (country_codes or set())}
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
            # code=XX is the authoritative country marker (CachyOS metadata lines)
            cm = _CODE_RE.search(header)
            if cm:
                in_match = cm.group(1).upper() in upper_codes
            else:
                in_match = any(name in header for name in lower_names)
            continue

        if not in_match:
            continue

        if s.startswith("Server = "):
            servers.append(s[9:])
        elif include_commented:
            m = _COMMENTED_SERVER_RE.match(s)
            if m:
                servers.append(m.group(1).strip())

    # None signals "no section headers" so callers can fall back to all mirrors.
    # An empty list means sections were found but no country matched.
    return servers if found_any_section else None


def _fetch_mirrorlist_text(ms: MirrorSet, timeout: int = 15) -> tuple[str, bool] | None:
    """
    Fetch raw mirrorlist text for a MirrorSet.

    Tries upstream URL first (include_commented=True — all Server lines,
    including commented-out ones), falls back to the locally installed file
    (include_commented=False — active Server lines only).
    Returns None if neither source is available.
    """
    try:
        response = requests.get(ms.source_url, timeout=timeout)
        response.raise_for_status()
        return response.text, True
    except requests.RequestException:
        pass
    if ms.mirrorlist_path.exists():
        return ms.mirrorlist_path.read_text(encoding="utf-8"), False
    return None


def fetch_mirrorlist(
    ms: MirrorSet,
    timeout: int = 15,
    country_names: set[str] | None = None,
    country_codes: set[str] | None = None,
) -> list[str]:
    """
    Fetch the upstream mirrorlist for a MirrorSet and return server templates.

    If country_names or country_codes are given (and not just {"Worldwide"}),
    only mirrors from matching country sections are returned. Falls back to all
    mirrors if no matching country sections are found.

    Falls back to the locally installed file if the upstream fetch fails.
    Returns an empty list if neither source is available.
    """
    # _fetch_mirrorlist_text handles the HTTP fetch + local fallback.
    # Returns (text, include_commented) or None if nothing is available.
    raw = _fetch_mirrorlist_text(ms, timeout)
    if raw is None:
        return []
    text, include_commented = raw

    # Apply country filter only for repos whose mirrorlist uses country-based sections.
    # Repos like Arch Linux CN (institution names) or RebornOS (CDN location descriptions)
    # have section headers that are not country names — filtering would return [] for any
    # country selection. For those, we always return all mirrors.
    if ms.country_filter_supported:
        effective_countries = (country_names or set()) - {"Worldwide"}
        if effective_countries or country_codes:
            filtered = _filter_servers_by_countries(text, effective_countries, include_commented, country_codes)
            if filtered is not None:
                # Sections exist: return only matching mirrors (may be empty — no fallback)
                return filtered
            # filtered is None: no section headers; fall back to all

    return parse_mirrorlist(text, include_commented=include_commented)


def get_template_countries(text: str, include_commented: bool = True) -> dict[str, str]:
    """
    Parse a mirrorlist text and return a {template: country} mapping.

    Country is determined by the nearest preceding section header:
      - code=XX marker (CachyOS-style "## tier=1 code=DE") → ISO-2 code, e.g. "DE"
      - plain country name (EndeavourOS-style "## Germany")  → header text, e.g. "Germany"

    Metadata lines (containing "=" or a skip word) do not change the current
    country — they are treated as continuation lines of the preceding header.
    Empty string for templates that appear before any recognisable country header.
    """
    result: dict[str, str] = {}
    current_country = ""

    for line in text.splitlines():
        s = line.strip()
        m = _SECTION_HEADER_RE.match(s)
        if m:
            header = m.group(1)
            cm = _CODE_RE.search(header.lower())
            if cm:
                # "## tier=1 code=DE" → current_country = "DE"
                current_country = cm.group(1).upper()
            elif "=" not in header and not any(w in header.lower() for w in _SKIP_WORDS):
                # "## Germany" → current_country = "Germany"
                current_country = header.strip()
            # else: metadata line ("## tier=1 code=GLOBAL", "## : OpenSSL …")
            #       → keep current_country unchanged
            continue

        if s.startswith("Server = "):
            result[s[9:]] = current_country
        elif include_commented:
            cm2 = _COMMENTED_SERVER_RE.match(s)
            if cm2:
                result[cm2.group(1).strip()] = current_country

    return result


def fetch_mirrorlist_with_countries(
    ms: MirrorSet,
    timeout: int = 15,
    country_names: set[str] | None = None,
    country_codes: set[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """
    Like fetch_mirrorlist but also returns a {template: country} mapping.

    Uses _fetch_mirrorlist_text once (single HTTP request) and passes the
    same raw text to both the country-filter logic and get_template_countries,
    so callers get templates + country info without a second network call.
    """
    # One HTTP call via the shared helper — result used twice below
    raw = _fetch_mirrorlist_text(ms, timeout)
    if raw is None:
        return [], {}
    text, include_commented = raw

    # Build country map from the full upstream text (before any filtering),
    # so every template knows its country even if the list gets narrowed down.
    country_map = get_template_countries(text, include_commented)

    # Same country-filter logic as fetch_mirrorlist — see comments there.
    if ms.country_filter_supported:
        effective_countries = (country_names or set()) - {"Worldwide"}
        if effective_countries or country_codes:
            filtered = _filter_servers_by_countries(text, effective_countries, include_commented, country_codes)
            if filtered is not None:
                return filtered, country_map
            # filtered is None: no section headers; fall back to all

    return parse_mirrorlist(text, include_commented=include_commented), country_map


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
        "# Generated by refract",
        f"# Mirror set: {ms.display_name}",
        "",
    ]
    for template, speed_bps in ranked:
        if speed_bps > 0:
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
        source_url="https://gitea.artixlinux.org/packages/artix-mirrorlist/raw/branch/master/mirrorlist",
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
        is_repo=True,
    ),
    MirrorSet(
        id="archlinuxcn",
        display_name="Arch Linux CN",
        mirrorlist_path=Path("/etc/pacman.d/archlinuxcn-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/archlinuxcn/mirrorlist-repo/master/archlinuxcn-mirrorlist",
        test_repo="archlinuxcn",
        test_arch="x86_64",
        test_db="archlinuxcn.db",
        is_repo=True,
        # Mirrorlist uses Chinese institution names as section headers, not country names.
        country_filter_supported=False,
    ),
    MirrorSet(
        id="rebornos",
        display_name="RebornOS",
        mirrorlist_path=Path("/etc/pacman.d/reborn-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/RebornOS-Team/rebornos-mirrorlist/main/reborn-mirrorlist",
        test_repo="",
        test_arch="x86_64",
        test_db="Reborn-OS.db",
        # Mirrorlist uses "# Location: City, Region" metadata, not country names.
        country_filter_supported=False,
    ),
    MirrorSet(
        id="arch4edu",
        display_name="Arch4edu",
        mirrorlist_path=Path("/etc/pacman.d/arch4edu-mirrorlist"),
        source_url=f"{_GITHUB_RAW}/arch4edu/mirrorlist/refs/heads/master/mirrorlist.arch4edu",
        test_repo="",
        test_arch="x86_64",
        test_db="arch4edu.db",
        is_repo=True,
        # Mirrorlist has country sections (China, Germany, etc.) but no Russian mirrors;
        # returning all mirrors on any country selection is more practical for a small repo.
        country_filter_supported=False,
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
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("ID="):
                return line[3:].strip().strip('"').lower()
    except OSError:
        pass
    return ""
