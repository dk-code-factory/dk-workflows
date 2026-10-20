import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/dk.py'


def test_large_state_is_retained_and_inspected_without_tool_output_parsing(tmp_path):
    data = tmp_path / 'data'
    state = tmp_path / 'state'
    record = data / 'dk-pr-iter/github.com/o/r/pr-42.json'
    record.parent.mkdir(parents=True)
    original = {'pr': 42, 'events': [{'detail': 'detail' * 1000, 'key': str(i)} for i in range(1000)]}
    record.write_text(json.dumps(original))
    env = dict(os.environ, XDG_DATA_HOME=str(data), XDG_STATE_HOME=str(state))
    command = [sys.executable, str(CLI), '--host', 'codex', '--cwd', str(tmp_path),
               '--pr', 'https://github.com/o/r/pull/42', 'run-state', 'show']
    compact = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    receipt = json.loads(compact.stdout)
    assert len(compact.stdout) < 1000 and receipt['outcome'] == 'success'
    artifact = Path(receipt['artifact'])
    assert artifact.is_relative_to(state)
    assert json.loads(artifact.read_text())['data'] == original
    page = subprocess.run([sys.executable, str(CLI), 'result', '--artifact', str(artifact),
        '--section', '/events', '--since', '998', '--limit', '1'], env=env,
        text=True, capture_output=True, check=True)
    selected = json.loads(page.stdout)
    assert selected['count'] == 1000 and selected['next'] == 999
    assert selected['items'] == [{'key': '998', 'value': {'type': 'object', 'count': 2}}]
    native = subprocess.run([*command[:2], '--full', *command[2:]], env=env,
                            text=True, capture_output=True, check=True)
    assert len(native.stdout) > 65536 and json.loads(native.stdout) == original
    assert json.loads(record.read_text()) == original


def test_mutation_receipt_keeps_complete_return_value_and_native_help(tmp_path):
    env = dict(os.environ, XDG_DATA_HOME=str(tmp_path / 'data'), XDG_STATE_HOME=str(tmp_path / 'state'))
    command = [sys.executable, str(CLI), '--host', 'codex', '--cwd', str(tmp_path),
               '--pr', 'https://github.com/o/r/pull/42', 'run-state']
    for i in range(3):
        result = subprocess.run([*command, 'event', '--key', str(i), '--kind', 'fixture',
            '--head', 'a' * 40, '--detail', 'x' * 70000], env=env, text=True,
            capture_output=True, check=True)
        receipt = json.loads(result.stdout)
        assert len(result.stdout) < 1000
        saved = json.loads(Path(receipt['artifact']).read_text())
        assert saved['data']['key'] == str(i) and saved['data']['detail'] == 'x' * 70000
    help_result = subprocess.run([*command, '--help'], env=env, text=True, capture_output=True, check=True)
    assert 'usage:' in help_result.stdout


def test_invalid_private_store_prevents_mutation(tmp_path):
    env = dict(os.environ, XDG_DATA_HOME=str(tmp_path / 'data'), XDG_STATE_HOME='relative')
    result = subprocess.run([sys.executable, str(CLI), '--host', 'codex', '--cwd', str(tmp_path),
        '--pr', 'https://github.com/o/r/pull/42', 'run-state', 'start-run'],
        env=env, text=True, capture_output=True)
    assert result.returncode == 2 and json.loads(result.stdout)['error_code'] == 'artifact_storage'
    assert not (tmp_path / 'data').exists()
