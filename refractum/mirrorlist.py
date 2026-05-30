"""
Mirrorlist saving utilities.

Saves one or more mirrorlist files via a single pkexec invocation
to avoid repeated password prompts.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

MIRRORLIST_PATH = Path("/etc/pacman.d/mirrorlist")


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

    tmp_pairs: list[tuple[Path, Path]] = []
    try:
        for content, dest in files:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".mirrorlist", delete=False) as tmp:
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
            timeout=60,
            check=False,
        )
        if result.returncode == 126:
            raise PermissionError("User cancelled the pkexec authorization dialog")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "pkexec")
    finally:
        for tmp_path, _ in tmp_pairs:
            tmp_path.unlink(missing_ok=True)
