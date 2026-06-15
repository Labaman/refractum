"""
Progress window for speed-based distro mirror ranking.

Shows a live table of mirror results as they come in,
with live per-mirror results as they arrive.

Architecture for sets with primary_id (e.g. CachyOS v3/v4):
  - Only the primary set (e.g. cachyos x86_64) is speed-tested.
  - Derived sets get their ranking from the primary: the same mirror servers,
    but with the arch variable substituted ($arch → $arch_v3 / $arch_v4).
  - This avoids testing the same servers 3× and bypasses rate-limiting.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

from ..distros import MirrorSet
from ..ranker import RankResult, rank_mirror_set, ranked_to_mirrorlist
from ..mirrorlist import save_mirrorlist_batch
from .widgets import defer_until_mapped, make_rank_result_row, show_cancel_dialog


class DistroProgressWindow(Gtk.Window):
    """
    Ranks one or more distro MirrorSets and shows live results.

    Primary sets are speed-tested directly.
    Derived sets (primary_id is set) inherit ranking from their primary
    via arch-variable substitution — no redundant network testing.
    """

    def __init__(
        self,
        app: Gtk.Application,
        mirror_sets: list[MirrorSet],
        max_workers: int = 10,
        timeout: float = 8.0,
        protocols: list[str] | None = None,
        max_results: int | None = None,
        country_names: set[str] | None = None,
        country_codes: set[str] | None = None,
        sort_by: str = "rate",
        ww_fallback_auto: bool = True,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(application=app, title="Ranking distro mirrors…")
        self.set_default_size(950, 600)

        self._sets = mirror_sets
        self._max_workers = max_workers
        self._timeout = timeout
        self._protocols = protocols
        self._max_results = max_results
        self._country_names = country_names
        self._country_codes = country_codes
        self._sort_by = sort_by
        self._ww_fallback_auto = ww_fallback_auto
        self._on_done = on_done

        self._results: dict[str, list[RankResult]] = {ms.id: [] for ms in mirror_sets}
        self._finished: set[str] = set()
        self._total_mirrors: dict[str, int] = {}

        # Prefetch coordination: all primary workers fetch mirrorlists in parallel,
        # then a single coordinator shows one combined dialog if needed before testing.
        self._prefetch_lock = threading.Lock()
        self._prefetch_results: dict[str, tuple[list[str], dict[str, str], bool]] = {}
        self._prefetch_remaining: int = 0  # set in _start_workers
        self._fallback_proceed = threading.Event()
        self._fallback_skip_ids: set[str] = set()

        self._cancelled = False
        self._closing = False
        self._cancel_event = threading.Event()

        # Quick lookup by id
        self._set_by_id: dict[str, MirrorSet] = {ms.id: ms for ms in mirror_sets}

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

        self._status = Gtk.Label(label="Starting speed tests…", xalign=0)
        root.append(self._status)

        self._fallback_bar = Gtk.InfoBar()
        self._fallback_bar.set_message_type(Gtk.MessageType.WARNING)
        self._fallback_label = Gtk.Label(label="", xalign=0, wrap=True)
        self._fallback_bar.add_child(self._fallback_label)
        self._fallback_bar.set_revealed(False)
        root.append(self._fallback_bar)

        overall_row = Gtk.Box(spacing=6)
        self._overall_bar = Gtk.ProgressBar()
        self._overall_bar.set_hexpand(True)
        self._overall_status = Gtk.Label(label="", xalign=0)
        overall_row.append(self._overall_bar)
        overall_row.append(self._overall_status)
        root.append(overall_row)

        self._list_boxes: dict[str, Gtk.ListBox] = {}
        self._progress_bars: dict[str, Gtk.ProgressBar] = {}
        self._pb_labels: dict[str, Gtk.Label] = {}

        # Derived sets get no expander — just notes under their primary.
        derived_by_primary: dict[str, list[MirrorSet]] = {}
        for ms in self._sets:
            if ms.primary_id:
                derived_by_primary.setdefault(ms.primary_id, []).append(ms)

        for ms in self._sets:
            if ms.primary_id:
                continue

            exp = Gtk.Expander(label=f"  {ms.display_name}")
            exp.set_expanded(True)
            exp.set_margin_top(4)

            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            pb_row = Gtk.Box(spacing=6)
            pb = Gtk.ProgressBar()
            pb.set_hexpand(True)
            pb_lbl = Gtk.Label(label="Fetching mirror list…", xalign=0)
            pb_lbl.set_hexpand(True)
            pb_row.append(pb)
            pb_row.append(pb_lbl)
            self._progress_bars[ms.id] = pb
            self._pb_labels[ms.id] = pb_lbl
            inner.append(pb_row)

            lb = Gtk.ListBox()
            lb.set_selection_mode(Gtk.SelectionMode.NONE)
            self._list_boxes[ms.id] = lb

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_max_content_height(220)
            scroll.set_propagate_natural_height(True)
            scroll.set_overlay_scrolling(False)
            scroll.set_child(lb)
            inner.append(scroll)

            for derived_ms in derived_by_primary.get(ms.id, []):
                note = Gtk.Label(
                    label=f"  ↳ will also update: {derived_ms.mirrorlist_path}",
                    xalign=0,
                    css_classes=["dim-label"],
                )
                inner.append(note)

            exp.set_child(inner)
            root.append(exp)

        self._btn_box = Gtk.Box(spacing=8)
        self._btn_box.set_halign(Gtk.Align.END)
        self._btn_box.set_margin_top(4)
        root.append(self._btn_box)

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch one worker thread per PRIMARY MirrorSet, deferring until mapped if needed."""
        defer_until_mapped(self, self._start_workers)

    def _start_workers(self) -> None:
        primaries = [ms for ms in self._sets if not ms.primary_id]
        self._prefetch_remaining = len(primaries)
        for ms in primaries:
            threading.Thread(target=self._worker, args=(ms,), daemon=True).start()

    # ------------------------------------------------------------------
    # Worker: speed-test a primary set
    # ------------------------------------------------------------------

    def _worker(self, ms: MirrorSet) -> None:
        from ..distros import fetch_mirrorlist_with_countries

        # ── Phase 1: fetch mirrorlist ─────────────────────────────────────
        templates: list[str] = []
        country_map: dict[str, str] = {}
        ww_fallback = False
        fetch_ok = False

        try:
            GLib.idle_add(self._set_pb_text, ms.id, "Fetching mirror list…")
            templates, country_map, ww_fallback = fetch_mirrorlist_with_countries(
                ms, country_names=self._country_names, country_codes=self._country_codes
            )
            fetch_ok = True
        except Exception:
            traceback.print_exc()
        finally:
            # Register result and decrement counter regardless of success.
            # When the last fetch completes the coordinator fires once.
            with self._prefetch_lock:
                self._prefetch_results[ms.id] = (templates, country_map, ww_fallback)
                self._prefetch_remaining -= 1
                trigger = self._prefetch_remaining == 0
            if trigger:
                GLib.idle_add(self._on_all_prefetched)

        if not fetch_ok:
            GLib.idle_add(self._set_pb_text, ms.id, "Error: failed to fetch mirror list.")
            if not self._cancelled:
                GLib.idle_add(self._on_set_done, ms.id, [])
            return

        # ── Phase 2: wait for fallback decision (if needed) ──────────────
        if ww_fallback:
            # Block until the coordinator has resolved the combined dialog.
            while not self._fallback_proceed.wait(timeout=0.5):
                if self._cancelled:
                    return
            if self._cancelled:
                return
            if ms.id in self._fallback_skip_ids:
                GLib.idle_add(self._set_pb_text, ms.id, "Skipped — no mirrors in selected countries.")
                if not self._cancelled:
                    GLib.idle_add(self._on_set_done, ms.id, [])
                return

        # ── Phase 3: sort or speed-test ───────────────────────────────────
        try:
            if self._sort_by == "country":
                # Fast path: no speed test — sort alphabetically by country code/name.
                if self._protocols:
                    templates = [t for t in templates if any(t.startswith(p + "://") for p in self._protocols)]
                # "\xff" pushes mirrors with no country info to the end of the list.
                templates.sort(key=lambda t: (country_map.get(t, "\xff"), t))
                if self._max_results:
                    templates = templates[: self._max_results]
                results = [
                    RankResult(
                        template=t,
                        speed=0.0,
                        reachable=True,
                        country=country_map.get(t, ""),
                    )
                    for t in templates
                ]
                self._total_mirrors[ms.id] = len(results)
                if not self._cancelled:
                    GLib.idle_add(self._on_set_done, ms.id, results)
                return

            # Normal speed-test path
            if not templates:
                GLib.idle_add(self._set_pb_text, ms.id, "No mirrors found.")
                if not self._cancelled:
                    GLib.idle_add(self._on_set_done, ms.id, [])
                return

            self._total_mirrors[ms.id] = len(templates)
            if ww_fallback:
                GLib.idle_add(self._set_pb_text, ms.id, f"No country mirrors — testing all {len(templates)}…")
            else:
                GLib.idle_add(self._set_pb_text, ms.id, f"Testing {len(templates)} mirrors…")

            def progress_cb(result: RankResult) -> None:
                if not self._cancelled:
                    GLib.idle_add(self._on_mirror_result, ms.id, result)

            results = rank_mirror_set(
                ms,
                templates=templates,
                max_workers=self._max_workers,
                timeout=self._timeout,
                protocols=self._protocols,
                max_results=self._max_results,
                on_progress=progress_cb,
                cancel=self._cancel_event,
            )

            if self._cancelled:
                return

            # rank_mirror_set has no country awareness; populate from country_map here.
            for r in results:
                r.country = country_map.get(r.template, "")

            GLib.idle_add(self._on_set_done, ms.id, results)

        except Exception:
            traceback.print_exc()
            GLib.idle_add(self._set_pb_text, ms.id, f"Error: {traceback.format_exc().splitlines()[-1]}")
            if not self._cancelled:
                GLib.idle_add(self._on_set_done, ms.id, [])

    # ------------------------------------------------------------------
    # Worker: derive a dependent set from its primary's results
    # ------------------------------------------------------------------

    def _derive_worker(self, primary_ms: MirrorSet, derived_ms: MirrorSet, primary_results: list[RankResult]) -> None:
        """
        Build the derived set's mirrorlist from the primary's ranked results.

        For each ranked primary mirror we substitute the primary's arch_var
        with the derived set's arch_var.

        Example: primary template "https://cdn77.cachyos.org/repo/$arch/$repo"
          → derived template  "https://cdn77.cachyos.org/repo/$arch_v3/$repo"
        """
        if self._cancelled:
            return

        try:
            GLib.idle_add(self._set_pb_text, derived_ms.id, f"Deriving from {primary_ms.display_name}…")

            if self._cancelled:
                return

            # Mirror speed is measured on the primary ($arch) repo; v3/v4 packages live
            # in a sibling directory on the same host, so the same ranking applies.
            # No cross-check against the upstream v3/v4 list — that fetch is unreliable
            # (GitHub times out after the long speed test) and the filtering it provides
            # is not worth the complexity: pacman handles missing paths by trying the next mirror.
            derived_results = [
                RankResult(
                    template=r.template.replace(primary_ms.arch_var, derived_ms.arch_var),
                    speed=r.speed,
                    reachable=r.reachable,
                )
                for r in primary_results
            ]

            if not self._cancelled:
                GLib.idle_add(self._on_set_done, derived_ms.id, derived_results)

        except Exception:
            traceback.print_exc()
            GLib.idle_add(self._set_pb_text, derived_ms.id, f"Error: {traceback.format_exc().splitlines()[-1]}")
            if not self._cancelled:
                GLib.idle_add(self._on_set_done, derived_ms.id, [])

    # ------------------------------------------------------------------
    # UI update callbacks (GTK main thread)
    # ------------------------------------------------------------------

    def _set_pb_text(self, set_id: str, text: str) -> bool:
        lbl = self._pb_labels.get(set_id)
        if lbl:
            lbl.set_text(text)
        return False

    def _on_all_prefetched(self) -> bool:
        """Called on the main thread when all primary mirrorlists have been fetched.
        Shows one combined dialog (manual mode) or the InfoBar (auto mode), then
        releases the _fallback_proceed event so waiting workers can continue."""
        if self._cancelled:
            return False
        fallback_sets = [
            ms for ms in self._sets if not ms.primary_id and self._prefetch_results.get(ms.id, ([], {}, False))[2]
        ]

        if not fallback_sets:
            self._fallback_proceed.set()
            return False

        names = [ms.display_name for ms in fallback_sets]

        if self._ww_fallback_auto:
            self._show_ww_fallback_notice(names)
            self._fallback_proceed.set()
            return False

        # Manual mode: one custom dialog with a "set as default" checkbox.
        fallback_ids = {ms.id for ms in fallback_sets}

        def _on_response(use_ww: bool, set_default: bool) -> None:
            if use_ww:
                if set_default:
                    from ..config import load_config, save_user_config
                    from ..models import ReflectorOptions

                    opts = load_config() or ReflectorOptions()
                    opts.distro_ww_fallback = True
                    save_user_config(opts)
                    self._ww_fallback_auto = True
                self._show_ww_fallback_notice(names)
            else:
                self._fallback_skip_ids = fallback_ids
            self._fallback_proceed.set()

        self._show_fallback_dialog(names, _on_response)
        return False

    def _show_fallback_dialog(self, names: list[str], on_response: object) -> None:
        """Create and present a custom dialog with a 'set as default' checkbox."""
        win = Gtk.Window(transient_for=self, modal=True, title="No mirrors in selected countries")
        win.set_default_size(400, -1)
        win.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(16)
        win.set_child(box)

        if len(names) == 1:
            msg = f"No {names[0]} mirrors in selected countries"
        else:
            msg = "No mirrors in selected countries"
        msg_label = Gtk.Label(label=msg, wrap=True, xalign=0)
        msg_label.add_css_class("title-4")
        box.append(msg_label)

        detail_lines = "\n".join(f"• {n}" for n in names)
        detail_label = Gtk.Label(label=f"{detail_lines}\n\nUse all worldwide mirrors instead?", wrap=True, xalign=0)
        detail_label.add_css_class("dim-label")
        box.append(detail_label)

        remember_cb = Gtk.CheckButton(label="Enable worldwide fallback by default")
        remember_cb.connect("toggled", lambda cb: skip_btn.set_sensitive(not cb.get_active()))
        box.append(remember_cb)

        btn_box = Gtk.Box(spacing=8, homogeneous=True)
        btn_box.set_margin_top(4)
        skip_btn = Gtk.Button(label="Skip")
        use_btn = Gtk.Button(label="Use worldwide")
        use_btn.add_css_class("suggested-action")
        btn_box.append(skip_btn)
        btn_box.append(use_btn)
        box.append(btn_box)

        _responded = [False]

        def _skip(_b):
            _responded[0] = True
            win.close()
            on_response(False, False)

        def _use(_b):
            _responded[0] = True
            set_default = remember_cb.get_active()
            win.close()
            on_response(True, set_default)

        def _wm_close(_w):
            # WM close (Alt+F4 / compositor X) without clicking a button — treat as Skip.
            if not _responded[0]:
                _responded[0] = True
                on_response(False, False)
            return False

        skip_btn.connect("clicked", _skip)
        use_btn.connect("clicked", _use)
        win.connect("close-request", _wm_close)
        win.present()

    def _show_ww_fallback_notice(self, names: list[str]) -> bool:
        """Show (or update) the persistent warning bar for worldwide-fallback repos."""
        self._fallback_label.set_text(
            f"No mirrors in selected countries for: {', '.join(names)} — all worldwide mirrors used instead."
        )
        self._fallback_bar.set_revealed(True)
        return False

    def _on_mirror_result(self, set_id: str, result: RankResult) -> bool:
        """Update progress bar and add a live result row as each mirror finishes."""
        self._results[set_id].append(result)

        pb = self._progress_bars[set_id]
        done = len(self._results[set_id])
        total = self._total_mirrors.get(set_id, 1)
        pb.set_fraction(min(1.0, done / total))
        self._pb_labels[set_id].set_text(f"{done}/{total}")

        lb = self._list_boxes.get(set_id)
        if lb:
            self._append_result_row(lb, result)

        self._update_overall_progress()
        return False

    def _append_result_row(self, lb: Gtk.ListBox, result: RankResult) -> None:
        lb.append(make_rank_result_row(result, result.country, show_no_data=(self._sort_by != "country")))

    def _update_overall_progress(self) -> None:
        total_done = sum(len(self._results.get(ms.id, [])) for ms in self._sets if not ms.primary_id)
        total_all = sum(self._total_mirrors.get(ms.id, 0) for ms in self._sets if not ms.primary_id)
        if total_all > 0:
            self._overall_bar.set_fraction(min(1.0, total_done / total_all))
            self._overall_status.set_text(f"{total_done}/{total_all} mirrors tested")

    def _on_set_done(self, set_id: str, results: list[RankResult]) -> bool:
        if self._cancelled:
            return False
        self._finished.add(set_id)
        self._results[set_id] = results

        reachable = sum(1 for r in results if r.reachable)

        pb = self._progress_bars.get(set_id)
        if pb:
            pb.set_fraction(1.0)
        lbl = self._pb_labels.get(set_id)
        if lbl:
            ww_note = " (worldwide)" if self._prefetch_results.get(set_id, ([], {}, False))[2] else ""
            lbl.set_text(f"Done{ww_note} — {reachable}/{len(results)} reachable")

        lb = self._list_boxes.get(set_id)
        if lb:
            # Re-populate in sorted order (live rows arrived in completion order)
            row = lb.get_row_at_index(0)
            while row is not None:
                lb.remove(row)
                row = lb.get_row_at_index(0)
            for r in results:
                self._append_result_row(lb, r)

        ms = self._set_by_id.get(set_id)

        # If this is a primary, kick off derivation for its dependent sets
        if ms and not ms.primary_id:
            for derived_ms in self._sets:
                if derived_ms.primary_id == set_id and not self._cancelled:
                    t = threading.Thread(
                        target=self._derive_worker,
                        args=(ms, derived_ms, results),
                        daemon=True,
                    )
                    t.start()

        if len(self._finished) == len(self._sets):
            self._all_done()
        return False

    def _all_done(self) -> None:
        self._status.set_text("All mirror sets ranked.")
        self._overall_bar.set_fraction(1.0)

        save_btn = Gtk.Button(label="Save all mirrorlists")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_all)
        self._btn_box.append(save_btn)

        close_btn = Gtk.Button(label="Close without saving")
        close_btn.connect("clicked", lambda _: self._finish())
        self._btn_box.append(close_btn)

    def _on_save_all(self, btn: Gtk.Button) -> None:
        btn.set_sensitive(False)
        errors: list[str] = []
        skipped: list[str] = []
        files_to_save: list[tuple[str, Path]] = []

        for ms in self._sets:
            results = self._results.get(ms.id, [])
            reachable = [r for r in results if r.reachable]

            if not reachable:
                skipped.append(ms.display_name)
                continue

            files_to_save.append((ranked_to_mirrorlist(ms, results), ms.mirrorlist_path))

        if files_to_save:
            try:
                save_mirrorlist_batch(files_to_save)
            except Exception as exc:
                errors.append(str(exc))

        parts: list[str] = []
        if skipped:
            parts.append("Skipped (0 reachable — existing file kept):\n" + "\n".join(f"  • {n}" for n in skipped))
        if errors:
            parts.append("Save errors:\n" + "\n".join(f"  • {e}" for e in errors))

        dialog = Gtk.AlertDialog()
        dialog.set_buttons(["OK"])
        if parts:
            dialog.set_message("Issues while saving")
            dialog.set_detail("\n\n".join(parts))

            def _on_response_issues(src, result):
                try:
                    dialog.choose_finish(result)
                except Exception:
                    pass
                if errors:
                    btn.set_sensitive(True)
                else:
                    self._finish()

            dialog.choose(self, None, _on_response_issues)
        else:
            dialog.set_message("All mirrorlists saved.")

            def _on_response(src, result):
                try:
                    dialog.choose_finish(result)
                except Exception:
                    pass
                self._finish()

            dialog.choose(self, None, _on_response)

    def _finish(self) -> None:
        if self._on_done:
            self._on_done()
        self.close()

    def _on_close_request(self, _: Gtk.Window) -> bool:
        if self._cancelled or len(self._finished) >= len(self._sets):
            return False
        if self._closing:
            return True
        self._closing = True

        def _confirm() -> None:
            self._cancelled = True
            self._cancel_event.set()
            self._fallback_proceed.set()  # unblock any workers waiting for dialog
            self._finish()

        show_cancel_dialog(self, on_dismiss=lambda: setattr(self, "_closing", False), on_confirm=_confirm)
        return True
