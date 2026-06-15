# Changelog

## [1.6.6] — 2026-06-15

### Fixed
- CachyOS v3/v4 derived mirrorlists always had 0 reachable mirrors —
  `_derive_worker` performed a secondary network fetch of the v3/v4
  mirrorlist to cross-check architecture support, but any secondary fetch
  after the primary speed test is unreliable (rate limiting or timeouts
  from any source can return an empty or partial list, excluding all
  mirrors from the derived set); derived sets now use 1:1 arch-variable
  substitution from primary results with no secondary network fetch
- "Save all" button stayed permanently disabled after dismissing the issues
  dialog (shown when some mirror sets had 0 reachable mirrors and were skipped)

### Removed
- Redundant `max_results` trimming in `_derive_worker` — primary results are
  already trimmed before derive starts

## [1.6.5] — 2026-06-14

### Fixed
- Cancel check in Arch ranking now runs after every mirror result, not only on
  queue timeout — ranking could ignore cancel if all results arrived without a gap
- Post-cancel `GLib.idle_add(_on_set_done)` calls in distro ranking are now
  suppressed — idle callbacks queued before cancel fired could trigger the Save
  button after ranking was stopped
- `_derive_worker` (derived mirrorlist fetch) now checks cancel at start, after
  the network fetch, and before posting results — previously ran to completion
  even after cancel
- Save All button now disables itself on first click to prevent double-write
- `ThreadPoolExecutor` pool in `rank_mirror_set` is now created inside the `try`
  block — an exception during pool setup previously left the pool open and leaked its threads
- Fallback reachability check now uses `is not None` instead of truthiness —
  `0.0` (mirror alive, speed unmeasurable) was incorrectly treated as unreachable
- `refractum-rank --rate` now measures 4 MB sustained throughput (same method as
  the GUI) instead of TTFB from a tiny `lastupdate` file

### Removed
- `Country.count` field — was populated but never read anywhere
- `replaces=('refract')` and `conflicts=('refract')` from PKGBUILD — migration
  window from the old package name has long since passed

## [1.6.4] — 2026-06-13

### Fixed
- Cancel button in distro ranking window now correctly triggers the next step
  (Arch mirror ranking) instead of silently closing the application
- "Save as global default" now also saves the "Worldwide fallback" checkbox state
- Cancelling Arch ranking via the dialog no longer loses results when ranking
  completes between the X-click and the cancel confirmation
- `_load_toml` now prints a warning to stderr instead of silently swallowing
  broken `settings.toml` parse errors
- `make_test_url()` hardcoded `x86_64` in the speed-test URL —
  now uses `platform.machine()` so mirror testing works correctly
  on non-x86_64 architectures

### Removed
- `RankResult.test_url` field — was populated in all constructors but never read
- `MirrorlistPreviewWindow._finish_saved()` trivial wrapper — inlined as `self.close()`
- Duplicate `MIRRORLIST_PATH` constant in `rank_info.py` — now imported from `mirrorlist.py`

### Changed
- `self._total` in `ArchProgressWindow` is now set via `GLib.idle_add` to avoid a
  minor cross-thread write from the worker thread
- Fixed misleading comment about `ThreadPoolExecutor` daemon threads in `arch_progress.py`

## [1.6.3] — 2026-06-12

### Added
- **Worldwide fallback** option for distro mirrors — when no mirrors are found in
  the selected countries, the app can fall back to all worldwide mirrors:
  - *Auto mode* (new "Worldwide fallback" checkbox in distro options): silently uses
    all mirrors and shows a persistent warning bar in the ranking window
  - *Manual mode* (default): shows a one-time confirmation dialog with a "Set as
    default" checkbox to switch to auto mode permanently
  - Setting is saved to user config and restored on next launch

### Fixed
- Plain Title-Case country headers (`# Germany`, `# Czech Republic`) now recognized
  in Artix and BlackArch mirrorlists — previously only `## Germany (DE)` and
  `# Germany (DE)` formats were parsed, causing country filtering to silently return
  all mirrors for those repos
- Single-hash (`# Russia (RU)`) and plain-name (`# Germany`) section headers are
  now also recognized by `get_template_countries`, so country labels in ranked
  results are correct for Artix, BlackArch, and Chaotic-AUR
- arch4edu country filter re-enabled — it was disabled as a workaround for a
  parser limitation that is now fixed
