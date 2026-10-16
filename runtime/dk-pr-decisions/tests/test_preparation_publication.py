import copy
import json
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared.review_loop import encode

spec = importlib.util.spec_from_file_location('prepared_publication', ROOT / 'dk-pr-decisions/scripts/decisions.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.state = {'pr': {'url': 'https://github.com/o/r/pull/1', 'number': 1, 'headRefOid': 'a'*40,
                             'title': 'Encoding fix', 'body': 'Original scope\n\n## Decisions\n\n- Keep API\n'},
                      'decisions': [], 'manual_decisions': [], 'comments': {}}
        self.item = {'text': 'Encoding only', 'references': []}
        self.state['decisions'] = [self.item]
        self.body = 'Intended encoding-only scope\n\n## Decisions\n\n- Keep API\n'
        self.token = encode('dk-pr-description', 1, True)
        self.live = dict(self.state['pr'])
        self.calls = []

    def github(self, args):
        self.calls.append(args)
        if args[:2] == ['pr', 'view']:
            return dict(self.live)
        self.live['body'] = args[-1][len('body='):]
        return dict(self.live)

    def test_combined_body_publishes_once_preserves_title_and_records_intent_before_write(self):
        snapshots = []
        with patch.object(mod, 'gh_json', side_effect=self.github):
            result = mod.publish(self.state, [self.item], self.body, self.token,
                                 persist=lambda: snapshots.append(copy.deepcopy(self.state)))
            again = mod.publish(self.state, [self.item], self.live['body'], self.token)
        self.assertTrue(result['updated'])
        self.assertFalse(again['updated'])
        self.assertEqual(sum(call[0] == 'api' for call in self.calls), 1)
        self.assertTrue(self.live['body'].startswith('Intended encoding-only scope'))
        self.assertIn('Encoding only', self.live['body'])
        self.assertIn('Keep API', self.live['body'])
        self.assertEqual(self.live['title'], 'Encoding fix')
        self.assertEqual(snapshots[0]['publication_intent']['status'], 'pending')
        self.assertEqual(snapshots[-1]['publication_intent']['status'], 'verified')

    def test_changed_snapshot_title_head_or_body_blocks_without_write(self):
        for key in ('title', 'body', 'headRefOid'):
            self.live = dict(self.state['pr'])
            self.live[key] = 'external change'
            self.calls.clear()
            with self.subTest(key=key), patch.object(mod, 'gh_json', side_effect=self.github):
                result = mod.publish(self.state, [self.item], self.body, self.token)
            self.assertIn('changed', result['reason'])
            self.assertFalse(any(call[0] == 'api' for call in self.calls))

    def test_description_requires_completed_review_and_cannot_change_decisions(self):
        for token in (None, encode('dk-pr-description', 1), encode('decisions', 1, True)):
            with self.subTest(token=token), self.assertRaises(ValueError):
                mod.compose_publication(self.state, [self.item], self.body, token)
        with self.assertRaisesRegex(ValueError, 'outside the decision workflow'):
            mod.compose_publication(self.state, [self.item], 'New scope\n\n## Decisions\n\n- Remove API', self.token)

    def test_combined_replacement_preserves_history(self):
        mod.add_decision(self.state, self.item, 'Exact user answer', mod.decision_id('Keep API'))
        combined = mod.compose_publication(self.state, [self.item], self.body, self.token)
        records = mod.decision_records({'pr': {'body': combined, 'url': self.state['pr']['url']}})
        self.assertEqual(next(r for r in records if r['text'] == 'Keep API')['status'], 'superseded')
        self.assertEqual(next(r for r in records if r['text'] == 'Encoding only')['status'], 'active')

    def test_uncertain_applied_write_reconciles_without_duplicate(self):
        def uncertain(args):
            result = self.github(args)
            if args[0] == 'api':
                raise RuntimeError('Connection lost after server applied write')
            return result
        with patch.object(mod, 'gh_json', side_effect=uncertain):
            first = mod.publish(self.state, [self.item], self.body, self.token)
        self.assertIn('Connection lost', first['reason'])
        self.assertEqual(self.state['publication_intent']['status'], 'pending')
        self.state = json.loads(json.dumps(self.state))  # Resume from durable state, not shared object aliases.
        with patch.object(mod, 'gh_json', side_effect=self.github):
            second = mod.publish(self.state, [self.item], self.body, self.token, reconcile=True)
        self.assertEqual(second['reconciled'], 'published')
        self.assertEqual(sum(call[0] == 'api' for call in self.calls), 1)
        self.assertEqual(self.state['publication_intent']['status'], 'verified')
        self.assertEqual(self.state['publication_history'][0]['status'], 'verified')

    def test_uncertain_unapplied_write_stays_pending_until_explicit_reconciliation(self):
        def offline(args):
            if args[0] == 'api':
                raise RuntimeError('Connection lost before write')
            return self.github(args)
        with patch.object(mod, 'gh_json', side_effect=offline):
            mod.publish(self.state, [self.item], self.body, self.token)
        with patch.object(mod, 'gh_json', side_effect=self.github):
            result = mod.publish(self.state, [self.item], self.body, self.token)
            self.assertIn('uncertain', result['reason'])
            mod.publish(self.state, [self.item], reconcile=True)
            self.assertEqual(self.state['publication_intent']['status'], 'not_applied')
            self.assertTrue(mod.publish(self.state, [self.item], self.body, self.token)['updated'])
        self.assertEqual(sum(call[0] == 'api' for call in self.calls), 1)
        self.assertEqual(len(self.state['publication_history']), 2)

    def test_reconciliation_retains_newer_choice_and_description_until_published(self):
        for change, reconcile in (('choice', False), ('choice', True), ('description', True)):
            with self.subTest(change=change, reconcile=reconcile):
                self.setUp()
                def uncertain(args):
                    result = self.github(args)
                    if args[0] == 'api':
                        raise RuntimeError('Connection lost after apply')
                    return result
                with patch.object(mod, 'gh_json', side_effect=uncertain):
                    mod.publish(self.state, [self.item], self.body, self.token)
                self.state = json.loads(json.dumps(self.state))
                choices = [self.item]
                description = self.body
                if change == 'choice':
                    choices.append({'text': 'Preserve configured locale', 'references': []})
                    self.state['decisions'] = choices
                else:
                    description = self.body.replace('Intended encoding-only scope', 'Revised intended scope')
                with patch.object(mod, 'gh_json', side_effect=self.github):
                    result = mod.publish(self.state, choices, description, self.token, reconcile=reconcile)
                    self.assertTrue(result.get('reason'), result)
                    self.assertEqual(mod.envelope('publish', publication=result)['status'], 'pending')
                    again = mod.publish(self.state, choices, description, self.token, reconcile=True)
                    self.assertEqual(mod.envelope('publish', publication=again)['status'], 'pending')
                    self.assertEqual(sum(call[0] == 'api' for call in self.calls), 1)
                    self.assertTrue(mod.publish(self.state, choices, description, self.token)['updated'])
                    self.assertFalse(mod.publish(self.state, choices, description, self.token)['updated'])
                self.assertEqual(sum(call[0] == 'api' for call in self.calls), 2)
                self.assertIn('Preserve configured locale' if change == 'choice' else 'Revised intended scope', self.live['body'])


if __name__ == '__main__':
    unittest.main()
