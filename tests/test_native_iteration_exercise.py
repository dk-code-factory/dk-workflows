"""Exercise-only safety and trace checks; no native agent or service is launched."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

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
