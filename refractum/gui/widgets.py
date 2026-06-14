"""Shared GTK widget helpers reused across progress windows."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango  # noqa: E402

from ..ranker import RankResult


def defer_until_mapped(window: Gtk.Window, callback: Callable[[], None]) -> None:
    """Call callback now if the window is already mapped, otherwise defer to the 'map' signal.

    Prevents GtkGizmo snapshot-without-allocation GTK warnings when widgets are
    modified before the window has been rendered for the first time.
    """
    if window.get_mapped():
        callback()
    else:
        hid = None

        def _on_map(_win: Gtk.Window) -> None:
            window.disconnect(hid)
            callback()

        hid = window.connect("map", _on_map)


def make_rank_result_row(
    result: RankResult,
    country: str,
    *,
    show_no_data: bool = False,
) -> Gtk.ListBoxRow:
    """Build a ListBoxRow for one ranked mirror result.

    Args:
        result:       The speed-test result.
        country:      ISO-2 code or country name to show in the country column.
        show_no_data: When True and the mirror is reachable but untested (speed=0),
                      show "up (no data)" instead of "—".  Used by the distro path
                      for non-country sort modes where speed=0 is unexpected.
    """
    row = Gtk.ListBoxRow()
    box = Gtk.Box(spacing=12)
    box.set_margin_start(6)
    box.set_margin_end(6)
    box.set_margin_top(2)
    box.set_margin_bottom(2)

    if result.reachable and result.speed > 0:
        speed_mb = result.speed / (1024 * 1024)
        speed_lbl = Gtk.Label(label=f"{speed_mb:6.2f} MB/s", xalign=1, width_chars=12)
        speed_lbl.add_css_class("success" if speed_mb > 1.0 else "warning")
    elif result.reachable:
        if show_no_data:
            speed_lbl = Gtk.Label(label="up (no data)", xalign=1, width_chars=12)
            speed_lbl.add_css_class("warning")
        else:
            speed_lbl = Gtk.Label(label="—", xalign=1, width_chars=12)
            speed_lbl.add_css_class("dim-label")
    else:
        speed_lbl = Gtk.Label(label="unreachable", xalign=1, width_chars=12)
        speed_lbl.add_css_class("error")

    country_lbl = Gtk.Label(label=country, xalign=0, width_chars=4)
    country_lbl.add_css_class("dim-label")
    country_lbl.set_visible(bool(country))

    url_lbl = Gtk.Label(label=result.template, xalign=0, hexpand=True)
    url_lbl.set_ellipsize(Pango.EllipsizeMode.END)

    box.append(speed_lbl)
    box.append(country_lbl)
    box.append(url_lbl)
    row.set_child(box)
    return row


def show_cancel_dialog(
    parent: Gtk.Window,
    on_dismiss: Callable[[], None],
    on_confirm: Callable[[], None],
) -> None:
    """Show the 'Cancel mirror ranking?' AlertDialog.

    on_dismiss is called on every response (including Cancel/dismiss).
    on_confirm is called only when the user chooses 'Cancel ranking'.
    """
    dialog = Gtk.AlertDialog()
    dialog.set_message("Cancel mirror ranking?")
    dialog.set_detail("Testing is still in progress. Results will be discarded.")
    dialog.set_buttons(["Continue", "Cancel ranking"])
    dialog.set_cancel_button(0)
    dialog.set_default_button(0)

    def _on_response(src: Gtk.AlertDialog, result: Gtk.AsyncResult) -> None:
        on_dismiss()
        try:
            idx = dialog.choose_finish(result)
        except Exception:
            return
        if idx == 1:
            on_confirm()

    dialog.choose(parent, None, _on_response)
