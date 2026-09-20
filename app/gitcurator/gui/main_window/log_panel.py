#!/usr/bin/env python3
"""gitcurator.gui.main_window.log_panel — the log-panel mixin.

Colored HTML log appends (theme-aware), filter/search re-render, panel
toggle and clear (verbatim methods of the original MainWindow).
"""

from datetime import datetime
from typing import Dict
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["LogPanelMixin"]


class LogPanelMixin:
    """LogPanelMixin — see module docstring (methods are verbatim moves)."""


    def _toggle_log_panel(self):
        """Toggle the log panel visibility."""
        # Find the right_widget (log panel) via the splitter
        splitter = self.findChild(QSplitter)
        if splitter and splitter.count() >= 2:
            log_widget = splitter.widget(1)
            is_visible = log_widget.isVisible()
            if is_visible:
                log_widget.setVisible(False)
                self.log_toggle_btn.setText("📋 Show Log")
                splitter.setSizes([600, 0])
            else:
                log_widget.setVisible(True)
                self.log_toggle_btn.setText("📋 Hide Log")
                splitter.setSizes([400, 300])

    def _set_log_filter(self, filter_type):
        """Set the log filter and re-render the log panel."""
        self._log_filter = filter_type
        # Update button checked states (only the active filter is checked)
        self.log_filter_all.setChecked(filter_type == "all")
        self.log_filter_errors.setChecked(filter_type == "error")
        self.log_filter_warnings.setChecked(filter_type == "warning")
        self.log_filter_success.setChecked(filter_type == "success")
        self._filter_log()

    def _filter_log(self):
        """Re-render the log panel from `self._all_log_entries` applying the
        current level filter + search text."""
        search = self.log_search.text().lower() if hasattr(self, 'log_search') else ""
        if not hasattr(self, '_all_log_entries'):
            self._all_log_entries = []

        # v31.1: theme-aware log colors (see _log_html_colors).
        html_color_map = self._log_html_colors()


        # Suppress auto-scroll flicker while we rebuild the log.
        self.log_text.clear()
        for entry in self._all_log_entries:
            level = entry.get('level', 'info')
            msg = entry.get('msg', '')
            timestamp = entry.get('timestamp', '')

            # Filter by level
            if self._log_filter != "all" and level != self._log_filter:
                continue
            # Filter by search
            if search and search not in msg.lower():
                continue

            color = html_color_map.get(level, "#9E9E9E")
            safe_msg = _html_module.escape(msg, quote=False)
            html_line = (
                f'<span style="color:#666; font-family:Consolas,monospace;">[{timestamp}]</span> '
                f'<span style="color:{color}; font-family:Consolas,monospace;">{safe_msg}</span>'
            )
            self.log_text.append(html_line)

        # Jump to the bottom after re-rendering.
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _clear_log(self):
        """Clear both the visible log and the stored entry cache."""
        if hasattr(self, '_all_log_entries'):
            self._all_log_entries.clear()
        self.log_text.clear()

    def log_message(self, msg, level="info"):
        """Append a colored line to the GUI log and auto-scroll to the bottom.

        Uses HTML coloring so different log levels are visually distinct:
          - error   -> red   (#f44336 — visible on both light & dark)
          - warning -> amber (#FF9800 — visible on both light & dark)
          - success -> green (#4CAF50 — visible on both light & dark)
          - info    -> gray  (#9E9E9E — visible on both light & dark)

        Also stores the entry in `self._all_log_entries` so the GUI log can
        be re-rendered when the user changes the active filter or search text
        (see `_set_log_filter` / `_filter_log`).
        """
        # Terminal colors (for console output)
        color_map = {
            "info": Fore.GREEN,
            "warning": Fore.YELLOW,
            "error": Fore.RED,
            "success": Fore.CYAN
        }
        color = color_map.get(level, Fore.WHITE)

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Store entry for re-rendering on filter/search change
        if not hasattr(self, '_all_log_entries'):
            self._all_log_entries = []
        self._all_log_entries.append({'msg': str(msg), 'level': level, 'timestamp': timestamp})
        # Prevent memory leak — cap at 1000 entries (oldest are dropped)
        if len(self._all_log_entries) > 1000:
            self._all_log_entries = self._all_log_entries[-1000:]

        # HTML colors chosen to be readable on the ACTIVE theme background
        # (v31.1: theme-aware — dark shades in light mode, light in dark).
        html_color = self._log_html_colors().get(level, "#6C6480")

        # Apply current filter — skip rendering if the entry doesn't match.
        if self._log_filter != "all" and level != self._log_filter:
            return
        search = self.log_search.text().lower() if hasattr(self, 'log_search') else ""
        if search and search not in str(msg).lower():
            return

        # Escape HTML special chars in the message

        safe_msg = _html_module.escape(str(msg), quote=False)
        html_line = (
            f'<span style="color:#666; font-family:Consolas,monospace;">[{timestamp}]</span> '
            f'<span style="color:{html_color}; font-family:Consolas,monospace;">{safe_msg}</span>'
        )
        self.log_text.append(html_line)

        # Auto-scroll to newest line
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _log_html_colors(self) -> Dict[str, str]:
        """v31.1: log text colors matched to the ACTIVE theme so every level
        passes AA contrast on its own background (dark shades on the light
        log, light shades on the dark log)."""
        if getattr(self, '_dark_mode', False):
            return {
                "error":   "#F4A9B8",  # pastel rose on plum
                "warning": "#F2DCA8",  # butter on plum
                "success": "#AEE5C6",  # pastel mint on plum
                "info":    "#B7AFC9",  # lavender-grey on plum
            }
        return {
            "error":   "#AE2237",  # deep rose (6.8:1 on white)
            "warning": "#8A5B0B",  # deep butter (5.9:1 on white)
            "success": "#1E6B4B",  # deep mint (6.4:1 on white)
            "info":    "#57506B",  # deep mauve (7.0:1 on white)
        }
