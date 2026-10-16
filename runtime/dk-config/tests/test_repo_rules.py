import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1]/'scripts/repo_rules.py'
HOOK = CLI.parents[3]/'scripts/repo_rules_hook.py'

def cmd(*args, env=None, input=None):
    return subprocess.run(args, text=True, input=input, capture_output=True, env=env)

class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.repo=self.base/'repo'; self.repo.mkdir()
        cmd('git','init','-q',str(self.repo))
        self.summarizer=self.base/'summary.py'
        self.summarizer.write_text('#!/usr/bin/env python3\nimport sys\nprint("- Setup: " + sys.stdin.read().strip()[:60])\n')
        self.summarizer.chmod(0o755)
        self.env=dict(os.environ,XDG_DATA_HOME=str(self.base/'data'),REPO_RULES_SUMMARIZER=str(self.summarizer), DK_HOST='claude', CLAUDE_CONFIG_DIR=str(self.base/'claude'))
        plugin = self.base/'plugin'
        (plugin/'.claude-plugin').mkdir(parents=True)
        (plugin/'skills/dk-pr-read-comments').mkdir(parents=True)
        (plugin/'skills/dk-pr-read-comments/SKILL.md').write_text('Read-only fixture')
        (plugin/'.claude-plugin/plugin.json').write_text(json.dumps({'name':'dk-workflows','version':'1.0.0'}))
        native = self.base/'native'
        (native/'.claude-plugin').mkdir(parents=True)
        (native/'skills/code-review-matpocock').mkdir(parents=True)
        (native/'skills/code-review-matpocock/SKILL.md').write_text('Review fixture')
        (native/'.claude-plugin/plugin.json').write_text(json.dumps({'name':'code-review-matpocock','version':'1.0.0'}))
        registry = self.base/'claude/plugins/installed_plugins.json'
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({'plugins':{'dk-workflows@fixture':[{'installPath':str(plugin),'version':'1.0.0'}], 'code-review-matpocock@fixture':[{'installPath':str(native),'version':'1.0.0'}]}}))
        binpath=self.base/'host-bin';binpath.mkdir()
        fake_host=binpath/'claude';fake_host.write_text('#!/usr/bin/env python3\nprint("fixture host")\n');fake_host.chmod(0o755)
        self.env['PATH']=str(binpath)+os.pathsep+self.env['PATH']
    def runcli(self,*args,repo=None):
        return cmd(sys.executable,str(CLI),'--cwd',str(repo or self.repo),*args,env=self.env)
    def test_reviewer_contract_and_required_configuration(self):
        default = json.loads(self.runcli('reviewers', 'list').stdout)
        self.assertEqual(default, {'reviewers': [], 'configured': False, 'source': None})
        self.assertNotEqual(self.runcli('reviewers', 'set', 'dk-pr-review').returncode, 0)
        self.assertNotEqual(self.runcli('reviewers', 'set', 'missing-skill').returncode, 0)
        self.assertEqual(self.runcli('reviewers', 'set', 'dk-pr-read-comments', 'code-review-claude-matpocock', '--scope', 'user').returncode, 0)
        self.assertEqual(json.loads(self.runcli('reviewers', 'list').stdout),
                         {'reviewers': [{'skill':'dk-workflows:dk-pr-read-comments','host':'current'}, {'skill':'code-review-matpocock:code-review-matpocock','host':'claude'}], 'configured': True, 'source': 'user'})
        self.assertEqual(self.runcli('reviewers', 'set', 'dk-pr-read-comments').returncode, 0)
        self.assertEqual(json.loads(self.runcli('reviewers', 'list').stdout)['source'], 'repo')
        self.assertEqual(self.runcli('reviewers', 'reset').returncode, 0)
        self.assertEqual(json.loads(self.runcli('reviewers', 'list').stdout)['source'], 'user')
        self.assertEqual(self.runcli('reviewers', 'reset', '--scope', 'user').returncode, 0)
        self.assertEqual(json.loads(self.runcli('reviewers', 'list').stdout), default)

    def test_malformed_repository_reviewers_do_not_fall_back_to_user(self):
        self.assertEqual(self.runcli('reviewers', 'set', 'dk-pr-read-comments', '--scope', 'user').returncode, 0)
        _, key, _ = self._identity(self.repo)
        folder = Path(self.env['XDG_DATA_HOME']) / 'codex-repo-rules' / hashlib.sha256(key.encode()).hexdigest()[:24]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'reviewers.json'
        for value in ({}, [], None, {'reviewers': None}, {'reviewers': 'dk-pr-read-comments'},
                      {'reviewers': [{}]}):
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                result = self.runcli('reviewers', 'list')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(json.loads(path.read_text()), value)
        self.assertEqual(self.runcli('reviewers', 'reset').returncode, 0)
        self.assertEqual(json.loads(self.runcli('reviewers', 'list').stdout)['source'], 'user')

    def test_fork_upstream_user_precedence_and_pr_context(self):
        upstream=self.base/'upstream'; upstream.mkdir()
        cmd('git','init','-q',str(upstream))
        cmd('git','-C',str(self.repo),'remote','add','origin','git@github.com:fork/repo.git')
        cmd('git','-C',str(self.repo),'remote','add','upstream','git@github.com:base/repo.git')
        cmd('git','-C',str(upstream),'remote','add','origin','git@github.com:base/repo.git')
        self.assertEqual(self.runcli('add','User rule','--scope','user').returncode,0)
        self.assertEqual(self.runcli('reviewers','set','dk-pr-read-comments','--scope','user').returncode,0)
        self.assertEqual(json.loads(self.runcli('list').stdout)['rules'][0]['text'],'User rule')
        self.assertEqual(json.loads(self.runcli('reviewers','list').stdout)['source'],'user')
        self.assertEqual(self.runcli('add','Upstream rule',repo=upstream).returncode,0)
        self.assertEqual(self.runcli('reviewers','set','code-review-claude-matpocock',repo=upstream).returncode,0)
        self.assertEqual(json.loads(self.runcli('list').stdout)['rules'][0]['text'],'Upstream rule')
        self.assertEqual(json.loads(self.runcli('reviewers','list').stdout)['source'],'upstream')
        self.assertEqual(self.runcli('add','Fork rule').returncode,0)
        self.assertEqual(self.runcli('reviewers','set','dk-pr-read-comments').returncode,0)
        self.assertEqual(json.loads(self.runcli('list').stdout)['rules'][0]['text'],'Fork rule')
        self.assertEqual(json.loads(self.runcli('reviewers','list').stdout)['source'],'repo')
        binpath=self.base/'bin'; binpath.mkdir()
        fake=binpath/'gh'
        fake.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps({"url":"https://github.com/base/repo/pull/7","headRepository":{"nameWithOwner":"fork/repo"}}))\n')
        fake.chmod(0o755)
        self.env['PATH']=str(binpath)+os.pathsep+self.env['PATH']
        pr='https://github.com/base/repo/pull/7'
        self.assertEqual(json.loads(self.runcli('--pr',pr,'list',repo=upstream).stdout)['rules'][0]['text'],'Fork rule')
        self.assertEqual(json.loads(self.runcli('--pr',pr,'reviewers','list',repo=upstream).stdout)['source'],'repo')
        base_index=self.runcli('refresh',repo=upstream).stdout
        pr_index=self.runcli('--pr',pr,'refresh',repo=upstream).stdout
        self.assertIn('Upstream rule',base_index)
        self.assertIn('Fork rule',pr_index)
        _,key,_=self._identity(upstream)
        folder=Path(self.env['XDG_DATA_HOME'])/'codex-repo-rules'/hashlib.sha256(key.encode()).hexdigest()[:24]
        self.assertEqual((folder/'index.md').read_text().strip(),base_index.strip())
    def test_identity_crud_cache_and_conflict(self):
        (self.repo/'AGENTS.md').write_text('# Setup\nUse pytest\n')
        p=self.runcli('add','Prefer unittest'); self.assertEqual(p.returncode,0,p.stderr)
        rule=json.loads(p.stdout)['rules'][0]['id']
        self.assertIn('Prefer unittest',self.runcli('get',rule).stdout)
        self.assertIn('AGENTS.md',self.runcli('refresh').stdout)
        self.assertIn('Use pytest',self.runcli('index').stdout)
        p=self.runcli('resolve','Use unittest for this repository; local preference wins over AGENTS.md','--private',rule,'--source','AGENTS.md','--source-rule','Use pytest')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertNotIn('RECONSIDER',self.runcli('refresh').stdout)
        (self.repo/'README.md').write_text('Use make')
        self.assertNotIn('RECONSIDER',self.runcli('refresh').stdout)
        (self.repo/'AGENTS.md').write_text('# Setup\nUse pytest\nDocument public functions\n')
        self.assertNotIn('RECONSIDER',self.runcli('refresh').stdout)
        (self.repo/'AGENTS.md').write_text('# Setup\nUse tox\n')
        self.assertIn('RECONSIDER',self.runcli('refresh').stdout)
        self.assertIn('Use tox',self.runcli('refresh').stdout)
        docs=self.repo/'docs'; docs.mkdir()
        (docs/'CODING.md').write_text('Use type hints')
        self.assertIn('docs/CODING.md',self.runcli('refresh').stdout)
        (self.repo/'AGENTS.md').unlink()
        self.assertNotIn('AGENTS.md:',self.runcli('refresh').stdout)
        self.assertEqual(self.runcli('update',rule,'Prefer pytest').returncode,0)
        self.assertEqual(self.runcli('delete',rule).returncode,0)
        self.assertEqual(json.loads(self.runcli('list').stdout)['rules'],[])
        clone=self.base/'clone'; clone.mkdir(); cmd('git','init','-q',str(clone))
        for path in (self.repo,clone):
            cmd('git','-C',str(path),'remote','add','origin','git@github.com:Owner/Repo.git')
        _,key1,folder1=self._identity(self.repo)
        _,key2,folder2=self._identity(clone)
        self.assertEqual((key1,folder1),(key2,folder2))
    def test_bounded_index_references_details(self):
        for i in range(75):
            folder=self.repo/f'd{i:02}'; folder.mkdir()
            (folder/'CODING.md').write_text('Follow the documented review process for this area '+str(i))
        p=self.runcli('refresh')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertLessEqual(len(p.stdout),6501)
        self.assertIn('Detailed summary:',p.stdout)
        self.assertIn('Index truncated:',p.stdout)
    def test_plain_english_rule_crud_and_conflict(self):
        general=json.loads(self.runcli('add','Prefer small functions').stdout)['rules'][0]['id']
        p=self.runcli('add','When reviewing pull requests, flag changes that drop request validation')
        self.assertEqual(p.returncode,0,p.stderr)
        review=json.loads(p.stdout)['rules'][1]['id']
        listed=json.loads(self.runcli('list').stdout)['rules']
        self.assertEqual([r['id'] for r in listed],[general,review])
        self.assertTrue(all('scope' not in r for r in listed))
        self.assertEqual(self.runcli('get',review).returncode,0)
        index=self.runcli('refresh').stdout
        self.assertIn('## Private rules\n- ['+general+']',index)
        self.assertIn('['+review+'] When reviewing pull requests',index)
        (self.repo/'AGENTS.md').write_text('## Code Review Rules\n- Ignore removed validation')
        self.runcli('refresh')
        self.assertNotEqual(self.runcli('resolve','Bad answer','--private',review,'--source','AGENTS.md','--source-rule','missing text').returncode,0)
        self.assertEqual(self.runcli('resolve','First answer','--private',review,'--source','AGENTS.md','--source-rule','Ignore removed validation').returncode,0)
        self.assertEqual(self.runcli('resolve','Revised answer','--private',review,'--source','AGENTS.md','--source-rule','Ignore removed validation').returncode,0)
        conflicts=json.loads(self.runcli('list').stdout)['conflicts']
        self.assertEqual(len(conflicts),1)
        self.assertIn('Revised answer',self.runcli('refresh').stdout)
        self.assertNotIn('First answer',self.runcli('refresh').stdout)
        self.assertEqual(self.runcli('delete',conflicts[0]['id']).returncode,0)
        self.assertEqual(json.loads(self.runcli('list').stdout)['conflicts'],[])
        self.assertEqual(self.runcli('resolve','Current answer','--private',review,'--source','AGENTS.md','--source-rule','Ignore removed validation').returncode,0)
        self.assertEqual(self.runcli('update',review,'When reviewing pull requests, flag removed validation').returncode,0)
        self.assertIn('flag removed validation',self.runcli('refresh').stdout)
        self.assertEqual(self.runcli('delete',review).returncode,0)
        self.assertEqual(len(json.loads(self.runcli('list').stdout)['rules']),1)
        self.assertEqual(json.loads(self.runcli('list').stdout)['conflicts'],[])
    def test_plain_english_rules_cover_every_task(self):
        rules = [
            'When changing code, use type hints for public APIs',
            'For commit messages, start the subject with a verb',
            'For pull request titles, start with a verb',
            'For pull request descriptions, include migration notes when needed',
            'For issue titles, name the observable symptom',
            'For issue descriptions, include reproduction steps',
        ]
        ids = {}
        for text in rules:
            p = self.runcli('add', text)
            self.assertEqual(p.returncode, 0, p.stderr)
            ids[text] = json.loads(p.stdout)['rules'][-1]['id']
        index = self.runcli('refresh').stdout
        for text in rules:
            self.assertIn(f'[{ids[text]}] {text}', index)
            self.assertEqual(self.runcli('get', ids[text]).returncode, 0)
        self.assertNotEqual(self.runcli('--scope', 'pr-title', 'list').returncode, 0)
    def test_legacy_categorized_rule_keeps_its_meaning(self):
        _, key, _ = self._identity(self.repo)
        folder = Path(self.env['XDG_DATA_HOME'])/'codex-repo-rules'/hashlib.sha256(key.encode()).hexdigest()[:24]
        folder.mkdir(parents=True)
        (self.repo/'AGENTS.md').write_text('## Code Review Rules\n- Keep validation unchanged\n')
        self.assertEqual(self.runcli('refresh').returncode, 0)
        source_hash = json.loads((folder/'cache.json').read_text())['sources']['AGENTS.md']['hash']
        original = 'Flag removed validation'
        (folder/'rules.json').write_text(json.dumps({'identity': key, 'rules': [
            {'id': 'rlegacy', 'scope': 'review', 'text': original}],
            'conflicts': [{'id': 'clegacy', 'scope': 'review', 'private_id': 'rlegacy',
                           'source': 'AGENTS.md', 'source_rule': 'Keep validation unchanged',
                           'source_hash': source_hash, 'private_hash': hashlib.sha256(original.encode()).hexdigest(),
                           'text': 'The private review rule wins'}]}))
        index = self.runcli('refresh').stdout
        self.assertIn('When reviewing pull requests: Flag removed validation', index)
        self.assertNotIn('RECONSIDER', index)
        self.assertNotIn('scope', (folder/'rules.json').read_text())
        listed = json.loads(self.runcli('list').stdout)['rules']
        self.assertEqual(listed, [{'id': 'rlegacy', 'text': 'When reviewing pull requests: Flag removed validation'}])
        self.assertEqual(self.runcli('update', 'rlegacy', 'When reviewing pull requests, flag removed authentication').returncode, 0)
        self.assertNotIn('scope', (folder/'rules.json').read_text())
    def test_issue_and_pr_templates_are_guidance_sources(self):
        issue_dir = self.repo/'.github'/'ISSUE_TEMPLATE'
        issue_dir.mkdir(parents=True)
        (issue_dir/'bug.yml').write_text('name: Bug\ndescription: Include the observable symptom\n')
        pr_dir = self.repo/'.github'/'PULL_REQUEST_TEMPLATE'
        pr_dir.mkdir()
        (pr_dir/'change.md').write_text('## Impact\nDescribe user-visible behavior\n')
        index = self.runcli('refresh').stdout
        self.assertIn('.github/ISSUE_TEMPLATE/bug.yml:', index)
        self.assertIn('.github/PULL_REQUEST_TEMPLATE/change.md:', index)
    def test_list_does_not_create_private_store(self):
        _,_,folder=self._identity(self.repo)
        self.assertFalse(folder.exists())
        p=self.runcli('list')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertFalse(folder.exists())
    def _identity(self,path):
        sys.path.insert(0,str(CLI.parent)); import repo_rules
        return repo_rules.identity(path)
    def test_hook_sessions_concurrent_and_failure(self):
        (self.repo/'AGENTS.md').write_text('Use pytest')
        added = self.runcli('add', 'When changing code, keep public APIs backward compatible')
        self.assertEqual(added.returncode, 0, added.stderr)
        def call(event):
            payload=json.dumps({'cwd':str(self.repo),'hook_event_name':event})
            return cmd(sys.executable,str(HOOK),env=self.env,input=payload)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(call,['SessionStart','SubagentStart']))
        for p,event in zip(results,['SessionStart','SubagentStart']):
            self.assertEqual(p.returncode,0,p.stderr)
            output=json.loads(p.stdout)
            self.assertEqual(output['hookSpecificOutput']['hookEventName'],event)
            self.assertIn('Use pytest',output['hookSpecificOutput']['additionalContext'])
            self.assertIn('## Private rules\n- [',output['hookSpecificOutput']['additionalContext'])
            self.assertIn('When changing code, keep public APIs backward compatible',output['hookSpecificOutput']['additionalContext'])
        broken=dict(self.env,REPO_RULES_SUMMARIZER='/missing/summarizer')
        (self.repo/'AGENTS.md').write_text('Use tox')
        p=cmd(sys.executable,str(HOOK),env=broken,input=json.dumps({'cwd':str(self.repo),'hook_event_name':'SessionStart'}))
        self.assertIn('refresh failed',json.loads(p.stdout)['systemMessage'])

if __name__=='__main__': unittest.main()
