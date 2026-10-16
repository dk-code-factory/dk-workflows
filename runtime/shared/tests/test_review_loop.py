import json
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'review_loop.py'
EDIT = Path(__file__).resolve().parents[2] / 'dk-pr-description/scripts/pr_edit_guard.py'


def call(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True)


class ReviewLoopTest(unittest.TestCase):
    def test_decisions_stop_after_three_changed_passes(self):
        result = json.loads(call('start', '--kind', 'decisions').stdout)
        self.assertEqual(result['max_passes'], 3)
        for review_pass in range(1, 4):
            result = json.loads(call('report', '--state', result['state'], '--updated', 'yes').stdout)
            self.assertTrue(result['updated'])
            self.assertEqual(result['complete'], review_pass == 3)
            self.assertEqual(result['next'], 'stop' if review_pass == 3 else 'review')
        self.assertEqual(result['stop_reason'], 'pass_limit')
        self.assertEqual(call('check', '--state', result['state'], '--kind', 'decisions').returncode, 0)
        self.assertNotEqual(call('report', '--state', result['state'], '--updated', 'yes').returncode, 0)

    def test_decisions_stop_on_unchanged_pass(self):
        result = json.loads(call('start', '--kind', 'decisions').stdout)
        result = json.loads(call('report', '--state', result['state'], '--updated', 'yes').stdout)
        result = json.loads(call('report', '--state', result['state'], '--updated', 'no').stdout)
        self.assertFalse(result['updated'])
        self.assertEqual((result['pass'], result['next'], result['stop_reason']), (2, 'stop', 'unchanged'))

    def test_decisions_reject_pass_above_cap(self):
        payload = {'version': 1, 'kind': 'decisions', 'pass': 4, 'complete': False}
        token = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
        self.assertNotEqual(call('report', '--state', token, '--updated', 'no').returncode, 0)

    def test_unchanged_pass_completes(self):
        started = json.loads(call('start', '--kind', 'commit-message').stdout)
        self.assertEqual(started['next'], 'review')
        result = json.loads(call('report', '--state', started['state'], '--updated', 'no').stdout)
        self.assertEqual((result['pass'], result['next'], result['complete']), (1, 'stop', True))
        self.assertEqual(call('check', '--state', result['state'], '--kind', 'commit-message').returncode, 0)
        self.assertNotEqual(call('report', '--state', result['state'], '--updated', 'no').returncode, 0)

    def test_updated_pass_requires_another_review_and_fifth_stops(self):
        result = json.loads(call('start', '--kind', 'split').stdout)
        for expected_pass in range(2, 6):
            self.assertNotEqual(call('check', '--state', result['state'], '--kind', 'split').returncode, 0)
            result = json.loads(call('report', '--state', result['state'], '--updated', 'yes').stdout)
            self.assertEqual((result['pass'], result['next']), (expected_pass, 'review'))
        result = json.loads(call('report', '--state', result['state'], '--updated', 'yes').stdout)
        self.assertEqual((result['pass'], result['next'], result['complete']), (5, 'stop', True))
        self.assertEqual(call('check', '--state', result['state'], '--kind', 'split').returncode, 0)
        self.assertNotEqual(call('check', '--state', result['state'], '--kind', 'dk-pr-description').returncode, 0)

    def test_invalid_states(self):
        for token in ('bad token', '', 'e30'):
            with self.subTest(token=token):
                self.assertNotEqual(call('report', '--state', token, '--updated', 'no').returncode, 0)
        self.assertNotEqual(call('start', '--kind', 'other').returncode, 0)
        self.assertNotEqual(call('report', '--state', 'e30', '--updated', 'maybe').returncode, 0)

    def test_pr_edit_requires_completed_description_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            title = root / 'title'; title.write_text('Reviewed title\n')
            body = root / 'body'; body.write_text('Reviewed body\n')
            log = root / 'gh-log'
            fake = root / 'gh'
            fake.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                            'open(os.environ["GH_LOG"],"w").write(" ".join(sys.argv[1:]))\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], GH_LOG=str(log))
            started = json.loads(call('start', '--kind', 'dk-pr-description').stdout)

            def edit(token):
                return subprocess.run([sys.executable, str(EDIT), '--review-state', token,
                                       '--title-file', str(title), '--body-file', str(body),
                                       'https://github.com/upstream/repo/pull/42'],
                                      env=env, text=True, capture_output=True)

            self.assertNotEqual(edit(started['state']).returncode, 0)
            self.assertFalse(log.exists())
            complete = json.loads(call('report', '--state', started['state'], '--updated', 'no').stdout)
            self.assertEqual(edit(complete['state']).returncode, 0)
            self.assertIn('pr edit https://github.com/upstream/repo/pull/42 --title Reviewed title --body-file', log.read_text())


if __name__ == '__main__':
    unittest.main()
