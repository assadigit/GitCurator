"""scan_plan_dialog.py — v0.62.0 THE GUI CONFIRM DOOR: the plan on the
owner's screen.

The owner's report (session, verbatim): "the scan now suggest new
folders to be made, but there is no modal or accept or confirm button
to actually LLM do them." The plan existed — the vault scan proposed
folders and moves and printed them into the log — but the only gate
that could APPLY it was the Telegram round-trip, so at the desk the
proposal was a dead letter: suggested, never actionable.

The answer is the SECOND door of the same gate (the Telegram ask stays
— it is the away-from-desk door): the scan plan now becomes a MODAL on
the owner's screen — ScanPlanDialog — with the whole proposal laid out
honestly (the deletions with their doors, the new folders, the moves,
the LLM's own summary, the inventory line) and two buttons:
**🗂️ Apply plan** (the primary — the folders are created, the notes
move byte-identically, the marked deletions go through the SAME
banishment machinery a confirmed Telegram ask triggers) and **✋ Keep
everything** (the safe default — the vault stays byte-for-byte as it
was, exactly like a Telegram decline). Esc / the window's ✕ / the
answering window running out are all the safe default too (the last
one reports 'timeout' — the same verdict the Telegram round-trip gives
a silent owner; nothing is done either way).

The plumbing is the login-code ask-gate's own pattern (v0.23.0,
``code_requested`` → modal → ``provide_code``): TestWorker grows
``scan_confirm_requested`` (the plan rides the signal to the GUI
thread), ``request_scan_confirm`` (the worker thread blocks on an
Event while the owner reads), and ``provide_scan_verdict`` (the GUI
thread hands the answer back). The job picks its door by config
``scan_confirm_door``: ``'gui'`` (the default whenever a GUI ask-gate
is injected — the desktop's own launches) or ``'telegram'`` (the old
round-trip; also the only door a headless/CLI launch has).

Two layers, the house law:

* ``plan_display_model(plan)`` is PURE (no Qt, no files — dict in,
  display rows out; the freeze-guard law: the WHOLE list rides the
  model now — the owner's v0.63.0 report: "the containers for each
  must be scrollable, just in case the quantity of sites were more
  than original viewport of the windows" — and only a list beyond
  :data:`MAX_ROWS` (far past any real plan) is counted instead of
  rendered, so a pathological plan can never freeze the modal);
* ``ScanPlanDialog`` only RENDERS that model (QDialog + the themed QSS
  roles — sync_card / info_header / cc_row_name / cc_item / help_box;
  no per-dialog stylesheet, v0.30.0's de-style law). v0.63.0 — THE
  PLAN AS PANELS: each list becomes a SECTION CARD with its own wash
  and its own scroll — the deletions on very pale red
  (``plan_delete_card``), the new folders on very pale green
  (``plan_create_card``), the re-file list on the neutral sheet
  (``plan_move_card``) under a 🚚 — and the headings grow into the
  ``plan_heading`` role (15px/800, tonal ink) so the hierarchy reads
  at a glance. The dialog never touches the vault; the apply pass
  lives in the job's thread, exactly where it always lived.
"""

from typing import Dict, List, Optional

# The Qt base is imported BEST-EFFORT at module load: the pure model
# below stays importable without PyQt6 (the hermetic law — the model's
# tests need no Qt); the dialog class is only ever instantiated behind
# a Qt guard, where the base is the real QDialog.
try:                                        # pragma: no cover — env
    from PyQt6.QtWidgets import QDialog as _QT_DIALOG_BASE
except Exception:                           # PyQt6 absent: inert base
    _QT_DIALOG_BASE = object

#: v0.63.0 — the freeze guard: the WHOLE list rides its scrollable
#: panel now (the owner's report: "the containers for each must be
#: scrollable, just in case the quantity of sites were more than
#: original viewport of the windows"), so the old 20-item cap is gone;
#: only a list past this size is counted instead of rendered — a
#: never-freeze law, far beyond any real plan (the LLM sees at most
#: ``scan_max_notes`` candidates; the deletions are the owner's own
#: marks, never a machine-generated pile).
MAX_ROWS = 500

#: Each section's list panel caps at this height (about ten rows) and
#: scrolls beyond — the modal keeps its shape whatever the pile.
_LIST_MAX_PX = 200

#: The verdict strings the door may answer (the Telegram channel's own
#: vocabulary — one grammar, two doors).
VERDICT_CONFIRMED = 'confirmed'
VERDICT_DECLINED = 'declined'
VERDICT_TIMEOUT = 'timeout'


