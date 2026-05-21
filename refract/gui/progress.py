"""
Progress window shown while reflector ranks mirrors — GTK4 / PyGObject.

Threading model:
  - reflector runs in a background thread
  - the thread calls GLib.idle_add() to schedule UI updates on the main loop
"""

from __future__ import annotations

import tempfile
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..reflector import ReflectorOptions, build_command, run_reflector


class ProgressWindow(Gtk.Window):
    """
    Window that shows reflector's output and a progress bar.

    Usage:
        win = ProgressWindow(app, options, expected_mirror_count)
        win.present()
        # The window calls on_done(output_path) or on_done(None) when finished.
        # Connect to the 'ranking-done' signal or pass a callback.
    """

    def __init__(
        self,
        app: Gtk.Application,
        options: ReflectorOptions,
        expected_count: int = 10,
        on_done: "callable[[str | None], None] | None" = None,
    ) -> None:
        super().__init__(application=app, title="Ranking mirrors…")
        self.set_default_size(900, 500)
        self.set_resizable(True)

        self._options = options
        self._expected = max(1, expected_count)
        self._on_done = on_done
        self._mirror_count = 0
        self._output_file: str | None = None

        # Prevent closing during ranking
        self.connect("close-request", self._on_close_request)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.set_margin_start(8)
        root.set_margin_end(8)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        self.set_child(root)

        # Status label
        self._status_label = Gtk.Label(label="Starting reflector…", xalign=0)
        root.append(self._status_label)

        # Progress bar
        self._progress = Gtk.ProgressBar()
        self._progress.set_fraction(0.0)
        self._progress.set_show_text(True)
        root.append(self._progress)

        # Scrollable log
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)

        self._log_buffer = Gtk.TextBuffer()
        self._log_view = Gtk.TextView(buffer=self._log_buffer)
        self._log_view.set_editable(False)
        self._log_view.set_monospace(True)
        self._log_view.set_wrap_mode(Gtk.WrapMode.NONE)
        scroll.set_child(self._log_view)
        root.append(scroll)

        # Button row (populated after completion)
        self._btn_box = Gtk.Box(spacing=8)
        self._btn_box.set_halign(Gtk.Align.END)
        self._btn_box.set_margin_top(4)
        root.append(self._btn_box)

    # ------------------------------------------------------------------
    # Public: start ranking
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker thread. Call after present()."""
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    # ------------------------------------------------------------------
    # Worker thread — runs in background, NEVER touches GTK widgets directly
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        """
        Run reflector and post UI updates via GLib.idle_add().

        GLib.idle_add(fn, arg) is thread-safe: it queues fn(arg) to run
        in the GTK main loop on the next idle cycle. This is the standard
        GTK way to update the UI from a background thread — no manual
        queue needed.
        """
        cmd = build_command(self._options)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".mirrorlist", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            gen = run_reflector(cmd, tmp_path)
            returncode = 0
            try:
                while True:
                    line = next(gen)
                    # Post UI update to main thread
                    GLib.idle_add(self._append_log, line)
                    if "rating" in line.lower() or "sorting" in line.lower():
                        GLib.idle_add(self._advance_progress, line)
            except StopIteration as stop:
                if stop.value is not None:
                    returncode = stop.value

        except Exception as exc:
            GLib.idle_add(self._on_error, str(exc))
            return

        GLib.idle_add(self._on_ranking_done, returncode, tmp_path)

    # ------------------------------------------------------------------
    # UI update callbacks — always called in the GTK main loop
    # ------------------------------------------------------------------

    def _append_log(self, line: str) -> bool:
        """
        Append a line to the log TextView.

        Returns False so GLib.idle_add won't reschedule this callback.
        (If a callback returns True, GLib keeps calling it repeatedly —
        that's useful for timers but not here.)
        """
        end_iter = self._log_buffer.get_end_iter()
        self._log_buffer.insert(end_iter, line + "\n")

        # Auto-scroll to the bottom
        adj = self._log_view.get_vadjustment()
        adj.set_value(adj.get_upper())

        return False  # don't reschedule

    def _advance_progress(self, line: str) -> bool:
        self._mirror_count += 1
        fraction = min(0.99, self._mirror_count / self._expected)
        self._progress.set_fraction(fraction)
        self._progress.set_text(line[:80])
        self._status_label.set_text(line[:100])
        return False

    def _on_ranking_done(self, returncode: int, tmp_path: str) -> bool:
        self._progress.set_fraction(1.0)
        self._progress.set_text("Done")

        if returncode == 0:
            self._output_file = tmp_path
            self._status_label.set_text("Ranking complete.")
            self._show_buttons(success=True)
        else:
            self._status_label.set_text(f"reflector exited with code {returncode}.")
            self._show_buttons(success=False)

        # Re-enable window close
        self.connect("close-request", lambda _: False)
        return False

    def _on_error(self, message: str) -> bool:
        self._status_label.set_text(f"Error: {message}")
        self._append_log(f"\n[ERROR] {message}")
        self._show_buttons(success=False)
        return False

    def _show_buttons(self, success: bool) -> None:
        if success:
            discard = Gtk.Button(label="Discard")
            discard.connect("clicked", lambda _: self._finish(None))
            self._btn_box.append(discard)

            ok = Gtk.Button(label="Continue")
            ok.add_css_class("suggested-action")
            ok.connect("clicked", lambda _: self._finish(self._output_file))
            self._btn_box.append(ok)
        else:
            close = Gtk.Button(label="Close")
            close.connect("clicked", lambda _: self._finish(None))
            self._btn_box.append(close)

    def _finish(self, result: str | None) -> None:
        if self._on_done:
            self._on_done(result)
        self.close()

    # ------------------------------------------------------------------
    # Prevent accidental close during ranking
    # ------------------------------------------------------------------

    def _on_close_request(self, _win: Gtk.Window) -> bool:
        """Return True to block the close, False to allow it."""
        return self._output_file is None and self._progress.get_fraction() < 1.0
