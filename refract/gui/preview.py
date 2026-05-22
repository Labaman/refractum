"""
Mirrorlist preview and confirmation window.

Shows the new mirrorlist with syntax highlighting, a summary panel,
and an optional diff view comparing it to the current file.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango  # noqa: E402

from ..mirrorlist import save_mirrorlist


# ---------------------------------------------------------------------------
# Colour constants (hex strings for TextTag foreground)
# ---------------------------------------------------------------------------
_C_COMMENT   = "#888888"   # grey  — plain comment lines
_C_HEADER    = "#4caf50"   # green — ## Country Name
_C_SERVER    = "#64b5f6"   # blue  — Server = https://…
_C_URL       = "#90caf9"   # light blue — the URL part of Server line
_C_DIFF_ADD  = "#1b5e20"   # dark green background — added lines
_C_DIFF_REM  = "#b71c1c"   # dark red background   — removed lines
_C_DIFF_META = "#555555"   # grey  — @@ … @@ context markers


class MirrorlistPreviewWindow(Gtk.Window):
    """
    Full-featured confirmation window shown before saving a mirrorlist.

    Features:
      - Summary: mirror count, countries detected, protocols, sort order
      - Syntax-highlighted text view (comments / country headers / servers)
      - Toggle between "New" and "Diff vs current" views
      - Save button with backup path info
    """

    def __init__(
        self,
        app: Gtk.Application,
        content: str,
        dest: Path,
        on_saved: "callable[[], None] | None" = None,
        on_discard: "callable[[], None] | None" = None,
    ) -> None:
        super().__init__(application=app, title="New mirrorlist — confirm save")
        self.set_default_size(860, 600)

        self._content  = content
        self._dest     = dest
        self._on_saved  = on_saved
        self._on_discard = on_discard

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        root.append(self._make_summary_bar())
        root.append(self._make_toolbar())

        # Text view — expands to fill available space
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_overlay_scrolling(False)

        self._buf = Gtk.TextBuffer()
        self._create_tags()
        self._insert_highlighted(self._content)

        tv = Gtk.TextView(buffer=self._buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_left_margin(8)
        tv.set_right_margin(8)
        tv.set_top_margin(4)
        self._scroll.set_child(tv)
        root.append(self._scroll)

        root.append(self._make_footer())

    # ------------------------------------------------------------------
    # Summary bar
    # ------------------------------------------------------------------

    def _make_summary_bar(self) -> Gtk.Box:
        """
        Horizontal strip at the top showing mirror statistics.
        Parses the mirrorlist text to extract counts and countries.
        """
        bar = Gtk.Box(spacing=24)
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.set_margin_top(8)
        bar.set_margin_bottom(8)

        mirror_count = self._content.count("\nServer = ")
        countries = re.findall(r"^## (.+)$", self._content, re.MULTILINE)
        countries = [c.strip() for c in countries
                     if not c.strip().startswith("Generated") and len(c.strip()) < 40]

        def _stat(label: str, value: str) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl = Gtk.Label(label=label, css_classes=["dim-label"])
            lbl.set_xalign(0.5)
            val = Gtk.Label(label=value)
            val.set_xalign(0.5)
            val.add_css_class("title-4")
            box.append(val)
            box.append(lbl)
            return box

        bar.append(_stat("Mirrors", str(mirror_count)))
        bar.append(_stat("Countries", str(len(countries))))
        if countries:
            bar.append(_stat("", "  ·  ".join(countries[:6]) + ("…" if len(countries) > 6 else "")))

        return bar

    # ------------------------------------------------------------------
    # Toolbar (view toggle)
    # ------------------------------------------------------------------

    def _make_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(spacing=4)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_bottom(4)

        # Toggle buttons for view mode
        # Gtk.ToggleButton: like a CheckButton but looks like a Button.
        # We link them so only one can be active at a time.
        self._btn_new  = Gtk.ToggleButton(label="New mirrorlist")
        self._btn_diff = Gtk.ToggleButton(label="Diff vs current")

        self._btn_new.set_active(True)
        # Link the buttons: activating one deactivates the other
        self._btn_diff.set_group(self._btn_new)

        self._btn_new.connect("toggled",  self._on_view_toggled)
        self._btn_diff.connect("toggled", self._on_view_toggled)

        bar.append(self._btn_new)
        bar.append(self._btn_diff)

        # Destination info on the right
        dest_label = Gtk.Label(
            label=f"→ {self._dest}",
            css_classes=["dim-label"],
            hexpand=True,
            xalign=1,
        )
        bar.append(dest_label)

        return bar

    # ------------------------------------------------------------------
    # Footer (backup info + buttons)
    # ------------------------------------------------------------------

    def _make_footer(self) -> Gtk.Box:
        footer = Gtk.Box(spacing=8)
        footer.set_margin_start(8)
        footer.set_margin_end(8)
        footer.set_margin_top(4)
        footer.set_margin_bottom(8)

        backup_path = str(self._dest) + ".bak"
        info = Gtk.Label(
            label=f"Current file will be backed up as  {backup_path}",
            css_classes=["dim-label"],
            hexpand=True,
            xalign=0,
        )
        footer.append(info)

        discard_btn = Gtk.Button(label="Discard")
        discard_btn.connect("clicked", self._on_discard_clicked)

        save_btn = Gtk.Button(label=f"Save to {self._dest.name}")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_clicked)

        footer.append(discard_btn)
        footer.append(save_btn)
        return footer

    # ------------------------------------------------------------------
    # Syntax highlighting
    # ------------------------------------------------------------------

    def _create_tags(self) -> None:
        """
        Gtk.TextBuffer uses *tags* to apply formatting to ranges of text.

        A tag is a named set of text attributes (colour, weight, style…).
        You create tags on the buffer, then apply them to ranges using
        apply_tag(tag, start_iter, end_iter).

        Pango constants:
          Pango.Weight.BOLD  — bold text
          Pango.Style.ITALIC — italic text
        """
        t = self._buf  # shorthand

        t.create_tag("comment",    foreground=_C_COMMENT, style=Pango.Style.ITALIC)
        t.create_tag("header",     foreground=_C_HEADER,  weight=Pango.Weight.BOLD)
        t.create_tag("server_kw",  foreground=_C_SERVER,  weight=Pango.Weight.BOLD)
        t.create_tag("url",        foreground=_C_URL)

        # Diff tags use background colour instead of foreground
        t.create_tag("diff_add",   background=_C_DIFF_ADD,  foreground="#ffffff")
        t.create_tag("diff_rem",   background=_C_DIFF_REM,  foreground="#ffffff")
        t.create_tag("diff_meta",  foreground=_C_DIFF_META, style=Pango.Style.ITALIC)

    def _insert_highlighted(self, text: str) -> None:
        """
        Insert `text` into the buffer line by line, applying tags.

        For each line we:
          1. Record the offset BEFORE inserting (start position)
          2. Insert the line
          3. Record the offset AFTER inserting (end position)
          4. Apply the appropriate tag to [start, end]

        Gtk.TextIter is a cursor into the buffer — like a file seek position.
        buf.get_iter_at_offset(n) returns an iter at character position n.
        """
        self._buf.set_text("")   # clear

        for line in text.splitlines(keepends=True):
            start_offset = self._buf.get_char_count()
            self._buf.insert(self._buf.get_end_iter(), line)
            end_offset = self._buf.get_char_count()

            start = self._buf.get_iter_at_offset(start_offset)
            end   = self._buf.get_iter_at_offset(end_offset)

            stripped = line.strip()

            if stripped.startswith("## "):
                self._buf.apply_tag_by_name("header", start, end)
            elif stripped.startswith("#"):
                self._buf.apply_tag_by_name("comment", start, end)
            elif stripped.startswith("Server = "):
                # Highlight "Server = " keyword and URL separately
                kw_end = self._buf.get_iter_at_offset(start_offset + len("Server = "))
                self._buf.apply_tag_by_name("server_kw", start, kw_end)
                self._buf.apply_tag_by_name("url", kw_end, end)

    def _insert_diff(self, new_text: str) -> None:
        """
        Compute and display a unified diff between the current file and new_text.

        difflib.unified_diff() produces lines like:
          --- old file
          +++ new file
          @@ -1,4 +1,6 @@
           context line
          -removed line
          +added line
        """
        old_text = ""
        if self._dest.exists():
            old_text = self._dest.read_text(encoding="utf-8")

        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"current {self._dest.name}",
            tofile=f"new {self._dest.name}",
            lineterm="",
        ))

        self._buf.set_text("")

        if not diff_lines:
            self._buf.set_text("(no changes — new mirrorlist is identical to current)")
            return

        for line in diff_lines:
            start_offset = self._buf.get_char_count()
            self._buf.insert(self._buf.get_end_iter(), line + "\n")
            end_offset = self._buf.get_char_count()

            start = self._buf.get_iter_at_offset(start_offset)
            end   = self._buf.get_iter_at_offset(end_offset)

            if line.startswith("+") and not line.startswith("+++"):
                self._buf.apply_tag_by_name("diff_add", start, end)
            elif line.startswith("-") and not line.startswith("---"):
                self._buf.apply_tag_by_name("diff_rem", start, end)
            elif line.startswith("@@"):
                self._buf.apply_tag_by_name("diff_meta", start, end)
            else:
                self._buf.apply_tag_by_name("comment", start, end)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_view_toggled(self, btn: Gtk.ToggleButton) -> None:
        if not btn.get_active():
            return   # only react to the button becoming active
        if btn is self._btn_diff:
            self._insert_diff(self._content)
        else:
            self._insert_highlighted(self._content)

    def _on_save_clicked(self, _: Gtk.Button) -> None:
        try:
            save_mirrorlist(self._content, self._dest)
        except PermissionError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:
            self._show_error(f"Failed to save:\n{exc}")
            return

        dialog = Gtk.AlertDialog()
        dialog.set_message("Saved")
        dialog.set_detail(f"Mirrorlist saved to {self._dest}\nBackup: {self._dest}.bak")
        dialog.set_buttons(["OK"])
        dialog.choose(self, None, lambda *_: self._finish_saved())

    def _on_discard_clicked(self, _: Gtk.Button) -> None:
        self.close()
        if self._on_discard:
            self._on_discard()

    def _finish_saved(self) -> None:
        self.close()
        if self._on_saved:
            self._on_saved()

    def _show_error(self, message: str) -> None:
        dialog = Gtk.AlertDialog()
        dialog.set_message("Error")
        dialog.set_detail(message)
        dialog.set_buttons(["OK"])
        dialog.show(self)
