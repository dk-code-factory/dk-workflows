import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/codex_rules_hook.py'
spec = importlib.util.spec_from_file_location('codex_rules_hook', SOURCE)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.fixture
def startup(tmp_path):
    root = tmp_path / 'codex/plugins/cache/dk-workflows/dk-workflows/1.2.3'
    (root / '.codex-plugin').mkdir(parents=True)
    (root / '.codex-plugin/plugin.json').write_text(json.dumps({'name': 'dk-workflows', 'version': '1.2.3'}))
    helper = root / 'runtime/dk-config/scripts/repo_rules.py'
    helper.parent.mkdir(parents=True)
    helper.write_text('''import json, os, pathlib, sys, time
pathlib.Path(os.environ['MOCK_REFRESH_MARKER']).write_text(json.dumps({'args':sys.argv[1:],'host':os.environ.get('DK_HOST')}))
time.sleep(float(os.environ.get('MOCK_REFRESH_DELAY', '0')))
if os.environ.get('MOCK_REFRESH_FAIL'):
    sys.stderr.write('refresh fixture failed')
    sys.exit(2)
print('fixture repository rules')
''')
    binary = tmp_path / 'bin/codex'
    binary.parent.mkdir()
    binary.write_text(f'#!{sys.executable}\n' + '''import os, sys, time
assert sys.argv[1:]==['plugin','list','--json']
time.sleep(float(os.environ.get('MOCK_LOOKUP_DELAY', '0')))
if os.environ.get('MOCK_LOOKUP_FAIL'):
    sys.stderr.write('lookup fixture failed')
    sys.exit(3)
print(os.environ['MOCK_REGISTRY'])
''')
    binary.chmod(0o755)
    copied_hook = tmp_path / 'stable-hooks/codex_rules_hook.py'
    copied_hook.parent.mkdir()
    shutil.copyfile(SOURCE, copied_hook)
    record = {'pluginId': 'dk-workflows@dk-workflows', 'name': 'dk-workflows',
              'marketplaceName': 'dk-workflows', 'version': '1.2.3', 'enabled': True, 'installed': True}
    marker = tmp_path / 'refresh.json'
    env = dict(os.environ, PATH=str(binary.parent) + os.pathsep + os.environ['PATH'],
               CODEX_HOME=str(tmp_path / 'codex'), MOCK_REFRESH_MARKER=str(marker))

    def invoke(records=None, payload=None, raw=None, options=(), **changes):
        current = dict(env, MOCK_REGISTRY=json.dumps({'installed': [record] if records is None else records}))
        current.update(changes)
        result = subprocess.run([sys.executable, str(copied_hook), *options, '--lookup-timeout', '0.8',
                                 '--refresh-timeout', '0.8'],
                                input=raw if raw is not None else json.dumps(payload or {
                                    'cwd': str(tmp_path / 'repo with spaces'), 'hook_event_name': 'SubagentStart'}),
                                text=True, capture_output=True, env=current, timeout=5)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return invoke, record, marker, root


@pytest.mark.parametrize('event', ['SessionStart', 'SubagentStart'])
def test_copied_hook_tolerates_slow_lookup_and_refreshes_exact_installation(startup, event):
    invoke, _, marker, _ = startup
    result = invoke(payload={'cwd': str(marker.parent / 'repo with spaces'), 'hook_event_name': event},
                    MOCK_LOOKUP_DELAY='0.15')
    assert result == {'hookSpecificOutput': {'hookEventName': event,
                                           'additionalContext': 'fixture repository rules'}}
    observed = json.loads(marker.read_text())
    assert observed['host'] == 'codex'
    assert observed['args'][-1] == 'refresh'
    assert observed['args'][0] == '--cwd'
    assert observed['args'][1].endswith('repo with spaces')


def test_default_lookup_budget_exceeds_old_ten_second_cutoff(startup, monkeypatch):
    _, record, _, _ = startup
    calls = []
    def run(command, timeout, env):
        calls.append(timeout)
        return json.dumps({'installed': [record]}) if command == hook.LOOKUP_COMMAND else 'rules'
    monkeypatch.setattr(hook, 'run', run)
    # A lookup taking 11 seconds must fit in the default budget.
    args = argparse.Namespace(plugin_id=record['pluginId'], lookup_timeout=hook.LOOKUP_TIMEOUT,
                              refresh_timeout=hook.REFRESH_TIMEOUT)
    env = dict(os.environ, CODEX_HOME=str(startup[3].parents[4]))
    assert 'hookSpecificOutput' in hook.hook({}, args, env)
    assert calls == [90, 180]
    assert calls[0] > 11


def test_custom_marketplace_cli_selects_its_versioned_installation(startup):
    invoke, record, marker, root = startup
    selected = root.parents[2] / 'custom-marketplace/dk-workflows/1.2.3'
    selected.parent.mkdir(parents=True)
    root.rename(selected)
    record.update(pluginId='dk-workflows@custom-marketplace', marketplaceName='custom-marketplace')
    assert 'hookSpecificOutput' in invoke(options=['--plugin-id', record['pluginId']])
    assert marker.is_file()


