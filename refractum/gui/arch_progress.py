"""
Progress window for Arch Linux mirror speed ranking.

Fetches mirror data from archlinux.org/mirrors/status/json/ and tests
each mirror concurrently.

Sort modes:
  "rate"    — concurrent download speed test, live results table
  "score" / "age" / "delay" / "country"
            — metadata-only sort from JSON, near-instant, no downloads
"""

from __future__ import annotations

import queue
import threading
import traceback
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..arch_mirrors import ArchMirror, fetch_mirrors, sort_no_test, format_mirrorlist
from ..ranker import RankResult, test_mirror_speed
from ..models import ReflectorOptions


class ArchProgressWindow(Gtk.Window):
    """
    Ranks Arch mirrors and shows live results.

    For sort_by="rate": tests all mirrors concurrently and updates the table
    as each result arrives.
    For other sort modes: sorts by JSON metadata instantly, no downloads.
    """

    def __init__(
        self,
        app: Gtk.Application,
        options: ReflectorOptions,
        on_done: Callable[[str | None], None] | None = None,
    ) -> None:
        super().__init__(application=app, title="Ranking Arch mirrors…")
        self.set_default_size(900, 550)

        self._options = options
        self._on_done = on_done

        self._results: list[tuple[RankResult, ArchMirror]] = []
        self._total = 0
        self._done = False
        self._cancelled = False
        self._closing = False
        self._pulse_timer: int | None = None

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

        self._status = Gtk.Label(label="Fetching mirror list…", xalign=0)
        root.append(self._status)

        pb_row = Gtk.Box(spacing=6)
        self._progress = Gtk.ProgressBar()
        self._progress.set_hexpand(True)
        self._pb_label = Gtk.Label(label="", xalign=0)
        pb_row.append(self._progress)
        pb_row.append(self._pb_label)
        root.append(pb_row)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)
        scroll.set_child(self._list_box)
        root.append(scroll)

        self._btn_box = Gtk.Box(spacing=8)
        self._btn_box.set_halign(Gtk.Align.END)
        self._btn_box.set_margin_top(4)
        root.append(self._btn_box)

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker thread. Waits for map signal if the window isn't
        allocated yet — prevents GtkGizmo snapshot-without-allocation warnings."""

        def _begin() -> None:
            self._pulse_timer = GLib.timeout_add(80, self._pulse_progress)
            threading.Thread(target=self._worker, daemon=True).start()

        if self.get_mapped():
            _begin()
        else:
            hid = None

            def _on_map(_win):
                self.disconnect(hid)
                _begin()

            hid = self.connect("map", _on_map)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        opts = self._options
        selected_codes = set(opts.countries) - {"WW"}

        try:
            mirrors = fetch_mirrors(
                countries=list(selected_codes) if selected_codes else None,
                protocols=opts.protocols or ["https"],
                age_hours=opts.age,
                use_latest=opts.latest if opts.use_latest else None,
            )
        except Exception as exc:
            GLib.idle_add(self._on_error, str(exc))
            return

        if not mirrors:
            GLib.idle_add(self._on_no_mirrors)
            return

        GLib.idle_add(self._set_total, len(mirrors))
        sort_by = opts.sort

        # Metadata-only sorts: no downloads, near-instant
        if sort_by in ("score", "age", "delay", "country"):
            sorted_mirrors = sort_no_test(mirrors, sort_by)[: opts.number]
            results = [
                (
                    RankResult(
                        template=m.server_template,
                        speed=0.0,
                        reachable=True,
                        country=m.country_code,
                    ),
                    m,
                )
                for m in sorted_mirrors
            ]
            GLib.idle_add(self._set_total, len(results))
            for r, m in results:
                GLib.idle_add(self._on_mirror_result, r, m)
            GLib.idle_add(self._on_all_done)
            return

        # Rate sort: daemon thread pool + result queue.
        # A plain ThreadPoolExecutor (with-block) blocks in __exit__ until all futures
        # complete — that would hang here if the user closes the window mid-test.
        # Daemon threads are killed when the process exits without any blocking.
        GLib.idle_add(self._status.set_text, f"Testing {len(mirrors)} mirrors…")
        max_workers = opts.threads or 5
        timeout = float(opts.download_timeout)

        work_q: queue.Queue = queue.Queue()
        result_q: queue.Queue = queue.Queue()
        _sentinel = object()

        def _pool_worker() -> None:
            while True:
                item = work_q.get()
                if item is _sentinel:
                    return
                m: ArchMirror = item
                try:
                    speed = None if self._cancelled else test_mirror_speed(m.make_test_url(), timeout)
                except Exception:
                    speed = None
                result_q.put((m, speed))

        for m in mirrors:
            work_q.put(m)
        for _ in range(max_workers):
            work_q.put(_sentinel)
            threading.Thread(target=_pool_worker, daemon=True).start()

        try:
            received = 0
            total = len(mirrors)
            while received < total and not self._cancelled:
                try:
                    m, speed = result_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                received += 1
                r = RankResult(
                    template=m.server_template,
                    speed=speed or 0.0,
                    reachable=speed is not None,
                    country=m.country_code,
                )
                GLib.idle_add(self._on_mirror_result, r, m)
        except Exception:
            traceback.print_exc()

        if not self._cancelled:
            GLib.idle_add(self._on_all_done)

    # ------------------------------------------------------------------
    # UI update callbacks (GTK main thread)
    # ------------------------------------------------------------------

    def _pulse_progress(self) -> bool:
        self._progress.pulse()
        return True

    def _stop_pulse(self) -> None:
        if self._pulse_timer is not None:
            GLib.source_remove(self._pulse_timer)
            self._pulse_timer = None

    def _set_total(self, total: int) -> bool:
        self._total = total
        return False

    def _on_mirror_result(self, result: RankResult, mirror: ArchMirror) -> bool:
        self._stop_pulse()
        self._results.append((result, mirror))
        done = len(self._results)
        total = max(1, self._total)
        self._progress.set_fraction(min(1.0, done / total))
        self._pb_label.set_text(f"{done}/{total}")
        self._append_row(result, mirror)
        return False

    def _append_row(self, result: RankResult, mirror: ArchMirror) -> None:
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
            # sort_by != "rate": speed=0 means not tested, not slow
            speed_lbl = Gtk.Label(label="—", xalign=1, width_chars=12)
            speed_lbl.add_css_class("dim-label")
        else:
            speed_lbl = Gtk.Label(label="unreachable", xalign=1, width_chars=12)
            speed_lbl.add_css_class("error")

        # Country code label — hidden when unknown
        country_lbl = Gtk.Label(label=mirror.country_code, xalign=0, width_chars=4)
        country_lbl.add_css_class("dim-label")
        country_lbl.set_visible(bool(mirror.country_code))

        url_lbl = Gtk.Label(label=result.template, xalign=0, hexpand=True)
        url_lbl.set_ellipsize(Pango.EllipsizeMode.END)

        box.append(speed_lbl)
        box.append(country_lbl)
        box.append(url_lbl)
        row.set_child(box)
        self._list_box.append(row)

    def _on_all_done(self) -> bool:
        self._stop_pulse()
        self._done = True
        self._progress.set_fraction(1.0)

        # Sort: reachable first, then by speed desc
        if self._options.sort == "rate":
            self._results.sort(key=lambda x: (not x[0].reachable, -x[0].speed))

        reachable = sum(1 for r, _ in self._results if r.reachable)
        self._status.set_text(f"Done — {reachable}/{len(self._results)} reachable")

        # Re-populate list in final sort order
        row = self._list_box.get_row_at_index(0)
        while row is not None:
            self._list_box.remove(row)
            row = self._list_box.get_row_at_index(0)
        for r, m in self._results:
            self._append_row(r, m)

        discard = Gtk.Button(label="Discard")
        discard.connect("clicked", lambda _: self._finish(None))
        self._btn_box.append(discard)

        ok = Gtk.Button(label="Continue")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda _: self._finish(self._build_mirrorlist()))
        self._btn_box.append(ok)
        return False

    def _on_no_mirrors(self) -> bool:
        self._stop_pulse()
        self._done = True
        self._status.set_text("No mirrors found.")
        self._progress.set_fraction(1.0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _: self._finish(None))
        self._btn_box.append(close)
        return False

    def _on_error(self, message: str) -> bool:
        self._stop_pulse()
        self._done = True
        self._status.set_text(f"Error: {message}")
        self._progress.set_fraction(1.0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _: self._finish(None))
        self._btn_box.append(close)
        return False

    def _build_mirrorlist(self) -> str:
        reachable = [(r, m) for r, m in self._results if r.reachable]
        top = reachable[: self._options.number]
        entries = [(r.template, r.speed, m.country) for r, m in top]
        return format_mirrorlist(entries)

    def _finish(self, content: str | None) -> None:
        if self._on_done:
            self._on_done(content)
        self.close()

    def _on_close_request(self, _: Gtk.Window) -> bool:
        if self._done or self._cancelled:
            return False
        if self._closing:
            return True
        self._closing = True
        dialog = Gtk.AlertDialog()
        dialog.set_message("Cancel mirror ranking?")
        dialog.set_detail("Testing is still in progress. Results will be discarded.")
        dialog.set_buttons(["Continue", "Cancel ranking"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        def _on_response(src, result):
            self._closing = False
            try:
                idx = dialog.choose_finish(result)
            except Exception:
                return
            if idx == 1:
                self._cancelled = True
                self._stop_pulse()
                if self._done:
                    self._finish(None)
                else:
                    self.close()

        dialog.choose(self, None, _on_response)
        return True
