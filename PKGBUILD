# Maintainer: Mikhail <efklid@gmail.com>
# shellcheck disable=SC2034,SC2154
pkgname=refract
pkgver=1.1.1
pkgrel=1
pkgdesc="GUI tool for ranking pacman mirrors on Arch Linux and Arch-based distributions"
arch=('any')
url="https://github.com/Labaman/refract"
license=('MIT')
depends=(
    'python'
    'python-requests'
    'python-gobject'
    'gtk4'
    'reflector'
    'polkit'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-hatchling'
)
options=('!strip')

build() {
    cd "$startdir" || return 1
    python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

package() {
    python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl

    install -Dm644 "$startdir/refract.desktop" \
        "$pkgdir/usr/share/applications/refract.desktop"

    install -Dm644 "$startdir/refract.svg" \
        "$pkgdir/usr/share/icons/hicolor/scalable/apps/refract.svg"
}