@pytest.mark.parametrize('field', ['installedPath', 'installPath'])
def test_registered_installation_path_is_supported(startup, field):
    invoke, record, marker, root = startup
    record[field] = str(root)
    assert 'hookSpecificOutput' in invoke(CODEX_HOME='/unused')
    assert marker.is_file()


@pytest.mark.parametrize('changes,phase,started', [
    ({'MOCK_LOOKUP_DELAY': '2'}, 'Plugin lookup', False),
    ({'MOCK_LOOKUP_FAIL': '1'}, 'Plugin lookup', False),
    ({'MOCK_REFRESH_DELAY': '2'}, 'Repository rule refresh', True),
    ({'MOCK_REFRESH_FAIL': '1'}, 'Repository rule refresh', True),
])
def test_phase_specific_failures_include_command_timeout_and_recovery(startup, changes, phase, started):
    invoke, _, marker, _ = startup
    result = invoke(**changes)
    message = result['systemMessage']
    assert message.startswith(phase + ' failed;')
    assert ('rule refresh started' if started else 'rule refresh did not start') in message
    assert 'Command:' in message and 'Timeout: 0.8s' in message and 'Recovery:' in message
    assert ('repo_rules.py' if started else 'codex plugin list --json') in message
    assert marker.exists() is started
    assert 'hookSpecificOutput' not in result


def test_refresh_launch_failure_reports_no_refresh_and_refresh_recovery(startup, monkeypatch):
    _, record, marker, root = startup
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command == hook.LOOKUP_COMMAND:
            return subprocess.CompletedProcess(command, 0, json.dumps({'installed': [record]}), '')
        raise OSError('fixture could not launch Python')

    monkeypatch.setattr(hook.subprocess, 'run', run)
    args = argparse.Namespace(plugin_id=record['pluginId'], lookup_timeout=0.8, refresh_timeout=0.8)
    result = hook.hook({}, args, dict(os.environ, CODEX_HOME=str(root.parents[4])))
    message = result['systemMessage']
    assert message.startswith('Repository rule refresh failed; rule refresh did not start.')
    assert 'fixture could not launch Python' in message
    assert f'Command: {sys.executable}' in message and 'repo_rules.py' in message
    assert 'Timeout: 0.8s' in message
    assert 'Recovery: Run $dk-workflows:dk-config refresh' in message
    assert 'codex plugin list' not in message
    assert len(calls) == 2
    assert not marker.exists()
    assert 'hookSpecificOutput' not in result


@pytest.mark.parametrize('problem', ['missing manifest', 'malformed manifest', 'missing helper', 'escaping helper'])
def test_invalid_local_installation_reports_validation_without_a_failing_command(startup, problem):
    invoke, _, marker, root = startup
    manifest = root / '.codex-plugin/plugin.json'
    helper = root / 'runtime/dk-config/scripts/repo_rules.py'
    if problem == 'missing manifest':
        manifest.unlink()
    elif problem == 'malformed manifest':
        manifest.write_text('not json')
    elif problem == 'missing helper':
        helper.unlink()
    else:
        outside = marker.parent / 'outside.py'
        helper.rename(outside)
        helper.symlink_to(outside)
    result = invoke()
    message = result['systemMessage']
    assert message.startswith('Plugin validation failed; rule refresh did not start.')
    assert 'Command:' not in message and 'Timeout:' not in message
    assert 'Recovery: Run codex plugin list --json' in message
    assert not marker.exists()
    assert 'hookSpecificOutput' not in result


@pytest.mark.parametrize('records', [[], [{}, {}]])
def test_missing_identity_never_starts_refresh(startup, records):
    invoke, _, marker, _ = startup
    assert 'Plugin validation failed' in invoke(records=records)['systemMessage']
    assert not marker.exists()


@pytest.mark.parametrize('change', [
    {'enabled': False}, {'installed': False}, {'version': '9.9.9'}, {'name': 'other-plugin'},
    {'marketplaceName': 'other-marketplace'}, {'version': '../1.2.3'},
])
def test_invalid_registry_identity_never_starts_refresh(startup, change):
    invoke, record, marker, root = startup
    record.update(change)
    record['installedPath'] = str(root)
    assert 'Plugin validation failed' in invoke()['systemMessage']
    assert not marker.exists()


def test_ambiguous_identity_never_starts_refresh(startup):
    invoke, record, marker, _ = startup
    assert 'found 2' in invoke(records=[record, record])['systemMessage']
    assert not marker.exists()


@pytest.mark.parametrize('registry', ['not json', '[]', '{"installed":{}}'])
def test_malformed_registry_never_starts_refresh(startup, registry):
    invoke, _, marker, _ = startup
    message = invoke(MOCK_REGISTRY=registry)['systemMessage']
    assert 'rule refresh did not start' in message
    assert 'codex plugin list --json' in message
    assert not marker.exists()


@pytest.mark.parametrize('payload', ['not json', '[]', '{"cwd":2}'])
def test_malformed_hook_input_is_reported_without_traceback(startup, payload):
    invoke, _, marker, _ = startup
    assert 'Hook input failed; rule refresh did not start' in invoke(raw=payload)['systemMessage']
    assert not marker.exists()


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        hook.positive_seconds(value)
