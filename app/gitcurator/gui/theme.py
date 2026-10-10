"""gitcurator.gui.theme — the ONE app theme kit (v0.30.0).

The external Settings-UI audit, implemented presentation-only: the old
theming lived as two ~90-rule objectName stylesheets duplicated inside
ThemeMixin.apply_light_theme / apply_dark_theme, plus eight kinds of
per-widget ``setStyleSheet`` button rules and 45+ scattered inline
``setStyleSheet`` calls. It is now this module:

* ONE token table per mode (LIGHT / DARK) — every color the app paints
  with, including the plum dark palette ported verbatim from the v32
  pastel-night QSS;
* ONE structural stylesheet builder — ``build_qss(theme)`` composes the
  app rules, the cards-and-navigation rules, the button variants and the
  audit's literal extras;
* ``apply_app(widget, dark)`` — palette + stylesheet in one call (the
  ThemeMixin methods are thin routers onto it);
* buttons wear a ``btn_kind`` dynamic PROPERTY picked up by QSS (no more
  per-widget stylesheets — a flip re-polishes everything for free);
* status lines / badges wear ``role`` + ``state`` properties.

The kit is deliberately presentation-only: no behavior, no text, no
pipeline logic — the ThemeMixin keeps its exact public surface
(apply_light_theme / apply_dark_theme / _style_btn / toggle_theme …).
"""
from __future__ import annotations

from typing import Dict

from PyQt6.QtGui import QColor, QPalette

# ---------------------------------------------------------------------------
# The one interactive-chrome accent (audit decision): violet #5F54B4 on
# light surfaces (6.2:1 on white), pastel lavender on the dark plum sheets.
# ---------------------------------------------------------------------------
ACCENT = "#5F54B4"

# --- button fills (theme-independent pastel pairs, AA-verified) -----------
_CTA_FILL, _CTA_HOVER, _CTA_TEXT = "#B9E3C9", "#A8DABA", "#17402B"
_ERR_FILL, _ERR_HOVER, _ERR_TEXT = "#F6C6CD", "#F0B2BC", "#5E1120"
_DANGER_FILL, _DANGER_HOVER, _DANGER_PRESS = "#D63A24", "#C43320", "#B32D1D"
_VIOLET_HOVER = "#514699"          # pressed/hover fill carrying white text
_OUTLINE_HOVER_FILL = "#ECE9FA"    # secondary hover wash (both modes)

# v0.31.0 (main-window balance pass): the SYNC fill is now THEME-AWARE so
# the dominant button clears 3:1 against the card in BOTH modes (the old
# shared pastel #B3A7F2 sat at 2.2:1 on a white card). Light mode wears the
# accent violet itself with a white label (6.2:1); dark mode wears the
# lavender accent with the plum label (8.2:1 fill / 9.2:1 label).
_HERO_FILL_L, _HERO_HOVER_L, _HERO_TEXT_L = "#5F54B4", "#514699", "#FFFFFF"
_HERO_FILL_D, _HERO_HOVER_D, _HERO_TEXT_D = "#C4BCF5", "#B3A7F2", "#221E2E"