# ---------------------------------------------------------------------------
# The pure layer — dict in, display rows out (no Qt, no files)
# ---------------------------------------------------------------------------

def _safe_int(v) -> int:
    """An int that never raises (a bad plan can never crash the
    modal — the never-crash contract)."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def plan_display_model(plan: Optional[Dict]) -> Dict:
    """Turn the scan plan into the modal's display rows (PURE).

    The plan is :func:`gitcurator.core.vault_scan.build_scan_plan`'s own
    shape (``deletions`` / ``moves`` / ``new_folders`` / ``summary`` /
    ``inventory`` / ``kept_handwritten``); the model is what the dialog
    renders — the WHOLE list (v0.63.0's scrollable-panel law: the
    owner reads every row, the panel scrolls when the pile outgrows
    the viewport), counted-not-rendered only past :data:`MAX_ROWS` (the
    freeze guard), every field a plain str (a bad plan can never crash
    the modal). Pure: no Qt, no file reads, never raises."""
    plan = plan or {}
    inv = plan.get('inventory')
    if not isinstance(inv, dict):
        inv = {}

    def _rows(raw, shaper):
        items = []
        for it in raw if isinstance(raw, list) else []:
            try:
                items.append(shaper(it if isinstance(it, dict) else {}))
            except Exception:
                continue
        shown = items[:MAX_ROWS]
        return shown, len(items)

    def _deletion(d: Dict) -> Dict:
        door = str(d.get('door') or 'note tag')
        return {'title': str(d.get('title') or d.get('url') or ''),
                'detail': str(d.get('url') or ''),
                'marker': str(d.get('marker') or '')[:80],
                'door': door}

    def _move(m: Dict) -> Dict:
        return {'note': str(m.get('note') or ''),
                'from': str(m.get('from') or '(vault root)'),
                'to': str(m.get('to') or ''),
                'reason': str(m.get('reason') or '')[:120]}

    deletions, deletions_total = _rows(plan.get('deletions'), _deletion)
    moves, moves_total = _rows(plan.get('moves'), _move)
    folders = [str(f) for f in (plan.get('new_folders') or [])
               if isinstance(f, (str,))][:MAX_ROWS]
    folders_total = len(plan.get('new_folders') or []) \
        if isinstance(plan.get('new_folders'), list) else 0
    return {
        'deletions': deletions, 'deletions_total': deletions_total,
        'moves': moves, 'moves_total': moves_total,
        'new_folders': folders, 'new_folders_total': folders_total,
        'summary': str(plan.get('summary') or '')[:400],
        'kept_handwritten': _safe_int(plan.get('kept_handwritten')),
        'inventory': {
            'total_notes': _safe_int(inv.get('total_notes')),
            'folder_count': _safe_int(inv.get('folder_count')),
            'root_notes': _safe_int(inv.get('root_notes')),
            'uncategorized_notes': _safe_int(
                inv.get('uncategorized_notes')),
            'trash_notes': _safe_int(inv.get('trash_notes'))},
        'has_deletions': deletions_total > 0,
        'has_filing': (moves_total + folders_total) > 0,
    }


# ---------------------------------------------------------------------------
# The dialog — the model's renderer (themed QSS roles only).
# The Qt base is the best-effort import above; the rest of PyQt6 is
# imported lazily inside the methods, so the pure model stays importable
# without Qt — the hermetic law.
# ---------------------------------------------------------------------------

class ScanPlanDialog(_QT_DIALOG_BASE):
    """The scan plan on the owner's screen — Apply / Keep, nothing else.

    v0.63.0 — THE PLAN AS PANELS: one SECTION CARD per list, each with
    its own wash and its own scroll — 🗑️ the deletions on very pale
    red, 🌱 the new folders on very pale green, 🚚 the re-file list on
    the neutral sheet — and big tonal headings (``plan_heading``,
    15px/800) over each so the hierarchy reads at a glance. Each list
    panel caps at ~ten rows and SCROLLS beyond (the whole list, never
    a counted-away tail). The LLM's summary rides the help box, the
    inventory line opens the story, and the answering window ticks
    down (at zero: the safe defer — the same 'timeout' verdict a
    silent Telegram ask gets; nothing is done). Apply is the ONLY
    path that leads to 'confirmed'. Esc and the ✕ are declines — the
    vault stays byte-for-byte as it was."""

    #: the item column's pixel width (640 dialog - 2*20 root margins -
    #: 2*12 sync_card margins - 2*10 section-card margins - 12 scrollbar
    #: allowance - 9 item indent — the same never-clip arithmetic the
    #: ConnectionTestDialog taught, re-counted for the nested panels).
    _ITEM_COL_PX = 640 - 2 * 20 - 2 * 12 - 2 * 10 - 12 - 9

    def __init__(self, main_window, model: Dict,
                 timeout_s: float = 300.0):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)
        super().__init__(main_window)
        self.setObjectName("scan_plan_dialog")
        self.setWindowTitle("Vault scan review")
        self.setModal(True)
        self.setMinimumWidth(640)
        self._verdict = VERDICT_DECLINED
        self._remaining = max(int(timeout_s or 300), 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = QLabel(
            "🗂️ Vault scan review — the whole plan, before anything is "
            "touched. Nothing is deleted or moved until you answer.")
        header.setWordWrap(True)
        header.setObjectName("info_header")
        root.addWidget(header)

        card = QWidget()
        card.setObjectName("sync_card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(4)

        inv = (model or {}).get('inventory') or {}
        self._heading(card_lay,
                      f"🏛️ The library: {inv.get('total_notes', 0)} "
                      f"note(s) in {inv.get('folder_count', 0)} "
                      f"folder(s) — {inv.get('root_notes', 0)} "
                      f"orphaned, {inv.get('uncategorized_notes', 0)}"
                      f" uncategorized")
        if inv.get('trash_notes'):
            self._line(card_lay,
                       f"🗑️ {inv.get('trash_notes')} note(s) rest in the "
                       f"Trash folder — they ride the deletions below",
                       bold=True)
        kept = int((model or {}).get('kept_handwritten') or 0)
        if kept:
            self._line(card_lay,
                       f"✍️ {kept} hand-written note(s) carry marks — "
                       f"kept (yours; the app never deletes what it did "
                       f"not write)", bold=True)

        self._section(card_lay, "plan_delete_card", "danger",
                      "🗑️ Marked for deletion",
                      (model or {}).get('deletions') or [],
                      int((model or {}).get('deletions_total') or 0),
                      self._deletion_line)
        self._section(card_lay, "plan_create_card", "grow",
                      "🌱 New folders to create",
                      (model or {}).get('new_folders') or [],
                      int((model or {}).get('new_folders_total') or 0),
                      self._folder_line)
        self._section(card_lay, "plan_move_card", "neutral",
                      "🚚 Notes to re-file",
                      (model or {}).get('moves') or [],
                      int((model or {}).get('moves_total') or 0),
                      self._move_line)

        summary = str((model or {}).get('summary') or '')
        if summary:
            box = QLabel(summary)
            box.setObjectName("help_box")
            box.setWordWrap(True)
            card_lay.addWidget(box)
        root.addWidget(card, 1)

        # ---- the answering window (the safe defer ticking) --------------
        self._clock = QLabel(self._clock_text())
        self._clock.setObjectName("cc_item")
        root.addWidget(self._clock)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        # ---- the two doors: Keep (safe) and Apply -----------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        keep_btn = QPushButton("✋ Keep everything")
        keep_btn.setToolTip(
            "The safe default — nothing is deleted, nothing moves, no "
            "folder is created; the vault stays exactly as it is (I'll "
            "propose again on the next scan).")
        keep_btn.clicked.connect(self._on_keep)
        main_window._style_btn(keep_btn, 'secondary')
        btn_row.addWidget(keep_btn)

        apply_btn = QPushButton("🗂️ Apply plan")
        apply_btn.setToolTip(
            "Create the new folders, move the listed notes (files only — "
            "never a byte rewritten), and run the marked deletions "
            "through the same machinery a confirmed Telegram ask uses "
            "(.trash/banished + never-fetch blacklist).")
        apply_btn.clicked.connect(self._on_apply)
        main_window._style_btn(apply_btn, 'primary')
        btn_row.addWidget(apply_btn)
        root.addLayout(btn_row)

    # -- rendering helpers ------------------------------------------------

    def _line(self, lay, text: str, bold: bool = False):
        from PyQt6.QtWidgets import QLabel
        label = QLabel(str(text or ''))
        label.setObjectName("cc_row_name" if bold else "cc_item")
        label.setWordWrap(not bold)
        lay.addWidget(label)

    def _heading(self, lay, text: str):
        """v0.63.0 — the big neutral section voice (``plan_heading``):
        the library line opens the story at the same size the sections
        speak, so the panel reads as one headed hierarchy."""
        from PyQt6.QtWidgets import QLabel
        label = QLabel(str(text or ''))
        label.setObjectName("plan_heading")
        label.setProperty("tone", "neutral")
        label.setWordWrap(True)
        lay.addWidget(label)

    def _section(self, lay, card_name: str, tone: str, title: str,
                 rows: List[Dict], total: int, shaper):
        """v0.63.0 — one SECTION PANEL: the big tonal heading, the list
        riding its OWN transparent scroll (the whole list, capped only
        by the model's freeze guard), and the honest '+N more' when
        even that guard bites. The card name picks the wash — very pale
        red for the deletions, very pale green for the creations, the
        neutral sheet for the re-file list — all from the theme kit's
        own roles (no per-dialog stylesheet, the de-style law)."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (QAbstractScrollArea, QFrame, QLabel,
                                     QScrollArea, QVBoxLayout, QWidget)
        if total <= 0:
            return
        card = QWidget()
        card.setObjectName(card_name)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(4)
        head = QLabel(f"{title} — {total}")
        head.setObjectName("plan_heading")
        head.setProperty("tone", tone)
        cl.addWidget(head)
        if rows:
            listw = QWidget()
            lw = QVBoxLayout(listw)
            lw.setContentsMargins(2, 0, 2, 0)
            lw.setSpacing(2)
            for row in rows:
                text, full = shaper(row)
                item = QLabel()
                item.setObjectName("cc_item")
                item.setWordWrap(False)      # ONE line, always
                try:
                    item.ensurePolished()    # the QSS 11px font applies
                    shown = item.fontMetrics().elidedText(
                        text, Qt.TextElideMode.ElideMiddle,
                        self._ITEM_COL_PX)
                except Exception:
                    shown = text
                item.setText(f"   {shown}")
                if full and full != text:
                    item.setToolTip(full)
                lw.addWidget(item)
            scroll = QScrollArea()
            scroll.setObjectName("plan_list")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            try:      # shrink-to-fit short lists, cap tall ones
                scroll.setSizeAdjustPolicy(
                    QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
            except Exception:
                pass
            scroll.setMaximumHeight(_LIST_MAX_PX)
            scroll.setWidget(listw)
            cl.addWidget(scroll)
        more = total - len(rows)
        if more > 0:
            self._line(cl, f"   … +{more} more (counted, not listed)")
        lay.addWidget(card)

    def _deletion_line(self, d: Dict):
        door = str(d.get('door') or 'note tag')
        via = {'note tag': 'your mark', 'trash folder':
               'you moved it to Trash'}.get(door, door)
        text = f"🗑️ {d.get('title', '')} — via {via}"
        full = f"{d.get('title', '')}\n{d.get('detail', '')}\n" \
               f"marker: {d.get('marker', '')} · door: {door}"
        return text, full

    def _folder_line(self, f):
        return f"📁 {f}", f"New folder: {f}"

    def _move_line(self, m: Dict):
        # v0.63.0 — the transportation glyph the owner asked for: notes
        # to re-file ride the 🚚 (the heading and the rows agree).
        text = f"🚚 {m.get('note', '')}: {m.get('from', '')} → " \
               f"{m.get('to', '')}"
        full = text + (f"\nwhy: {m.get('reason')}" if m.get('reason')
                       else '')
        return text, full

    # -- the verdict doors --------------------------------------------------

    def _clock_text(self) -> str:
        m, s = divmod(max(self._remaining, 0), 60)
        return (f"⏱ Answering window: {m}:{s:02d} — at zero nothing is "
                f"done (the same safe defer as a silent Telegram ask)")

    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self._verdict = VERDICT_TIMEOUT
            self.reject()
            return
        self._clock.setText(self._clock_text())

    def _on_keep(self):
        self._verdict = VERDICT_DECLINED
        self.reject()

    def _on_apply(self):
        self._verdict = VERDICT_CONFIRMED
        self.accept()

    def verdict(self) -> str:
        """'confirmed' | 'declined' | 'timeout' — the owner's answer."""
        return self._verdict


def ask_scan_plan(main_window, plan: Optional[Dict],
                  timeout_s: float = 300.0) -> str:
    """Open the scan plan modal and return the owner's verdict.

    Builds the display model (pure), renders it, and blocks in the
    dialog's own event loop until the owner answers or the window
    closes. NEVER touches the vault — the verdict is the caller's to
    enforce (the job's apply pass, exactly like a Telegram confirm)."""
    model = plan_display_model(plan)
    dlg = ScanPlanDialog(main_window, model, timeout_s=timeout_s)
    dlg.exec()
    return dlg.verdict()