- Country filtering no longer silently returns an empty list when sections exist but
  the selected country is absent — the fallback path notifies the user instead of
  producing zero results without explanation
- Ranking window no longer freezes when a pool worker thread throws an unexpected
  exception — the collector loop would previously spin forever waiting for a result
  that was never put on the queue; any exception is now caught and the mirror is
  recorded as unreachable
- WM close (Alt+F4 / compositor X) on the worldwide-fallback dialog now releases
  waiting workers — previously this left `_fallback_proceed` unset and all pending
  workers deadlocked
- Cancelling distro ranking while a mirrorlist fetch is in flight no longer produces
  a zombie fallback dialog after the fetch completes
- Cancelling distro ranking now immediately unblocks workers that were waiting for
  the fallback dialog instead of leaving them blocked until the next 0.5 s poll

### Removed
- Dead `Country.__str__` method (no callers; `.name` and `.code` were always
  accessed directly)

## [1.6.2] — 2026-06-08

### Added
- Indeterminate progress bar animation while the mirror list is being fetched
- Closing a ranking window mid-test now shows a confirmation dialog; confirming
  cancels the test and exits immediately without hanging

### Fixed
- Slow mirrors no longer stall ranking indefinitely — a wall-clock cap now
  limits total download time per mirror regardless of trickle speed (the
  socket timeout only fires when no bytes arrive, not on total elapsed time)
- App no longer hangs on exit after cancelling ranking — worker threads are
  now daemon threads and are killed immediately when the process exits
- Chaotic-AUR country filter: single-hash `# Country (XX)` section markers
  are now recognized; previously any country selection returned 0 mirrors
- Third-party repo checkboxes (e.g. Chaotic-AUR) are now auto-detected and
  pre-selected on first launch; the selection is persisted across launches
- Unchecking all repos is now remembered — an empty saved selection was
  previously treated as "not yet saved" and reset to auto-select on next launch
- Rapid double close-request no longer spawns two confirmation dialogs
- Pulse animation correctly stops in all exit paths of the Arch ranking window

### Changed
- Sort dropdown replaced deprecated `Gtk.ComboBoxText` with `Gtk.DropDown`

## [1.6.1] — 2026-05-31

### Fixed
- Split "Latest synced" pool size and result count into separate settings —
  previously both used the same `number` value, causing fast mirrors to be
  excluded before the speed test. Pool size is now configurable (default: 30).
- Speed test now downloads 4 MB per mirror to skip CDN burst zones.
- Mirror count in the preview window now counts correctly for all mirrorlist formats.
- Fixed stale "Generated by refract" header in generated mirrorlists.

## [1.6.0] — 2026-05-30

### Changed
- **Renamed the project to `refractum`** to avoid clashing with other tools
  named "refract" on AUR, PyPI and GitHub
  - command: `refract` → `refractum` (and `refract-rank` → `refractum-rank`)
  - application ID: `io.github.Labaman.refractum` (freedesktop reverse-DNS)
  - icon and desktop file: `io.github.Labaman.refractum.{svg,desktop}`
  - config: `~/.config/refractum/`, cache: `~/.cache/refractum/`,
    system config: `/etc/refractum.toml`
- Window icon now resolves automatically via GTK4 app-ID lookup; explicit
  `set_icon_name()` calls removed from all windows

### Migration
- The package `replaces` the old `refract` package, so `pacman -Syu` / AUR
  helpers switch over automatically; the post-upgrade hook removes the old
  icon and desktop files
- Settings are not carried over from the old `refract` config directory — pick
  your countries and options once on first launch

## [1.5.2] — 2026-05-29

### Fixed
- Window icon now correctly displays the refract icon instead of the default
  Wayland "W" placeholder — added `set_icon_name("refract")` to override
  GTK4's app-ID-based icon lookup

## [1.5.1] — 2026-05-28

### Fixed
- Country detection now tries methods sequentially (ipinfo → geoiplookup →
  locale) instead of concurrently — concurrent mode always returned the locale
  result first since it completes instantaneously, ignoring more accurate
  geolocation methods

### Removed
- rsync protocol option removed from the Arch mirrors tab — pacman has never
  supported rsync mirrorlist entries (libcurl limitation); the option was
  producing "Protocol `rsync` not supported" errors in pacman

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
