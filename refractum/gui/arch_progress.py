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
from gi.repository import Gtk, GLib  # noqa: E402

from ..arch_mirrors import ArchMirror, fetch_mirrors, sort_no_test, format_mirrorlist
from ..ranker import RankResult, test_mirror_speed
from ..models import ReflectorOptions
from .widgets import defer_until_mapped, make_rank_result_row, show_cancel_dialog


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
        self._cancel_event = threading.Event()
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
        """Spawn the worker thread, deferring until the window is mapped if needed."""

        def _begin() -> None:
            self._pulse_timer = GLib.timeout_add(80, self._pulse_progress)
            threading.Thread(target=self._worker, daemon=True).start()

        defer_until_mapped(self, _begin)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        opts = self._options
        cancel = self._cancel_event
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

        if cancel.is_set():
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

        # Rate sort: plain daemon threads rather than ThreadPoolExecutor.
        # TPE registers an atexit handler that joins all pool threads on process
        # exit — if in-flight requests are still blocked when the user cancels,
        # the process hangs until download_timeout expires. Daemon threads are
        # killed immediately when the process exits, so there is no hang.
        GLib.idle_add(self._status.set_text, f"Testing {len(mirrors)} mirrors…")
        max_workers = opts.threads or 5
        timeout = float(opts.download_timeout)

        # Deduplicate by server_template before dispatching.
        unique_mirrors = list({m.server_template: m for m in mirrors}.values())

        work_q: queue.SimpleQueue[ArchMirror | object] = queue.SimpleQueue()
        result_q: queue.SimpleQueue[tuple[ArchMirror, float | None]] = queue.SimpleQueue()
        _sentinel = object()

        def _test_worker() -> None:
            while True:
                item = work_q.get()
                if item is _sentinel:
                    return
                m: ArchMirror = item  # type: ignore[assignment]
                try:
                    speed = test_mirror_speed(m.make_test_url(), timeout)
                except Exception:
                    speed = None
                result_q.put((m, speed))

        for m in unique_mirrors:
            work_q.put(m)
        for _ in range(max_workers):
            work_q.put(_sentinel)
            threading.Thread(target=_test_worker, daemon=True).start()

        cancelled_mid_run = False
        received = 0
        total = len(unique_mirrors)
        try:
            while received < total:
                try:
                    m, speed = result_q.get(timeout=0.1)  # type: ignore[assignment]
                except queue.Empty:
                    if cancel.is_set():
                        cancelled_mid_run = True
                        break
                    continue
                received += 1
                if cancel.is_set():
                    cancelled_mid_run = True
                    break
                r = RankResult(
                    template=m.server_template,
                    speed=speed or 0.0,
                    reachable=speed is not None,
                    country=m.country_code,
                )
                GLib.idle_add(self._on_mirror_result, r, m)
        except Exception:
            traceback.print_exc()
            cancelled_mid_run = True

        if not cancelled_mid_run:
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
        self._list_box.append(make_rank_result_row(result, mirror.country_code))

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

        def _confirm() -> None:
            self._cancelled = True
            self._cancel_event.set()
            self._stop_pulse()
            if self._done:
                self._finish(None)
            else:
                self.close()

        show_cancel_dialog(self, on_dismiss=lambda: setattr(self, "_closing", False), on_confirm=_confirm)
        return True
