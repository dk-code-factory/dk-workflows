import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/dk.py'
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import invocations


def test_begin_routed_invocation_retains_context_and_idempotent_end(tmp_path):
    config = tmp_path / 'config/dk-code-factory/updates.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"mode":"off"}')
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / 'state'), XDG_CONFIG_HOME=str(tmp_path / 'config'))
    for key in ('DK_UPDATE_PARENT', 'DK_UPDATE_MANAGED_RECEIPT', 'DK_MANAGED_INVOCATION', 'DK_CLI_REFERENCE'):
        env.pop(key, None)
    begin = subprocess.run([sys.executable, str(CLI), '--host', 'codex', '--cwd', str(tmp_path),
                            'begin', '--skill', 'dk-pr-review'], env=env, text=True, capture_output=True, check=True)
    value = json.loads(begin.stdout)
    assert Path(value['invocation']).is_file() and Path(value['reference']).is_file()
    assert Path(value['runtime_root']) != ROOT
    args = [sys.executable, str(CLI), '--invocation', value['invocation'], '--cwd', str(tmp_path)]
    result = subprocess.run([*args, '--pr', 'https://github.com/o/r/pull/42', '--dry-run', 'run-state', 'show'],
                            env=env, text=True, capture_output=True, check=True)
    routed = json.loads(result.stdout)
    assert routed['environment']['DK_HOST'] == 'codex'
    assert routed['environment']['DK_UPDATE_PARENT'] == value['token']
    assert routed['argv'][1].startswith(value['runtime_root'])
    for _ in range(2):
        done = subprocess.run([*args, 'end'], env=env, text=True, capture_output=True, check=True)
        assert json.loads(done.stdout)['token'] == value['token']


def test_managed_begin_reuses_receipt_without_storage_or_lifecycle_calls(tmp_path, monkeypatch):
    value = {'schema_version': 1, 'kind': 'dk-invocation', 'host': 'codex', 'skill': 'dk-pr-review',
        'runtime_root': str(ROOT), 'token': 'token', 'reference': 'reference', 'runtime_digest': 'digest',
        'managed': True, 'invocation': str(tmp_path / 'invocation.json')}
    Path(value['invocation']).write_text(json.dumps(value))
    monkeypatch.setattr(invocations, 'verify', lambda root: 'digest')
    monkeypatch.setenv('DK_MANAGED_INVOCATION', value['invocation'])
    monkeypatch.setattr(invocations.workflow_storage, 'allocate', lambda **kw: pytest.fail('duplicate entry'))
    assert invocations.begin(ROOT, {}, 'codex', 'dk-pr-review', tmp_path) == value
    assert invocations.end(value['invocation'])['status'] == 'executor_managed'


def test_begin_dry_run_and_help_do_not_create_state(tmp_path):
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / 'state'))
    for arguments in (['--host', 'codex', '--dry-run', 'begin', '--skill', 'dk-pr-review'], ['begin', '--help'], ['end', '--help']):
        result = subprocess.run([sys.executable, str(CLI), *arguments], env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'state').exists()


def test_normal_host_entry_uses_short_lifecycle_without_loading_recovery():
    for host in ('codex', 'claude'):
        for path in (ROOT / host / 'skills').glob('*/SKILL.md'):
            text = path.read_text()
            assert 'begin --skill ' + path.parent.name in text
            assert '--invocation INVOCATION_FILE end' in text
            assert 'recovery.md' not in text
            assert 'Before work, read [the lifecycle procedure]' not in text


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_executor_owns_one_entry_and_completion_with_managed_begin(tmp_path, monkeypatch, host):
    from shared import executors, runtime_bundles
    config = tmp_path / 'config/dk-code-factory/updates.json'
    config.parent.mkdir(parents=True); config.write_text('{"mode":"off"}')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for key in ('DK_BINDING_STACK', 'DK_UPDATE_PARENT', 'DK_MANAGED_INVOCATION', 'DK_UPDATE_MANAGED_RECEIPT'):
        monkeypatch.delenv(key, raising=False)
    api = runtime_bundles.updater()
    installed = api.installed
    # Retained manifests remain authoritative; avoid consulting a real host registry.
    monkeypatch.setattr(api, 'installed', lambda root, selected: installed(
        root if (root / api.BUNDLE_MARKER).exists() else runtime_bundles.retain(root), selected))
    resolved = {'root': str(ROOT), 'procedure': str(ROOT / host / 'skills/dk-pr-review/SKILL.md'),
                'executor': host, 'binding': {'skill': 'dk-workflows:dk-pr-review'}}
    observed = []
    def provider(prompt, checkout, selected, env, binding, attempt, timeout):
        receipt = invocations.load(env['DK_MANAGED_INVOCATION'])
        assert env['DK_MANAGED_INVOCATION'] in prompt and receipt['runtime_root'] in prompt
        assert 'begin/end commands reuse' in prompt
        assert 'only when the procedure or a reported diagnostic requires them' in prompt
        observed.append(receipt)
        with monkeypatch.context() as child:
            child.setenv('DK_MANAGED_INVOCATION', env['DK_MANAGED_INVOCATION'])
            same = invocations.begin(Path(receipt['runtime_root']), {}, selected,
                                     'dk-pr-review', checkout, receipt['token'])
            assert same == receipt
            assert invocations.end(receipt['invocation'])['status'] == 'executor_managed'
        return '{"findings":[]}'
    monkeypatch.setattr(executors, 'execute_process', provider)
    assert executors.execute('Review', tmp_path, resolved=resolved) == '{"findings":[]}'
    retained = api.inspect_lifecycle(ROOT, observed[0]['token'])
    assert len(retained['invocations']) == 1 and retained['invocations'][0]['complete']
