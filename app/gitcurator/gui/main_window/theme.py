"""MainWindow ThemeMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md).

v0.30.0 (external Settings-UI audit, presentation-only): this mixin is now
a thin ROUTER onto gitcurator.gui.theme — the one app theme kit. The two
~90-rule per-mode stylesheets and the eight per-widget button stylesheets
live there as token tables + property selectors; apply_light_theme /
apply_dark_theme are one-line appliers, _style_btn sets the ``btn_kind``
dynamic property (+ repolish) instead of a per-widget stylesheet, and the
new _set_status / _set_badge_state helpers replace every inline
``setStyleSheet`` status color the app used to paint. The public surface
(apply_light_theme / apply_dark_theme / toggle_theme / _style_btn /
_style_btn kinds / _refresh_button_styles …) is unchanged — every caller
in every mixin keeps working untouched.
"""

import sys as _sys
import sys
import os
import re
import json
import time
import urllib.error
import sqlite3
import subprocess
import html as _html_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import threading
import logging
from logging.handlers import RotatingFileHandler

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import note_state as _note_state
from gitcurator.core import website_pipeline as _website_pipeline
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.gui import icons as _icons
from gitcurator.gui import theme as _theme
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)

_APP_DIR = APP_DIR

class ThemeMixin:
    """ThemeMixin"""

    # ------------------------------------------------------------------
    # UI polish helpers (Inter font, design-system buttons, animations)
    # ------------------------------------------------------------------
    def _load_fonts(self):
        """Load bundled Inter font if available, otherwise use system fallback.

        Scans ``assets/fonts/`` next to this script for any ``.ttf``/``.otf``
        file and registers it with Qt's font database. Logs the actual family
        names registered so the user can verify the font loaded correctly.
        """
        from PyQt6.QtGui import QFontDatabase
        fonts_dir = os.path.join(_APP_DIR, "assets", "fonts")
        loaded_families = []
        if os.path.isdir(fonts_dir):
            for font_file in os.listdir(fonts_dir):
                if font_file.lower().endswith(('.ttf', '.otf')):
                    font_path = os.path.join(fonts_dir, font_file)
                    try:
                        font_id = QFontDatabase.addApplicationFont(font_path)
                        if font_id != -1:
                            families = QFontDatabase.applicationFontFamilies(font_id)
                            loaded_families.extend(families)
                    except Exception:
                        pass
        if hasattr(self, 'log_text'):
            if loaded_families:
                self.log_message(f"🔤 Fonts loaded: {', '.join(loaded_families)}", "info")
            else:
                self.log_message("🔤 Using system font (place Inter TTFs in assets/fonts/ for modern look)", "info")
        else:
            print(f"[main] {'Fonts loaded: ' + ', '.join(loaded_families) if loaded_families else 'Using system font fallback'}",
                  file=_sys.stderr, flush=True)
        return len(loaded_families) > 0

    # ------------------------------------------------------------------
    # v0.30.0 — theme application: routers onto gui.theme (the kit).
    # ------------------------------------------------------------------
    def apply_light_theme(self):
        """Pastel-cream light mode — one call into the theme kit (palette
        + the full structural stylesheet; children inherit both)."""
        _theme.apply_app(self, dark=False)

    def apply_dark_theme(self):
        """Pastel-night dark mode (the plum palette) — one call into the
        theme kit."""
        _theme.apply_app(self, dark=True)

    def _style_btn(self, btn, kind: str):
        """Apply a design-system variant to a button and track it.

        v0.30.0: the variant is a ``btn_kind`` dynamic PROPERTY resolved by
        the app stylesheet's property selectors — no per-widget stylesheet.
        A theme flip re-applies the whole stylesheet, so both modes restyle
        automatically; the tracking list stays for API compatibility (and
        for the explicit repolish below).
        """
        if not hasattr(self, '_ds_buttons'):
            self._ds_buttons = []
        self._ds_buttons = [(b, k) for (b, k) in self._ds_buttons if b is not btn]
        self._ds_buttons.append((btn, kind))
        btn.setProperty('btn_kind', kind)
        _theme.repolish(btn)
        return btn

    def _refresh_button_styles(self):
        """Re-apply tracked button variants after a theme change.

        v0.30.0: the stylesheet covers both modes, so this is now a plain
        repolish (kept for the startup path + any external caller)."""
        for btn, _kind in getattr(self, '_ds_buttons', []):
            try:
                _theme.repolish(btn)
            except RuntimeError:
                pass  # widget already destroyed

    # -- property-role helpers (the replacement for inline status colors) --

    def _set_status(self, label, state: str, strong: bool = False):
        """Paint a QLabel as a semantic status line (success / warning /
        error / muted) via the ``role``/``state`` QSS properties — the
        theme-aware replacement for every per-label ``setStyleSheet``
        color the app used to compute by hand. ``strong`` renders the
        13px bold treatment the Backup tab's status dots use."""
        label.setProperty('role', 'status')
        label.setProperty('state', state)
        label.setProperty('strong', 'true' if strong else 'false')
        _theme.repolish(label)

    def _set_badge_state(self, badge, state: str):
        """Paint a QLabel as a stateful pill badge (``pending`` zinc /
        ``ok`` mint) via QSS properties."""
        badge.setProperty('role', 'badge')
        badge.setProperty('state', state)
        _theme.repolish(badge)

    def _wrap_scroll(self, content: QWidget) -> QScrollArea:
        """Wrap a tab's content in a scrollable container.

        The window is fixed at 1000×750, so any tab whose natural content is
        taller than the tab pane scrolls instead of stretching. Content always
        starts at the same top position and keeps its natural height (no
        padded/fixed-height containers). The content widget carries the
        `tab_sheet` object name so the theme QSS paints it a solid sheet
        color (never a transparent/rgba fill — see the kit's sheet token).

        v32.2: the scroll is VERTICAL-ONLY — the horizontal bar is always
        off and the widget is resized to the viewport width
        (setWidgetResizable), so content wraps (wordWrap labels) instead
        of ever scrolling sideways.

        v33.1: the page widget is TOP-ALIGNED inside a sheet-colored holder
        with a trailing stretch. Fixes the Settings 'Vault' defect: with
        widgetResizable, a short page is stretched to the viewport height and
        QVBoxLayout gave ALL the extra space to the page's only vertically
        growable item — a plain QLabel — whose vertically-centered text made
        it look like a giant gap between the label and the fields below."""
        content.setObjectName("tab_sheet")
        holder = QWidget()
        holder.setObjectName("tab_sheet")
        holder_lay = QVBoxLayout(holder)
        holder_lay.setContentsMargins(0, 0, 0, 0)
        holder_lay.setSpacing(0)
        holder_lay.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
        holder_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        return scroll

    def _animate_dialog(self, dialog):
        """Apply a subtle fade-in animation to a dialog.

        IMPORTANT: The animation object is stored as a child of the dialog
        (via setParent) so it survives until the dialog is destroyed.
        Without this, Python's GC collects it mid-fade and the dialog
        stays at opacity 0 = invisible but blocking.
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QTimer
        # Set initial opacity
        dialog.setWindowOpacity(0.0)
        # Create fade-in animation — store on dialog to prevent GC
        animation = QPropertyAnimation(dialog, b"windowOpacity")
        animation.setParent(dialog)  # CRITICAL: prevent GC from collecting it
        animation.setDuration(200)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Start after a tiny delay so the dialog is positioned
        QTimer.singleShot(10, animation.start)
        # Safety: if animation fails for any reason, force opacity to 1.0
        # after 500ms so the dialog is never stuck invisible
        QTimer.singleShot(500, lambda: dialog.setWindowOpacity(1.0))
        return animation

    def toggle_theme(self):
        """Toggle between light and dark theme."""
        self._dark_mode = not getattr(self, '_dark_mode', False)
        if self._dark_mode:
            self.apply_dark_theme()
            self.log_message("🌙 Dark mode enabled", "info")
        else:
            self.apply_light_theme()
            self.log_message("☀️ Light mode enabled", "info")
        # Sync the Settings-menu check state and re-apply the (theme-aware)
        # outlined variants on the persistent window buttons.
        self.theme_btn.setChecked(self._dark_mode)
        self._sync_theme_toggle_btn()  # v32.1: header icon button
        self._refresh_button_styles()  # v0.30.0: plain repolish (QSS is two-mode)
        self._refresh_main_icons()     # v0.07: re-tint baked icon pixmaps
        # v32.2: the three Backup-tab status dots carry theme-aware text
        # colors — re-run their refreshers so they don't keep the previous
        # theme's palette after a toggle (Good Repos stayed deep-butter on
        # plum, ~2.5:1, while VaultSeal refreshed correctly).
        self._vaultseal_refresh_status()
        self._goodrepos_refresh_status()
        self._backup_refresh_status()
        # v0.30.0: re-tint the Settings sidebar's two-mode nav icons (the
        # Selected-mode pixmap swaps white ⇄ plum with the mode).
        self._refresh_settings_nav_icons()
        self.save_config()  # persist the theme choice

    def _refresh_settings_nav_icons(self):
        """v0.30.0: re-tint the Settings window's sidebar icons after a
        theme flip. Duck-typed — any open top-level dialog that renders
        nav icons (SettingsDialog) gets the call; nothing breaks when it
        is closed."""
        from PyQt6.QtWidgets import QApplication
        for dlg in QApplication.topLevelWidgets():
            refresh = getattr(dlg, 'refresh_nav_icons', None)
            if callable(refresh):
                try:
                    refresh()
                except RuntimeError:
                    pass  # dialog mid-close

    def _hero_text_color(self) -> str:
        """v0.31.0 (balance pass): the SYNC fill is theme-aware (deep
        violet in light mode, pastel lavender in dark), so the baked glyph
        tint must follow the ACTIVE theme's hero_text token instead of the
        old shared constant."""
        t = _theme.DARK if getattr(self, '_dark_mode', False) else _theme.LIGHT
        return t['hero_text']

    def _sync_theme_toggle_btn(self):
        """v32.1: keep the ALWAYS-VISIBLE header light/dark toggle in sync.

        v0.07: the glyph is the unified sun/moon SVG (was a full-color emoji)
        tinted with the active accent; the tooltip names the current mode —
        icon + text, never color alone (WCAG 1.4.1)."""
        if getattr(self, '_dark_mode', False):
            _icons.set_btn_icon(self.theme_toggle_btn, 'sun', COLORS['primary_dark'], 16)
            self.theme_toggle_btn.setToolTip("Switch to light mode (current: Dark)")
        else:
            _icons.set_btn_icon(self.theme_toggle_btn, 'moon', COLORS['primary'], 16)
            self.theme_toggle_btn.setToolTip("Switch to dark mode (current: Light)")

    def _log_html_colors(self) -> Dict[str, str]:
        """The LOG GLYPH colors, matched to the ACTIVE theme so every
        level passes the 3:1 graphics bar on the flat log tint. v0.32
        (five-change pass): the MESSAGE ink is now ONE muted tone for
        every level (the log_text / log_ts tokens — see _log_row_html);
        these per-level colors tint the shape-coded glyphs only."""
        if getattr(self, '_dark_mode', False):
            # v0.09.1 polish: saturated accents (pastels read muddy on plum).
            return {
                "error":   "#FF9AAB",  # vivid rose on plum (~7.5:1)
                "warning": "#FFD37E",  # vivid butter on plum (~11:1)
                "success": "#7CE2A9",  # vivid mint on plum (~9.5:1)
                "info":    "#C6BFE0",  # lifted lavender-grey on plum
            }
        return {
            "error":   "#AE2237",  # deep rose (6.8:1 on white)
            "warning": "#75510A",  # deep butter (~6.6:1 on white)
            "success": "#1E6B4B",  # deep mint (6.4:1 on white)
            "info":    "#57506B",  # deep mauve (7.0:1 on white)
        }

    def _refresh_main_icons(self):
        """v0.07: re-tint the theme-dependent main-screen glyphs after a
        theme flip (the icon colors are baked into pixmaps at render time,
        so they need one explicit refresh — like _refresh_button_styles).

        v0.31.0 (balance pass): also re-renders the pipeline-state glyph,
        the empty-state glyph and the LOG ROWS themselves — row colors and
        level glyphs are baked into the HTML at render time, so a flip
        re-renders the list (via _filter_log) instead of leaving the old
        mode's colors behind.
        """
        try:
            dark = getattr(self, '_dark_mode', False)
            accent = COLORS['primary_dark'] if dark else COLORS['primary']
            # Theme toggle shows the mode you'll switch TO.
            if dark:
                _icons.set_btn_icon(self.theme_toggle_btn, 'sun', accent, 16)
            else:
                _icons.set_btn_icon(self.theme_toggle_btn, 'moon', accent, 16)
            _icons.set_btn_icon(self.settings_btn, 'settings', accent, 16)
            # Test Connectivity: activity glyph in the active accent.
            _icons.set_btn_icon(self.test_btn, 'activity', accent, 18)
            # Hero state glyph (fetching's gray loader stays neutral;
            # v0.32: 'running' wears the white Stop square on the danger
            # fill — the same button, no separate STOP control).
            state = getattr(self, '_hero_state', 'sync')
            if state == 'running':
                _icons.set_btn_icon(self.start_btn, 'stop', '#FFFFFF', 18)
            elif state == 'fetching':
                _icons.set_btn_icon(self.start_btn, 'loader', '#6C6480', 18)
            else:
                _icons.set_btn_icon(self.start_btn, 'refresh',
                                    self._hero_text_color(), 18)
            # v0.32: the retry banner's warning glyph re-tints too.
            self._refresh_retry_banner_tint()
            # v0.31.0: the pipeline-state glyph + the empty-state glyph.
            self._set_pipeline_state(getattr(self, '_pipeline_state', 'idle'))
            if hasattr(self, '_log_empty_icon'):
                t = _theme.DARK if dark else _theme.LIGHT
                self._log_empty_icon.setPixmap(
                    _icons.pixmap('inbox', t['text_muted'], 36))
            # Log panel controls.
            hint = COLORS['hint_dark'] if dark else COLORS['hint_light']
            if hasattr(self, '_log_search_action'):
                self._log_search_action.setIcon(_icons.icon('search', hint, 16))
            if getattr(self, '_clear_log_btn', None) is not None:
                trash_color = '#B7AFC9' if dark else '#6C6480'
                _icons.set_btn_icon(self._clear_log_btn, 'trash', trash_color, 14)
            # v0.31.0: the log rows re-render with the new mode's colors
            # (level glyphs re-register inside _filter_log).
            self._log_icons_theme = None
            self._filter_log()
        except RuntimeError:
            pass  # widgets already destroyed during shutdown

    def _show_custom_message_box(self, title: str, message: str, success: bool = True):
        """Show a custom message box with theme-aware colors.
        Works in both light and dark mode.

        v0.30.0 (audit): DE-STYLED — no inline colors anywhere. The glyph
        and heading wear message-box roles (``msg_glyph`` / ``msg_heading``
        + a ``tone`` property) resolved by the app stylesheet in both
        modes; the OK button rides the design-system 'primary' variant.

        v0.06 — Fix (zombie process): if the main window is closing (or was
        already closed) the message is logged instead of shown — a modal
        opened after the window is gone blocks app.exec() forever."""
        if getattr(self, '_closing', False) or not self.isVisible():
            try:
                self.log_message(f"{title}: {message}", "info")
            except Exception:
                pass
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Icon + title row (roles: glyph + heading-with-tone)
        header = QHBoxLayout()
        if success:
            icon_label = QLabel("✅")
            title_text = " Success!"
            tone = "success"
        else:
            icon_label = QLabel("❌")
            title_text = " Error"
            tone = "error"
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)

        title_label = QLabel(title_text)
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", tone)
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        # Message (plain label — the themed QWidget rule colors the text)
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # OK button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        self._style_btn(ok_btn, 'primary')
        ok_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        dialog.exec()

    def _show_custom_question(self, title: str, message: str) -> bool:
        """Show a theme-aware Yes/No question dialog.
        Returns True if user clicks Yes, False otherwise.

        v0.30.0 (audit): DE-STYLED — roles instead of inline colors (see
        _show_custom_message_box); Yes rides the 'danger' variant (it is
        the destructive answer), No the 'secondary' outline.

        v0.06 — Fix (zombie process): during shutdown there is no one to
        answer a question — return False (the safe default) and log it,
        never open a modal."""
        if getattr(self, '_closing', False) or not self.isVisible():
            try:
                self.log_message(f"(auto-answer No during shutdown) {title}: {message}", "info")
            except Exception:
                pass
            return False
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Icon + title (roles: glyph + heading-with-tone)
        header = QHBoxLayout()
        icon_label = QLabel("⚠️")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "warning")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # Yes/No buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        no_btn = QPushButton("No")
        self._style_btn(no_btn, 'secondary')
        no_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(no_btn)
        yes_btn = QPushButton("Yes")
        self._style_btn(yes_btn, 'danger')
        yes_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(yes_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        result = dialog.exec()
        return result == QDialog.DialogCode.Accepted
