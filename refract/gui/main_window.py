"""
Main selection window — GTK4 / PyGObject.

Two tabs via Gtk.Notebook:
  1. "Arch mirrors"  — country + reflector options (as before)
  2. "Distro mirrors" — CachyOS / EndeavourOS / Artix speed-based ranking
"""

from __future__ import annotations

import gi
import math

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..reflector import Country, ReflectorOptions
from ..config import save_user_config, save_global_config
from ..distros import MirrorSet, ALL_MIRROR_SETS, installed_mirror_sets, detect_distro_id


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SelectionResult:
    """Everything the user chose in the main window."""

    options: ReflectorOptions
    distro_sets: list[MirrorSet] = field(default_factory=list)
    distro_workers: int = 10
    cancelled: bool = False


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(Gtk.ApplicationWindow):
    FREE_PARAMS_FILE = Path.home() / ".config" / "refract" / "free-params.txt"

    def __init__(
        self,
        app: Gtk.Application,
        countries: list[Country],
        local_code: str,
        defaults: ReflectorOptions,
        columns: int = 5,
        width: int = 1000,
        height: int = 750,
        on_result: Callable[[SelectionResult], None] | None = None,
    ) -> None:
        super().__init__(application=app, title="refract — Select Arch mirrors")
        self.set_default_size(width, height)

        self._countries = countries
        self._local_code = local_code
        self._defaults = defaults
        self._columns = columns
        self._on_result = on_result

        self._country_checks: list[Gtk.CheckButton] = []
        self._distro_checks: dict[str, Gtk.CheckButton] = {}  # id → CheckButton
        self._updating_countries = False

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_start(8)
        root.set_margin_end(8)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        self.set_child(root)

        # Gtk.Notebook — tabbed container
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)
        root.append(notebook)

        # Tab 1: Arch mirrors
        arch_page = self._build_arch_tab()
        notebook.append_page(arch_page, Gtk.Label(label="Arch mirrors"))

        # Tab 2: Distro mirrors
        distro_page = self._build_distro_tab()
        notebook.append_page(distro_page, Gtk.Label(label="Distro mirrors"))

        root.append(self._make_buttons())

    # ------------------------------------------------------------------
    # Tab 1: Arch mirrors (reflector-based)
    # ------------------------------------------------------------------

    def _build_arch_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(8)

        box.append(
            Gtk.Label(
                label="Select countries to include in mirror ranking.\n"
                "Closest locations are usually the fastest. HTTPS is preferred.",
                xalign=0,
            )
        )

        scroll = self._make_country_scroll()
        scroll.set_vexpand(True)
        box.append(scroll)
        box.append(self._make_options())
        return box

    def _make_country_scroll(self) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)

        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(4)
        grid.set_margin_start(4)
        grid.set_margin_top(4)

        pre_selected = set(self._defaults.countries) | {self._local_code}
        ww_active = "WW" in pre_selected

        n = len(self._countries)
        rows = math.ceil(n / self._columns)

        for idx, country in enumerate(self._countries):
            cb = Gtk.CheckButton(label=country.name)
            cb.set_active(country.code in pre_selected)
            if country.code != "WW":
                cb.set_sensitive(not ww_active)
            self._country_checks.append(cb)
            col = idx // rows
            row = idx % rows
            grid.attach(cb, col, row, 1, 1)
            if country.code == "WW":
                cb.connect("toggled", self._on_worldwide_toggled)
            else:
                cb.connect("toggled", self._on_country_toggled)

        scroll.set_child(grid)
        return scroll

    def _make_options(self) -> Gtk.Frame:
        frame = Gtk.Frame(label="Reflector options")
        frame.set_margin_top(4)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        frame.set_child(grid)

        row = 0

        # Protocols
        grid.attach(Gtk.Label(label="Protocols:", xalign=0), 0, row, 1, 1)
        proto_box = Gtk.Box(spacing=8)
        self._https_cb = Gtk.CheckButton(label="https")
        self._https_cb.set_active("https" in self._defaults.protocols if self._defaults.protocols else True)
        self._http_cb = Gtk.CheckButton(label="http")
        self._http_cb.set_active("http" in self._defaults.protocols)
        self._rsync_cb = Gtk.CheckButton(label="rsync")
        self._rsync_cb.set_active("rsync" in self._defaults.protocols)
        for w in (self._https_cb, self._http_cb, self._rsync_cb):
            proto_box.append(w)
        grid.attach(proto_box, 1, row, 3, 1)
        row += 1

        # Sort
        grid.attach(Gtk.Label(label="Sort by:", xalign=0), 0, row, 1, 1)
        self._sort_combo = Gtk.ComboBoxText()
        sort_opts = ["rate", "age", "country", "score", "delay"]
        for opt in sort_opts:
            self._sort_combo.append_text(opt)
        current = self._defaults.sort or "rate"
        self._sort_combo.set_active(sort_opts.index(current) if current in sort_opts else 0)
        grid.attach(self._sort_combo, 1, row, 1, 1)
        row += 1

        # Sync recency — mutually exclusive: --age N  vs  --latest N
        grid.attach(Gtk.Label(label="Sync recency:", xalign=0), 0, row, 1, 1)
        freshness_box = Gtk.Box(spacing=12)

        self._radio_age = Gtk.CheckButton(label="Max age (h):")
        self._radio_latest = Gtk.CheckButton(label="Latest synced")
        self._radio_latest.set_group(self._radio_age)  # mutual exclusion

        adj_age = Gtk.Adjustment(value=self._defaults.age or 24, lower=1, upper=8760, step_increment=1)
        self._age_spin = Gtk.SpinButton(adjustment=adj_age, climb_rate=1, digits=0)
        self._age_spin.set_width_chars(6)

        use_latest = self._defaults.use_latest
        self._radio_age.set_active(not use_latest)
        self._radio_latest.set_active(use_latest)
        self._age_spin.set_sensitive(not use_latest)

        self._radio_age.connect("toggled", lambda btn: self._age_spin.set_sensitive(btn.get_active()))

        freshness_box.append(self._radio_age)
        freshness_box.append(self._age_spin)
        freshness_box.append(self._radio_latest)
        grid.attach(freshness_box, 1, row, 3, 1)
        row += 1

        # Number
        grid.attach(Gtk.Label(label="Max mirrors:", xalign=0), 0, row, 1, 1)
        adj = Gtk.Adjustment(value=self._defaults.number or 10, lower=1, upper=200, step_increment=1)
        self._number_spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self._number_spin.set_width_chars(6)
        grid.attach(self._number_spin, 1, row, 1, 1)
        row += 1

        # Timeout
        grid.attach(Gtk.Label(label="Download timeout (s):", xalign=0), 0, row, 1, 1)
        adj2 = Gtk.Adjustment(value=self._defaults.download_timeout or 10, lower=1, upper=120, step_increment=1)
        self._timeout_spin = Gtk.SpinButton(adjustment=adj2, climb_rate=1, digits=0)
        self._timeout_spin.set_width_chars(6)
        grid.attach(self._timeout_spin, 1, row, 1, 1)
        row += 1

        # Threads
        grid.attach(Gtk.Label(label="Threads:", xalign=0), 0, row, 1, 1)
        adj3 = Gtk.Adjustment(value=self._defaults.threads or 5, lower=1, upper=32, step_increment=1)
        self._threads_spin = Gtk.SpinButton(adjustment=adj3, climb_rate=1, digits=0)
        self._threads_spin.set_width_chars(6)
        grid.attach(self._threads_spin, 1, row, 1, 1)
        row += 1

        # Extra args
        grid.attach(Gtk.Label(label="Extra reflector args:", xalign=0), 0, row, 1, 1)
        self._extra_entry = Gtk.Entry()
        self._extra_entry.set_text(self._load_free_params())
        self._extra_entry.set_hexpand(True)
        grid.attach(self._extra_entry, 1, row, 3, 1)

        return frame

    # ------------------------------------------------------------------
    # Tab 2: Distro mirrors (speed-based)
    # ------------------------------------------------------------------

    def _build_distro_tab(self) -> Gtk.Box:
        """
        Shows all installed distro mirrorlist sets with checkboxes.
        The user picks which ones to re-rank.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)

        box.append(
            Gtk.Label(
                label="Select which distro mirror lists to rank by download speed.\n"
                "Greyed out entries are not installed on this system.\n"
                "This is independent of the Arch mirrors tab — uncheck all to skip.",
                xalign=0,
            )
        )

        # All primary sets — derived sets (v3/v4) are ranked automatically.
        all_primaries = [ms for ms in ALL_MIRROR_SETS if not ms.primary_id]
        installed_ids = {ms.id for ms in installed_mirror_sets()}

        derived_by_primary: dict[str, list] = {}
        for ms in ALL_MIRROR_SETS:
            if ms.primary_id:
                derived_by_primary.setdefault(ms.primary_id, []).append(ms)

        # Pre-select the set that matches the running distro (if any).
        # Plain Arch uses reflector (Arch tab), so no distro set is pre-selected.
        _distro_to_set_id = {
            "cachyos": "cachyos",
            "endeavouros": "endeavouros",
            "artix": "artix",
            "blackarch": "blackarch",
            "rebornos": "rebornos",
        }
        auto_id = _distro_to_set_id.get(detect_distro_id(), "")

        hint_label = Gtk.Label(label="", xalign=0, css_classes=["dim-label"])
        hint_label.set_margin_top(2)
        hint_label.set_margin_start(4)

        groups = [
            ("Distributions", [ms for ms in all_primaries if not ms.is_repo]),
            ("Third-party repositories", [ms for ms in all_primaries if ms.is_repo]),
        ]

        for group_label, members in groups:
            header = Gtk.Label(label=group_label, xalign=0)
            header.set_margin_top(8)
            header.set_margin_bottom(2)
            box.append(header)

            list_box = Gtk.ListBox()
            list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            list_box.add_css_class("boxed-list")

            for ms in members:
                is_installed = ms.id in installed_ids
                paths = [ms.mirrorlist_path] + [d.mirrorlist_path for d in derived_by_primary.get(ms.id, [])]
                suffix = "" if is_installed else "  (not installed)"
                hint_text = "  |  ".join(str(p) for p in paths) + suffix

                row = Gtk.ListBoxRow()
                row_box = Gtk.Box(spacing=12)
                row_box.set_margin_start(8)
                row_box.set_margin_end(8)
                row_box.set_margin_top(6)
                row_box.set_margin_bottom(6)

                cb = Gtk.CheckButton()
                cb.set_active(is_installed and ms.id == auto_id)
                cb.set_sensitive(is_installed)
                self._distro_checks[ms.id] = cb

                name_label = Gtk.Label(label=ms.display_name, xalign=0, hexpand=True)
                if not is_installed:
                    name_label.add_css_class("dim-label")

                row_box.append(cb)
                row_box.append(name_label)
                row.set_child(row_box)

                motion = Gtk.EventControllerMotion()
                motion.connect("enter", lambda _c, _x, _y, t=hint_text: hint_label.set_text(t))
                motion.connect("leave", lambda _c: hint_label.set_text(""))
                row.add_controller(motion)

                list_box.append(row)

            box.append(list_box)

        box.append(hint_label)

        # Speed test settings (timeout and max mirrors come from Arch mirrors tab)
        opts_frame = Gtk.Frame(label="Speed test options")
        opts_frame.set_margin_top(8)
        opts_grid = Gtk.Grid()
        opts_grid.set_column_spacing(12)
        opts_grid.set_row_spacing(6)
        opts_grid.set_margin_start(8)
        opts_grid.set_margin_end(8)
        opts_grid.set_margin_top(6)
        opts_grid.set_margin_bottom(6)
        opts_frame.set_child(opts_grid)

        opts_grid.attach(Gtk.Label(label="Concurrent workers:", xalign=0), 0, 0, 1, 1)
        adj = Gtk.Adjustment(value=10, lower=1, upper=32, step_increment=1)
        self._workers_spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self._workers_spin.set_width_chars(6)
        opts_grid.attach(self._workers_spin, 1, 0, 1, 1)

        opts_grid.attach(
            Gtk.Label(
                label="Timeout and max mirrors are taken from the Arch mirrors tab.",
                xalign=0,
                css_classes=["dim-label"],
            ),
            0,
            1,
            3,
            1,
        )

        box.append(opts_frame)
        return box

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _make_buttons(self) -> Gtk.Box:
        box = Gtk.Box(spacing=8)
        box.set_margin_top(8)

        global_btn = Gtk.Button(label="Save as global default")
        global_btn.set_tooltip_text("Write current settings to /etc/refract.conf (requires root)")
        global_btn.connect("clicked", self._on_save_global)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self._on_cancel)

        ok_btn = Gtk.Button(label="OK")
        ok_btn.add_css_class("suggested-action")
        ok_btn.connect("clicked", self._on_ok)

        box.append(global_btn)
        box.append(spacer)
        box.append(cancel_btn)
        box.append(ok_btn)
        return box

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _collect_options(self) -> ReflectorOptions:
        selected_codes = [c.code for c, cb in zip(self._countries, self._country_checks) if cb.get_active()]
        if "WW" in selected_codes:
            selected_codes = ["WW"]  # WW = no country filter; discard greyed-out individuals
        protocols = []
        if self._https_cb.get_active():
            protocols.append("https")
        if self._http_cb.get_active():
            protocols.append("http")
        if self._rsync_cb.get_active():
            protocols.append("rsync")

        extra_raw = self._extra_entry.get_text().strip()
        use_latest = self._radio_latest.get_active()
        return ReflectorOptions(
            countries=selected_codes,
            protocols=protocols,
            sort=self._sort_combo.get_active_text() or "rate",
            use_latest=use_latest,
            age=None if use_latest else int(self._age_spin.get_value()),
            number=int(self._number_spin.get_value()),
            download_timeout=int(self._timeout_spin.get_value()),
            threads=int(self._threads_spin.get_value()),
            extra_args=extra_raw.split() if extra_raw else [],
        )

    def _on_ok(self, _: Gtk.Button) -> None:
        extra_raw = self._extra_entry.get_text().strip()
        self._save_free_params(extra_raw)

        opts = self._collect_options()
        save_user_config(opts)

        # Distro sets: start with checked primaries, then append their derived sets.
        all_installed = installed_mirror_sets()
        selected_distros: list[MirrorSet] = []
        for ms in all_installed:
            if ms.primary_id:
                continue  # derived sets are added below
            cb = self._distro_checks.get(ms.id)
            if cb and cb.get_active():
                selected_distros.append(ms)
                # Include derived sets (v3/v4 etc.) automatically
                for derived in all_installed:
                    if derived.primary_id == ms.id:
                        selected_distros.append(derived)

        result = SelectionResult(
            options=opts,
            distro_sets=selected_distros,
            distro_workers=int(self._workers_spin.get_value()),
        )
        # Fire callback BEFORE closing so the next window opens first.
        # If we closed first, GTK might see zero windows and try to quit.
        if self._on_result:
            self._on_result(result)
        self.close()

    def _on_save_global(self, _: Gtk.Button) -> None:
        opts = self._collect_options()
        try:
            save_global_config(opts)
            self._show_toast("System defaults saved to /etc/refract.conf")
        except PermissionError:
            pass  # user cancelled pkexec — no error needed
        except Exception as exc:
            self._show_toast(f"Failed to save: {exc}")

    def _on_cancel(self, _: Gtk.Button) -> None:
        result = SelectionResult(options=self._defaults, cancelled=True)
        if self._on_result:
            self._on_result(result)
        self.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_worldwide_toggled(self, cb: Gtk.CheckButton) -> None:
        if self._updating_countries:
            return
        ww_active = cb.get_active()
        for check, country in zip(self._country_checks, self._countries):
            if country.code != "WW":
                check.set_sensitive(not ww_active)

    def _on_country_toggled(self, cb: Gtk.CheckButton) -> None:
        if self._updating_countries or not cb.get_active():
            return
        self._updating_countries = True
        for check, country in zip(self._country_checks, self._countries):
            if country.code == "WW":
                check.set_active(False)
        self._updating_countries = False

    def _show_toast(self, message: str) -> None:
        dialog = Gtk.AlertDialog()
        dialog.set_message(message)
        dialog.set_buttons(["OK"])
        dialog.show(self)

    def _load_free_params(self) -> str:
        if self.FREE_PARAMS_FILE.exists():
            return self.FREE_PARAMS_FILE.read_text(encoding="utf-8").strip()
        return ""

    def _save_free_params(self, params: str) -> None:
        self.FREE_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.FREE_PARAMS_FILE.write_text(params, encoding="utf-8")
