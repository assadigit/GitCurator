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
