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
  display rows out; the cap law: 20 items shown per list, the rest
  counted, exactly the way the Telegram ask caps its lists);
* ``ScanPlanDialog`` only RENDERS that model (QDialog + the themed QSS
  roles — sync_card / info_header / cc_row_name / cc_item / help_box;
  no per-dialog stylesheet, v0.30.0's de-style law). The dialog never
  touches the vault; the apply pass lives in the job's thread, exactly
  where it always lived.
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

#: How many items each list shows before "… +N more" (the Telegram
#: ask's own cap — the modal keeps the same honesty).
MAX_SHOWN = 20

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
    renders — every list capped at :data:`MAX_SHOWN` items with the
    honest ``+N more`` count, every field a plain str (a bad plan can
    never crash the modal). Pure: no Qt, no file reads, never raises."""
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
        shown = items[:MAX_SHOWN]
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
               if isinstance(f, (str,))][:MAX_SHOWN]
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

    One section per list (🗑️ deletions with their doors, 📁 new folders,
    📦 moves), the LLM's summary in the help box, the inventory line,
    and the answering window ticking down (at zero: the safe defer —
    the same 'timeout' verdict a silent Telegram ask gets; nothing is
    done). Apply is the ONLY path that leads to 'confirmed'. Esc and
    the ✕ are declines — the vault stays byte-for-byte as it was."""

    #: the item column's pixel width (600 dialog - 2*20 root margins -
    #: 2*12 card margins - 18 item indent — the ConnectionTestDialog's
    #: own arithmetic, kept so no line can ever clip).
    _ITEM_COL_PX = 600 - 2 * 20 - 2 * 12 - 18

    def __init__(self, main_window, model: Dict,
                 timeout_s: float = 300.0):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)
        super().__init__(main_window)
        self.setObjectName("scan_plan_dialog")
        self.setWindowTitle("Vault scan review")
        self.setModal(True)
        self.setMinimumWidth(600)
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
        self._line(card_lay, f"📚 The library: {inv.get('total_notes', 0)} "
                             f"note(s) in {inv.get('folder_count', 0)} "
                             f"folder(s) — {inv.get('root_notes', 0)} "
                             f"orphaned, {inv.get('uncategorized_notes', 0)}"
                             f" uncategorized", bold=True)
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

        self._section(card_lay, "🗑️ Marked for deletion",
                      (model or {}).get('deletions') or [],
                      int((model or {}).get('deletions_total') or 0),
                      self._deletion_line)
        self._section(card_lay, "📁 New folders to create",
                      (model or {}).get('new_folders') or [],
                      int((model or {}).get('new_folders_total') or 0),
                      self._folder_line)
        self._section(card_lay, "📦 Notes to re-file",
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

    def _section(self, lay, title: str, rows: List[Dict], total: int,
                 shaper):
        """One list: the bold heading with its count, the capped item
        lines, and the honest '+N more' when the cap bit."""
        from PyQt6.QtWidgets import QLabel
        if total <= 0:
            return
        self._line(lay, f"{title} — {total}", bold=True)
        for row in rows:
            text, full = shaper(row)
            item = QLabel()
            item.setObjectName("cc_item")
            item.setWordWrap(False)      # ONE line, always
            try:
                from PyQt6.QtCore import Qt
                item.ensurePolished()    # the QSS 11px font applies
                shown = item.fontMetrics().elidedText(
                    text, Qt.TextElideMode.ElideMiddle, self._ITEM_COL_PX)
            except Exception:
                shown = text
            item.setText(f"   {shown}")
            if full and full != text:
                item.setToolTip(full)
            lay.addWidget(item)
        more = total - len(rows)
        if more > 0:
            self._line(lay, f"   … +{more} more (counted, not listed)")

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
        text = f"📦 {m.get('note', '')}: {m.get('from', '')} → " \
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
