# refract

GUI tool for ranking pacman mirrors on Arch Linux and Arch-based distributions.

![refract icon](refract.svg)

## Features

- **Arch mirrors** — ranks mirrors via [reflector](https://wiki.archlinux.org/title/Reflector) with a graphical country selector and full option control
- **Distro mirrors** — speed-tests mirrors for CachyOS, EndeavourOS, Artix, BlackArch, Chaotic-AUR, Arch Linux CN, Arch4edu, RebornOS, ArcoLinux
- **Live progress** — results appear in real time as each mirror is tested
- **Country filter** — applies to both Arch and distro mirrors
- **Auto-detection** — pre-selects the current distro's mirror set automatically
- **Preview + diff** — shows the new mirrorlist with syntax highlighting and a diff against the current file before saving
- **Single sudo prompt** — all mirrorlist files are saved in one `pkexec` call, no matter how many

## Supported mirrors

### Distributions

| Distro | Mirrorlist file |
|---|---|
| Arch Linux | `/etc/pacman.d/mirrorlist` (via reflector) |
| CachyOS (x86\_64 / v3 / v4) | `/etc/pacman.d/cachyos-mirrorlist` + derived |
| EndeavourOS | `/etc/pacman.d/endeavouros-mirrorlist` |
| Artix Linux | `/etc/pacman.d/artix-mirrorlist` |
| BlackArch Linux | `/etc/pacman.d/blackarch-mirrorlist` |
| RebornOS | `/etc/pacman.d/reborn-mirrorlist` |
| ArcoLinux | `/etc/pacman.d/arcolinux-mirrorlist` |

### Third-party repositories

| Repository | Mirrorlist file |
|---|---|
| Chaotic-AUR | `/etc/pacman.d/chaotic-mirrorlist` |
| Arch Linux CN | `/etc/pacman.d/archlinuxcn-mirrorlist` |
| Arch4edu | `/etc/pacman.d/arch4edu-mirrorlist` |

Only mirrorlist files present on the system are active; the rest are shown greyed out.

## Requirements

- Python ≥ 3.11
- GTK4 (`gtk4`)
- PyGObject (`python-gobject`)
- reflector (`reflector`)
- polkit (`polkit`)
- python-requests (`python-requests`)

## Installation

### Via pacman (recommended)

```bash
cd /path/to/refract
makepkg -si
```

This builds and installs the package, adds `refract` to `/usr/bin`, and registers the application in the system menu.

### Development install

```bash
pip install --user -e .
```

## Usage

Launch from the application menu or run:

```bash
refract
```

**Arch mirrors tab** — select countries, protocols, sort order and mirror count, then click OK to run reflector. The result is shown in a preview window before saving.

**Distro mirrors tab** — select which distro mirror sets to re-rank. Rankings run concurrently; CachyOS v3/v4 are derived from the x86\_64 results without redundant network tests.

## Configuration

Refract reads reflector configuration on startup to pre-populate the Arch mirrors tab (countries, protocols, sort order, mirror count). It checks the following files in order, using the first one found:

1. `/etc/xdg/reflector/reflector.conf` — reflector's own config
2. `/etc/reflector-simple.conf` — reflector-simple config (if you use that tool alongside)

If neither file exists, built-in defaults are used.

## Acknowledgements

Refract was inspired by two existing tools:

- **[reflector-simple](https://github.com/endeavouros-team/PKGBUILDS/tree/master/reflector-simple)** — shell-based GUI wrapper for reflector, part of the EndeavourOS project
- **[rate-mirrors](https://github.com/westandskif/rate-mirrors)** — fast, map-aware mirror ranking tool that inspired the distro mirror speed-testing approach
