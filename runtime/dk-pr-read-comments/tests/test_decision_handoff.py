import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/report_pr_comments.py'
spec = importlib.util.spec_from_file_location('comments_report', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DecisionHandoffTests(unittest.TestCase):
    def test_report_and_json_include_description_choices_and_distinguish_history(self):
        state = {'pr': {'number': 2, 'title': 'Change API', 'url': 'https://github.com/o/r/pull/2',
                        'headRefOid': 'a'*40, 'body': '## Decisions\n\n- Old behavior\n'},
                 'viewer': 'me', 'run_number': 1, 'comments': {}, 'threads': {}, 'summaries': {},
                 'decisions': [], 'manual_decisions': []}
        new = {'text': 'Current behavior', 'references': []}
        core = mod.core
        core.add_decision(state, new, 'Use current behavior', core.decision_id('Old behavior'))
        body = core.merge_body(core.apply_replacements(state['pr']['body'], state['replacements']), [new])
        # Simulate a fresh cache: only the PR description contains the choices and history.
        state['pr']['body'] = body
        state['decisions'] = []
        state['manual_decisions'] = []
        state['replacements'] = []
        with tempfile.TemporaryDirectory() as td, \
             patch.object(core, 'scan', return_value=(state, Path(td)/'cache.json')), \
             patch.object(core, 'publish', side_effect=AssertionError('read-only report')):
            with patch('sys.stdout', new_callable=io.StringIO) as stdout:
                mod.main(pr=state['pr']['url'])
            self.assertIn('Current behavior', stdout.getvalue())
            self.assertNotIn('Old behavior', stdout.getvalue())
            with patch('sys.stdout', new_callable=io.StringIO) as stdout:
                mod.main(context_json=True, pr=state['pr']['url'])
            records = json.loads(stdout.getvalue())['decisions']
            self.assertEqual([r['text'] for r in records if r['status'] == 'active'], ['Current behavior'])
            self.assertEqual([r['text'] for r in records if r['status'] == 'superseded'], ['Old behavior'])


if __name__ == '__main__':
    unittest.main()
