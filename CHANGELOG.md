# Changelog

## [1.4.0] — 2026-05-22

### Added
- **Threads** control in Arch mirrors options: `--threads N` for parallel mirror
  ranking (reflector 2023+); persisted in `settings.conf`
- **Distro mirrors tab** now groups entries into **Distributions** and
  **Third-party repositories** sections

## [1.3.0] — 2026-05-22

### Fixed
- **Worldwide** selection now correctly means "no country filter" — previously
  greyed-out but still-checked countries were silently passed to reflector
- **Worldwide** persisted in `settings.conf` and restored on next launch
- Country grid filled column-by-column so each column reads alphabetically
  top-to-bottom
- Country names with accented characters sort correctly
  (`Réunion` before `Romania`/`Russia`, `Türkiye` in correct T-position)
- Distro mirror speed measurement now starts after the first response chunk,
  excluding DNS, TLS handshake, and TTFB — measures pure download throughput
- X-button unblocked after reflector exits with an error
- Removed debug log output left in production code
- `assert process.stderr is not None` replaced with `RuntimeError`
  (assertions are disabled by `python -O`)
- Double-slash in speed-test URLs for empty `test_repo` (RebornOS, Arch4edu)

### Optimised
- Test download size 200 KB → 500 KB; chunk size 8 KB → 64 KB for more
  stable throughput readings on fast mirrors
- Removed dead code: `excluded_countries`, `_diff_text`, `CountryDetectionResult.name`
- Commented-server and ISO-2 code regex patterns compiled once at module level
- Paths in pkexec bash scripts quoted with `shlex.quote`
- `future.result()` in ranker wrapped in `try/except`
- `refract-rank`: mirror testing now concurrent (`ThreadPoolExecutor`)

## [1.1.1] — 2026-05-21

### Added
- **Settings persistence** — options are saved to `~/.config/refract/settings.conf`
  on every OK click and restored on next launch
- **Sync recency** control in Arch mirrors options: **Max age (h)** (`--age N`)
  or **Latest synced** (`--latest N`), mutually exclusive; defaults to Max age 24 h
- **Global config** `/etc/refract.conf` — admins can set system-wide defaults via
  the new **Save as global default** button (requires root via pkexec)

### Fixed
- Countries are now displayed in alphabetical order
- Multi-country ranking: default mode uses `--age N` + `--number N` so mirrors
  from all selected countries are considered (previously `--latest N` could
  exclude entire countries by filling the pool from the most active ones)
- Selecting **Worldwide** now unchecks all individual countries and vice versa

### Changed
- First-launch bootstrap reads the first available config in order:
  `/etc/refract.conf` → `/etc/reflector-simple.conf` →
  `/etc/xdg/reflector/reflector.conf` → built-in defaults;
  result is written to `~/.config/refract/settings.conf` immediately so
  external configs are never read again

## [0.8.3] — 2026-05-21 — Initial release

- GUI tool for ranking pacman mirrors on Arch Linux and Arch-based distributions
- Built-in speed-testing engine for distro-specific mirrors: CachyOS
  (x86\_64 / v3 / v4), EndeavourOS, Artix, BlackArch, RebornOS, ArcoLinux
- Third-party repo mirror support: Chaotic-AUR, Arch Linux CN, Arch4edu
- Live progress with per-mirror speed display
- Country filter for both Arch and distro mirrors
- Auto-detection of the current distro for checkbox pre-selection
- Preview + unified diff before saving
- Single `pkexec` call for all mirrorlist files
