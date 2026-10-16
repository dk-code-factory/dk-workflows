import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from decisions import section,replace,decision_api

class DecisionsTest(unittest.TestCase):
    def test_preserve_and_replace(self):
        old='## Fix\nDetails\n\n## Decisions\n- **API:** retain v1. Reason: clients. Source: issue #3.\n\n## Results\nDone\n'
        self.assertIn('retain v1',section(old))
        same=replace(old,section(old))
        self.assertEqual(section(same),section(old))
        self.assertIn('## Results\nDone',same)
        new='## Decisions\n- **API:** retain v2. Reason: migration. Source: review #4.'
        self.assertIn('retain v2',section(replace(old,new)))
        self.assertNotIn('retain v1',replace(old,new))
    def test_bound(self):
        with self.assertRaises(ValueError): replace('body','## Decisions\n'+'- a\n'*13)

    def test_replace_first_section_does_not_add_leading_blank_lines(self):
        body='## Decisions\n- old\n\n## Results\nDone\n'
        self.assertEqual(replace(body,'## Decisions\n- new'),
                         '## Decisions\n- new\n\n## Results\nDone\n')
        self.assertEqual(replace(body,''),'## Results\nDone\n')

    def history_body(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'body': '', 'headRefOid': 'a'*40},
                 'decisions': [], 'manual_decisions': []}
        previous = None
        for number in range(14):
            item = {'text': f'Choice {number}', 'references': []}
            previous = decision_api.add_decision(state, item, 'User chose behavior', previous)['id']
        body = decision_api.apply_replacements('', state['replacements'])
        return decision_api.merge_body(body, state['decisions'])

    def test_history_does_not_count_toward_active_entry_limit(self):
        body = self.history_body()
        self.assertEqual(section(replace(body, section(body))), section(body))

    def test_rewrite_cannot_drop_history_or_its_current_replacement(self):
        body = self.history_body()
        with self.assertRaisesRegex(ValueError, 'preserved verbatim'):
            replace(body, '## Decisions\n- New choice')
        without_current = section(body).replace('- **Choice:** Choice 13', '- New choice')
        with self.assertRaisesRegex(ValueError, 'missing replacement'):
            replace(body, without_current)

if __name__=='__main__': unittest.main()
