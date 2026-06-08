"""
Read and save refractum's configuration in TOML format.

User config: ~/.config/refractum/settings.toml  (written on every OK)
System config: /etc/refractum.toml              (written via pkexec)

First-launch bootstrap (read-only, never written):
  /etc/reflector-simple.conf  — reflector-simple format (imported once, read-only)
  /etc/xdg/reflector/reflector.conf  — reflector format (imported once, read-only)
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import ReflectorOptions


USER_CONF = Path.home() / ".config" / "refractum" / "settings.toml"
GLOBAL_CONF = Path("/etc/refractum.toml")
_REFLECTOR_SIMPLE_CONF = Path("/etc/reflector-simple.conf")
_REFLECTOR_CONF = Path("/etc/xdg/reflector/reflector.conf")


def load_config(path: Path | None = None) -> ReflectorOptions | None:
    """
    Load config and return ReflectorOptions, or None if no config exists.

    Search order when path is not given:
      1. ~/.config/refractum/settings.toml  (TOML)
      2. /etc/refractum.toml                (TOML)
      3. /etc/reflector-simple.conf         (reflector-simple format, imported once on first launch)
      4. /etc/xdg/reflector/reflector.conf  (reflector format, imported once on first launch)
    """
    if path is not None:
        return _load_toml(path) if path.exists() else None

    if USER_CONF.exists():
        return _load_toml(USER_CONF)
    if GLOBAL_CONF.exists():
        return _load_toml(GLOBAL_CONF)
    for legacy in (_REFLECTOR_SIMPLE_CONF, _REFLECTOR_CONF):
        if legacy.exists():
            return _bootstrap_from_reflector(legacy)
    return None


def save_user_config(opts: ReflectorOptions, path: Path = USER_CONF) -> None:
    """Save options to the personal config file (no root needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_to_toml(opts), encoding="utf-8")


def save_global_config(opts: ReflectorOptions, path: Path = GLOBAL_CONF) -> None:
    """
    Save options to the system-wide config file via pkexec (requires root).

    Raises PermissionError if the user cancels the pkexec dialog.
    Raises subprocess.CalledProcessError on other failures.
    """
    content = _to_toml(opts)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        result = subprocess.run(
            ["pkexec", "bash", "-c", f"cp {shlex.quote(str(tmp_path))} {shlex.quote(str(path))}"],
            timeout=60,
            check=False,
        )
        if result.returncode == 126:
            raise PermissionError("User cancelled the pkexec authorization dialog")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "pkexec")
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TOML read / write
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> ReflectorOptions | None:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    opts = ReflectorOptions()
    opts.countries = data.get("countries", [])
    opts.protocols = data.get("protocols", ["https"])
    opts.sort = data.get("sort", "rate")
    opts.number = int(data.get("number", 10))
    opts.use_latest = bool(data.get("use_latest", False))
    opts.latest = int(data.get("latest", 30))
    if "age" in data:
        opts.age = int(data["age"])
    opts.download_timeout = int(data.get("download_timeout", 10))
    if "threads" in data:
        opts.threads = int(data["threads"])
    opts.distro_sets = data.get("distro_sets", None)
    return opts


def _to_toml(opts: ReflectorOptions) -> str:
    lines = []
    lines.append(f"countries = {_toml_list(opts.countries)}")
    lines.append(f"protocols = {_toml_list(opts.protocols)}")
    lines.append(f'sort = "{opts.sort}"')
    lines.append(f"use_latest = {'true' if opts.use_latest else 'false'}")
    lines.append(f"number = {opts.number}")
    lines.append(f"latest = {opts.latest}")
    if opts.age is not None:
        lines.append(f"age = {opts.age}")
    lines.append(f"download_timeout = {opts.download_timeout}")
    if opts.threads is not None:
        lines.append(f"threads = {opts.threads}")
    if opts.distro_sets is not None:
        lines.append(f"distro_sets = {_toml_list(opts.distro_sets)}")
    return "\n".join(lines) + "\n"


def _toml_list(values: list[str]) -> str:
    """Serialize a list of strings as a TOML inline array."""
    items = ", ".join(f'"{v}"' for v in values)
    return f"[{items}]"


# ---------------------------------------------------------------------------
# Legacy reflector format bootstrap (read-only, first launch only)
# ---------------------------------------------------------------------------


@dataclass
class _ReflectorConfig:
    countries: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    sort: str = ""
    age: str = ""
    number: str = ""
    latest: str = ""
    download_timeout: str = ""
    threads: str = ""


def _bootstrap_from_reflector(path: Path) -> ReflectorOptions | None:
    """Parse a legacy reflector-format config into ReflectorOptions."""
    cfg = _parse_reflector_file(path)
    if cfg is None:
        return None

    opts = ReflectorOptions()
    opts.countries = cfg.countries
    opts.protocols = cfg.protocols or ["https"]
    opts.sort = cfg.sort or "rate"
    if cfg.latest:
        opts.latest = int(cfg.latest)
        opts.use_latest = True
    opts.number = int(cfg.number or 10)
    if cfg.age:
        opts.age = int(cfg.age)
    if cfg.download_timeout:
        opts.download_timeout = int(cfg.download_timeout)
    if cfg.threads:
        opts.threads = int(cfg.threads)
    return opts


def _parse_reflector_file(path: Path) -> _ReflectorConfig | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    cfg = _ReflectorConfig()
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip().strip("\"'")
        if not line:
            continue
        parts = line.split(None, 1)
        opt = parts[0]
        val = parts[1] if len(parts) > 1 else ""

        m = re.match(r"^(-[cpanl])(.+)$", opt)
        if m:
            opt, val = m.group(1), m.group(2)

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
