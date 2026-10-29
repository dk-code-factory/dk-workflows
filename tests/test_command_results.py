import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared.command_results import ResultFile, classification

SCRIPT = ROOT / 'scripts/dk.py'


@pytest.mark.parametrize('command,data,code,outcome,error', [
    ('ci-wait', {'result': 'failure'}, 1, 'blocked', 'ci_failure'),
    ('ci-wait', {'result': 'timeout'}, 1, 'blocked', 'ci_timeout'),
    ('ci-wait', {'result': 'error'}, 2, 'error', 'helper_error'),
    ('completion', {'gate': 'head_changed'}, 1, 'blocked', 'completion_head_changed'),
    ('replies', {'action': 'reconcile'}, 1, 'blocked', 'reply_reconcile'),
    ('decisions', {'data': {'readiness': 'needs_decision'}}, 0, 'blocked', 'needs_decision'),
    ('reviewers', {'complete': False}, 0, 'blocked', 'review_incomplete'),
    ('update', {'action': 'stop'}, 0, 'error', 'runtime_unavailable'),
    ('ci-wait', {'result': 'failure'}, 2, 'error', 'helper_failed'),
    ('replies', None, 1, 'error', 'helper_failed'),
    ('run-state', {'status': 'failed'}, 0, 'success', None),
    ('run-state', {'status': 'blocked', 'reason': 'New review obligation'}, 0, 'blocked', 'review_reconciliation_blocked'),
])
def test_only_known_contracts_are_workflow_blocks(command, data, code, outcome, error):
    assert classification(command, code, data)[:2] == (outcome, error)


def call(tmp_path, *arguments):
    path = tmp_path / 'results' / 'result.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--result-file', str(path),
        '--cwd', str(tmp_path), '--host', 'codex', *arguments], text=True, capture_output=True)
    return result, json.loads(result.stdout), json.loads(path.read_text())


def test_real_invalid_publication_retains_diagnostic_and_does_not_mask_exit(tmp_path):
    result, receipt, artifact = call(tmp_path, '--pr', 'https://github.com/o/r/pull/42',
        'edit-pr', '--review-state', 'invalid', '--title-file', 'missing', '--body-file', 'missing')
    assert result.returncode == receipt['exit_code'] == 1
    assert receipt['outcome'] == 'error' and receipt['retryable'] is False
    assert 'invalid review state token' in Path(artifact['stderr']).read_text()
    assert len(result.stdout) < 1000
    assert (Path(receipt['artifact']).stat().st_mode & 0o777) == 0o600


def test_dispatch_errors_and_commands_have_one_typed_result(tmp_path):
    result, receipt, artifact = call(tmp_path, 'unknown-helper')
    assert result.returncode == 2 and receipt['error_code'] == 'invocation_error'
    assert 'invalid choice' in artifact['message']
    other = tmp_path / 'other'
    other.mkdir()
    result, receipt, artifact = call(other, 'commands')
    assert result.returncode == 0 and receipt['outcome'] == 'success'
    assert artifact['data']['schema_version'] == 1


def test_large_output_and_progress_stay_complete_in_files(tmp_path):
    artifact = ResultFile(tmp_path / 'result.json', tmp_path, ROOT)
    text = 'x' * 100000
    receipt, code = artifact.run('fixture', [sys.executable, '-c',
        'import json,sys;print(json.dumps({"text":"x"*100000}));print("progress",file=sys.stderr)'],
        tmp_path, None)
    saved = json.loads(Path(receipt['artifact']).read_text())
    assert code == 0 and saved['data']['text'] == text
    assert Path(saved['stderr']).read_text() == 'progress\n'
    assert len(json.dumps(receipt)) < 1000


