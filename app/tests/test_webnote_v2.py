"""web-v2 website-note format tests (v0.27.0).

The v0.27.0 note template is decision-oriented:
  - "What it does" replaces "Core offerings"/"Standout feature"
  - unknown values are OMITTED from the body (no "Login required: unknown")
  - new "Practical details" (price terms, sign-up, framework/license/install)
    and "Watch out" sections, rendered only when non-empty
  - "Best used for" keeps its exact heading + always-present fallback line,
    and "Similar tools" always closes the body — the recall regex
    (gitcurator.core.recall._BEST_USED_RE) needs a heading AFTER the
    Best-used-for section, so that invariant is locked here too.
"""
import unittest

from gitcurator.core.recall import extract_website_fields
from gitcurator.core.website_pipeline import (
    WEBSITE_PROMPT_VERSION, build_website_note,
)

FULL_V2 = {
    'name': 'Reverse UI',
    'one_line': '68 animated, ready-to-use web UI components with styling '
                'included.',
    'what_it_does': [
        '68 animated UI components (loaders, menus, effects)',
        'Copy-paste components with the styling included',
    ],
    'best_used_for':
        'Use when you need a polished loader or menu animation quickly and '
        'plain CSS is fine.',
    'practical_details': [
        'License: personal and commercial use',
        'Delivery: download after purchase',
    ],
    'pricing': 'paid',
    'pricing_detail': '$60 one-time',
    'login_required': 'yes',
    'watch_out': ['No free tier visible on the page'],
    'similar_tools': ['Acid UI', 'Shadcn/ui'],
    'tags': ['ui', 'animation'],
    'confidence': 'high',
}

OLD_SHAPE = {
    'name': 'Golden test site',
    'one_line': 'Offline golden-run placeholder description.',
    'core_offerings': ['Offline validation', 'Report generation'],
    'standout_feature': '',
    'best_used_for':
        'Use when you need to validate the pipeline without network.',
    'pricing': 'unknown',
    'login_required': 'unknown',
    'similar_tools': [],
    'tags': ['golden'],
    'confidence': 'high',
}


class TestWebNoteV2(unittest.TestCase):

    def _note(self, analysis, **kw):
        return build_website_note(
            'https://example.com/', analysis, 'Design', 'UI kits',
            'ok', **kw)

    def test_prompt_version_bumped(self):
        self.assertEqual(WEBSITE_PROMPT_VERSION, 'web-v2')

    def test_full_v2_render(self):
        note = self._note(dict(FULL_V2))
        self.assertIn('prompt_version: "web-v2"', note)
        self.assertIn('## What it does', note)
        self.assertIn('- 68 animated UI components', note)
        self.assertIn('## Best used for', note)
        self.assertIn('Use when you need a polished loader', note)
        self.assertIn('## Practical details', note)
        self.assertIn('- Pricing: paid — $60 one-time', note)
        self.assertIn('- Sign-up required: yes', note)
        self.assertIn('- License: personal and commercial use', note)
        self.assertIn('## Watch out', note)
        self.assertIn('- No free tier visible on the page', note)
        self.assertIn('## Similar tools', note)
        self.assertIn('Acid UI, Shadcn/ui', note)
        # gone: the marketing-prone sections and their fillers
        self.assertNotIn('Core offerings', note)
        self.assertNotIn('Standout feature', note)
        self.assertNotIn('(none identified)', note)
        self.assertNotIn('Pricing & sign-up', note)

    def test_unknown_values_omitted_not_printed(self):
        sparse = {
            'name': 'Mystery site',
            'one_line': 'Nobody knows.',
            'best_used_for': 'Use when you need to test omissions.',
        }
        note = self._note(sparse)
        body = note.split('---', 2)[2]          # frontmatter stripped
        self.assertNotIn('unknown', body)
        self.assertNotIn('## Practical details', note)
        self.assertNotIn('## Watch out', note)
        self.assertNotIn('## What it does', note)   # nothing stated -> omitted
        self.assertIn('## Best used for', note)
        self.assertIn('## Similar tools', note)
        self.assertIn('—', note)                    # empty similar -> dash
        # frontmatter still records the searchable enums
        self.assertIn('pricing: "unknown"', note)
        self.assertIn('login_required: "unknown"', note)

    def test_old_shape_analysis_still_renders(self):
        note = self._note(dict(OLD_SHAPE))
        self.assertIn('## What it does', note)
        self.assertIn('- Offline validation', note)
        self.assertIn('## Best used for', note)
        self.assertIn('## Similar tools', note)

    def test_best_used_for_fallback_and_closing_section(self):
        # no best_used_for at all: the section must still render (recall
        # parses it) with the fallback line, and Similar tools must follow.
        note = self._note({'name': 'X', 'one_line': 'Y'})
        self.assertIn(
            '## Best used for\n'
            'Use when you need to… (not captured — see the source link).',
            note)
        self.assertLess(note.index('## Best used for'),
                        note.index('## Similar tools'))

    def test_pricing_known_without_detail(self):
        note = self._note({'name': 'X', 'one_line': 'Y', 'pricing': 'free'})
        self.assertIn('- Pricing: free', note)

    def test_pricing_detail_no_stutter(self):
        # a detail that already starts with the enum word must not double up
        note = self._note({'name': 'X', 'one_line': 'Y', 'pricing': 'paid',
                           'pricing_detail': 'paid product, $60'})
        self.assertIn('- Pricing: paid product, $60', note)
        self.assertNotIn('paid — paid', note)

    def test_practical_and_watch_caps(self):
        analysis = {
            'name': 'X', 'one_line': 'Y',
            'practical_details': [f'fact {i}' for i in range(7)],
            'watch_out': [f'caveat {i}' for i in range(5)],
        }
        note = self._note(analysis)
        self.assertEqual(note.count('- fact '), 4)
        self.assertEqual(note.count('- caveat '), 2)

    def test_recall_extraction_works_on_v2_note(self):
        # integration: the recall/linking regex still reads a v2 note
        note = self._note(dict(FULL_V2))
        fields = extract_website_fields(note)
        self.assertEqual(fields['best_used_for'],
                         FULL_V2['best_used_for'])

    def test_recall_extraction_works_on_minimal_v2_note(self):
        note = self._note({'name': 'X', 'one_line': 'Y'})
        fields = extract_website_fields(note)
        self.assertIn('not captured', fields['best_used_for'])


if __name__ == '__main__':
    unittest.main()
