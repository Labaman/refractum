# Changelog

## [1.5.0] — 2026-05-26

### Added
- Native Arch mirror ranking — reflector is no longer a runtime dependency;
  mirrors are fetched directly from `archlinux.org/mirrors/status/json/` and
  tested concurrently using a thread pool
- Speed tests use `User-Agent: pacman/6.1.0` so mirrors that filter by
  User-Agent respond correctly; measurements reflect actual pacman download
  conditions rather than generic HTTP client behavior
- rsync mirrors are now speed-tested via a `rsync` subprocess, matching
  reflector's own approach
- Mirror status JSON is cached locally for 5 minutes to reduce startup time
  on repeated launches
- Metadata-only sort modes (`score`, `age`, `delay`, `country`) resolve
  instantly from cached JSON — no download tests required

### Changed
- User config migrated from reflector CLI flag format (`settings.conf`) to
  TOML (`settings.toml`); legacy `/etc/reflector.conf` and
  `/etc/reflector-simple.conf` are still read for first-launch bootstrap
- `reflector.py` renamed to `models.py` — now contains only shared data
  models (`Country`, `ReflectorOptions`)
- "Reflector options" panel renamed to "Mirror options"

### Fixed
- Detected local country was permanently injected into the country selection
  on every launch, overriding the user's saved choices
- `_detect_via_locale` regex never matched — `locale.getlocale()` returns the
  locale string without an encoding suffix, so the dot anchor was always wrong
- Section header parser now requires `##` — single-`#` lines such as speed
  annotations (`# 5.23 MB/s`) were incorrectly treated as section headers,
  breaking country filtering when falling back to a locally installed mirrorlist
- Arch mirrorlist output no longer includes unreachable mirrors

### Removed
- reflector CLI dependency — refract no longer calls reflector at runtime
- "Extra reflector args" free-form field removed from GUI

## [1.4.3] — 2026-05-25

### Fixed
- Fixed country filter for CachyOS mirrors: `code=XX`-style section headers were
  not matched because ISO country codes were not passed to the section matcher
  in the updated code path, causing all mirrors to be returned regardless of
  country selection
- Fixed incorrect fallback behavior in distro mirror filtering: an empty filtered
  result (sections present, selected country not found) was treated as "no sections"
  and fell back to all mirrors; a redundant worker-level fallback that bypassed
  the country filter entirely was also removed
- Repos without country-based mirrorlist sections (Arch Linux CN, RebornOS, Arch4edu)
  now correctly return all mirrors instead of an empty list when a country is selected
- Artix Linux mirrorlist source URL updated — previous GitHub URL was returning 404
- `Gtk.AlertDialog` async result now properly finalized in save dialogs — was
  producing GTK warnings in the log
- Country-sorted mirrorlist output no longer contains spurious `0.00 MB/s` annotations

### Changed
- Default download timeout raised from 5 s to 10 s — reduces false "unreachable"
  results for slow-responding mirrors
- Removed dead `CountryDetectionResult.method` field
- ArcoLinux dropped — project has been officially discontinued

## [1.4.2] — 2026-05-23

### Fixed
- Overall progress bar in distro ranking now updates in real time
- `https` protocol checkbox now correctly restores saved state
- `community.db` removed from fallback list (merged into `extra` in Arch 2023)
- Config and free-params files now stored consistently under `~/.config/refract/`
- `Callable` type annotations corrected in GUI modules
- `read_mirrors()` now handles `OSError` explicitly
- Mirror with response file < 1 KB now correctly marked as reachable

### Optimized
- Country detection no longer blocks app startup waiting for slow detection methods

## [1.4.1] — 2026-05-23

### Fixed
- Mirror results in distro ranking display in real time again
- Encoding parameters corrected for subprocess calls; temp files cleaned up reliably
- Unused parameters removed from `_on_ranking_done`

### Changed
- `collections.abc` imports modernized; `urllib.parse.unquote` applied where appropriate
- Code formatted with ruff

## [1.4.0] — 2026-05-22

### Added
- **Threads** control in Arch mirrors options: `--threads N` for parallel mirror
  ranking (reflector 2023+); persisted in `settings.conf`
- **Distro mirrors tab** now groups entries into **Distributions** and
  **Third-party repositories** sections

## [1.3.0] — 2026-05-22

### Fixed
- **Worldwide** selection now correctly means "no country filter" — previously
  grayed-out but still-checked countries were silently passed to reflector
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

### Optimized
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
