import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/review_context.py'
spec = importlib.util.spec_from_file_location('review_context', SCRIPT)
review_context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_context)

class ReviewContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        for key,value in [('user.name','Fixture'),('user.email','fixture@example.invalid')]:
            subprocess.run(['git','-C',str(self.root),'config',key,value],check=True)
        (self.root/'a.py').write_text('one')
        subprocess.run(['git','-C',str(self.root),'add','a.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'commit','-qm','base'],check=True)
        self.sha=subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        binpath = self.root/'bin'; binpath.mkdir()
        fake = binpath/'gh'
        fake.write_text('''#!/usr/bin/env python3
import json,os,sys
mode=os.environ.get('GH_FIXTURE_MODE','ok')
if mode=='fail': print('missing PR',file=sys.stderr); sys.exit(1)
if sys.argv[1]=='api':
 files=[{'filename':'a.py','additions':1,'deletions':1,'status':'modified'},
        {'filename':'b.py','additions':1,'deletions':0,'status':'added'}]
 if mode=='large_missing': files=files[:1]
 print(json.dumps([files]))
elif sys.argv[1:3]==['pr','view']:
 sha=os.environ.get('GH_FIXTURE_SHA','a'*40)
 base=os.environ.get('GH_FIXTURE_BASE_SHA',sha)
 if mode=='moving' and sys.argv[-1]=='headRefOid,baseRefOid': sha='b'*40
 files=[{'path':'a b.py'}] if mode=='spaces' else [{'path':'a.py'},{'path':'b.py'}]
 if mode in ('large_missing','large_truncated'): files=[{'path':'a.py'}]
 print(json.dumps({'number':7,'url':'https://github.com/o/r/pull/7','title':'fixture','body':'test','baseRefName':'main','baseRefOid':base,'headRefName':'topic','headRefOid':sha,'files':files}))
elif sys.argv[1:3]==['pr','diff']:
 if mode in ('large','large_missing','large_truncated'):
  print('HTTP 406: Sorry, the diff exceeded the maximum number of lines (20000)',file=sys.stderr); sys.exit(1)
 if '--name-only' in sys.argv: print('a b.py' if mode=='spaces' else 'a.py\\nb.py')
 elif mode=='empty': print('')
 elif mode=='partial': print('diff --git a/a.py b/a.py\\n+one')
 elif mode=='spaces': print('diff --git a/a b.py b/a b.py\\n+one')
 else: print('diff --git a/a.py b/a.py\\n+one\\ndiff --git a/b.py b/b.py\\n+two')
else: sys.exit(2)
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binpath)+os.pathsep+os.environ['PATH'],GH_FIXTURE_SHA=self.sha)
    def call(self, mode):
        return subprocess.run([sys.executable,str(SCRIPT),'--cwd',str(self.root),'--pr','7'],
                              env=dict(self.env,GH_FIXTURE_MODE=mode), text=True,capture_output=True)
    def test_fetch_and_coverage(self):
        p=self.call('ok')
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout)
        self.assertFalse(result['local_matches_pr'])
        self.assertEqual(json.loads(Path(result['metadata']).read_text())['number'],7)
        self.assertIn('diff --git a/b.py',Path(result['patch']).read_text())
    def test_fails_closed(self):
        for mode,message in [('fail','missing PR'),('empty','empty or incomplete'),('partial','every changed file'),('moving','head or base changed')]:
            with self.subTest(mode=mode):
                p=self.call(mode)
                self.assertNotEqual(p.returncode,0)
                self.assertIn(message,p.stderr)
                self.assertFalse(p.stdout)
    def test_patch_with_spaces_in_filename(self):
        p=self.call('spaces')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(Path(json.loads(p.stdout)['metadata']).read_text())['files'],
                         [{'path':'a b.py'}])
    def test_dirty_checkout_is_not_remote_context(self):
        repo=self.root/'work'; repo.mkdir()
        subprocess.run(['git','init','-q',str(repo)],check=True)
        for key,value in [('user.name','Fixture'),('user.email','fixture@example.invalid')]:
            subprocess.run(['git','-C',str(repo),'config',key,value],check=True)
        (repo/'a.py').write_text('one')
        subprocess.run(['git','-C',str(repo),'add','a.py'],check=True)
        subprocess.run(['git','-C',str(repo),'commit','-qm','base'],check=True)
        sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        env=dict(self.env,GH_FIXTURE_SHA=sha)
        args=[sys.executable,str(SCRIPT),'--cwd',str(repo),'--pr','7']
        clean=json.loads(subprocess.run(args,env=env,text=True,capture_output=True,check=True).stdout)
        self.assertTrue(clean['local_matches_pr'])
        (repo/'untracked').write_text('local only')
        dirty=json.loads(subprocess.run(args,env=env,text=True,capture_output=True,check=True).stdout)
        self.assertFalse(dirty['worktree_clean'])
        self.assertFalse(dirty['local_matches_pr'])

    def test_large_diff_falls_back_to_pinned_local_git_diff(self):
        base=self.sha
        (self.root/'a.py').write_text('two\n')
        (self.root/'b.py').write_text('new\n')
        subprocess.run(['git','-C',str(self.root),'add','a.py','b.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'commit','-qm','head'],check=True)
        head=subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        self.env.update(GH_FIXTURE_BASE_SHA=base,GH_FIXTURE_SHA=head)
        result=self.call('large')
        self.assertEqual(result.returncode,0,result.stderr)
        context=json.loads(result.stdout)
        self.assertEqual(context['base_sha'],base)
        self.assertEqual(context['head_sha'],head)
        patch=Path(context['patch']).read_text()
        self.assertIn('diff --git a/a.py b/a.py',patch)
        self.assertIn('diff --git a/b.py b/b.py',patch)
        self.assertEqual(subprocess.check_output(['git','-C',context['checkout'],'rev-parse','HEAD'],text=True).strip(),head)
        truncated=self.call('large_truncated')
        self.assertEqual(truncated.returncode,0,truncated.stderr)
        self.assertEqual(len(json.loads(Path(json.loads(truncated.stdout)['metadata']).read_text())['files']),2)
        missing=self.call('large_missing')
        self.assertNotEqual(missing.returncode,0)
        self.assertIn('every changed file',missing.stderr)

    def test_missing_fork_commits_fetch_from_correct_repositories(self):
        metadata = {'url': 'https://github.com/upstream/repo/pull/7',
                    'headRefOid': 'b' * 40,
                    'headRepository': {'nameWithOwner': 'contributor/repo'}}
        calls = []
        def fake_command(args, cwd):
            calls.append(args)
            if args == ['git', 'remote']:
                return 'origin\nfork\n'
            if args[-2:] == ['get-url', 'origin']:
                return 'git@github.com:upstream/repo.git'
            if args[-2:] == ['get-url', 'fork']:
                return 'git@github.com:contributor/repo.git'
            return ''
        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, '', '')
        with patch.object(review_context, 'command', side_effect=fake_command), \
             patch.object(review_context.subprocess, 'run', side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, 'base is unavailable'):
                review_context.ensure_commit(self.root, 'a' * 40, self.root, metadata)
            self.assertIn(['git', 'fetch', '--quiet',
                           'git@github.com:upstream/repo.git', 'a' * 40], calls)
            calls.clear()
            review_context.ensure_commit(self.root, 'b' * 40, self.root, metadata)
            self.assertIn(['git', 'fetch', '--quiet',
                           'git@github.com:upstream/repo.git', 'b' * 40], calls)
            self.assertIn(['git', 'fetch', '--quiet',
                           'git@github.com:contributor/repo.git', 'b' * 40], calls)

    def test_plain_diff_checkout_contains_remote_base_for_native_reviewers(self):
        upstream = self.root / 'upstream'
        subprocess.run(['git', 'clone', '-q', str(self.root), str(upstream)], check=True)
        for key, value in [('user.name', 'Fixture'), ('user.email', 'fixture@example.invalid')]:
            subprocess.run(['git', '-C', str(upstream), 'config', key, value], check=True)
        (upstream / 'upstream.txt').write_text('new base commit\n')
        subprocess.run(['git', '-C', str(upstream), 'add', 'upstream.txt'], check=True)
        subprocess.run(['git', '-C', str(upstream), 'commit', '-qm', 'advance base'], check=True)
        base = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
        (self.root / 'a.py').write_text('two\n')
        (self.root / 'b.py').write_text('new\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'a.py', 'b.py'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'head'], check=True)
        head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        env = dict(self.env, GH_FIXTURE_BASE_SHA=base, GH_FIXTURE_SHA=head, GH_FIXTURE_MODE='ok')
        with patch.dict(os.environ, env), \
             patch.object(review_context, 'repository_remote', return_value=str(upstream)):
            context = review_context.fetch(self.root, '7')
        result = subprocess.run(['git', 'diff', '--name-only', f'{base}...{head}'],
                                cwd=context['checkout'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), {'a.py', 'b.py'})

if __name__=='__main__': unittest.main()
