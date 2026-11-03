"""Exercise-only safety and trace checks; no native agent or service is launched."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT/'tests/fixtures/native_iteration_exercise.py'


def test_frozen_interface_records_failed_reads_and_rejects_cross_task_claim(tmp_path):
    arm = tmp_path/'arm'
    initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm), 'init', 'before'],
                                 capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stdout+initialized.stderr
    manifest = json.loads((arm/'exercise.json').read_text())
    frozen = arm/'frozen/interface.py'
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == manifest['interface_sha256']
    assert (arm/'checkout/code.py').read_text() == 'VALUE = 2\n'
    def command(*args):
        return subprocess.run([sys.executable, str(frozen), '--root', str(arm), *args],
                              capture_output=True, text=True)
    missing = command('read', 'missing.json')
    assert missing.returncode == 2 and json.loads(missing.stdout)['error'] == 'FileNotFoundError'
    blocked = command('read', str(tmp_path/'outside.json'))
    assert blocked.returncode == 2 and json.loads(blocked.stdout)['error'] == 'ValueError'
    started = command('state', 'start', '--spec', 'scope-partition', '--run', 'exercise')
    assert started.returncode == 0, started.stdout+started.stderr
    claim = json.loads(started.stdout)['claim']['id']
    mismatched = command('invoke', 'series-cleanup', claim)
    assert mismatched.returncode == 2
    invoked = command('invoke', 'scope-partition', claim)
    assert invoked.returncode == 0 and json.loads(invoked.stdout)['result']['complete'] is True
    malformed = [command('read'), command('finish'), command('state', 'start', '--spec')]
    for result in malformed:
        assert result.returncode == 2, result.stdout+result.stderr
        assert json.loads(result.stdout)['error'] == 'IndexError'
        assert not result.stderr
    trace = [json.loads(line) for line in (arm/'trace.jsonl').read_text().splitlines()]
    assert [row['ok'] for row in trace] == [False, False, True, False, True, False, False, False]
    assert trace[0]['output_bytes'] == len(missing.stdout.rstrip('\n').encode())
    for row, result in zip(trace[-3:], malformed):
        assert row['output_bytes'] == len(result.stdout.rstrip('\n').encode())


@pytest.mark.parametrize('arguments,error', [
    (('read',), 'IndexError'),
    (('invoke',), 'ValueError'),
    (('invoke', 'unknown-task', 'claim'), 'ValueError'),
    (('finish',), 'IndexError'),
    (('state', 'start', '--spec'), 'IndexError'),
    (('finish', '['*60000+']'*60000), 'RecursionError'),
], ids=['missing-read-operand', 'missing-invoke-operands', 'unknown-invoke-task',
        'missing-finish-operand', 'missing-state-spec', 'deeply-nested-assessment'])
def test_frozen_interface_summarizes_dispatched_argument_and_data_failures(tmp_path, arguments, error):
    arm = tmp_path/'arm'
    initialized = subprocess.run([sys.executable, str(INTERFACE), '--root', str(arm), 'init', 'before'],
                                 capture_output=True, text=True)
    assert initialized.returncode == 0, initialized.stdout+initialized.stderr
    frozen = arm/'frozen/interface.py'
    def command(*args):
        return subprocess.run([sys.executable, str(frozen), '--root', str(arm), *args],
                              capture_output=True, text=True)
    failed = command(*arguments)
    assert failed.returncode == 2, failed.stdout+failed.stderr
    assert json.loads(failed.stdout)['error'] == error
    assert not failed.stderr
    finished = command('finish', '{}')
    assert finished.returncode == 0, finished.stdout+finished.stderr
    summarized = command('summarize')
    assert summarized.returncode == 0, summarized.stdout+summarized.stderr
    summary = json.loads(summarized.stdout)
    assert summary['failed_operations'] == 1
    assert summary['operation_counts'][arguments[0]] == (2 if arguments[0] == 'finish' else 1)
    assert summary['task_invocations'] == []
    assert summary['assessment'] == {}
    assert summary['output_bytes'] == len(failed.stdout.rstrip('\n').encode())+len(finished.stdout.rstrip('\n').encode())
    trace = [json.loads(line) for line in (arm/'trace.jsonl').read_text().splitlines()]
    assert [row['ok'] for row in trace] == [False, True, True]
    assert trace[0]['args'] == list(arguments[1:])
    assert trace[0]['output_bytes'] == len(failed.stdout.rstrip('\n').encode())