def test_artifact_reservation_precedes_mutation_and_never_clobbers(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('result_cli', SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    helper = tmp_path / 'helper.py'
    marker = tmp_path / 'mutated'
    helper.write_text(f'from pathlib import Path;Path({str(marker)!r}).touch()')
    monkeypatch.setattr(cli, 'ROOT', tmp_path / 'plugin')
    monkeypatch.setattr(cli, 'ROUTES', {'fixture': ('../helper.py', None, None, False)})
    path = tmp_path / 'result.json'
    path.write_text('existing')
    monkeypatch.setattr(sys, 'argv', ['dk.py', '--host', 'codex', '--result-file', str(path), 'fixture'])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out)['error_code'] == 'artifact_storage'
    assert path.read_text() == 'existing' and not marker.exists()


def test_repository_and_plugin_artifacts_are_rejected(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path / 'repo')], check=True)
    for path in (tmp_path / 'repo' / 'result.json', ROOT / 'result.json'):
        with pytest.raises(ValueError, match='outside'):
            ResultFile(path, tmp_path / 'repo', ROOT)
        assert not path.exists()


@pytest.mark.parametrize('fallback', ['not_needed', 'unsafe', 'unavailable'])
def test_routed_decisions_timeout_retains_exchange_action(tmp_path, fallback):
    checkout = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    helper = tmp_path / 'timeout.py'
    helper.write_text(f'''
import os, subprocess, sys
from unittest.mock import Mock
sys.path.insert(0, {str(ROOT / 'runtime/dk-pr-decisions/scripts')!r})
sys.path.insert(0, {str(ROOT / 'runtime')!r})
from shared import executors, model_diagnostics
from shared.diagnostics import task
import decisions
os.environ['XDG_STATE_HOME'] = {str(tmp_path / 'state')!r}
model_diagnostics.tempfile.tempdir = {str(checkout if fallback == 'unsafe' else tmp_path)!r}
save = model_diagnostics.write
def write(path, value):
    if value.get('status') == 'provider_failed' and {fallback!r} != 'not_needed':
        if path.is_relative_to({str(tmp_path / 'state')!r}):
            raise OSError('journal disk full')
        raise OSError('fallback disk full')
    return save(path, value)
model_diagnostics.write = write
failure = subprocess.TimeoutExpired(['provider'], 5, output='private timeout output')
failure.action = task('Repair the selected provider.')
failure.action['diagnostic'] = 'Existing recovery context.'
provider = Mock(side_effect=['private rejected response', failure])
decisions.cli = lambda: executors.execute_json('Frozen evidence', {str(checkout)!r},
    executor=provider, diagnostic_scope='engineering-preparation')
sys.exit(decisions.main())
''')
    artifact = ResultFile(tmp_path / 'result.json', checkout, ROOT)
    receipt, code = artifact.run('decisions', [sys.executable, str(helper)], checkout, None)
    assert code == receipt['exit_code'] == 1
    assert receipt['outcome'] == 'error' and receipt['error_code'] == 'helper_error'
    saved = json.loads(Path(receipt['artifact']).read_text())
    assert saved['data']['error']['code'] == 'TimeoutExpired'
    assert receipt['action'] == saved['data']['error']['action']
    assert receipt['action']['task'] == 'Repair the selected provider.'
    assert receipt['message'] == "Command '['provider']' timed out after 5 seconds"
    evidence, = map(Path, receipt['action']['artifacts'])
    exchange = json.loads(evidence.read_text())
    assert exchange['status'] == ('provider_failed' if fallback == 'not_needed' else 'running')
    assert [attempt['attempt'] for attempt in exchange['attempts']] == [0, 1]
    assert exchange['attempts'][0]['response'] == 'private rejected response'
    if fallback == 'not_needed':
        assert exchange['attempts'][1]['provider_error']['stdout'] == 'private timeout output'
    else:
        detail = receipt['action']['diagnostic']
        assert detail.startswith('Existing recovery context.')
        assert 'journal disk full' in detail and 'Fallback persistence failed' in detail
        assert ('outside the plugin and repository' if fallback == 'unsafe' else 'fallback disk full') in detail
        assert 'provider_error' not in exchange['attempts'][1]
        assert list(checkout.iterdir()) == [checkout / '.git']
    assert 'private timeout output' not in json.dumps(receipt)
    assert 'private rejected response' not in json.dumps(receipt)
    assert Path(saved['stderr']).read_text() == ''
