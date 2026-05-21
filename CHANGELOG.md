# Changelog

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