# ---------------------------------------------------------------------------
# Token tables. Every value is ported from the v32 pastel QSS the app has
# shipped since v0.07 — this refactor changes STRUCTURE, not the palette
# (the audit's light palette matched the existing tokens; only the dark
# selected/hover pairs and the warning role were added).
# ---------------------------------------------------------------------------
LIGHT: Dict[str, str] = {
    # surfaces
    "window":        "#FBF8F2",   # warm cream background
    "sheet":         "#FFFFFF",   # cards / inputs / sheets
    "sheet_alt":     "#F2EDE3",   # alternate + tab idle + ghost bg
    "sheet_hover":   "#EAE3D6",   # hover wash on sheets
    "disabled_bg":   "#F2EDE3",
    "disabled_fg":   "#9B937F",
    # lines
    "border":        "#EAE3D6",
    "border_input":  "#D8D0BE",
    "border_hover":  "#B5AC9C",
    # ink
    "text":          "#423A52",   # soft plum
    "text_soft":     "#514A63",   # check/radio labels, group titles
    "text_hover":    "#57506B",
    "text_muted":    "#6C6480",   # mauve
    "text_disabled": "#A79F92",
    # accent chrome
    "accent":        "#5F54B4",
    "focus":         "#8B80D6",
    "select_bg":     "#5F54B4",
    "select_fg":     "#FFFFFF",
    "on_accent":     "#FFFFFF",   # text/icons on the accent fill
    "toolbtn_press": "#514699",
    "placeholder":   "#7A7288",
    # status text (deep tones on white)
    "success":       "#1E6B4B",
    "warning":       "#75510A",
    "error":         "#AE2237",
    "muted":         "#6C6480",
    # callouts
    "note_bg":       "#E9F5EE",   # info_note
    "note_fg":       "#423A52",
    "note_indigo":   "#EEEBFA",   # info_note_indigo
    "warn_box_border": "#F5E3C0",
    "warn_box_text":   "#B45309",
    # progress / scrollbars / misc chrome
    "log_well":        "#FFFFFF",
    # v0.32 (five-change pass): the log panel is VISUALLY SECONDARY — a flat
    # tinted surface (no card border) with muted ink (AA on the tint):
    # log_text 6.5:1 · log_ts 4.8:1 on log_tint.
    "log_tint":        "#F2EDE3",
    "log_text":        "#57506B",
    "log_ts":          "#6C6480",
    "banner_warn_bg":  "#FAF3E3",   # the retry banner's amber wash
    # v0.63.0 — the scan plan's SECTION PANELS (the owner's report:
    # "very pale red" for the deletions list, "very pale green" for
    # the creations): near-white tints on the cream library, deep-plum
    # twins for the night mode. The borders stay one step deeper than
    # the wash so the panel reads as a card, not a hole.
    "plan_del_bg":      "#FBEDEF",  # very pale rose — the deletions panel
    "plan_del_border":  "#F1CFD7",
    "plan_grow_bg":     "#ECF5EF",  # very pale mint — the creations panel
    "plan_grow_border": "#CFE7D9",
    "progress_track":  "#E3DACA",
    "progress_chunk":  "#5F54B4",
    "progress_text":   "#514A63",
    "pipeline_caption": "#57506B",
    "scroll":        "#DCD4C4",
    "scroll_hover":  "#B5AC9C",
    "tab_selected":  "#FFFFFF",
    "tab_hover":     "#EAE3D6",
    "log_checked_hover": "#514699",
    # message-box heading tones
    "msg_success":   "#16A34A",
    "msg_error":     "#DC2626",
    "msg_warning":   "#EA580C",
    # button fills (the hero pair is themed — see the v0.31.0 note above)
    "cta_fill": _CTA_FILL, "cta_hover": _CTA_HOVER, "cta_text": _CTA_TEXT,
    "err_fill": _ERR_FILL, "err_hover": _ERR_HOVER, "err_text": _ERR_TEXT,
    "hero_fill": _HERO_FILL_L, "hero_hover": _HERO_HOVER_L, "hero_text": _HERO_TEXT_L,
    "danger_fill": _DANGER_FILL, "danger_hover": _DANGER_HOVER,
    "danger_press": _DANGER_PRESS,
    "outline_hover": _OUTLINE_HOVER_FILL,
    "outline_press": _VIOLET_HOVER,
    "ghost_bg":      "#FBF8F2",
    "ghost_hover_bg": "#F2EDE3",
    "ghost_hover_fg": "#57506B",
    "icon_border":   "#D8D0BE",
    "hero2_bg":      "#FFFFFF",
}

DARK: Dict[str, str] = {
    # surfaces (the plum night palette, ported verbatim)
    "window":        "#221E2E",
    "sheet":         "#2B2639",
    "sheet_alt":     "#352F4A",   # raised/hover plum
    "sheet_hover":   "#352F4A",
    "disabled_bg":   "#231F30",
    "disabled_fg":   "#7E7794",
    # lines
    "border":        "#3B344F",
    "border_input":  "#4A4263",
    "border_hover":  "#5C5378",
    # ink
    "text":          "#F2EEE7",   # warm white
    "text_soft":     "#DDD7EC",
    "text_hover":    "#DDD7EC",
    "text_muted":    "#B7AFC9",   # lavender-grey
    "text_disabled": "#7E7794",
    # accent chrome (pastel lavender on plum)
    "accent":        "#C4BCF5",
    "focus":         "#C4BCF5",
    "select_bg":     "#5F54B4",
    "select_fg":     "#FFFFFF",
    "on_accent":     "#221E2E",   # plum text/icons on the lavender fill
    "toolbtn_press": "#514699",
    "placeholder":   "#A6A2AC",
    # status text (vivid accents on plum)
    "success":       "#7CE2A9",
    "warning":       "#FFD37E",
    "error":         "#FF9AAB",
    "muted":         "#B7AFC9",
    # callouts
    "note_bg":       "#26332D",
    "note_fg":       "#DDD7EC",
    "note_indigo":   "#2E2A4A",
    "warn_box_border": "#4A3F28",
    "warn_box_text":   "#F2DCA8",
    # progress / scrollbars / misc chrome
    "log_well":        "#17131F",
    # v0.32 (five-change pass): the flat secondary log surface — the deep
    # plum well becomes the whole panel (no card chrome); muted ink
    # (log_text 10.3:1 · log_ts 5.4:1 on log_tint).
    "log_tint":        "#17131F",
    "log_text":        "#C6BFE0",
    "log_ts":          "#8F89A3",
    "banner_warn_bg":  "#322B20",   # the retry banner's amber-on-plum wash
    # v0.63.0 — the scan plan's section panels, night twins: the same
    # rose/mint reading at plum-night luminance (the wash stays a WASH —
    # far from the fill reds/greens the buttons use).
    "plan_del_bg":      "#3A2731",  # rose-tinted plum — the deletions panel
    "plan_del_border":  "#54333E",
    "plan_grow_bg":     "#25352B",  # mint-tinted plum — the creations panel
    "plan_grow_border": "#33503C",
    "progress_track":  "#17131F",
    "progress_chunk":  "#C4BCF5",
    "progress_text":   "#DDD7EC",
    "pipeline_caption": "#C9C2DC",
    "scroll":        "#7A7199",
    "scroll_hover":  "#8D84AD",
    "tab_selected":  "#352F4A",
    "tab_hover":     "#352F4A",
    "log_checked_hover": "#D3CDF9",
    # message-box heading tones
    "msg_success":   "#4ADE80",
    "msg_error":     "#F87171",
    "msg_warning":   "#FB923C",
    # button fills (the hero pair is themed — see the v0.31.0 note above)
    "cta_fill": _CTA_FILL, "cta_hover": _CTA_HOVER, "cta_text": _CTA_TEXT,
    "err_fill": _ERR_FILL, "err_hover": _ERR_HOVER, "err_text": _ERR_TEXT,
    "hero_fill": _HERO_FILL_D, "hero_hover": _HERO_HOVER_D, "hero_text": _HERO_TEXT_D,
    "danger_fill": _DANGER_FILL, "danger_hover": _DANGER_HOVER,
    "danger_press": _DANGER_PRESS,
    "outline_hover": _OUTLINE_HOVER_FILL,
    "outline_press": _VIOLET_HOVER,
    "ghost_bg":      "#2B2639",
    "ghost_hover_bg": "#352F4A",
    "ghost_hover_fg": "#DDD7EC",
    "icon_border":   "#4A4263",
    "hero2_bg":      "#352F4A",
}


