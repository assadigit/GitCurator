"""gitcurator.gui.icons — the OFFICIAL Lucide icon pack, bundled verbatim (v0.07.1).

The v0.07 draft shipped hand-redrawn "Lucide-style" paths; the owner
rejected self-drawn icons. This module now embeds the REAL Lucide icons,
verbatim from the official lucide-static distribution — same geometry,
same 24x24 viewBox, same 2px round strokes. Icons are tinted per theme at
render time by substituting the stroke color (upstream uses
stroke="currentColor", which QSvgRenderer cannot resolve).

Source:    https://lucide.dev  (npm package: lucide-static)
Version:   v0.544.0
License:   ISC (permissive, commercial use OK) — full text below.
Rendering: QSvgRenderer at 2x for HiDPI. PyQt6 bundles QtSvg; if it is
           ever unavailable the helpers degrade to empty icons (buttons
           keep their text labels), so the app still runs.

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2023 as
part of Feather (MIT). All other copyright (c) for Lucide are held by
Lucide Contributors 2025.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR
IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

Usage:
    from gitcurator.gui import icons
    btn.setIcon(icons.icon('settings', '#5F54B4'))      # theme-tinted QIcon
    icons.set_btn_icon(btn, 'trash', '#6C6480')          # icon + size in one
    label.setPixmap(icons.pixmap('dot', '#42D75A', 12))  # status dots
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

try:  # PyQt6 bundles QtSvg, but degrade gracefully if it is missing.
    from PyQt6.QtSvg import QSvgRenderer
    _HAS_SVG = True
except Exception:  # pragma: no cover - defensive only
    _HAS_SVG = False

# ---------------------------------------------------------------------------
# The set — VERBATIM inner SVG markup from lucide-static v0.544.0 (ISC).
# Do NOT redraw these by hand: they are the official icon files, only
# reformatted to one line each. '{color}' marks the render-time tint;
# the three solid glyphs (stop/play/dot) fill the official geometry.
# ---------------------------------------------------------------------------
ICONS: dict = {
    'layers': '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z" /> <path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12" /> <path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17" />',  # official lucide-static 'layers'
    'settings': '<path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /> <circle cx="12" cy="12" r="3" />',  # official lucide-static 'settings'
    'moon': '<path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401" />',  # official lucide-static 'moon'
    'sun': '<circle cx="12" cy="12" r="4" /> <path d="M12 2v2" /> <path d="M12 20v2" /> <path d="m4.93 4.93 1.41 1.41" /> <path d="m17.66 17.66 1.41 1.41" /> <path d="M2 12h2" /> <path d="M20 12h2" /> <path d="m6.34 17.66-1.41 1.41" /> <path d="m19.07 4.93-1.41 1.41" />',  # official lucide-static 'sun'
    'refresh': '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /> <path d="M21 3v5h-5" /> <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /> <path d="M8 16H3v5" />',  # official lucide-static 'refresh-cw'
    'stop': '<rect width="18" height="18" x="3" y="3" rx="2" fill="{color}" stroke="none"/>',  # official lucide-static 'square' (solid render of official geometry)
    'play': '<path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z" fill="{color}" stroke="none"/>',  # official lucide-static 'play' (solid render of official geometry)
    'loader': '<path d="M21 12a9 9 0 1 1-6.219-8.56" />',  # official lucide-static 'loader-circle'
    'activity': '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />',  # official lucide-static 'activity'
    'search': '<path d="m21 21-4.34-4.34" /> <circle cx="11" cy="11" r="8" />',  # official lucide-static 'search'
    'trash': '<path d="M10 11v6" /> <path d="M14 11v6" /> <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /> <path d="M3 6h18" /> <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />',  # official lucide-static 'trash-2'
    'dot': '<circle cx="12" cy="12" r="10" fill="{color}" stroke="none"/>',  # official lucide-static 'circle' (solid render of official geometry)
    # v0.31.0 (main-window balance pass) — four state glyphs, same verbatim
    # lucide-static v0.544.0 set. The pipeline-state indicator and the log
    # rows code state by SHAPE (WCAG 1.4.1 — never color alone):
    #   circle (hollow ring) = idle · loader (arc) = syncing
    #   check = done/success · triangle-alert = warning · circle-x = error
    'circle': '<circle cx="12" cy="12" r="10"/>',  # official lucide-static 'circle' (stroked hollow ring)
    'check': '<path d="M20 6 9 17l-5-5"/>',  # official lucide-static 'check'
    'triangle-alert': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',  # official lucide-static 'triangle-alert'
    'circle-x': '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',  # official lucide-static 'circle-x'
    # v0.30.0 (Settings-UI audit) — the Settings sidebar's nine section
    # glyphs, same verbatim lucide-static v0.544.0 set ('currentColor'
    # fills swapped for the render-time {color} tint).
    'key-round': '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z" /> <circle cx="16.5" cy="7.5" r=".5" fill="{color}" stroke="none" />',  # official lucide-static 'key-round'
    'globe': '<circle cx="12" cy="12" r="10" /> <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /> <path d="M2 12h20" />',  # official lucide-static 'globe'
    'folder': '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />',  # official lucide-static 'folder'
    'brain': '<path d="M12 18V5" /> <path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4" /> <path d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5" /> <path d="M17.997 5.125a4 4 0 0 1 2.526 5.77" /> <path d="M18 18a4 4 0 0 0 2-7.464" /> <path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517" /> <path d="M6 18a4 4 0 0 1-2-7.464" /> <path d="M6.003 5.125a4 4 0 0 0-2.526 5.77" />',  # official lucide-static 'brain'
    'inbox': '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12" /> <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />',  # official lucide-static 'inbox'
    # v0.34 (follow-up review) — the log empty state's glyph. The inbox
    # tray read as "two eyes" at 36px; the plain list glyph says "no
    # entries" at one glance.
    'list': '<line x1="8" x2="21" y1="6" y2="6" /> <line x1="8" x2="21" y1="12" y2="12" /> <line x1="8" x2="21" y1="18" y2="18" /> <line x1="3" x2="3.01" y1="6" y2="6" /> <line x1="3" x2="3.01" y1="12" y2="12" /> <line x1="3" x2="3.01" y1="18" y2="18" />',  # official lucide-static 'list'
    'bar-chart-3': '<path d="M3 3v16a2 2 0 0 0 2 2h16" /> <path d="M18 17V9" /> <path d="M13 17V5" /> <path d="M8 17v-3" />',  # official lucide-static 'bar-chart-3'
    'bot': '<path d="M12 8V4H8" /> <rect width="16" height="12" x="4" y="8" rx="2" /> <path d="M2 14h2" /> <path d="M20 14h2" /> <path d="M15 13v2" /> <path d="M9 13v2" />',  # official lucide-static 'bot'
    'satellite-dish': '<path d="M4 10a7.31 7.31 0 0 0 10 10Z" /> <path d="m9 15 3-3" /> <path d="M17 13a6 6 0 0 0-6-6" /> <path d="M21 13A10 10 0 0 0 11 3" />',  # official lucide-static 'satellite-dish'
    'archive': '<rect width="20" height="5" x="2" y="3" rx="1" /> <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /> <path d="M10 12h4" />',  # official lucide-static 'archive'
    # v0.40.0 — the Sound settings section's glyph (same verbatim
    # lucide-static v0.544.0 set; geometry fetched from the official
    # unpkg distribution so the pack stays byte-faithful).
    'volume-2': '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z" /> <path d="M16 9a5 5 0 0 1 0 6" /> <path d="M19.364 18.364a9 9 0 0 0 0-12.728" />',  # official lucide-static 'volume-2'
}


def _svg(name: str, color: str) -> str:
    """Build a complete tinted SVG document for one icon."""
    body = ICONS[name].replace('{color}', color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'width="24" height="24" fill="none" '
        f'stroke="{color}" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'{body}</svg>'
    )


def pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    """Render one icon to a transparent pixmap, tinted with `color`.

    Rendered at 2x and tagged with a devicePixelRatio so it stays crisp
    on HiDPI screens.

    v0.08 — Fix (owner report: "icons are big for their area — only parts
    of the sun are visible"): ``QSvgRenderer.render(painter)`` WITHOUT a
    target rect paints the SVG at its NATURAL 24×24-unit size in the
    painter's LOGICAL coordinate system. On a DPR-2 pixmap that logical
    area is only ``size`` px, so every glyph rendered 24/size× too big
    (2× at the default 16px) and was clipped at the right and bottom
    edges — exactly the "partial sun" the owner saw. Passing an explicit
    QRectF maps the viewBox onto the full logical canvas instead."""
    if not _HAS_SVG or name not in ICONS:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(_svg(name, color).encode('utf-8')))
    px = QPixmap(size * 2, size * 2)
    px.setDevicePixelRatio(2.0)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    try:
        from PyQt6.QtCore import QRectF
        renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    finally:
        painter.end()
    return px


def icon(name: str, color: str, size: int = 16) -> QIcon:
    """Theme-tinted QIcon for one icon (empty QIcon if QtSvg is absent —
    the button's text label remains, so nothing breaks)."""
    if not _HAS_SVG or name not in ICONS:
        return QIcon()
    return QIcon(pixmap(name, color, size))


def set_btn_icon(btn, name: str, color: str, size: int = 18) -> None:
    """Apply a tinted icon (plus icon size) to a QPushButton/QToolButton."""
    btn.setIcon(icon(name, color, size))
    btn.setIconSize(QSize(size, size))


def nav_icon(name: str, color: str, selected: str = '#FFFFFF',
             size: int = 16) -> QIcon:
    """v0.30.0 (Settings-UI audit): TWO-MODE navigation icon.

    ``color`` paints the plain row; ``selected`` paints the row while it
    is selected — QSS-selected QListWidget items render the QIcon's
    QIcon.Mode.Selected pixmap (verified on Qt 6.11), which is how the
    filled sidebar row keeps a legible glyph: white on the violet fill in
    light mode, plum on the lavender fill in dark mode. Both pixmaps are
    registered for State.On/Off so check-state never blanks the icon.
    """
    ic = icon(name, color, size)
    for state in (QIcon.State.On, QIcon.State.Off):
        ic.addPixmap(pixmap(name, selected, size),
                     QIcon.Mode.Selected, state)
    return ic
