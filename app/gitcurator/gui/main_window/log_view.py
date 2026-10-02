"""gitcurator.gui.main_window.log_view — the log card's LIST area (v0.31.0).

The main-window balance pass reworks the Progress Logs panel: a fixed
toolbar (filter chips · search · clear) at the top of the card and, below
it, this scrolling list. It is a read-only QTextEdit specialized for the
log ROW contract:

* one row per entry — leading status glyph, muted timestamp, message
  (the HTML itself is composed by ``UiMixin._log_row_html``; this widget
  owns no formatting decisions);
* consistent row height — ``NoWrap`` + no horizontal scrollbar + a
  measured truncation (UiMixin elides to the view width); the FULL text
  rides in a per-row tooltip;
* a small inter-row gap via a per-block bottom margin (2px);
* width-aware re-render — a resize (debounced 120ms) asks the owner to
  re-render so truncation follows the new width.

Presentation-only: no filter logic, no entry storage — those live in
UiMixin (``_all_log_entries`` + ``_render_log``).
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QTextBlockFormat, QTextCursor
from PyQt6.QtWidgets import QTextEdit, QToolTip

# The inter-row gap (instruction: "a subtle separator or a small gap
# between rows"). 2px reads as spacing, not a divider.
ROW_BOTTOM_MARGIN = 2.0


class LogView(QTextEdit):
    """The log list: per-row tooltips, block margins, resize re-render."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # One line per row: never wrap, never scroll sideways — messages
        # are elided to the view width by the renderer instead.
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        # blockNumber -> full message (for the per-row tooltip)
        self._block_full_text: Dict[int, str] = {}
        # Owner callback (UiMixin._render_log) — fired (debounced) on
        # resize so truncation follows the new width.
        self._rerender_callback: Optional[Callable[[], None]] = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._fire_rerender)

    # -- owner wiring -------------------------------------------------------

    def set_rerender_callback(self, callback: Callable[[], None]) -> None:
        self._rerender_callback = callback

    def _fire_rerender(self) -> None:
        if callable(self._rerender_callback):
            try:
                self._rerender_callback()
            except RuntimeError:
                pass  # widgets already destroyed during shutdown

    # -- row bookkeeping -----------------------------------------------------

    def remember_full_text(self, block_number: int, full_text: str) -> None:
        """Map one rendered row (block) to its full, untruncated message."""
        self._block_full_text[int(block_number)] = full_text

    def reset_full_texts(self) -> None:
        self._block_full_text.clear()

    def apply_row_format(self) -> None:
        """Give the LAST block the inter-row gap (call right after append)."""
        cursor = QTextCursor(self.document().lastBlock())
        fmt = QTextBlockFormat()
        fmt.setTopMargin(0.0)
        fmt.setBottomMargin(ROW_BOTTOM_MARGIN)
        cursor.setBlockFormat(fmt)

    # -- Qt overrides ---------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-render (debounced) so truncation tracks the new width.
        self._resize_timer.start()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        try:
            pos: QPoint = event.position().toPoint()
            cursor = self.cursorForPosition(pos)
            full = self._block_full_text.get(cursor.blockNumber())
            if full:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  full, self)
            else:
                QToolTip.hideText()
        except RuntimeError:
            pass  # document already gone (shutdown)
