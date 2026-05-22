"""Entry point for `python -m refract` and the `refract` console script."""

from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path

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

from .config import load_reflector_config, save_user_config, USER_CONF
from .country_detect import detect_country
from .reflector import get_countries, ReflectorOptions
from .mirrorlist import (
    fetch_full_mirrorlist,
    annotate_with_countries,
    MIRRORLIST_PATH,
)
from .gui.main_window import MainWindow
from .gui.progress import ProgressWindow
from .gui.distro_progress import DistroProgressWindow
from .gui.preview import MirrorlistPreviewWindow


APP_ID = "org.refract.mirrors"


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

    # ------------------------------------------------------------------
    # Load reflector config and detect country
    # ------------------------------------------------------------------
    refl_cfg = load_reflector_config()

    detection = detect_country()
    local_code = detection.code if detection else "WW"

    # ------------------------------------------------------------------
    # Fetch country list
    # ------------------------------------------------------------------
    try:
        countries = get_countries()
    except RuntimeError as exc:
        _show_error(app, f"Cannot fetch country list:\n{exc}")
        return

    # ------------------------------------------------------------------
    # Build defaults from config file
    # ------------------------------------------------------------------
    defaults = ReflectorOptions()
    if refl_cfg:
        defaults.countries = refl_cfg.countries
        defaults.protocols = refl_cfg.protocols or ["https"]
        defaults.sort = refl_cfg.sort or "rate"
        if refl_cfg.latest:
            defaults.number = int(refl_cfg.latest)
            defaults.use_latest = True
        else:
            defaults.number = int(refl_cfg.number or 10)
        if refl_cfg.age:
            defaults.age = int(refl_cfg.age)
        if refl_cfg.download_timeout:
            defaults.download_timeout = int(refl_cfg.download_timeout)
        if refl_cfg.threads:
            defaults.threads = int(refl_cfg.threads)

    # Bootstrap: write own settings file on first launch so subsequent
    # launches never need to read third-party configs again.
    if not USER_CONF.exists():
        save_user_config(defaults)

    # ------------------------------------------------------------------
    # Step 1: Main selection window
    # ------------------------------------------------------------------
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
        # Convert selected country codes → names for distro mirrorlist filtering.
        # "WW" means Worldwide (no filter), so we exclude it.
        selected_codes = set(result.options.countries) - {"WW"}
        country_names: set[str] | None = None
        if selected_codes:
            country_names = {c.name for c in countries if c.code in selected_codes}

        distro_win = DistroProgressWindow(
            app=app,
            mirror_sets=result.distro_sets,
            max_workers=result.distro_workers,
            timeout=result.options.download_timeout,
            protocols=result.options.protocols or ["https"],
            max_results=result.options.number,
            country_names=country_names,
            on_done=lambda: _start_arch_ranking(app, result),
        )
        distro_win.present()
        distro_win.start()
    else:
        _start_arch_ranking(app, result)


def _start_arch_ranking(app: Gtk.Application, result) -> None:
    """Step 2b/3: Run reflector and show progress."""
    progress = ProgressWindow(
        app=app,
        options=result.options,
        expected_count=result.options.number,
        on_done=lambda path: _on_ranking_done(app, path),
    )
    progress.present()
    progress.start()


def _on_ranking_done(app, tmp_path) -> None:
    """Step 4: reflector finished — annotate and show preview."""
    if tmp_path is None:
        return

    try:
        ranked_content = Path(tmp_path).read_text(encoding="utf-8")
    except OSError as exc:
        _show_error(app, f"Cannot read ranked mirrorlist:\n{exc}")
        return
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if "Server = " not in ranked_content:
        _show_error(
            app,
            "No mirrors found!\n\n"
            "Try adjusting options:\n"
            "  • add more countries\n"
            "  • increase mirror count\n"
            "  • enable http in addition to https",
        )
        return

    # Annotate with country headers (best-effort, not fatal)
    annotated = ranked_content
    try:
        full_ml = fetch_full_mirrorlist()
        annotated = annotate_with_countries(
            ranked_content=ranked_content,
            full_mirrorlist=full_ml,
        )
    except Exception:
        pass

    # Step 5: preview + save confirmation
    win = MirrorlistPreviewWindow(
        app=app,
        content=annotated,
        dest=MIRRORLIST_PATH,
    )
    win.present()


def _show_error(app: Gtk.Application, message: str) -> None:
    print(f"[refract] ERROR: {message}", file=sys.stderr)
    windows = app.get_windows()
    if windows:
        dialog = Gtk.AlertDialog()
        dialog.set_message("refract error")
        dialog.set_detail(message)
        dialog.set_buttons(["Close"])
        dialog.show(windows[0])
    else:
        # No parent window — create a minimal standalone error window.
        win = Gtk.ApplicationWindow(application=app, title="refract error")
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
