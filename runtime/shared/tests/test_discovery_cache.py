import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'discovery_cache.py'


class DiscoveryCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'AGENTS.md').write_text('# Writing\nUse a direct title.\n')
        subprocess.run(['git', 'add', 'AGENTS.md'], cwd=self.repo, check=True)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'gh'
        fake.write_text('''#!/usr/bin/env python3
import json,os,sys
a=sys.argv[1:]
if a[:2]==['pr','list']:
 if os.environ.get('GH_EXPECT_BASE_REPO') and ('--repo' not in a or a[a.index('--repo')+1]!='github.com/acme/repo'): sys.exit(4)
 print(json.dumps([{'number':1,'title':'Fix title','body':'## Fix\\nDone','author':{'login':'person'},'mergedAt':'2026-01-01'}]))
elif a[:2]==['issue','list']:
 print(json.dumps([{'number':2,'title':'Bug','body':'Steps','labels':[],'author':{'login':'person'}}]))
elif a[:2]==['pr','view']:
 if a[-1]=='headRefOid': print(json.dumps({'headRefOid':'a'*40}))
 else: print(json.dumps({'number':1,'url':'https://github.com/acme/repo/pull/1','title':os.environ.get('FAKE_TITLE','Fix title'),'body':'Fixes #2','baseRefName':'main','headRefOid':'a'*40,'files':[{'path':'a.py'}]}))
elif a[:2]==['pr','diff']:
 print('diff --git a/a.py b/a.py\\n+changed')
elif a[:2]==['issue','view']:
 if '--repo' not in a or a[a.index('--repo')+1]!='github.com/acme/repo': sys.exit(5)
 print(json.dumps({'number':2,'url':'https://github.com/acme/repo/issues/2','title':'Bug','body':'Expected behavior','state':'OPEN'}))
else: sys.exit(2)
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, XDG_DATA_HOME=str(self.base / 'data'),
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'])

    def call(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              env=env or self.env, text=True, capture_output=True)

    def lookup(self, kind, *args):
        result = self.call('lookup', '--kind', kind, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def submit(self, ticket, answer):
        path = self.base / 'answer.json'
        path.write_text(json.dumps(answer))
        return self.call('submit', '--ticket', ticket, '--result-file', str(path))

    def test_format_miss_submit_hit_and_source_change(self):
        first = self.lookup('pr-format')
        self.assertEqual(first['status'], 'needs_discovery')
        evidence = json.loads(Path(first['evidence_file']).read_text())
        self.assertIn('file:AGENTS.md', evidence['sources'])
        self.assertIn('merged-pr-sample', evidence['sources'])
        inside = self.repo / 'answer.json'
        inside.write_text(json.dumps({'summary': 'Title', 'sources': ['file:AGENTS.md']}))
        refused = self.call('submit', '--ticket', first['ticket'], '--result-file', str(inside))
        self.assertNotEqual(refused.returncode, 0)
        inside.unlink()
        done = self.submit(first['ticket'], {'summary': 'Use a direct title and Fix section.',
                                             'sources': ['file:AGENTS.md']})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(Path(first['evidence_file']).exists())
        self.assertEqual(self.lookup('pr-format')['status'], 'ready')
        (self.repo / 'AGENTS.md').write_text('# Writing\nUse an imperative title.\n')
        changed = self.lookup('pr-format')
        self.assertEqual(changed['status'], 'needs_discovery')
        invalid = self.submit(changed['ticket'], {'summary': 'Guess', 'sources': ['invented']})
        self.assertNotEqual(invalid.returncode, 0)
        (self.repo / 'AGENTS.md').write_text('# Writing\nUse a short title.\n')
        stale = self.submit(changed['ticket'], {'summary': 'Use an imperative title.',
                                                'sources': ['file:AGENTS.md']})
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn('evidence changed', stale.stderr)
        self.assertEqual(self.lookup('commit-format')['status'], 'needs_discovery')

    def test_pr_format_samples_upstream_repository(self):
        env = dict(self.env, GH_EXPECT_BASE_REPO='1')
        result = self.call('lookup', '--kind', 'pr-format', '--pr',
                           'https://github.com/acme/repo/pull/1', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(Path(json.loads(result.stdout)['evidence_file']).read_text())
        self.assertIn('merged-pr-sample', evidence['sources'])
        self.assertEqual(evidence['scope'], 'https://github.com/acme/repo')

    def test_pr_scope_and_map_are_scoped_to_live_metadata(self):
        scope = self.lookup('pr-scope')
        evidence = json.loads(Path(scope['evidence_file']).read_text())
        self.assertIn('issue:2', evidence['sources'])
        done = self.submit(scope['ticket'], {'summary': 'Fix the linked bug.',
                                             'sources': ['pr-metadata', 'issue:2']})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.lookup('pr-scope')['status'], 'ready')
        changed_env = dict(self.env, FAKE_TITLE='Fix revised title')
        result = self.call('lookup', '--kind', 'pr-scope', env=changed_env)
        self.assertEqual(json.loads(result.stdout)['status'], 'needs_discovery')
        mapping = self.lookup('pr-map')
        self.assertEqual(mapping['status'], 'needs_discovery')
        self.assertIn('pr-diff', json.loads(Path(mapping['evidence_file']).read_text())['sources'])

    def test_thread_summaries_refresh_only_changed_thread(self):
        snapshot = self.base / 'snapshot.json'
        def save(items):
            canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            snapshot.write_text(json.dumps({'complete': True, 'itemCount': len(items),
                                            'itemsSha256': hashlib.sha256(canonical.encode()).hexdigest(),
                                            'pullRequest': {'url': 'https://github.com/acme/repo/pull/1',
                                                            'headRefOid': 'a' * 40, 'description': 'Fix'},
                                            'items': items}))
        items = [{'reference': 'T1', 'text': 'Change a'}, {'reference': 'T2', 'text': 'Change b'}]
        save(items)
        first = self.lookup('thread-summaries', '--snapshot-file', str(snapshot))
        self.assertEqual(first['status'], 'needs_discovery')
        done = self.submit(first['ticket'], {'thread_summaries': [
            {'reference': 'T1', 'summary': 'Change a is requested.'},
            {'reference': 'T2', 'summary': 'Change b is requested.'}]})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.lookup('thread-summaries', '--snapshot-file', str(snapshot))['status'], 'ready')
        items[1]['text'] = 'Change b differently'
        save(items)
        changed = self.lookup('thread-summaries', '--snapshot-file', str(snapshot))
        evidence = json.loads(Path(changed['evidence_file']).read_text())
        self.assertEqual(list(evidence['sources']), ['thread:T2'])
        wrong = self.submit(changed['ticket'], {'thread_summaries': [
            {'reference': 'T1', 'summary': 'Stale'}]})
        self.assertNotEqual(wrong.returncode, 0)
        done = self.submit(changed['ticket'], {'thread_summaries': [
            {'reference': 'T2', 'summary': 'Change b differently is requested.'}]})
        self.assertEqual(done.returncode, 0, done.stderr)
        context = json.loads(done.stdout)['context']
        self.assertEqual(set(context), {'T1', 'T2'})

    def test_pr_scope_reuses_complete_thread_snapshot(self):
        snapshot = self.base / 'scope-snapshot.json'
        items = []
        snapshot.write_text(json.dumps({
            'complete': True, 'itemCount': 0, 'items': items,
            'itemsSha256': hashlib.sha256(b'[]').hexdigest(),
            'pullRequest': {'number': 1, 'url': 'https://github.com/acme/repo/pull/1',
                            'title': 'Fix title', 'description': 'Fixes #2',
                            'baseRefName': 'main', 'headRefOid': 'a' * 40},
        }))
        scope = self.lookup('pr-scope', '--pr', 'https://github.com/acme/repo/pull/1',
                            '--snapshot-file', str(snapshot))
        evidence = json.loads(Path(scope['evidence_file']).read_text())
        self.assertEqual(evidence['sources']['pr-metadata']['body'], 'Fixes #2')
        self.assertIn('issue:2', evidence['sources'])
        changed = self.call('lookup', '--kind', 'pr-scope', '--pr',
                            'https://github.com/acme/repo/pull/1', '--snapshot-file',
                            str(snapshot), env=dict(self.env, FAKE_TITLE='Changed'))
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn('changed since thread snapshot', changed.stderr)


if __name__ == '__main__':
    unittest.main()
