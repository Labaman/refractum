"""
Read reflector configuration to pre-populate the Arch mirrors tab.

Lookup order (first file that exists wins):
  1. /etc/xdg/reflector/reflector.conf  — reflector's own config
  2. /etc/reflector-simple.conf         — legacy reflector-simple config

Both files use the same format: reflector CLI flags, one per line:

    --protocol https
    --sort rate
    --country Germany,France
    --latest 10

If neither file exists, built-in defaults are used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


REFLECTOR_CONF        = Path("/etc/xdg/reflector/reflector.conf")
REFLECTOR_SIMPLE_CONF = Path("/etc/reflector-simple.conf")


@dataclass
class ReflectorConfig:
    """Options parsed from reflector's config file."""
    countries: list[str] = field(default_factory=list)
    excluded_countries: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    sort: str = ""
    age: str = ""
    number: str = ""
    latest: str = ""


def load_reflector_config(path: Path | None = None) -> ReflectorConfig | None:
    """
    Parse a reflector config file into a ReflectorConfig.

    If path is not given, tries REFLECTOR_CONF then REFLECTOR_SIMPLE_CONF.
    Returns None if no file is found.
    """
    if path is None:
        for candidate in (REFLECTOR_CONF, REFLECTOR_SIMPLE_CONF):
            if candidate.exists():
                path = candidate
                break
        else:
            return None
    if not path.exists():
        return None

    cfg = ReflectorConfig()
    raw_lines = _read_clean_lines(path)

    tokens: list[tuple[str, str]] = []
    for line in raw_lines:
        parts = line.split(None, 1)
        if not parts:
            continue
        opt = parts[0]
        val = parts[1] if len(parts) > 1 else ""

        # Compact short options: -cDE,FR  =>  opt="-c"  val="DE,FR"
        match = re.match(r"^(-[cpanl])(.+)$", opt)
        if match:
            opt = match.group(1)
            val = match.group(2)

        tokens.append((opt, val))

    for opt, val in tokens:
        match opt:
            case "--protocol" | "-p":
                cfg.protocols.extend(v.strip() for v in val.split(","))
            case "--sort":
                cfg.sort = val
            case "--age" | "-a":
                cfg.age = val
            case "--number" | "-n":
                cfg.number = val
            case "--latest" | "-l":
                cfg.latest = val
            case "--country" | "-c":
                cfg.countries.extend(v.strip() for v in val.split(","))
            case "--country-exclude":
                cfg.excluded_countries.extend(v.strip() for v in val.split(","))

    return cfg


def _read_clean_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        line = line.strip("\"'")
        if line:
            lines.append(line)
    return lines
