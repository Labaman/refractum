# refract

GUI tool for ranking pacman mirrors on Arch Linux and Arch-based distributions.

![refract icon](refract.svg)

## Features

- **Arch mirrors** — fetches mirror data directly from `archlinux.org/mirrors/status/json/` and ranks by download speed; no reflector required
- **Distro mirrors** — ranks distro-specific and third-party repo mirrors by download speed
- **Live progress** — results appear in real time as each mirror is tested
- **Country filter** — applies to both Arch and distro mirrors
- **Parallel testing** — configurable thread count for faster ranking
- **pacman User-Agent** — speed tests identify as pacman so mirrors that filter by User-Agent respond correctly
- **Auto-detection** — pre-selects the current distro's mirror set automatically
- **Preview + diff** — shows the new mirrorlist with syntax highlighting and a diff against the current file before saving

## Supported mirrors

### Distributions

| Distro | Mirrorlist file |
| --- | --- |
| Arch Linux | `/etc/pacman.d/mirrorlist` |
| CachyOS (x86\_64 / v3 / v4) | `/etc/pacman.d/cachyos-mirrorlist` + derived |
| EndeavourOS | `/etc/pacman.d/endeavouros-mirrorlist` |
| Artix Linux | `/etc/pacman.d/artix-mirrorlist` |
| BlackArch Linux | `/etc/pacman.d/blackarch-mirrorlist` |
| RebornOS | `/etc/pacman.d/reborn-mirrorlist` |

### Third-party repositories

| Repository | Mirrorlist file |
| --- | --- |
| Chaotic-AUR | `/etc/pacman.d/chaotic-mirrorlist` |
| Arch Linux CN | `/etc/pacman.d/archlinuxcn-mirrorlist` |
| Arch4edu | `/etc/pacman.d/arch4edu-mirrorlist` |

Only mirrorlist files present on the system are active; the rest are shown grayed out.

## Requirements

- Python ≥ 3.11
- GTK4 (`gtk4`)
- PyGObject (`python-gobject`)
- polkit (`polkit`)
- python-requests (`python-requests`)

**Optional:**
- `geoip` — improves country auto-detection via `geoiplookup`

## Installation

### AUR (recommended)

```bash
yay -S refract
```

Or with any other AUR helper (`paru`, `trizen`, etc.).

### Pre-built package

Download the `.pkg.tar.zst` from the [latest release](https://github.com/Labaman/refract/releases/latest) and install it:

```bash
sudo pacman -U refract-*.pkg.tar.zst
```

### From source

Make sure `base-devel` and `git` are installed:

```bash
sudo pacman -S --needed git base-devel
git clone https://github.com/Labaman/refract.git
cd refract
makepkg -si
```

### Development install

```bash
pip install --user -e .
```

## Usage

Launch from the application menu or run:

```bash
refract
```

**Arch mirrors tab** — select countries, protocols, sort order and mirror count, then click OK. Mirror data is fetched from `archlinux.org/mirrors/status/json/` (cached for 5 minutes) and tested concurrently. The result is shown in a preview window before saving.

**Distro mirrors tab** — select which distro mirror sets to re-rank. Rankings run concurrently; CachyOS v3/v4 are derived from the x86\_64 results without redundant network tests.

## Configuration

Refract stores personal settings in `~/.config/refract/settings.toml`, written
automatically on every OK click and restored on the next launch.

The **Save as global default** button writes the current settings to
`/etc/refract.toml` (requires root via pkexec). This lets an admin set
system-wide defaults that new users inherit on their first launch.

On first run, if no personal settings file exists yet, initial values are
bootstrapped from the first available source (checked in order):

1. `/etc/refract.toml` — refract's own system-wide config (TOML)
2. `/etc/reflector-simple.conf` — reflector-simple config (imported once on first launch, read-only)
3. `/etc/xdg/reflector/reflector.conf` — reflector config (imported once on first launch, read-only)
4. Built-in defaults

The bootstrapped settings are saved immediately to `~/.config/refract/settings.toml`,
so external config files are never read again after the first launch.

## Acknowledgements

Refract was inspired by two existing tools:

- **[reflector-simple](https://github.com/endeavouros-team/PKGBUILDS/tree/master/reflector-simple)** — shell-based GUI wrapper for reflector, part of the EndeavourOS project
- **[rate-mirrors](https://github.com/westandskif/rate-mirrors)** — fast, map-aware mirror ranking tool that inspired the distro mirror speed-testing approach

## License

This project is licensed under [GPL-3.0-or-later](LICENSE). This license applies retroactively to all previous releases (v0.8.3 and later).
