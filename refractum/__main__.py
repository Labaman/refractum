"""Entry point for `python -m refractum` and the `refractum` console script."""

from __future__ import annotations

import ctypes
import sys
import traceback

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402


def _log_writer(log_level, fields, n_fields, user_data):  # pylint: disable=unused-argument
    # GTK4 bug: scrollbar slider reports min size -2 before CSS is applied.
    # Harmless — GTK clamps it to 0 internally. Suppress to avoid noise.
    for field in fields:
        if field.key == "MESSAGE":
            try:
                length = field.length
                if length == -1:
                    msg = ctypes.string_at(field.value).decode("utf-8", errors="replace")
                else:
                    msg = ctypes.string_at(field.value, length).decode("utf-8", errors="replace")
                if "GtkGizmo" in msg and "slider" in msg:
                    return GLib.LogWriterOutput.HANDLED
            except Exception:
                pass
    return GLib.log_writer_default(log_level, fields)


GLib.log_set_writer_func(_log_writer, None)

from .config import load_config, save_user_config, USER_CONF
from .country_detect import detect_country
from .arch_mirrors import get_countries
from .mirrorlist import MIRRORLIST_PATH
from .models import ReflectorOptions
from .gui.main_window import MainWindow
from .gui.arch_progress import ArchProgressWindow
from .gui.distro_progress import DistroProgressWindow
from .gui.preview import MirrorlistPreviewWindow


APP_ID = "io.github.Labaman.refractum"


def main() -> None:
    app = Gtk.Application(application_id=APP_ID)
    app.connect("activate", _on_activate)
    sys.exit(app.run(None))


def _on_window_removed(app: Gtk.Application, _window) -> None:
    """Quit when the last window is closed — handles X-button and all exit paths."""
    if not app.get_windows():
        app.quit()


def _on_activate(app: Gtk.Application) -> None:
    """Called by GTK when the application starts."""
    app.connect("window-removed", _on_window_removed)

    detection = detect_country()
    local_code = detection.code if detection else "WW"

    try:
        countries = get_countries()
    except RuntimeError as exc:
        _show_error(app, f"Cannot fetch mirror list:\n{exc}")
        return

    defaults = load_config() or ReflectorOptions()

    if not USER_CONF.exists():
        save_user_config(defaults)

    _show_main_window(app, countries, local_code, defaults)


def _show_main_window(app, countries, local_code, defaults) -> None:
    def _on_result(result) -> None:
        try:
            _handle_main_result(app, result, countries)
        except Exception:
            traceback.print_exc()
            _show_error(app, f"Unexpected error:\n{traceback.format_exc()}")

    win = MainWindow(
        app=app,
        countries=countries,
        local_code=local_code,
        defaults=defaults,
        on_result=_on_result,
    )
    win.present()


def _handle_main_result(app, result, countries) -> None:
    if result.cancelled:
        return

    # Step 2a: distro mirror ranking (if any sets selected)
    if result.distro_sets:
        selected_codes = set(result.options.countries) - {"WW"}
        country_names: set[str] | None = None
        if selected_codes:
            country_names = {c.name for c in countries if c.code in selected_codes}

        distro_win = DistroProgressWindow(
            app=app,
            mirror_sets=result.distro_sets,
            max_workers=result.options.threads or 5,
            timeout=result.options.download_timeout,
            protocols=result.options.protocols or ["https"],
            max_results=result.options.number,
            country_names=country_names,
            country_codes=selected_codes if selected_codes else None,
            # Distro mirrors have no score/age/delay metadata — normalize to "rate".
            # "country" takes the fast path (no speed test, alphabetical by country).
            sort_by=result.options.sort if result.options.sort in ("rate", "country") else "rate",
            ww_fallback_auto=result.options.distro_ww_fallback,
            on_done=lambda: _start_arch_ranking(app, result),
        )
        distro_win.present()
        distro_win.start()
    else:
        _start_arch_ranking(app, result)


def _start_arch_ranking(app: Gtk.Application, result) -> None:
    """Step 2b/3: Rank Arch mirrors using our own speed tester."""
    progress = ArchProgressWindow(
        app=app,
        options=result.options,
        on_done=lambda content: _on_ranking_done(app, content),
    )
    progress.present()
    progress.start()


def _on_ranking_done(app, content: str | None) -> None:
    """Step 4: ranking finished — show preview for confirmation."""
    if content is None:
        return

    if "Server = " not in content:
        _show_error(
            app,
            "No mirrors found!\n\n"
            "Try adjusting options:\n"
            "  • add more countries\n"
            "  • increase mirror count\n"
            "  • enable http in addition to https",
        )
        return

    win = MirrorlistPreviewWindow(
        app=app,
        content=content,
        dest=MIRRORLIST_PATH,
    )
    win.present()


def _show_error(app: Gtk.Application, message: str) -> None:
    print(f"[refractum] ERROR: {message}", file=sys.stderr)
    windows = app.get_windows()
    if windows:
        dialog = Gtk.AlertDialog()
        dialog.set_message("refractum error")
        dialog.set_detail(message)
        dialog.set_buttons(["Close"])
        dialog.show(windows[0])
    else:
        win = Gtk.ApplicationWindow(application=app, title="refractum error")
        win.set_default_size(480, 200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        lbl = Gtk.Label(label=message, wrap=True, xalign=0)
        btn = Gtk.Button(label="Close")
        btn.connect("clicked", lambda _: win.close())
        box.append(lbl)
        box.append(btn)
        win.set_child(box)
        win.present()


if __name__ == "__main__":
    main()
