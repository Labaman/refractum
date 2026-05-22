"""
Progress window for speed-based distro mirror ranking.

Shows a live table of mirror results as they come in,
instead of the log-style progress used for reflector.

Architecture for sets with primary_id (e.g. CachyOS v3/v4):
  - Only the primary set (e.g. cachyos x86_64) is speed-tested.
  - Derived sets get their ranking from the primary: the same mirror servers,
    but with the arch variable substituted ($arch → $arch_v3 / $arch_v4).
  - Mirrors not present in the derived set's upstream list are excluded.
  - This avoids testing the same servers 3× and bypasses rate-limiting.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..distros import MirrorSet
from ..ranker import RankResult, rank_mirror_set, ranked_to_mirrorlist
from ..mirrorlist import save_mirrorlist_batch


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
        on_done: "callable[[], None] | None" = None,
    ) -> None:
        super().__init__(application=app, title="Ranking distro mirrors…")
        self.set_default_size(950, 600)

        self._sets        = mirror_sets
        self._max_workers = max_workers
        self._timeout     = timeout
        self._protocols   = protocols
        self._max_results = max_results
        self._country_names = country_names
        self._on_done     = on_done

        self._results:       dict[str, list[RankResult]] = {ms.id: [] for ms in mirror_sets}
        self._finished:      set[str]                    = set()
        self._total_mirrors: dict[str, int]              = {}

        # Quick lookup by id
        self._set_by_id: dict[str, MirrorSet] = {ms.id: ms for ms in mirror_sets}

        self.connect("close-request", self._block_close_during_ranking)
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

        overall_row = Gtk.Box(spacing=6)
        self._overall_bar = Gtk.ProgressBar()
        self._overall_bar.set_hexpand(True)
        self._overall_status = Gtk.Label(label="", xalign=0)
        overall_row.append(self._overall_bar)
        overall_row.append(self._overall_status)
        root.append(overall_row)

        self._list_boxes:    dict[str, Gtk.ListBox]    = {}
        self._progress_bars: dict[str, Gtk.ProgressBar] = {}
        self._pb_labels:     dict[str, Gtk.Label]      = {}

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
            self._pb_labels[ms.id]     = pb_lbl
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
        """Launch one worker thread per PRIMARY MirrorSet.
        Derived sets are handled automatically after their primary finishes."""
        for ms in self._sets:
            if not ms.primary_id:
                t = threading.Thread(target=self._worker, args=(ms,), daemon=True)
                t.start()

    # ------------------------------------------------------------------
    # Worker: speed-test a primary set
    # ------------------------------------------------------------------

    def _worker(self, ms: MirrorSet) -> None:
        from ..distros import fetch_mirrorlist

        try:
            GLib.idle_add(self._set_pb_text, ms.id, "Fetching mirror list…")
            templates = fetch_mirrorlist(ms, country_names=self._country_names)

            if not templates:
                GLib.idle_add(self._set_pb_text, ms.id, "No mirrors found.")
                GLib.idle_add(self._on_set_done, ms.id, [])
                return

            self._total_mirrors[ms.id] = len(templates)
            GLib.idle_add(self._set_pb_text, ms.id, f"Testing {len(templates)} mirrors…")

            def progress_cb(result: RankResult) -> None:
                GLib.idle_add(self._on_mirror_result, ms.id, result)

            results = rank_mirror_set(
                ms,
                templates=templates,
                max_workers=self._max_workers,
                timeout=self._timeout,
                protocols=self._protocols,
                max_results=self._max_results,
                on_progress=progress_cb,
            )

            # If country filter gave 0 reachable, fall back to all mirrors
            reachable = sum(1 for r in results if r.reachable)
            if reachable == 0 and self._country_names:
                GLib.idle_add(
                    self._set_pb_text, ms.id,
                    "No reachable country mirrors — trying all…",
                )
                all_templates = fetch_mirrorlist(ms)
                tested = {r.template for r in results}
                extra  = [t for t in all_templates if t not in tested]
                if extra:
                    self._total_mirrors[ms.id] += len(extra)
                    extra_results = rank_mirror_set(
                        ms,
                        templates=extra,
                        max_workers=self._max_workers,
                        timeout=self._timeout,
                        protocols=self._protocols,
                        on_progress=progress_cb,
                    )
                    results = results + extra_results
                    results.sort(key=lambda r: (not r.reachable, -r.speed))
                    if self._max_results:
                        ok  = [r for r in results if r.reachable]
                        bad = [r for r in results if not r.reachable]
                        results = ok[: self._max_results] + bad

            GLib.idle_add(self._on_set_done, ms.id, results)

        except Exception:
            traceback.print_exc()
            GLib.idle_add(self._set_pb_text, ms.id,
                          f"Error: {traceback.format_exc().splitlines()[-1]}")
            GLib.idle_add(self._on_set_done, ms.id, [])

    # ------------------------------------------------------------------
    # Worker: derive a dependent set from its primary's results
    # ------------------------------------------------------------------

    def _derive_worker(self, primary_ms: MirrorSet, derived_ms: MirrorSet,
                       primary_results: list[RankResult]) -> None:
        """
        Build the derived set's mirrorlist from the primary's ranked results.

        For each ranked primary mirror we substitute the primary's arch_var
        with the derived set's arch_var.  We then cross-check against the
        derived set's upstream mirrorlist so mirrors that don't support the
        derived architecture are excluded.

        Example: primary template "https://cdn77.cachyos.org/repo/$arch/$repo"
          → derived template  "https://cdn77.cachyos.org/repo/$arch_v3/$repo"
        """
        from ..distros import fetch_mirrorlist

        try:
            GLib.idle_add(self._set_pb_text, derived_ms.id,
                          f"Deriving from {primary_ms.display_name}…")

            # Fetch derived set's upstream list to know which mirrors support this arch
            derived_all = fetch_mirrorlist(derived_ms, country_names=self._country_names)
            derived_set = set(derived_all)

            derived_results: list[RankResult] = []
            for r in primary_results:
                derived_tmpl = r.template.replace(
                    primary_ms.arch_var, derived_ms.arch_var
                )
                # Only include mirrors verified to exist in the derived list
                if derived_set and derived_tmpl not in derived_set:
                    continue
                derived_results.append(RankResult(
                    template=derived_tmpl,
                    test_url=derived_ms.make_test_url(derived_tmpl),
                    speed=r.speed,
                    reachable=r.reachable,
                ))

            if self._max_results:
                ok  = [r for r in derived_results if r.reachable]
                bad = [r for r in derived_results if not r.reachable]
                derived_results = ok[: self._max_results] + bad

            GLib.idle_add(self._on_set_done, derived_ms.id, derived_results)

        except Exception:
            traceback.print_exc()
            GLib.idle_add(self._set_pb_text, derived_ms.id,
                          f"Error: {traceback.format_exc().splitlines()[-1]}")
            GLib.idle_add(self._on_set_done, derived_ms.id, [])

    # ------------------------------------------------------------------
    # UI update callbacks (GTK main thread)
    # ------------------------------------------------------------------

    def _set_pb_text(self, set_id: str, text: str) -> bool:
        lbl = self._pb_labels.get(set_id)
        if lbl:
            lbl.set_text(text)
        return False

    def _on_mirror_result(self, set_id: str, result: RankResult) -> bool:
        """Update progress bar as each mirror finishes. Rows are added in bulk in _on_set_done."""
        self._results[set_id].append(result)

        pb    = self._progress_bars[set_id]
        done  = len(self._results[set_id])
        total = self._total_mirrors.get(set_id, 1)
        pb.set_fraction(min(1.0, done / total))
        self._pb_labels[set_id].set_text(f"{done}/{total}")
        return False

    def _append_result_row(self, lb: Gtk.ListBox, result: RankResult) -> None:
        row     = Gtk.ListBoxRow()
        row_box = Gtk.Box(spacing=12)
        row_box.set_margin_start(6)
        row_box.set_margin_end(6)
        row_box.set_margin_top(2)
        row_box.set_margin_bottom(2)

        if result.reachable and result.speed > 0:
            speed_mb = result.speed / (1024 * 1024)
            speed_label = Gtk.Label(label=f"{speed_mb:6.2f} MB/s",
                                    xalign=1, width_chars=12)
            speed_label.add_css_class("success" if speed_mb > 1.0 else "warning")
        elif result.reachable:
            speed_label = Gtk.Label(label="up (no data)", xalign=1, width_chars=12)
            speed_label.add_css_class("warning")
        else:
            speed_label = Gtk.Label(label="unreachable", xalign=1, width_chars=12)
            speed_label.add_css_class("error")

        url_label = Gtk.Label(label=result.template, xalign=0, hexpand=True)
        url_label.set_ellipsize(Pango.EllipsizeMode.END)

        row_box.append(speed_label)
        row_box.append(url_label)
        row.set_child(row_box)
        lb.append(row)

    def _update_overall_progress(self) -> None:
        total_done = sum(len(v) for v in self._results.values())
        total_all  = sum(
            self._total_mirrors.get(ms.id, 0)
            for ms in self._sets
            if not ms.primary_id
        )
        if total_all > 0:
            self._overall_bar.set_fraction(min(1.0, total_done / total_all))
            self._overall_status.set_text(f"{total_done}/{total_all} mirrors tested")

    def _on_set_done(self, set_id: str, results: list[RankResult]) -> bool:
        self._finished.add(set_id)
        self._results[set_id] = results

        reachable = sum(1 for r in results if r.reachable)

        pb = self._progress_bars.get(set_id)
        if pb:
            pb.set_fraction(1.0)
        lbl = self._pb_labels.get(set_id)
        if lbl:
            lbl.set_text(f"Done — {reachable}/{len(results)} reachable")

        lb = self._list_boxes.get(set_id)
        if lb:
            for r in results:
                self._append_result_row(lb, r)

        ms = self._set_by_id.get(set_id)

        # If this is a primary, kick off derivation for its dependent sets
        if ms and not ms.primary_id:
            for derived_ms in self._sets:
                if derived_ms.primary_id == set_id:
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

    def _on_save_all(self, _: Gtk.Button) -> None:
        errors:        list[str]              = []
        skipped:       list[str]              = []
        files_to_save: list[tuple[str, Path]] = []

        for ms in self._sets:
            results   = self._results.get(ms.id, [])
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
            parts.append("Skipped (0 reachable — existing file kept):\n"
                         + "\n".join(f"  • {n}" for n in skipped))
        if errors:
            parts.append("Save errors:\n" + "\n".join(f"  • {e}" for e in errors))

        dialog = Gtk.AlertDialog()
        dialog.set_buttons(["OK"])
        if parts:
            dialog.set_message("Issues while saving")
            dialog.set_detail("\n\n".join(parts))
            dialog.choose(self, None, lambda *_: None)   # just dismiss
        else:
            dialog.set_message("All mirrorlists saved.")
            dialog.choose(self, None, lambda *_: self._finish())

    def _finish(self) -> None:
        if self._on_done:
            self._on_done()
        self.close()

    def _block_close_during_ranking(self, _: Gtk.Window) -> bool:
        return len(self._finished) < len(self._sets)
