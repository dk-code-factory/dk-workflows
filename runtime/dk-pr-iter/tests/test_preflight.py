import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/preflight.py'
spec = importlib.util.spec_from_file_location('iteration_preflight', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
PR = 'https://github.com/base/repo/pull/1'


def response(value, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value), 'lookup failed' if code else '')


class PreflightTests(unittest.TestCase):
    def test_cli_cold_setup_then_configuration_and_corrupt_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / 'repo'
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'local-work.txt').write_text('Preserve local work')
            bin_dir = base / 'bin'
            bin_dir.mkdir()
            gh = bin_dir / 'gh'
            gh.write_text('#!' + sys.executable + '\nimport json,sys\n'
                          'assert sys.argv[1:3] == ["pr", "view"]\n'
                          'print(json.dumps({"url": ' + repr(PR) + ', "headRepository": '
                          '{"nameWithOwner": "fork/repo"}}))\n')
            gh.chmod(0o755)
            bindings_file=base/'bindings.json'
            bindings_file.write_text(json.dumps([{'command':[sys.executable,'{handoff}']}]))
            env = dict(os.environ, XDG_DATA_HOME=str(base / 'data'),
                       PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])

            def run(script, *args):
                return subprocess.run([sys.executable, str(script), '--cwd', str(repo),
                                       '--pr', PR, *args], env=env, text=True, capture_output=True)

            cold = run(SCRIPT)
            self.assertEqual(cold.returncode, 1, cold.stderr)
            self.assertFalse(json.loads(cold.stdout)['ready'])
            self.assertEqual(json.loads(cold.stdout)['completion']['source'], 'default')
            configured = run(mod.ROOT / 'dk-config/scripts/repo_rules.py',
                             'reviewers', 'set', '--bindings-file', str(bindings_file))
            self.assertEqual(configured.returncode, 0, configured.stderr)
            ready = run(SCRIPT)
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertTrue(json.loads(ready.stdout)['ready'])
            override = base / 'data/dk-pr-iter/completion-prompts/user.txt'
            override.parent.mkdir(parents=True)
            override.write_text('')
            invalid = run(SCRIPT)
            self.assertEqual(invalid.returncode, 1, invalid.stderr)
            self.assertEqual(json.loads(invalid.stdout)['blockers'][0]['check'], 'completion')
            self.assertEqual((repo / 'local-work.txt').read_text(), 'Preserve local work')
            self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'],
                                                    text=True), '?? local-work.txt\n')

    def check(self, replies):
        replies.insert(1, response({'reviewers': [], 'drafting': {}}))
        with patch.object(mod.shutil, 'which', return_value='/bin/tool'), \
                patch.object(mod.subprocess, 'run', side_effect=replies) as run:
            result = mod.configuration(Path('/repo'), PR)
        for call in run.call_args_list:
            self.assertIn(PR, call.args[0])
            self.assertNotIn('set', call.args[0])
        return result

    def test_missing_tools_block_without_starting_helpers(self):
        with patch.object(mod.shutil, 'which', side_effect=lambda name: None if name == 'git' else '/bin/' + name), \
                patch.object(mod.subprocess, 'run') as run:
            result = mod.configuration(Path('/repo'), PR)
        self.assertFalse(result['ready'])
        self.assertIn('git', result['blockers'][0]['message'])
        run.assert_not_called()

    def test_cold_install_requires_reviewers_and_keeps_default_completion(self):
        result = self.check([response({'reviewers': [], 'configured': False, 'source': None}),
                             response({'source': 'default', 'prompt': 'Built-in completion'})])
        self.assertFalse(result['ready'])
        self.assertEqual(result['blockers'][0]['check'], 'reviewers')
        self.assertEqual(result['completion']['source'], 'default')

    def test_valid_configuration_is_ready(self):
        result = self.check([response({'reviewers': ['review-skill'], 'configured': True, 'source': 'repo'}),
                             response({'source': 'default', 'prompt': 'Built-in completion'})])
        self.assertTrue(result['ready'])
        self.assertEqual(result['blockers'], [])

    def test_invalid_completion_blocks_before_work(self):
        result = self.check([response({'reviewers': ['review-skill'], 'configured': True}),
                             response({}, code=1)])
        self.assertFalse(result['ready'])
        self.assertIsNone(result['completion'])
        self.assertEqual(result['blockers'][0]['check'], 'completion')

    def test_invalid_json_timeout_and_lookup_failures_are_not_ready(self):
        for failure in (response({}, code=1), subprocess.CompletedProcess([], 0, '{', ''),
                        subprocess.TimeoutExpired('gh', 60), response(None)):
            with self.subTest(failure=failure):
                result = self.check([failure, response({'source': 'default', 'prompt': 'Completion'})])
                self.assertFalse(result['ready'])
                self.assertEqual(result['blockers'][0]['check'], 'reviewers')


if __name__ == '__main__':
    unittest.main()


def test_preflight_resolves_modern_cli_repository_identity(tmp_path, monkeypatch):
    """Exercise configuration, diagnostics and completion with actual helpers."""
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    gh = bin_dir / 'gh'
    gh.write_text('#!' + sys.executable + '''
import json, os, sys
fork = os.environ.get('FIXTURE_FORK') == 'yes'
head = 'fork/repo' if fork else 'base/repo'
meta = {'url': 'https://github.com/base/repo/pull/1', 'number': 1,
        'headRefOid': 'a' * 40, 'headRefName': 'topic', 'isCrossRepository': fork,
        'headRepository': {'id': 'R_head', 'name': 'repo', 'nameWithOwner': ''}}
if sys.argv[1] == 'api':
    result = {'html_url': meta['url'], 'number': 1,
        'head': {'sha': 'a' * 40, 'ref': 'topic', 'repo':
            {'full_name': head, 'name': 'repo', 'node_id': 'R_other' if os.environ.get('FIXTURE_CONFLICT') else 'R_head'}},
        'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'full_name': 'base/repo', 'name': 'repo'}}}
else:
    result = meta
print(json.dumps(result))
''')
    gh.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    bindings = tmp_path / 'bindings.json'
    bindings.write_text(json.dumps([{'command': [sys.executable, '{handoff}']}]))
    for fork in ('no', 'yes'):
        monkeypatch.setenv('FIXTURE_FORK', fork)
        configured = subprocess.run([sys.executable, str(mod.ROOT / 'dk-config/scripts/repo_rules.py'),
            '--cwd', str(repo), '--pr', PR, 'reviewers', 'set', '--bindings-file', str(bindings)],
            text=True, capture_output=True)
        assert configured.returncode == 0, configured.stderr
        assert mod.configuration(repo, PR)['ready']
    monkeypatch.setenv('FIXTURE_CONFLICT', 'yes')
    result = mod.configuration(repo, PR)
    assert not result['ready'] and len(result['blockers']) == 3
    assert all('inconsistent' in entry['message'] for entry in result['blockers'])
