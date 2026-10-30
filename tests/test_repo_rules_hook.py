import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

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


@pytest.mark.parametrize('leader_exits', [False, True])
def test_refresh_timeout_stops_real_summarizer_and_its_child(tmp_path, monkeypatch, leader_exits):
    repository = SOURCE.parents[1]
    work = tmp_path / 'repository'
    work.mkdir()
    (work / 'README.md').write_text('Example repository rules')
    trace = tmp_path / 'processes.json'
    heartbeat = tmp_path / 'heartbeat'
    child = tmp_path / 'child.py'
    child.write_text('''import pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 10
count = 0
while time.monotonic() < deadline:
    marker.write_text(str(count))
    count += 1
    time.sleep(0.02)
''')
    summarizer = tmp_path / 'summarizer'
    summarizer.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, os.environ['CHILD_SCRIPT'], os.environ['HEARTBEAT']])
pathlib.Path(os.environ['TRACE']).write_text(json.dumps([os.getpid(), child.pid]))
child.wait()
print('NONE')
''')
    summarizer.chmod(0o700)
    env = dict(os.environ, REPO_RULES_SUMMARIZER=str(summarizer), TRACE=str(trace),
               CHILD_SCRIPT=str(child), HEARTBEAT=str(heartbeat))
    command = [sys.executable, str(repository / 'runtime/dk-config/scripts/repo_rules.py'),
               '--cwd', str(work), 'refresh']
    if leader_exits:
        command = [sys.executable, '-c', 'import subprocess, sys; subprocess.Popen(sys.argv[1:])',
                   *command]
    popen = subprocess.Popen

    def start_when_ready(*args, **kwargs):
        process = popen(*args, **kwargs)
        # Measure cleanup after workers start, independently of interpreter/Git startup.
        deadline = time.monotonic() + 15
        while not heartbeat.is_file():
            if (not leader_exits and process.poll() is not None) or time.monotonic() >= deadline:
                process.kill()
                process.wait()
                pytest.fail('refresh did not start its nested summarizer')
            time.sleep(0.01)
        return process

    monkeypatch.setattr(hook.subprocess, 'Popen', start_when_ready)
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            hook.run(command, 2, env)
        assert time.monotonic() - started < 20
        assert trace.is_file(), 'the real refresh must launch its summarizer before timing out'
        assert heartbeat.is_file(), 'the summarizer must launch its nested child'
        last_heartbeat = heartbeat.read_bytes()
        time.sleep(0.2)
        assert heartbeat.read_bytes() == last_heartbeat, 'a descendant kept running after timeout'
    finally:
        # Also clean up when this regression is run against the unfixed implementation.
        if trace.is_file():
            for pid in reversed(json.loads(trace.read_text())):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_sigterm_cancellation_stops_refresh_and_worker(startup):
    _, record, marker, root = startup
    heartbeat = marker.parent / 'heartbeat'
    trace = marker.parent / 'worker-pids.json'
    worker = marker.parent / 'worker.py'
    worker.write_text('''import pathlib, sys, time
marker = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 10
count = 0
while time.monotonic() < deadline:
    marker.write_text(str(count))
    count += 1
    time.sleep(0.02)
''')
    helper = root / 'runtime/dk-config/scripts/repo_rules.py'
    helper.write_text('''import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, os.environ['WORKER'], os.environ['HEARTBEAT']])
pathlib.Path(os.environ['TRACE']).write_text(json.dumps([os.getpid(), child.pid]))
child.wait()
''')
    env = dict(os.environ, PATH=str(marker.parent / 'bin') + os.pathsep + os.environ['PATH'],
               CODEX_HOME=str(root.parents[4]), MOCK_REGISTRY=json.dumps({'installed': [record]}),
               WORKER=str(worker), HEARTBEAT=str(heartbeat), TRACE=str(trace))
    event = marker.parent / 'event.json'
    event.write_text(json.dumps({'cwd': str(marker.parent)}))
    command = [sys.executable, str(marker.parent / 'stable-hooks/codex_rules_hook.py'),
               '--lookup-timeout', '5', '--refresh-timeout', '20']
    with event.open() as source:
        process = subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        deadline = time.monotonic() + 15
        while not heartbeat.is_file():
            assert process.poll() is None, 'hook exited before its refresh worker started'
            assert time.monotonic() < deadline, 'refresh worker did not start'
            time.sleep(0.01)
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode != 0, 'cancellation must not become successful completion'
        assert 'hookSpecificOutput' not in stdout and 'Traceback' not in stderr
        last_heartbeat = heartbeat.read_bytes()
        time.sleep(0.2)
        assert heartbeat.read_bytes() == last_heartbeat, 'worker survived hook cancellation'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if trace.is_file():
            for pid in reversed(json.loads(trace.read_text())):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_refresh_launch_failure_reports_no_refresh_and_refresh_recovery(startup, monkeypatch):
    _, record, marker, root = startup
    calls = []
    original_run = hook.run

    def run(command, timeout, env):
        calls.append(command)
        if command == hook.LOOKUP_COMMAND:
            return json.dumps({'installed': [record]})
        return original_run(command, timeout, env)

    def fail_to_launch(*args, **kwargs):
        raise OSError('fixture could not launch Python')

    monkeypatch.setattr(hook, 'run', run)
    monkeypatch.setattr(hook.subprocess, 'Popen', fail_to_launch)
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