def shade(color: str, factor: float = 0.9) -> str:
    """Audit #20: hover states DARKEN the base (factor < 1), never a new
    hue. Returns the shaded hex string."""
    c = QColor(color)
    if not c.isValid():
        return color
    c = c.darker(round((1.0 / max(factor, 0.01)) * 100))
    return c.name()


def palette_for(t: Dict[str, str]) -> QPalette:
    """QPalette from one token table (the old per-mode palettes, verbatim,
    plus the Warning role the audit asked for)."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(t["window"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Base, QColor(t["sheet"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t["sheet_alt"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(t["window"]))
    p.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.Button, QColor(t["sheet"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.ColorRole.BrightText, QColor(t["error"]))
    p.setColor(QPalette.ColorRole.Link, QColor(t["accent"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(t["select_bg"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(t["select_fg"]))
    # (QPalette::Warning no longer exists in Qt 6.5+ — the warning tone is
    # carried by the stylesheet's status/warn-box rules instead.)
    try:  # PlaceholderText needs Qt >= 6.5 — older builds keep the default
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["placeholder"]))
    except AttributeError:
        pass
    return p


# ---------------------------------------------------------------------------
# The audit's literal EXTRA rules — presentation niceties that ride along
# with the app rules in BOTH modes (parameterized by the token table):
#   * placeholder text is tinted via QSS as well as the palette (Qt 6.5+;
#     unknown properties are ignored harmlessly on older builds);
#   * the combo chevron is a CSS triangle — no pixmap, so no dark edge
#     artifact on the dropdown border in dark mode;
#   * list items never paint a focus rectangle (the filled selection is
#     the focus indication).
# ---------------------------------------------------------------------------
def _extra_qss(t: Dict[str, str]) -> str:
    return f"""
            QLineEdit, QTextEdit, QPlainTextEdit {{ placeholder-text-color: {t['placeholder']}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox::down-arrow {{ image: none; width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {t['text_muted']}; }}
            QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{ border-top-color: {t['accent']}; }}
            QListWidget::item, QListView::item {{ outline: none; }}
        """


def _app_rules(t: Dict[str, str]) -> str:
    """Every rule the old per-mode stylesheets carried (moved verbatim,
    tokens substituted) — plus one generic QListWidget rule so plain lists
    inside themed dialogs are no longer left to the OS palette."""
    return f"""
            QMainWindow {{ background-color: {t['window']}; }}
            QWidget {{ font-family: 'Segoe UI', 'SF Pro Display', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; color: {t['text']}; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; top: -1px; background: {t['sheet']}; }}
            QTabBar::tab {{ background: {t['sheet_alt']}; border: none; border-bottom: 3px solid transparent; padding: 8px 16px; margin-right: 2px; font-weight: 500; color: {t['text_muted']}; }}
            QTabBar::tab:selected {{ background: {t['tab_selected']}; border-bottom: 3px solid {t['accent']}; color: {t['accent']}; }}
            QTabBar::tab:hover:!selected {{ background: {t['tab_hover']}; color: {t['text_hover']}; }}
            QGroupBox {{ font-weight: 600; font-size: 14px; border: 1px solid {t['border']}; border-radius: 8px; margin-top: 14px; padding: 10px 8px 6px 8px; background: {t['sheet']}; color: {t['text']}; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {t['text_soft']}; }}
            QGroupBox#log_group {{ margin-top: 0px; padding: 2px 2px 2px 2px; }}
            /* v0.32 (five-change pass): the log panel is FLAT — no card border,
            no sheet fill; a quiet tinted surface one step below the cards. */
            QGroupBox#log_group {{ border: none; background-color: {t['log_tint']}; border-radius: 10px; }}
            /* the log LIST rides transparently on that tint (no well-in-a-card) */
            QTextEdit#log_list {{ border: none; border-radius: 8px; background: transparent; padding: 8px 10px; }}
            QTextEdit#log_list:focus {{ border: 1px solid {t['focus']}; padding: 7px 9px; }}
            QLineEdit {{ padding: 6px; border: 1px solid {t['border_input']}; border-radius: 6px; background: {t['sheet']}; selection-background-color: {t['select_bg']}; selection-color: {t['select_fg']}; }}
            QLineEdit:focus {{ border: 2px solid {t['accent']}; padding: 5px; outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QLineEdit:disabled {{ background: {t['disabled_bg']}; color: {t['text_disabled']}; }}
            QComboBox {{ padding: 6px; border: 1px solid {t['border_input']}; border-radius: 6px; background: {t['sheet']}; selection-background-color: {t['select_bg']}; selection-color: {t['select_fg']}; }}
            QComboBox:focus {{ border: 2px solid {t['accent']}; padding: 5px; outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QComboBox:disabled {{ background: {t['disabled_bg']}; color: {t['text_disabled']}; }}
            QComboBox QAbstractItemView {{ background: {t['sheet']}; color: {t['text']}; selection-background-color: {t['select_bg']}; selection-color: {t['select_fg']}; border: 1px solid {t['border']}; outline: none; }}
            QCheckBox {{ spacing: 8px; color: {t['text_soft']}; }}
            QCheckBox:focus {{ outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {t['border_input']}; border-radius: 4px; background: {t['sheet']}; }}
            QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
            QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
            QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {t['progress_track']}; }}
            QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px; background: {t['progress_chunk']}; }}
            QSlider::add-page:horizontal {{ height: 6px; border-radius: 3px; background: {t['progress_track']}; }}
            QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: {t['sheet']}; border: 2px solid {t['border_input']}; }}
            QSlider::handle:horizontal:hover {{ border-color: {t['accent']}; }}
            QSlider::handle:horizontal:focus {{ border-color: {t['focus']}; }}
            QSlider::handle:horizontal:disabled {{ background: {t['disabled_bg']}; border-color: {t['border']}; }}
            QSlider:focus {{ outline: none; }}
            QSlider:disabled {{ color: {t['text_disabled']}; }}
            QPushButton {{ padding: 8px 16px; border: 1px solid {t['border_input']}; border-radius: 6px; background: {t['sheet']}; font-weight: bold; color: {t['text']}; }}
            QPushButton:hover {{ background: {t['sheet_hover']}; border-color: {t['border_hover']}; }}
            QPushButton:pressed {{ background: {t['sheet_hover']}; }}
            QPushButton:disabled {{ color: {t['text_disabled']}; background: {t['disabled_bg']}; border-color: {t['border']}; }}
            QPushButton:focus {{ outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QToolButton {{ padding: 8px 16px; border: 1px solid {t['focus']}; border-radius: 6px; background-color: {t['sheet']}; color: {t['accent']}; font-weight: 600; font-size: 13px; }}
            QToolButton:hover {{ background-color: {t['select_bg']}; color: {t['select_fg']}; }}
            QToolButton:pressed {{ background-color: {t['toolbtn_press']}; color: #FFFFFF; }}
            QToolButton:focus {{ outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QToolButton::menu-indicator {{ image: none; width: 0; }}
            QMenu {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 8px 0; }}
            QMenu::item {{ padding: 8px 24px; color: {t['text']}; }}
            QMenu::item:selected {{ background: {t['select_bg']}; color: {t['select_fg']}; }}
            QMenu::separator {{ height: 1px; background: {t['border']}; margin: 8px 0; }}
            QMenu::item:disabled {{ color: {t['text_disabled']}; }}
            QScrollArea {{ border: none; background-color: {t['sheet']}; }}
            QWidget#tab_sheet {{ background-color: {t['sheet']}; }}
            QListWidget {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 6px; color: {t['text']}; }}
            QLabel#info_header {{ background-color: {t['sheet_alt']}; border-radius: 4px; padding: 8px; font-size: 12px; color: {t['text_soft']}; }}
            QLabel#info_note {{ background-color: {t['note_bg']}; border-radius: 4px; padding: 8px; font-size: 12px; color: {t['note_fg']}; }}
            QLabel#info_note_indigo {{ background-color: {t['note_indigo']}; border-radius: 4px; padding: 8px; font-size: 12px; color: {t['note_fg']}; }}
            QTextEdit {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['log_well']}; padding: 8px; selection-background-color: {t['select_bg']}; selection-color: {t['select_fg']}; }}
            QTextEdit:read-only {{ font-family: 'Consolas', 'Monaco', 'Menlo', 'Courier New', monospace; font-size: 12px; }}
            QTextEdit:focus {{ border: 2px solid {t['accent']}; padding: 7px; outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            /* v0.31.0 (balance pass): the bar is a pure fill gauge — 12px,
               no text inside (the state word + count live at the row ends). */
            QProgressBar {{ border: none; border-radius: 6px; background: {t['progress_track']}; max-height: 12px; }}
            QProgressBar::chunk {{ background: {t['progress_chunk']}; border-radius: 6px; }}
            QLabel {{ color: {t['text']}; }}
            QLabel#proxy_status_text {{ color: {t['text_muted']}; font-size: 12px; }}
            /* v0.37.0 — Test Connection hierarchy: the section heading is
            the row's BOLD voice; every check item under it is ONE small,
            pale line (the full detail rides the item tooltip). */
            QLabel#cc_row_name {{ font-size: 13px; font-weight: 700; color: {t['text']}; background: transparent; }}
            /* v0.39.0 — the Batch Complete scorecard reuses the bold
            value role with the message-box tone tokens (zero new colors):
            the "needs retry" count reads as warning, the all-clear as
            success, in both modes. */
            QLabel#cc_row_name[tone="success"] {{ color: {t['msg_success']}; }}
            QLabel#cc_row_name[tone="warning"] {{ color: {t['msg_warning']}; }}
            QLabel#cc_row_status {{ font-size: 12px; font-weight: 600; background: transparent; }}
            QLabel#cc_item {{ font-size: 11px; color: {t['text_muted']}; background: transparent; }}
            QRadioButton {{ spacing: 8px; padding: 2px; color: {t['text_soft']}; }}
            QRadioButton:focus {{ outline: 2px solid {t['focus']}; outline-offset: 2px; }}
            QRadioButton::indicator {{ width: 16px; height: 16px; border: 2px solid {t['border_input']}; border-radius: 8px; background: {t['sheet']}; }}
            QRadioButton::indicator:checked {{ border-color: {t['accent']}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {t['accent']}, stop:0.5 {t['accent']}, stop:0.5 transparent, stop:1 transparent); }}
            QRadioButton::indicator:hover {{ border-color: {t['accent']}; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::handle:vertical:hover {{ background: {t['scroll_hover']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: {t['scroll']}; border-radius: 4px; min-width: 24px; }}
            QScrollBar::handle:horizontal:hover {{ background: {t['scroll_hover']}; }}
            QScrollBar::add-line:horizontal {{ height: 0; width: 0; }}
            QScrollBar::add-page:horizontal {{ background: transparent; }}
            QWidget#sync_card {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 12px; }}
            /* v0.63.0 — the scan plan's SECTION PANELS: the deletions
               wash very pale red, the creations very pale green, the
               re-file list stays on the neutral sheet (the owner's
               report: "so it better signal that those are deleted
               list" / "to signal creation"). The headings grow into
               the plan_heading role — 15px/800, tonal ink — so the
               hierarchy reads at a glance (the owner's report:
               "bigger headings, to better signal hierarchy"). */
            QWidget#plan_delete_card {{ background-color: {t['plan_del_bg']}; border: 1px solid {t['plan_del_border']}; border-radius: 10px; }}
            QWidget#plan_create_card {{ background-color: {t['plan_grow_bg']}; border: 1px solid {t['plan_grow_border']}; border-radius: 10px; }}
            QWidget#plan_move_card {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 10px; }}
            QLabel#plan_heading {{ font-size: 15px; font-weight: 800; background: transparent; }}
            QLabel#plan_heading[tone="danger"] {{ color: {t['msg_error']}; }}
            QLabel#plan_heading[tone="grow"] {{ color: {t['msg_success']}; }}
            QLabel#plan_heading[tone="neutral"] {{ color: {t['text']}; }}
            /* each section's list rides its own transparent scroll —
               the panel's wash shows through and the list scrolls when
               the pile outgrows the viewport (the owner's report:
               "the containers for each must be scrollable") */
            QScrollArea#plan_list {{ border: none; background-color: transparent; }}
            QScrollArea#plan_list > QWidget {{ border: none; background-color: transparent; }}
            QScrollArea#plan_list > QWidget > QWidget {{ border: none; background-color: transparent; }}
            /* v0.32 (five-change pass): the retry banner — a flat amber band
            under the status card; the warning word rides the status-role
            QSS (state=warning) for its AA ink. */
            QWidget#retry_banner {{ background-color: {t['banner_warn_bg']}; border: 1px solid {t['warn_box_border']}; border-radius: 10px; }}
            QLabel#retry_banner_text {{ background: transparent; font-size: 12px; font-weight: 600; }}
            /* v0.31.0 (balance pass): the count is the row's loudest text
               (15px bold mono); the caption stays small and quiet. */
            QLabel#progress_count {{ font-family: 'Consolas', 'Monaco', 'Menlo', monospace; font-size: 15px; font-weight: 700; color: {t['accent']}; background: transparent; }}
            QLabel#pipeline_caption {{ color: {t['pipeline_caption']}; font-size: 11px; font-weight: 700; background: transparent; }}
            QLabel#pipeline_state_text {{ font-size: 12px; background: transparent; }}
            /* v0.34 (follow-up review): the quiet LAST-SYNC line under the
               progress row — one muted answer to "what happened last
               time" the moment the app reopens. */
            QLabel#last_sync_line {{ font-size: 11px; color: {t['text_muted']}; background: transparent; }}
            /* the log card's empty state — muted, so it never competes
               with the SYNC button */
            QWidget#log_empty {{ background: transparent; }}
            QLabel#log_empty_title {{ font-size: 14px; font-weight: 700; color: {t['text_muted']}; background: transparent; }}
            QLabel#log_empty_hint {{ font-size: 12px; color: {t['text_muted']}; background: transparent; }}
            QPushButton#log_filter {{ background: transparent; border: none; border-radius: 6px; padding: 5px 12px; font-size: 11px; font-weight: 600; color: {t['text_muted']}; }}
            QPushButton#log_filter:hover {{ background: {t['sheet_hover']}; color: {t['text_hover']}; }}
            QPushButton#log_filter:checked {{ background: {t['accent']}; color: {t['on_accent']}; }}
            QPushButton#log_filter:checked:hover {{ background: {shade(t['accent'])}; }}
            QPushButton#log_filter:focus {{ outline: 2px solid {t['focus']}; outline-offset: 1px; }}
            /* v0.34 (follow-up review): the whole toolbar is disabled while
               the log is empty — filters/search/clear do nothing with no
               entries, so they grey out until the first row appears. */
            QPushButton#log_filter:disabled {{ background: transparent; color: {t['text_disabled']}; }}
            QLineEdit#log_search {{ padding: 4px 8px; }}
            QLineEdit#log_search:focus {{ padding: 3px 7px; }}
            QLabel#logo_box {{ background-color: {t['select_bg']}; border-radius: 7px; font-size: 14px; }}
            QLabel#logo_title {{ font-size: 14px; font-weight: 800; color: {t['text']}; background: transparent; }}
            QLabel#logo_sub {{ font-size: 10px; color: {t['text_muted']}; background: transparent; }}
            QDialog {{ background-color: {t['window']}; }}
            QDialog#settings_dialog {{ background-color: {t['window']}; }}
        """


def _cards_and_nav_qss(t: Dict[str, str]) -> str:
    """The Settings window: sidebar navigation + the flat cards + the
    property-role rules (status lines, badges, message-box roles, helper
    captions, dividers). Dark twin of the light set — same structure,
    tokens swapped."""
    return f"""
            QWidget#settings_header {{ background-color: {t['sheet']}; border-bottom: 1px solid {t['border']}; }}
            QLabel#settings_title {{ font-size: 20px; font-weight: 800; color: {t['text']}; background: transparent; }}
            QLabel#settings_hint {{ font-size: 12px; color: {t['text_muted']}; background: transparent; }}
            QListWidget#settings_nav {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; font-size: 13px; color: {t['text']}; outline: none; }}
            QListWidget#settings_nav::item {{ padding: 10px 12px; border-radius: 8px; margin: 1px 2px; }}
            QListWidget#settings_nav::item:selected {{ background-color: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
            QListWidget#settings_nav::item:hover:!selected {{ background-color: {t['sheet_hover']}; color: {t['text_hover']}; }}
            /* v0.30.0 — flat cards (Settings-UI audit) */
            QWidget#card {{ background-color: {t['sheet']}; border: 1px solid {t['border']}; border-radius: 10px; }}
            QLabel#card_heading {{ font-size: 14px; font-weight: 700; color: {t['text']}; background: transparent; }}
            QLabel#card_subhead {{ font-size: 12px; font-weight: 700; color: {t['accent']}; background: transparent; }}
            QLabel[role="muted"] {{ color: {t['text_muted']}; font-size: 12px; background: transparent; }}
            QLabel[role="form_label"] {{ color: {t['text_soft']}; background: transparent; }}
            QLabel#law_note {{ font-size: 11px; color: {t['warning']}; background: transparent; }}
            QLabel#muted_note {{ font-size: 11px; color: {t['text_muted']}; background: transparent; }}
            QFrame#divider {{ border: none; border-top: 1px solid {t['border']}; background: transparent; }}
            QLabel#help_box {{ font-size: 11px; font-family: 'Consolas', 'Monaco', 'Courier New', monospace; background-color: {t['sheet_alt']}; border-radius: 4px; padding: 10px; color: {t['text']}; }}
            QLabel#warn_box {{ font-size: 12px; color: {t['warn_box_text']}; background: transparent; border: 1px solid {t['warn_box_border']}; border-radius: 6px; padding: 8px; }}
            /* status lines + badges (property-driven; see ThemeMixin) */
            QLabel[role="status"] {{ font-size: 12px; background: transparent; }}
            QLabel[role="status"][strong="true"] {{ font-weight: 700; }}
            QLabel[role="status"][state="success"] {{ color: {t['success']}; }}
            QLabel[role="status"][state="warning"] {{ color: {t['warning']}; }}
            QLabel[role="status"][state="error"] {{ color: {t['error']}; }}
            QLabel[role="status"][state="muted"] {{ color: {t['text_muted']}; }}
            QLabel[role="status"][state="accent"] {{ color: {t['accent']}; }}
            QLabel[role="badge"] {{ padding: 4px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; }}
            QLabel[role="badge"][state="pending"] {{ background-color: {t['text_muted']}; color: #FFFFFF; }}
            QLabel[role="badge"][state="ok"] {{ background-color: {t['cta_fill']}; color: {t['cta_text']}; }}
            QLabel#pending_badge {{ padding: 4px 8px; border-radius: 10px; font-size: 12px; font-weight: 700; }}
            QLabel#pending_badge[state="pending"] {{ background-color: {t['text_muted']}; color: #FFFFFF; }}
            QLabel#pending_badge[state="ok"] {{ background-color: {t['cta_fill']}; color: {t['cta_text']}; }}
            /* message-box roles (ThemeMixin dialogs, de-styled) */
            QLabel#msg_glyph {{ font-size: 32px; background: transparent; }}
            QLabel#msg_heading {{ font-size: 16px; font-weight: 700; background: transparent; }}
            QLabel#msg_heading[tone="success"] {{ color: {t['msg_success']}; }}
            QLabel#msg_heading[tone="error"] {{ color: {t['msg_error']}; }}
            QLabel#msg_heading[tone="warning"] {{ color: {t['msg_warning']}; }}
        """


def _button_qss(t: Dict[str, str]) -> str:
    """The eight button variants, as dynamic-PROPERTY selectors. A button
    joins the system via ``_style_btn(btn, kind)`` (sets ``btn_kind`` +
    re-polishes); the theme flip re-applies the whole stylesheet so both
    modes restyle for free — no per-widget stylesheets anywhere."""
    return f"""
            QPushButton[btn_kind="primary"] {{ background-color: {t['cta_fill']}; color: {t['cta_text']}; border: none; font-weight: bold; padding: 8px 24px; border-radius: 6px; font-size: 13px; }}
            QPushButton[btn_kind="primary"]:hover {{ background-color: {t['cta_hover']}; }}
            QPushButton[btn_kind="primary"]:pressed {{ background-color: {shade(t['cta_fill'], 0.92)}; }}
            QPushButton[btn_kind="primary"]:disabled {{ background-color: {t['disabled_bg']}; color: {t['disabled_fg']}; border: none; }}
            QPushButton[btn_kind="secondary"] {{ background-color: {t['sheet']}; color: {t['accent']}; border: 1px solid {t['accent']}; font-weight: 600; padding: 8px 16px; border-radius: 6px; font-size: 13px; }}
            QPushButton[btn_kind="secondary"]:hover {{ background-color: {t['outline_hover']}; color: {t['outline_press']}; border-color: {t['outline_press']}; }}
            QPushButton[btn_kind="secondary"]:pressed {{ background-color: {t['outline_press']}; color: #FFFFFF; }}
            QPushButton[btn_kind="secondary"]:disabled {{ color: {t['text_disabled']}; border-color: {t['border']}; background-color: {t['sheet']}; }}
            QPushButton[btn_kind="danger"] {{ background-color: {t['err_fill']}; color: {t['err_text']}; border: none; font-weight: bold; padding: 8px 24px; border-radius: 6px; font-size: 13px; }}
            QPushButton[btn_kind="danger"]:hover {{ background-color: {t['err_hover']}; }}
            QPushButton[btn_kind="danger"]:pressed {{ background-color: {shade(t['err_fill'], 0.92)}; }}
            QPushButton[btn_kind="danger"]:disabled {{ background-color: {t['disabled_bg']}; color: {t['disabled_fg']}; border: none; }}
            QPushButton[btn_kind="ghost"] {{ background-color: {t['ghost_bg']}; color: {t['text_muted']}; border: none; font-weight: 500; padding: 4px 8px; border-radius: 6px; font-size: 12px; }}
            QPushButton[btn_kind="ghost"]:hover {{ background-color: {t['ghost_hover_bg']}; color: {t['ghost_hover_fg']}; }}
            QPushButton[btn_kind="ghost"]:pressed {{ background-color: {t['ghost_hover_bg']}; }}
            QPushButton[btn_kind="ghost"]:disabled {{ color: {t['text_disabled']}; }}
            QPushButton[btn_kind="icon"] {{ background-color: {t['sheet']}; color: {t['accent']}; border: 1px solid {t['icon_border']}; font-size: 16px; font-weight: 600; padding: 0; border-radius: 8px; }}
            QPushButton[btn_kind="icon"]:hover {{ background-color: {t['sheet_hover']}; border-color: {t['accent']}; }}
            QPushButton[btn_kind="icon"]:pressed {{ background-color: {t['sheet_hover']}; }}
            /* v0.34 (follow-up review): the icon buttons (gear + theme
               toggle) carry Qt.TabFocus focus POLICY in ui.py — a mouse
               click can never focus them, so this :focus outline appears
               ONLY for keyboard Tab navigation. (QSS :focus-visible was
               tested on Qt 6.11 and silently never matches; the policy
               achieves the same effect version-independently. The old
               click-then-clearFocus trick failed when the Settings
               dialog returned focus to the gear — the "stuck" purple
               square.) */
            QPushButton[btn_kind="icon"]:focus {{ outline: 2px solid {t['accent']}; outline-offset: 2px; }}
            QPushButton[btn_kind="hero_primary"] {{ background-color: {t['hero_fill']}; color: {t['hero_text']}; font-weight: 800; font-size: 14px; padding: 8px 22px; border: none; border-radius: 8px; }}
            QPushButton[btn_kind="hero_primary"]:hover {{ background-color: {t['hero_hover']}; }}
            QPushButton[btn_kind="hero_primary"]:pressed {{ background-color: {t['hero_hover']}; }}
            QPushButton[btn_kind="hero_primary"]:disabled {{ background-color: {t['disabled_bg']}; color: {t['disabled_fg']}; }}
            QPushButton[btn_kind="hero_primary"]:focus {{ outline: 2px solid {t['accent']}; outline-offset: 2px; }}
            QPushButton[btn_kind="hero_danger"] {{ background-color: {t['danger_fill']}; color: #FFFFFF; font-weight: 800; font-size: 14px; padding: 8px 22px; border: none; border-radius: 8px; }}
            QPushButton[btn_kind="hero_danger"]:hover {{ background-color: {t['danger_hover']}; }}
            QPushButton[btn_kind="hero_danger"]:pressed {{ background-color: {t['danger_press']}; }}
            QPushButton[btn_kind="hero_danger"]:disabled {{ background-color: {t['disabled_bg']}; color: {t['disabled_fg']}; }}
            QPushButton[btn_kind="hero_danger"]:focus {{ outline: 2px solid {t['accent']}; outline-offset: 2px; }}
            QPushButton[btn_kind="hero_secondary"] {{ background-color: {t['hero2_bg']}; color: {t['accent']}; border: 1px solid {t['accent']}; font-weight: 700; font-size: 13px; padding: 5px 20px; border-radius: 8px; }}
            QPushButton[btn_kind="hero_secondary"]:hover {{ background-color: {t['outline_hover']}; color: {t['outline_press']}; border-color: {t['outline_press']}; }}
            QPushButton[btn_kind="hero_secondary"]:pressed {{ background-color: {t['outline_press']}; color: #FFFFFF; }}
            QPushButton[btn_kind="hero_secondary"]:disabled {{ color: {t['text_disabled']}; border-color: {t['border']}; background-color: {t['hero2_bg']}; }}
            QPushButton[btn_kind="hero_secondary"]:focus {{ outline: 2px solid {t['accent']}; outline-offset: 2px; }}
        """


def build_qss(t: Dict[str, str]) -> str:
    """The complete app stylesheet for one mode's token table."""
    return (_app_rules(t) + _cards_and_nav_qss(t) + _button_qss(t)
            + _extra_qss(t))


def repolish(widget) -> None:
    """Force QSS property selectors to re-evaluate for one widget (dynamic
    properties do not restyle on assignment alone)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def apply_app(widget, dark: bool = False) -> None:
    """Apply the full theme — palette + stylesheet — to one top-level
    widget (the main window; children inherit both)."""
    t = DARK if dark else LIGHT
    widget.setPalette(palette_for(t))
    widget.setStyleSheet(build_qss(t))
