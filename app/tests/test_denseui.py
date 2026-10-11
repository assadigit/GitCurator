"""tests/test_denseui.py — v0.66.0 THE DENSE PAGES + THE VIBRANT BAR.

The owner's asks (session, verbatim):
  * "In the vaults part of the setting, hide tips and explanations
    behind a '?' emoji, so the browse fields are next to each other as
    much as possible. remove unnecessary text like '(new — the phase 2
    pipeline)'"
  * "do the same organization and tidyness for backup section as well.
    it's too confusing."
  * "use a more vibrant color for progress bar, it's too dark and ugly."
  * "add an icon for scan button just like you did for sync and test
    connection."

Pinned here: the '?' affordance exists and is wired into the theme kit;
the paragraph walls and the release-history labels are gone; the bar's
segment tokens are the vibrant twins; the Scan CTA carries a glyph.
"""

import inspect
import unittest

from gitcurator.gui import theme as _theme
from gitcurator.gui import app as gui_app
from gitcurator.gui.main_window import theme as _mw_theme


class TestTheHelpAffordance(unittest.TestCase):

    def test_the_help_button_kind_exists_in_the_theme_kit(self):
        qss = _theme.build_qss(_theme.LIGHT) + _theme.build_qss(_theme.DARK)
        self.assertIn('btn_kind="help"', qss)
        self.assertIn('QPushButton[btn_kind="help"]', qss)

    def test_the_mixin_builds_help_buttons(self):
        self.assertTrue(callable(getattr(
            gui_app.MainWindow, '_make_help_button', None)))


class TestTheDenseVaultPage(unittest.TestCase):

    def _src(self):
        return inspect.getsource(gui_app.MainWindow.initUI)

    def test_the_release_history_label_is_gone(self):
        # the OWNER-VISIBLE labels must not carry release history (the
        # check walks the quoted strings, not the code comments that
        # cite the owner's ask)
        src = self._src()
        for line in src.splitlines():
            line = line.strip()
            if line.startswith('#'):
                continue        # a comment may quote the ask itself
            if line.startswith('QGroupBox(') or line.startswith('QLabel(') \
                    or 'setPlaceholderText(' in line:
                self.assertNotIn('Phase 2 pipeline', line)
                self.assertNotIn('new —', line)

    def test_the_group_titles_say_what_they_are(self):
        src = self._src()
        self.assertIn('QGroupBox("Websites vault")', src)
        self.assertIn('QGroupBox("Manual Notes vault")', src)

    def test_the_vault_picker_is_one_row(self):
        # combo + Browse + Remove + '?' side by side — no separate button row
        src = self._src()
        self.assertIn('vault_row.addWidget(self.vault_combo, 1)', src)
        self.assertIn('vault_row.addWidget(browse_btn)', src)
        self.assertIn('vault_row.addWidget(remove_btn)', src)
        self.assertIn('vault_row.addWidget(self._make_help_button(', src)

    def test_the_paragraph_walls_are_behind_question_marks(self):
        src = self._src()
        self.assertNotIn('web_law_label', src)
        self.assertNotIn('manual_mirror_hint', src)
        self.assertNotIn('pipes_info', src)
        # the law list rides behind a '?' glyph now
        self.assertIn("self._make_help_button(\n            \"🔒 Always banned", src)

    def test_the_law_domains_live_in_the_help_tooltip(self):
        from gitcurator.core import links as _links
        src = self._src()
        self.assertIn("', '.join(_links.LAW_BLOCKED_DOMAINS)", src)


class TestTheDenseBackupPage(unittest.TestCase):

    def _src(self):
        from gitcurator.gui.main_window import backup_seal as _bs
        return inspect.getsource(_bs.BackupSealMixin._create_backup_tab)

    def test_the_paragraph_walls_are_gone(self):
        src = self._src()
        self.assertNotIn('info_label = QLabel(', src)
        self.assertNotIn('seal_info = QLabel(', src)
        self.assertNotIn('good_info = QLabel(', src)
        self.assertNotIn('dash_info = QLabel(', src)

    def test_every_group_carries_a_question_mark(self):
        src = self._src()
        self.assertGreaterEqual(src.count('self._make_help_button('), 5)

    def test_the_browse_row_is_styled_and_dense(self):
        src = self._src()
        self.assertIn("self._style_btn(browse_btn, 'secondary')", src)
        self.assertIn('folder_row.addWidget(self.backup_folder_input, 1)',
                      src)
        self.assertIn('folder_row.addWidget(browse_btn)', src)


class TestTheVibrantBar(unittest.TestCase):

    def test_light_mode_tokens_are_vibrant(self):
        self.assertEqual(_theme.LIGHT['progress_chunk'], '#7C3AED')
        self.assertEqual(_theme.LIGHT['bar_saved'], '#22C55E')
        self.assertEqual(_theme.LIGHT['bar_retry'], '#F59E0B')

    def test_dark_mode_tokens_are_vibrant(self):
        self.assertEqual(_theme.DARK['progress_chunk'], '#A78BFA')
        self.assertEqual(_theme.DARK['bar_saved'], '#4ADE80')
        self.assertEqual(_theme.DARK['bar_retry'], '#FBBF24')

    def test_the_segment_bar_paints_the_bar_tokens(self):
        from gitcurator.gui.main_window import ui as _ui
        src = inspect.getsource(_ui.SegmentBar.paintEvent)
        self.assertIn("t['bar_saved']", src)
        self.assertIn("t['bar_retry']", src)
        self.assertNotIn("t['success']", src)
        self.assertNotIn("t['warning']", src)

    def test_the_qss_chunk_rides_the_theme_token(self):
        qss = _theme.build_qss(_theme.LIGHT)
        self.assertIn(_theme.LIGHT['progress_chunk'], qss)


class TestTheScanIcon(unittest.TestCase):

    def test_the_scan_btn_gets_the_search_glyph(self):
        src = inspect.getsource(_mw_theme.ThemeMixin._refresh_main_icons)
        self.assertIn("set_btn_icon(self.scan_btn, 'search', accent, 18)",
                      src)

    def test_the_search_glyph_exists_in_the_icon_pack(self):
        from gitcurator.gui import icons as _icons
        self.assertIn('search', _icons.ICONS)


if __name__ == '__main__':
    unittest.main()
