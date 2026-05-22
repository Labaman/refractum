"""
Read and save refract's own configuration.

Config files (reflector CLI flags, one per line):

    --country RU
    --protocol https
    --sort rate
    --age 24
    --number 10
    --download-timeout 5

Startup lookup (first file that exists wins):
  1. ~/.config/refract/settings.conf   — personal settings (written on every OK)
  2. /etc/refract.conf                 — system-wide defaults (set by admin)
  3. /etc/reflector-simple.conf        — first-launch bootstrap from reflector-simple
  4. /etc/xdg/reflector/reflector.conf — first-launch bootstrap from reflector

On first launch the bootstrapped defaults are written to settings.conf
immediately, so external configs are never read again after the first run.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .reflector import ReflectorOptions


USER_CONF             = Path.home() / ".config" / "refract" / "settings.conf"
GLOBAL_CONF           = Path("/etc/refract.conf")
REFLECTOR_CONF        = Path("/etc/xdg/reflector/reflector.conf")
REFLECTOR_SIMPLE_CONF = Path("/etc/reflector-simple.conf")


@dataclass
class ReflectorConfig:
    """Options parsed from a reflector-format config file."""
    countries: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    sort: str = ""
    age: str = ""
    number: str = ""
    latest: str = ""
    download_timeout: str = ""
    threads: str = ""


def load_reflector_config(path: Path | None = None) -> ReflectorConfig | None:
    """
    Parse a reflector config file into a ReflectorConfig.

    If path is not given, tries USER_CONF, GLOBAL_CONF, REFLECTOR_SIMPLE_CONF,
    then REFLECTOR_CONF. Returns None if no file is found.
    """
    if path is None:
        for candidate in (USER_CONF, GLOBAL_CONF, REFLECTOR_SIMPLE_CONF, REFLECTOR_CONF):
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
            case "--download-timeout":
                cfg.download_timeout = val
            case "--threads":
                cfg.threads = val

    return cfg


def save_user_config(opts: ReflectorOptions, path: Path = USER_CONF) -> None:
    """Save options to the personal config file (no root needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_build_config_lines(opts)) + "\n")


def save_global_config(opts: ReflectorOptions, path: Path = GLOBAL_CONF) -> None:
    """
    Save options to the system-wide config file via pkexec (requires root).

    Raises PermissionError if the user cancels the pkexec dialog.
    Raises subprocess.CalledProcessError on other failures.
    """
    content = "\n".join(_build_config_lines(opts)) + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        result = subprocess.run(
            ["pkexec", "bash", "-c",
             f"cp {shlex.quote(str(tmp_path))} {shlex.quote(str(path))}"],
            timeout=60, check=False,
        )
        if result.returncode == 126:
            raise PermissionError("User cancelled the pkexec authorisation dialog")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "pkexec")
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


def _build_config_lines(opts: ReflectorOptions) -> list[str]:
    lines = []
    for code in opts.countries:
        if code:
            lines.append(f"--country {code}")
    for proto in opts.protocols:
        if proto:
            lines.append(f"--protocol {proto}")
    if opts.sort:
        lines.append(f"--sort {opts.sort}")
    if opts.use_latest:
        lines.append(f"--latest {opts.number}")
    else:
        if opts.age:
            lines.append(f"--age {opts.age}")
        lines.append(f"--number {opts.number}")
    lines.append(f"--download-timeout {opts.download_timeout}")
    if opts.threads is not None and opts.threads > 1:
        lines.append(f"--threads {opts.threads}")
    return lines


def _read_clean_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        line = line.strip("\"'")
        if line:
            lines.append(line)
    return lines
