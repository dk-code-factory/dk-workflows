import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/split_guard.py'
LOOP=Path(__file__).resolve().parents[2]/'shared/review_loop.py'
spec=importlib.util.spec_from_file_location('split_guard', SCRIPT)
guard_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard_module)

def run(*a,cwd=None,env=None):
    return subprocess.run(a,cwd=cwd,env=env,text=True,capture_output=True)

class SplitTest(unittest.TestCase):
    def test_single_monolithic_commit_can_be_split_and_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); remote=base/'remote.git'; repo=base/'work'; repo.mkdir()
            run('git','init','--bare','-q',str(remote))
            run('git','init','-q',str(repo))
            for key,value in [('user.name','Tester'),('user.email','tester@example.com')]:
                run('git','config',key,value,cwd=repo)
            (repo/'base').write_text('base')
            run('git','add','base',cwd=repo); run('git','commit','-qm','base',cwd=repo)
            base_sha=run('git','rev-parse','HEAD',cwd=repo).stdout.strip()
            run('git','checkout','-qb','topic',cwd=repo)
            (repo/'a').write_text('a'); (repo/'b').write_text('b')
            run('git','add','a','b',cwd=repo); run('git','commit','-qm','monolithic change',cwd=repo)
            original_head=run('git','rev-parse','HEAD',cwd=repo).stdout.strip()
            original_tree=run('git','rev-parse','HEAD^{tree}',cwd=repo).stdout.strip()
            run('git','remote','add','origin',str(remote),cwd=repo)
            run('git','push','-qu','origin','topic',cwd=repo)
            binpath=base/'bin'; binpath.mkdir()
            fake=binpath/'gh'
            fake.write_text('#!/usr/bin/env python3\nimport json,subprocess\nprint(json.dumps({"number":1,"url":"https://github.com/tester/repo/pull/1","headRefName":"topic","headRefOid":subprocess.check_output(["git","rev-parse","origin/topic"],text=True).strip(),"isCrossRepository":False,"headRepository":{"nameWithOwner":"tester/repo"}}))\n')
            fake.chmod(0o755)
            env=dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH'])
            def guard(*args): return run(sys.executable,str(SCRIPT),*args,cwd=repo,env=env)
            p=guard('prepare','--base',base_sha)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('split requires at least two',guard('verify').stderr)
            run('git','reset','-q','--soft',base_sha,cwd=repo)
            run('git','reset','-q',cwd=repo)
            run('git','add','a',cwd=repo); run('git','commit','-qm','add a',cwd=repo)
            run('git','add','b',cwd=repo); run('git','commit','-qm','add b',cwd=repo)
            self.assertEqual(run('git','rev-parse','HEAD^{tree}',cwd=repo).stdout.strip(),original_tree)
            def loop(*args): return run(sys.executable,str(LOOP),*args,cwd=repo,env=env)
            state=json.loads(loop('start','--kind','split').stdout)['state']
            self.assertIn('completed split',guard('push','--allow-force-push','--review-state',state).stderr)
            for message in ('add b with context','add b as separate change'):
                run('git','commit','--amend','-qm',message,cwd=repo)
                state=json.loads(loop('report','--state',state,'--updated','yes').stdout)['state']
            state=json.loads(loop('report','--state',state,'--updated','no').stdout)['state']
            run('git','commit','--amend','-qm','x'*81,cwd=repo)
            self.assertIn('line 1 has 81 characters',guard('verify').stderr)
            self.assertIn('line 1 has 81 characters',
                          guard('push','--allow-force-push','--review-state',state).stderr)
            run('git','commit','--amend','-qm','add b as separate change',cwd=repo)
            self.assertEqual(guard('verify').returncode,0)
            self.assertEqual(run('git','ls-remote','--heads','origin','topic',cwd=repo).stdout.split()[0],original_head)
            refused=guard('push')
            self.assertNotEqual(refused.returncode,0)
            self.assertIn('--allow-force-push',refused.stderr)
            self.assertEqual(run('git','ls-remote','--heads','origin','topic',cwd=repo).stdout.split()[0],original_head)
            p=guard('push','--allow-force-push','--review-state',state)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(run('git','ls-remote','--heads','origin','topic',cwd=repo).stdout.split()[0],
                             run('git','rev-parse','HEAD',cwd=repo).stdout.strip())
            self.assertFalse((repo/'.git/pr-split-state.json').exists())

    def test_rewritten_tree_and_stale_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); remote=base/'remote.git'; repo=base/'work'; repo.mkdir()
            run('git','init','--bare','-q',str(remote))
            run('git','init','-q',str(repo))
            for k,v in [('user.name','Tester'),('user.email','tester@example.com')]:
                run('git','config',k,v,cwd=repo)
            (repo/'a').write_text('base'); run('git','add','a',cwd=repo); run('git','commit','-qm','base',cwd=repo)
            base_sha=run('git','rev-parse','HEAD',cwd=repo).stdout.strip()
            run('git','checkout','-qb','topic',cwd=repo)
            (repo/'a').write_text('changed'); run('git','commit','-qam','first',cwd=repo)
            (repo/'b').write_text('new'); run('git','add','b',cwd=repo); run('git','commit','-qm','second',cwd=repo)
            original_tree=run('git','rev-parse','HEAD^{tree}',cwd=repo).stdout.strip()
            run('git','remote','add','origin',str(remote),cwd=repo)
            run('git','push','-qu','origin','topic',cwd=repo)
            binpath=base/'bin'; binpath.mkdir()
            fake=binpath/'gh'; fake.write_text('#!/usr/bin/env python3\nimport json,subprocess\nprint(json.dumps({"number":1,"url":"https://github.com/tester/repo/pull/1","headRefName":"topic","headRefOid":subprocess.check_output(["git","rev-parse","origin/topic"],text=True).strip(),"isCrossRepository":False,"headRepository":{"nameWithOwner":"tester/repo"}}))\n'); fake.chmod(0o755)
            env=dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH'])
            def guard(*args): return run(sys.executable,str(SCRIPT),*args,cwd=repo,env=env)
            p=guard('prepare','--base',base_sha); self.assertEqual(p.returncode,0,p.stderr)
            run('git','reset','-q','--soft',base_sha,cwd=repo)
            run('git','reset','-q',cwd=repo)
            run('git','add','a',cwd=repo); run('git','commit','-qm','change existing file',cwd=repo)
            run('git','add','b',cwd=repo); run('git','commit','-qm','add new file',cwd=repo)
            self.assertEqual(run('git','rev-parse','HEAD^{tree}',cwd=repo).stdout.strip(),original_tree)
            p=guard('verify'); self.assertEqual(p.returncode,0,p.stderr)
            other=base/'other'; run('git','clone','-q','-b','topic',str(remote),str(other))
            for k,v in [('user.name','Other'),('user.email','other@example.com')]:
                run('git','config',k,v,cwd=other)
            (other/'c').write_text('concurrent'); run('git','add','c',cwd=other); run('git','commit','-qm','concurrent',cwd=other)
            run('git','push','-q','origin','HEAD:topic',cwd=other)
            state=json.loads(run(sys.executable,str(LOOP),'start','--kind','split').stdout)['state']
            state=json.loads(run(sys.executable,str(LOOP),'report','--state',state,'--updated','no').stdout)['state']
            p=guard('push','--allow-force-push','--review-state',state); self.assertNotEqual(p.returncode,0)
            self.assertIn('remote SHA changed',p.stderr)

    def test_fork_repository_identity_and_remote_urls(self):
        pr={'number': 7, 'url': 'https://github.com/base/repo/pull/7',
            'headRepository': {'nameWithOwner': 'contributor/repo'},
            'isCrossRepository': True}
        self.assertEqual(guard_module.pr_identity(pr),
                         ('github.com', 'base/repo', 'contributor/repo'))
        self.assertEqual(guard_module.remote_identity('git@github.com:contributor/repo.git'),
                         ('github.com', 'contributor/repo'))
        self.assertEqual(guard_module.remote_identity('https://github.com/contributor/repo.git'),
                         ('github.com', 'contributor/repo'))
        urls={'remote get-url fork': 'git@github.com:contributor/repo.git',
              'remote get-url --push --all fork': 'https://github.com/contributor/repo.git'}
        with patch.object(guard_module, 'git', side_effect=lambda *args: urls[' '.join(args)]):
            guard_module.check_remote('fork', 'github.com', 'contributor/repo', True)
            urls['remote get-url --push --all fork']='https://github.com/base/repo.git'
            with self.assertRaisesRegex(RuntimeError, 'PR head repository'):
                guard_module.check_remote('fork', 'github.com', 'contributor/repo', True)
            urls['remote get-url --push --all fork']='https://github.com/contributor/repo.git\nhttps://github.com/base/repo.git'
            with self.assertRaisesRegex(RuntimeError, 'exactly one push URL'):
                guard_module.check_remote('fork', 'github.com', 'contributor/repo', True)
        with self.assertRaisesRegex(RuntimeError, 'inconsistent'):
            guard_module.pr_identity(pr | {'isCrossRepository': False})

if __name__=='__main__': unittest.main()
