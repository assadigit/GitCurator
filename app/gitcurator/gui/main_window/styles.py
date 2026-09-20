#!/usr/bin/env python3
"""gitcurator.gui.main_window.styles — the theming + styling mixin.

Font loading (Inter), pastel cream-day / plum-night palettes, button
kind styles, scroll wrappers, dialog animation, and the light/dark
theme application + toggle (verbatim methods of the original
MainWindow).
"""

import os
from typing import Dict, Optional
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["StyleMixin"]


class StyleMixin:
    """StyleMixin — see module docstring (methods are verbatim moves)."""


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
    # v31.1 — UI/UX spec helpers: 3-variant button hierarchy + per-tab
    # scrollable containers for the fixed 1000×750 window.
    # ------------------------------------------------------------------
    def _accent(self) -> str:
        """Secondary accent (pastel violet) tuned for the active theme:
        #5F54B4 on light surfaces (6.2:1), pastel lavender #C4BCF5 on the
        dark plum panels (8.2:1) — outline text/border contrast."""
        return COLORS['primary_dark'] if getattr(self, '_dark_mode', False) else COLORS['primary']

    def _panel_bg(self) -> str:
        """Solid panel/sheet color for the active theme. Used instead of
        `transparent`/rgba backgrounds — Qt's QSS composits semi-transparent
        widget backgrounds over a LIGHT base, which breaks dark mode.
        v32 pastel: white sheets on cream (light), plum sheets (dark)."""
        return '#2B2639' if getattr(self, '_dark_mode', False) else '#FFFFFF'

    def _panel_bg_alt(self) -> str:
        """Muted/disabled panel color for the active theme."""
        return '#231F30' if getattr(self, '_dark_mode', False) else '#F2EDE3'

    def _status_colors(self) -> Dict[str, str]:
        """v32 pastel: theme-aware semantic TEXT colors for status labels.
        Light: deep tones on white sheets. Dark: pastel accents on plum."""
        if getattr(self, '_dark_mode', False):
            return {
                "success": "#AEE5C6",  # pastel mint on plum (10.3:1)
                "warning": "#F2DCA8",  # butter on plum
                "error":   "#F4BCC8",  # pastel rose on plum
                "muted":   "#B7AFC9",  # lavender-grey on plum
            }
        return {
            "success": "#1E6B4B",  # deep mint on white (6.4:1)
            "warning": "#8A5B0B",  # deep butter on white (5.9:1)
            "error":   "#AE2237",  # deep rose on white (6.8:1)
            "muted":   "#6C6480",  # mauve on white (5.6:1)
        }

    def _btn_kind_style(self, kind: str) -> str:
        """Stylesheet for one of the THREE action-button variants (v32 pastel):
          'primary'   — FILLED pastel mint + deep-forest text (max ONE per tab)
          'secondary' — outlined violet (theme-aware), panel bg
          'danger'    — FILLED pastel rose + deep-rose text (destructive only)
          'ghost'     — small quiet utility (log-panel controls only)
        """
        if kind == 'secondary':
            c = self._accent()
            bg = self._panel_bg()
            bg_alt = self._panel_bg_alt()
            hover_fill = '#ECE9FA' if getattr(self, '_dark_mode', False) else '#ECE9FA'
            return f"""
                QPushButton {{
                    background-color: {bg};
                    color: {c};
                    border: 1px solid {c};
                    font-weight: 600;
                    padding: 8px 16px;
                    border-radius: 6px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background-color: {hover_fill}; color: {COLORS['primary_hover']}; border-color: {COLORS['primary_hover']}; }}
                QPushButton:pressed {{ background-color: {COLORS['primary_hover']}; color: #FFFFFF; }}
                QPushButton:disabled {{ color: #A79F92; border-color: {bg_alt}; background-color: {bg}; }}
            """
        if kind == 'danger':
            return self._btn_style(COLORS['error'], COLORS['error_hover'],
                                   text=COLORS['error_text'])
        if kind == 'ghost':
            bg = '#2B2639' if getattr(self, '_dark_mode', False) else '#FBF8F2'
            hover_bg = '#352F4A' if getattr(self, '_dark_mode', False) else '#F2EDE3'
            hover_fg = '#DDD7EC' if getattr(self, '_dark_mode', False) else '#57506B'
            return f"""
                QPushButton {{
                    background-color: {bg};
                    color: #6C6480;
                    border: none;
                    font-weight: 500;
                    padding: 4px 8px;
                    border-radius: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background-color: {hover_bg}; color: {hover_fg}; }}
                QPushButton:pressed {{ background-color: {hover_bg}; }}
                QPushButton:disabled {{ color: #A79F92; }}
            """
        if kind == 'icon':
            # v32.1: square ICON-ONLY header button (the always-visible
            # light/dark toggle). 16px glyph on a 38×36 target, panel bg +
            # accent text so it reads on both themes (violet 6.2:1 on white,
            # lavender 8.2:1 on plum).
            bg = '#2B2639' if getattr(self, '_dark_mode', False) else '#FFFFFF'
            hover_bg = '#352F4A' if getattr(self, '_dark_mode', False) else '#F2EDE3'
            border = '#4A4263' if getattr(self, '_dark_mode', False) else '#D8D0BE'
            fg = '#C4BCF5' if getattr(self, '_dark_mode', False) else '#5F54B4'
            return f"""
                QPushButton {{
                    background-color: {bg};
                    color: {fg};
                    border: 1px solid {border};
                    font-size: 16px;
                    font-weight: 600;
                    padding: 0;
                    border-radius: 8px;
                }}
                QPushButton:hover {{ background-color: {hover_bg}; border-color: {fg}; }}
                QPushButton:pressed {{ background-color: {hover_bg}; }}
                QPushButton:focus {{ outline: 2px solid {self._accent()}; outline-offset: 2px; }}
            """
        # default: 'primary' — filled pastel mint + deep-forest text
        return self._btn_style(COLORS['cta'], COLORS['cta_hover'],
                               text=COLORS['cta_text'])

    def _style_btn(self, btn, kind: str):
        """Apply a design-system variant to a persistent window button and
        track it so the variants can be re-applied when the theme flips
        (outline text/border is theme-aware)."""
        if not hasattr(self, '_ds_buttons'):
            self._ds_buttons = []
        self._ds_buttons = [(b, k) for (b, k) in self._ds_buttons if b is not btn]
        self._ds_buttons.append((btn, kind))
        btn.setStyleSheet(self._btn_kind_style(kind))
        return btn

    def _refresh_button_styles(self):
        """Re-apply tracked button variants after a theme change."""
        for btn, kind in getattr(self, '_ds_buttons', []):
            try:
                btn.setStyleSheet(self._btn_kind_style(kind))
            except RuntimeError:
                pass  # widget already destroyed

    def _wrap_scroll(self, content: QWidget) -> QScrollArea:
        """Wrap a tab's content in a scrollable container.

        The window is fixed at 1000×750, so any tab whose natural content is
        taller than the tab pane scrolls instead of stretching. Content always
        starts at the same top position and keeps its natural height (no
        padded/fixed-height containers). The content widget carries the
        `tab_sheet` object name so the theme QSS paints it a solid sheet
        color (never a transparent/rgba fill — see _panel_bg).

        v32.2: the scroll is VERTICAL-ONLY — the horizontal bar is always
        off and the widget is resized to the viewport width
        (setWidgetResizable), so content wraps (wordWrap labels) instead
        of ever scrolling sideways."""
        content.setObjectName("tab_sheet")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _btn_style(self, color: str, hover: str, variant: str = 'solid',
                   text: Optional[str] = None) -> str:
        """Return a QSS stylesheet for a colored button.

        Variants (v32 pastel):
        - 'solid':    Filled background; ``text`` is the ON-FILL text color
                      (deep companions for pastel fills — AA on both hovers).
        - 'outline':  Panel bg, colored border+text (SECONDARY actions)
        - 'ghost':    Panel bg, gray text (TERTIARY/utility actions)
        """
        if variant == 'outline':
            bg = self._panel_bg()
            bg_alt = self._panel_bg_alt()
            return f"""
                QPushButton {{
                    background-color: {bg};
                    color: {color};
                    border: 1px solid {color};
                    font-weight: 600;
                    padding: 8px 16px;
                    border-radius: 6px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background-color: #F2EDE3; color: {color}; border-color: {color}; }}
                QPushButton:pressed {{ background-color: {hover}; color: #FFFFFF; }}
                QPushButton:disabled {{ color: #A79F92; border-color: {bg_alt}; background-color: {bg}; }}
            """
        elif variant == 'ghost':
            return f"""
                QPushButton {{
                    background-color: transparent;
                    color: #6C6480;
                    border: none;
                    font-weight: 500;
                    padding: 4px 8px;
                    border-radius: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background-color: #F2EDE3; color: #57506B; }}
                QPushButton:pressed {{ background-color: #EAE3D6; }}
                QPushButton:disabled {{ color: #A79F92; }}
            """
        else:  # solid (default) — pastel fill + deep companion text
            fg = text if text else "#FFFFFF"
            return f"""
                QPushButton {{
                    background-color: {color};
                    color: {fg};
                    font-weight: bold;
                    padding: 8px 24px;
                    border: none;
                    border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:pressed {{ background-color: {hover}; }}
            QPushButton:disabled {{ background-color: #ECE6DA; color: #9B937F; }}
        """

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

    def apply_light_theme(self):
        """Set the v32 PASTEL CREAM light theme — warm cream surfaces, plum
        text, mint/violet/rose pastel actions.

        Design tokens (v32 pastel, all text pairs AA-verified):
          - 60% background — warm cream #FBF8F2, white sheets #FFFFFF
          - Text — soft plum #423A52 (10.1:1 on cream)
          - 30% accent — violet #5F54B4 (6.2:1 on white); focus ring #8B80D6
          - Primary fill — pastel mint #B9E3C9 + deep-forest #17402B (8.3:1)
          - 8px spacing scale, 4-size type scale, 2px focus outlines
        """
        palette = QPalette()
        # 60% background — warm cream #FBF8F2 (NOT pure white)
        palette.setColor(QPalette.ColorRole.Window, QColor(0xFB, 0xF8, 0xF2))
        # Soft plum text #423A52 (NOT pure black)
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0x42, 0x3A, 0x52))
        # Input background — pure white is OK for inputs
        palette.setColor(QPalette.ColorRole.Base, QColor(0xFF, 0xFF, 0xFF))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(0xF2, 0xED, 0xE3))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0x42, 0x3A, 0x52))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0xFB, 0xF8, 0xF2))
        palette.setColor(QPalette.ColorRole.Text, QColor(0x42, 0x3A, 0x52))
        palette.setColor(QPalette.ColorRole.Button, QColor(0xFF, 0xFF, 0xFF))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0x42, 0x3A, 0x52))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(0xAE, 0x22, 0x37))
        # 30% accent — violet #5F54B4 (the QSS below carries the visible
        # accent — this palette entry covers native palettes)
        palette.setColor(QPalette.ColorRole.Link, QColor(0x5F, 0x54, 0xB4))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0x5F, 0x54, 0xB4))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
        self.setPalette(palette)

        # Global app stylesheet — v32 PASTEL CREAM design system (light):
        # cream #FBF8F2 bg, white sheets, warm-sand borders #EAE3D6,
        # violet accent #5F54B4, focus ring #8B80D6, pastel mint progress.
        self.setStyleSheet("""
            QMainWindow { background-color: #FBF8F2; }
            QWidget { font-family: 'Segoe UI', 'SF Pro Display', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; color: #423A52; }
            QTabWidget::pane { border: 1px solid #EAE3D6; border-radius: 8px; top: -1px; background: #FFFFFF; }
            QTabBar::tab { background: #F2EDE3; border: none; border-bottom: 3px solid transparent; padding: 8px 16px; margin-right: 2px; font-weight: 500; color: #6C6480; }
            QTabBar::tab:selected { background: #FFFFFF; border-bottom: 3px solid #5F54B4; color: #5F54B4; }
            QTabBar::tab:hover:!selected { background: #EAE3D6; color: #57506B; }
            QGroupBox { font-weight: 600; font-size: 16px; border: 1px solid #EAE3D6; border-radius: 8px; margin-top: 16px; padding: 16px 8px 8px 8px; background: #FFFFFF; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #514A63; }
            QLineEdit { padding: 8px; border: 1px solid #D8D0BE; border-radius: 6px; background: #FFFFFF; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QLineEdit:focus { border: 2px solid #5F54B4; padding: 7px; outline: 2px solid #8B80D6; outline-offset: 2px; }
            QLineEdit:disabled { background: #F2EDE3; color: #A79F92; }
            QComboBox { padding: 8px; border: 1px solid #D8D0BE; border-radius: 6px; background: #FFFFFF; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QComboBox:focus { border: 2px solid #5F54B4; padding: 7px; outline: 2px solid #8B80D6; outline-offset: 2px; }
            QComboBox:disabled { background: #F2EDE3; color: #A79F92; }
            QComboBox QAbstractItemView { background: #FFFFFF; color: #423A52; selection-background-color: #5F54B4; selection-color: #FFFFFF; border: 1px solid #EAE3D6; outline: none; }
            QCheckBox { spacing: 8px; color: #514A63; }
            QCheckBox:focus { outline: 2px solid #8B80D6; outline-offset: 2px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #D8D0BE; border-radius: 4px; background: #FFFFFF; }
            QCheckBox::indicator:checked { background: #5F54B4; border-color: #5F54B4; }
            QCheckBox::indicator:hover { border-color: #5F54B4; }
            QPushButton { padding: 8px 16px; border: 1px solid #D8D0BE; border-radius: 6px; background: #FFFFFF; font-weight: bold; color: #423A52; }
            QPushButton:hover { background: #F2EDE3; border-color: #B5AC9C; }
            QPushButton:pressed { background: #EAE3D6; }
            QPushButton:disabled { color: #A79F92; background: #F2EDE3; border-color: #EAE3D6; }
            QPushButton:focus { outline: 2px solid #8B80D6; outline-offset: 2px; }
            QToolButton { padding: 8px 16px; border: 1px solid #8B80D6; border-radius: 6px; background-color: #FFFFFF; color: #5F54B4; font-weight: 600; font-size: 13px; }
            QToolButton:hover { background-color: #5F54B4; color: #FFFFFF; }
            QToolButton:pressed { background-color: #514699; color: #FFFFFF; }
            QToolButton:focus { outline: 2px solid #8B80D6; outline-offset: 2px; }
            QToolButton::menu-indicator { image: none; width: 0; }
            QMenu { background-color: #FFFFFF; border: 1px solid #EAE3D6; border-radius: 8px; padding: 8px 0; }
            QMenu::item { padding: 8px 24px; color: #423A52; }
            QMenu::item:selected { background: #5F54B4; color: #FFFFFF; }
            QMenu::separator { height: 1px; background: #EAE3D6; margin: 8px 0; }
            QMenu::item:disabled { color: #A79F92; }
            QScrollArea { border: none; background-color: #FFFFFF; }
            QWidget#tab_sheet { background-color: #FFFFFF; }
            QLabel#info_header { background-color: #F2EDE3; border-radius: 4px; padding: 8px; font-size: 12px; color: #514A63; }
            QLabel#info_note { background-color: #E9F5EE; border-radius: 4px; padding: 8px; font-size: 12px; color: #423A52; }
            QLabel#info_note_indigo { background-color: #EEEBFA; border-radius: 4px; padding: 8px; font-size: 12px; color: #423A52; }
            QTextEdit { border: 1px solid #EAE3D6; border-radius: 8px; background: #FDFCF8; padding: 8px; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QTextEdit:read-only { font-family: 'Consolas', 'Monaco', 'Menlo', 'Courier New', monospace; font-size: 12px; }
            QTextEdit:focus { border: 2px solid #5F54B4; padding: 7px; outline: 2px solid #8B80D6; outline-offset: 2px; }
            QProgressBar { border: none; border-radius: 6px; background: #EAE3D6; text-align: center; height: 24px; font-size: 12px; color: #514A63; }
            QProgressBar::chunk { background: #A8DABA; border-radius: 6px; }
            QLabel { color: #423A52; }
            QLabel#proxy_status_text { color: #57506B; font-size: 12px; }
            QRadioButton { spacing: 8px; padding: 2px; color: #514A63; }
            QRadioButton:focus { outline: 2px solid #8B80D6; outline-offset: 2px; }
            QRadioButton::indicator { width: 16px; height: 16px; border: 2px solid #D8D0BE; border-radius: 8px; background: #FFFFFF; }
            QRadioButton::indicator:checked { border-color: #5F54B4; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #5F54B4, stop:0.5 #5F54B4, stop:0.5 transparent, stop:1 transparent); }
            QRadioButton::indicator:hover { border-color: #5F54B4; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
            QScrollBar::handle:vertical { background: #DCD4C4; border-radius: 4px; min-height: 24px; }
            QScrollBar::handle:vertical:hover { background: #B5AC9C; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
            QScrollBar::handle:horizontal { background: #DCD4C4; border-radius: 4px; min-width: 24px; }
            QScrollBar::handle:horizontal:hover { background: #B5AC9C; }
            QScrollBar::add-line:horizontal { height: 0; width: 0; }
            QScrollBar::add-page:horizontal { background: transparent; }
        """)
    def apply_dark_theme(self):
        """Set the v32 PASTEL NIGHT dark theme — soft plum surfaces, warm
        white text, pastel lavender/mint accents.

        Tokens (v32 pastel, AA-verified on the plum panels):
          - Background: #221E2E (soft plum-charcoal)
          - Cards / inputs: #2B2639 (plum sheet) / alt #352F4A
          - Text: #F2EEE7 (warm white, 12.6:1) · muted #B7AFC9 (lavender-grey)
          - Accent: pastel lavender #C4BCF5 (8.2:1) for tabs/focus/outline
          - Selection fill: violet #5F54B4 (white text 6.2:1)
          - Pastel mint #A8DABA progress chunk pops on the plum track
        """
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0x22, 0x1E, 0x2E))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0xF2, 0xEE, 0xE7))
        palette.setColor(QPalette.ColorRole.Base, QColor(0x2B, 0x26, 0x39))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(0x35, 0x2F, 0x4A))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0xF2, 0xEE, 0xE7))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0x22, 0x1E, 0x2E))
        palette.setColor(QPalette.ColorRole.Text, QColor(0xF2, 0xEE, 0xE7))
        palette.setColor(QPalette.ColorRole.Button, QColor(0x2B, 0x26, 0x39))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0xF2, 0xEE, 0xE7))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(0xF4, 0xBC, 0xC8))
        palette.setColor(QPalette.ColorRole.Link, QColor(0xC4, 0xBC, 0xF5))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0x5F, 0x54, 0xB4))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
        self.setPalette(palette)

        # Global app stylesheet — v32 PASTEL NIGHT design system (dark):
        # plum #221E2E bg, plum sheets #2B2639, borders #3B344F,
        # lavender accent #C4BCF5, pastel mint progress chunk.
        self.setStyleSheet("""
            QMainWindow { background-color: #221E2E; }
            QWidget { font-family: 'Segoe UI', 'SF Pro Display', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; color: #F2EEE7; }
            QTabWidget::pane { border: 1px solid #3B344F; border-radius: 8px; top: -1px; background: #2B2639; }
            QTabBar::tab { background: #2B2639; border: none; border-bottom: 3px solid transparent; padding: 8px 16px; margin-right: 2px; font-weight: 500; color: #B7AFC9; }
            QTabBar::tab:selected { background: #352F4A; border-bottom: 3px solid #C4BCF5; color: #C4BCF5; }
            QTabBar::tab:hover:!selected { background: #352F4A; color: #DDD7EC; }
            QGroupBox { font-weight: 600; font-size: 16px; border: 1px solid #3B344F; border-radius: 8px; margin-top: 16px; padding: 16px 8px 8px 8px; background: #2B2639; color: #F2EEE7; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #DDD7EC; }
            QLineEdit { padding: 8px; border: 1px solid #4A4263; border-radius: 6px; background: #2B2639; color: #F2EEE7; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QLineEdit:focus { border: 2px solid #C4BCF5; padding: 7px; outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QLineEdit:disabled { background: #231F30; color: #7E7794; }
            QComboBox { padding: 8px; border: 1px solid #4A4263; border-radius: 6px; background: #2B2639; color: #F2EEE7; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QComboBox:focus { border: 2px solid #C4BCF5; padding: 7px; outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QComboBox:disabled { background: #231F30; color: #7E7794; }
            QComboBox QAbstractItemView { background: #2B2639; color: #F2EEE7; selection-background-color: #5F54B4; selection-color: #FFFFFF; border: 1px solid #3B344F; outline: none; }
            QCheckBox { spacing: 8px; color: #DDD7EC; }
            QCheckBox:focus { outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4A4263; border-radius: 4px; background: #2B2639; }
            QCheckBox::indicator:checked { background: #C4BCF5; border-color: #C4BCF5; }
            QCheckBox::indicator:hover { border-color: #C4BCF5; }
            QPushButton { padding: 8px 16px; border: 1px solid #4A4263; border-radius: 6px; background: #2B2639; font-weight: bold; color: #F2EEE7; }
            QPushButton:hover { background: #352F4A; border-color: #5C5378; }
            QPushButton:pressed { background: #2B2639; }
            QPushButton:disabled { color: #7E7794; background: #231F30; border-color: #2B2639; }
            QPushButton:focus { outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QToolButton { padding: 8px 16px; border: 1px solid #7A6EB8; border-radius: 6px; background-color: #2B2639; color: #C4BCF5; font-weight: 600; font-size: 13px; }
            QToolButton:hover { background-color: #5F54B4; color: #FFFFFF; border-color: #5F54B4; }
            QToolButton:pressed { background-color: #514699; color: #FFFFFF; }
            QToolButton:focus { outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QToolButton::menu-indicator { image: none; width: 0; }
            QMenu { background-color: #2B2639; border: 1px solid #3B344F; border-radius: 8px; padding: 8px 0; }
            QMenu::item { padding: 8px 24px; color: #F2EEE7; }
            QMenu::item:selected { background: #5F54B4; color: #FFFFFF; }
            QMenu::separator { height: 1px; background: #3B344F; margin: 8px 0; }
            QMenu::item:disabled { color: #7E7794; }
            QScrollArea { border: none; background-color: #2B2639; }
            QWidget#tab_sheet { background-color: #2B2639; }
            QLabel#info_header { background-color: #2B2639; border-radius: 4px; padding: 8px; font-size: 12px; color: #DDD7EC; }
            QLabel#info_note { background-color: #26332D; border-radius: 4px; padding: 8px; font-size: 12px; color: #DDD7EC; }
            QLabel#info_note_indigo { background-color: #2E2A4A; border-radius: 4px; padding: 8px; font-size: 12px; color: #DDD7EC; }
            QTextEdit { border: 1px solid #3B344F; border-radius: 8px; background: #241F31; padding: 8px; color: #F2EEE7; selection-background-color: #5F54B4; selection-color: #FFFFFF; }
            QTextEdit:read-only { font-family: 'Consolas', 'Monaco', 'Menlo', 'Courier New', monospace; font-size: 12px; }
            QTextEdit:focus { border: 2px solid #C4BCF5; padding: 7px; outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QProgressBar { border: none; border-radius: 6px; background: #352F4A; text-align: center; height: 24px; font-size: 12px; color: #DDD7EC; }
            QProgressBar::chunk { background: #A8DABA; border-radius: 6px; }
            QLabel { color: #F2EEE7; }
            QLabel#proxy_status_text { color: #B7AFC9; font-size: 12px; }
            QRadioButton { spacing: 8px; padding: 2px; color: #DDD7EC; }
            QRadioButton:focus { outline: 2px solid #C4BCF5; outline-offset: 2px; }
            QRadioButton::indicator { width: 16px; height: 16px; border: 2px solid #4A4263; border-radius: 8px; background: #2B2639; }
            QRadioButton::indicator:checked { border-color: #C4BCF5; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #C4BCF5, stop:0.5 #C4BCF5, stop:0.5 transparent, stop:1 transparent); }
            QRadioButton::indicator:hover { border-color: #C4BCF5; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
            QScrollBar::handle:vertical { background: #7A7199; border-radius: 4px; min-height: 24px; }
            QScrollBar::handle:vertical:hover { background: #8D84AD; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
            QScrollBar::handle:horizontal { background: #7A7199; border-radius: 4px; min-width: 24px; }
            QScrollBar::handle:horizontal:hover { background: #8D84AD; }
            QScrollBar::add-line:horizontal { height: 0; width: 0; }
            QScrollBar::add-page:horizontal { background: transparent; }
        """)
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
        self._refresh_button_styles()
        # v32.2: the three Backup-tab status dots carry theme-aware text
        # colors — re-run their refreshers so they don't keep the previous
        # theme's palette after a toggle (Good Repos stayed deep-butter on
        # plum, ~2.5:1, while VaultSeal refreshed correctly).
        self._vaultseal_refresh_status()
        self._goodrepos_refresh_status()
        self._backup_refresh_status()
        self.save_config()  # persist the theme choice

    def _sync_theme_toggle_btn(self):
        """v32.1: keep the ALWAYS-VISIBLE header light/dark toggle in sync.

        The button shows the mode you'll switch TO (🌙 while light, ☀️ while
        dark) with a tooltip naming the current mode — icon + text, never
        color alone (WCAG 1.4.1)."""
        if getattr(self, '_dark_mode', False):
            self.theme_toggle_btn.setText("☀️")
            self.theme_toggle_btn.setToolTip("Switch to light mode (current: Dark)")
        else:
            self.theme_toggle_btn.setText("🌙")
            self.theme_toggle_btn.setToolTip("Switch to dark mode (current: Light)")
