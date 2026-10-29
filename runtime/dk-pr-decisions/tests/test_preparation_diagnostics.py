"""Preparation failures remain inspectable without granting publication authority."""
import json
import importlib.util
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, ROOT, analysis, prep, response
from shared.model_diagnostics import FieldError, ValidationFailure
from shared import executors, model_diagnostics, private_state
from shared.command_results import classification
from shared.diagnostics import Diagnostic, task


@pytest.fixture(autouse=True)
def private_temp_root(tmp_path, monkeypatch):
    # Emergency evidence remains under pytest's ownership on assertion failures.
    monkeypatch.setattr(model_diagnostics.tempfile, 'tempdir', str(tmp_path))


@pytest.mark.parametrize('member', ['production', 'tests', 'reuse', 'entrypoints', 'dependencies'])
@pytest.mark.parametrize('locations', [None, [], '', 'file.py', [None], [''], [' '], [17], {}])
def test_exact_placement_field(member, locations):
    value = analysis()
    if locations is None:
        del value['placement'][member]['locations']
    else:
        value['placement'][member]['locations'] = locations
    with pytest.raises(FieldError) as caught:
        prep.validate_analysis(value, {'production/0': {}}, ['example'])
    assert caught.value.field == 'placement.' + member + '.locations'
    assert 'nonempty array of nonempty location strings' in str(caught.value)


def test_cli_retains_both_rejections_and_restart_budget_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    package = Package(tmp_path)
    package.manifest['mode'] = 'iteration'
    spec = importlib.util.spec_from_file_location('diagnostic_run_state', ROOT / 'dk-pr-iter/scripts/run_state.py')
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    history = {}
    state.start_run(history)
    history.update(cycles=[{'number': 2, 'head': package.head}], repairs=[{'cycle': 2, 'number': 1}])
    record = package.write('run-record.json', history)
    package.manifest['evidence']['run_history'] = ['run-record.json']
    package.write('manifest.json', package.manifest)
    before = record.read_bytes()
    invalid = response()
    invalid['analysis']['placement']['dependencies']['locations'] = []
    output = package.write('provider-response.json', invalid)
    binary = tmp_path / 'bin'
    binary.mkdir()
    calls = tmp_path / 'calls.txt'
    model = binary / 'codex'
    model.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                     f'with pathlib.Path({str(calls)!r}).open("a") as f: f.write("call\\n")\n'
                     f'pathlib.Path(sys.argv[sys.argv.index("-o") + 1]).write_text(pathlib.Path({str(output)!r}).read_text())\n'
                     'print("private-provider-output")\n'
                     'print("private-provider-stderr", file=sys.stderr)\n')
    model.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'DK_HOST': 'codex'}
    env.pop('DK_REVIEW_STATS_TRACE', None)
    argv = [sys.executable, str(ROOT / 'dk-pr-decisions/scripts/decisions.py'),
            'prepare', '--input-manifest', str(package.manifest_path), '--output-dir', str(package.versions)]
    first = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=30)
    assert first.returncode == 1, first.stdout + first.stderr
    receipt = json.loads(first.stdout)
    assert receipt['error']['code'] == 'model_validation_failed'
    assert 'placement.dependencies.locations' in receipt['error']['message']
    assert 'private-provider' not in first.stdout + first.stderr
    assert not receipt['publication']['updated']
    assert classification('decisions', 1, receipt)[:2] == ('error', 'model_validation_failed')
    evidence, = map(Path, receipt['error']['action']['artifacts'])
    retained = json.loads(evidence.read_text())
    assert evidence.stat().st_mode & 0o777 == 0o600
    assert evidence.parent.stat().st_mode & 0o777 == 0o700
    assert len(retained['attempts']) == 2
    for index, attempt in enumerate(retained['attempts']):
        assert attempt['attempt'] == index
        assert json.loads(attempt['response']) == invalid
        assert attempt['validation']['field'] == 'placement.dependencies.locations'
        assert attempt['provider']['stdout'] == 'private-provider-output\n'
        assert attempt['provider']['stderr'] == 'private-provider-stderr\n'
        assert attempt['provider']['returncode'] == 0
    assert 'Prior response:' in retained['attempts'][1]['prompt']
    assert 'placement.dependencies.locations' in retained['attempts'][1]['prompt']
    original = evidence.read_bytes()
    restarted = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=30)
    assert restarted.returncode == 1
    assert json.loads(restarted.stdout)['error']['action']['artifacts'] == [str(evidence)]
    assert calls.read_text() == 'call\ncall\n'
    assert record.read_bytes() == before
    assert evidence.read_bytes() == original
    # Losing only the index must not turn an exhausted exchange into a new one.
    index, = (tmp_path / 'state/dk-code-factory/workflows/model-exchanges').glob('*.json')
    lock = index.with_suffix('.lock')
    lock_bytes = lock.read_bytes()
    journals = {path: path.read_bytes() for path in (tmp_path / 'state').rglob('model-exchange.json')}
    index.unlink()
    lost_index = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=30)
    assert lost_index.returncode == 1, lost_index.stdout + lost_index.stderr
    error = json.loads(lost_index.stdout)['error']
    assert error['code'] == 'model_diagnostics_invalid'
    assert error['action']['artifacts'] == [str(index), str(lock)]
    assert 'prior exchange reservation' in error['message']
    assert not index.exists()
    assert lock.read_bytes() == lock_bytes
    assert {path: path.read_bytes() for path in (tmp_path / 'state').rglob('model-exchange.json')} == journals
    assert calls.read_text() == 'call\ncall\n'
    assert record.read_bytes() == before
    assert not list(package.versions.glob('*/preparation.json'))
    with pytest.raises(ValueError, match='preparation required'):
        state.next_cycle(history, package.head)
    # A concrete evidence correction permits a new assessment in the same run.
    package.write('user.json', {'instruction': 'Dependencies must include the existing transform.py module.'})
    package.write('provider-response.json', response())
    recovered = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=30)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    data = json.loads(recovered.stdout)['data']
    assert data['readiness'] == 'ready'
    assert calls.read_text() == 'call\ncall\ncall\n'
    assert record.read_bytes() == before
    assert evidence.read_bytes() == original
    artifact = prep.load(data['artifact'], package.context, package.decisions)
    assert artifact['identity']['head_sha'] == package.head
    wrong_base = {**package.context, 'base_sha': '0' * 40}
    with pytest.raises(ValueError):
        prep.load(data['artifact'], wrong_base, package.decisions)


def test_interrupted_exchange_cannot_reset_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    (tmp_path / 'repo').mkdir()
    model = Mock(side_effect=['not json', KeyboardInterrupt()])
    kwargs = {'executor': model, 'diagnostic_scope': 'engineering-preparation'}
    with pytest.raises(KeyboardInterrupt):
        executors.execute_json('Frozen evidence', tmp_path / 'repo', **kwargs)
    assert model.call_count == 2
    with pytest.raises(ValidationFailure) as caught:
        executors.execute_json('Frozen evidence', tmp_path / 'repo', **kwargs)
    assert model.call_count == 2
    journal = json.loads(Path(caught.value.action['artifacts'][0]).read_text())
    assert journal['attempts'][0]['response'] == 'not json'
    assert len(journal['attempts']) == 2


@pytest.mark.parametrize('stage', ['journal', 'index'])
def test_interruption_before_index_blocks_without_provider_call(tmp_path, monkeypatch, stage):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    write = model_diagnostics.write

    def interrupted_write(path, value):
        if (path.name == 'model-exchange.json' if stage == 'journal'
                else path.parent.name == 'model-exchanges'):
            raise KeyboardInterrupt()
        write(path, value)

    monkeypatch.setattr(model_diagnostics, 'write', interrupted_write)
    provider = Mock(return_value='{}')
    kwargs = {'executor': provider, 'diagnostic_scope': 'engineering-preparation'}
    with pytest.raises(KeyboardInterrupt):
        executors.execute_json('Frozen evidence', checkout, **kwargs)
    provider.assert_not_called()
    directory = tmp_path / 'state/dk-code-factory/workflows/model-exchanges'
    lock, = directory.glob('*.lock')
    index = lock.with_suffix('.json')
    assert not index.exists()
    journals = {path: path.read_bytes() for path in (tmp_path / 'state').rglob('model-exchange.json')}
    assert len(journals) == (0 if stage == 'journal' else 1)
    lock_bytes = lock.read_bytes()
    monkeypatch.setattr(model_diagnostics, 'write', write)
    with pytest.raises(model_diagnostics.StorageFailure) as caught:
        executors.execute_json('Frozen evidence', checkout, **kwargs)
    assert caught.value.code == 'model_diagnostics_invalid'
    assert caught.value.action['artifacts'] == [str(index), str(lock)]
    assert 'unresolved storage or setup failure' in caught.value.action['task']
    provider.assert_not_called()
    assert not index.exists()
    assert lock.read_bytes() == lock_bytes
    assert {path: path.read_bytes() for path in (tmp_path / 'state').rglob('model-exchange.json')} == journals


def test_public_failure_does_not_echo_validator_data(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    (tmp_path / 'repo').mkdir()
    validator = Mock(side_effect=ValueError('private-credential-in-validator'))
    with pytest.raises(ValidationFailure) as caught:
        executors.execute_json('Frozen evidence', tmp_path / 'repo', validate=validator,
                               executor=Mock(return_value='{}'), diagnostic_scope='engineering-preparation')
    assert 'private-credential' not in str(caught.value)
    journal = json.loads(Path(caught.value.action['artifacts'][0]).read_text())
    assert journal['attempts'][1]['validation']['detail'] == 'private-credential-in-validator'


def test_provider_repair_resumes_remaining_correction_without_reset(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    (tmp_path / 'repo').mkdir()
    model = Mock(side_effect=['invalid first response', RuntimeError('provider unavailable')])
    kwargs = {'executor': model, 'diagnostic_scope': 'engineering-preparation'}
    with pytest.raises(RuntimeError, match='provider unavailable'):
        executors.execute_json('Frozen evidence', tmp_path / 'repo', **kwargs)
    correction_prompt = model.call_args_list[1].args[0]
    repaired = Mock(return_value='invalid correction')
    with pytest.raises(ValidationFailure) as caught:
        executors.execute_json('Frozen evidence', tmp_path / 'repo', executor=repaired,
                               diagnostic_scope='engineering-preparation')
    repaired.assert_called_once_with(correction_prompt, tmp_path / 'repo', attempt=1)
    journal = json.loads(Path(caught.value.action['artifacts'][0]).read_text())
    assert [item['attempt'] for item in journal['attempts']] == [0, 1, 1]
    assert journal['attempts'][1]['provider_error']['detail'] == 'provider unavailable'


@pytest.mark.parametrize('change', ['validation', 'prompt', 'checkout'])
def test_retained_escalation_revalidates_and_guards_the_request(tmp_path, monkeypatch, change):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    moved = tmp_path / 'moved'
    moved.mkdir()
    routing = {'full_reassessment': True, 'reason': 'Inspect all interactions'}
    provider = Mock(return_value=json.dumps(routing))
    monkeypatch.setattr(executors, 'execute', provider)
    scope = {'evidence': 'same governing inputs', 'phase': 'incremental'}
    assert prep.analyze('Frozen evidence', checkout, diagnostic_scope=scope) == routing
    validate = Mock(side_effect=ValueError('routing no longer valid'))
    with pytest.raises(ValueError):
        prep.analyze('Changed prompt' if change == 'prompt' else 'Frozen evidence',
                     moved if change == 'checkout' else checkout,
                     validate=validate, diagnostic_scope=scope)
    assert provider.call_count == 1
    if change == 'validation':
        validate.assert_called_once_with(routing)
    else:
        validate.assert_not_called()


@pytest.mark.parametrize('stage', ['response', 'rejected', 'accepted'])
@pytest.mark.parametrize('destination', ['external', 'repository', 'plugin'])
def test_correction_storage_failure_does_not_grant_another_response(tmp_path, monkeypatch, stage, destination):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    plugin = tmp_path / 'plugin'
    monkeypatch.setattr(model_diagnostics, '__file__', str(plugin / 'runtime/shared/model_diagnostics.py'))
    temporary = {'external': tmp_path / 'external', 'repository': checkout / 'tmp',
                 'plugin': plugin / 'tmp'}[destination]
    temporary.mkdir(parents=True)
    monkeypatch.setenv('TMPDIR', str(temporary))
    monkeypatch.setattr(model_diagnostics.tempfile, 'tempdir', None)
    save = model_diagnostics.write

    def write(path, value):
        attempts = value.get('attempts', [])
        if (attempts and attempts[-1]['attempt'] == 1
                and (('response' in attempts[-1]) if stage == 'response'
                     else value.get('status') == stage)
                and str(path).startswith(str(tmp_path / 'state'))):
            raise OSError('journal disk full')
        return save(path, value)

    monkeypatch.setattr(model_diagnostics, 'write', write)
    model = Mock(side_effect=['not json', '{}' if stage == 'accepted' else 'still not json'])
    with pytest.raises(model_diagnostics.StorageFailure) as caught:
        executors.execute_json('Frozen evidence', tmp_path / 'repo', executor=model,
                               diagnostic_scope='engineering-preparation')
    assert model.call_count == 2
    retained = Path(caught.value.action['artifacts'][0])
    assert 'journal disk full' in str(caught.value)
    if destination == 'external':
        fallback = Path(caught.value.action['artifacts'][1])
        assert fallback.is_relative_to(temporary)
        saved = json.loads(fallback.read_text())
        assert saved['attempts'][-1]['response'] == ('{}' if stage == 'accepted' else 'still not json')
        assert saved['status'] != 'provider_failed'
        assert fallback.stat().st_mode & 0o777 == 0o600
        assert fallback.parent.stat().st_mode & 0o777 == 0o700
        if stage == 'rejected':
            assert saved['attempts'][-1]['validation']['type'] == 'JSONDecodeError'
    else:
        assert caught.value.action['artifacts'] == [str(retained)]
        assert 'Fallback persistence failed' in str(caught.value)
        assert 'outside the plugin and repository' in str(caught.value)
        assert not list(temporary.iterdir())
    monkeypatch.setattr(model_diagnostics, 'write', save)
    with pytest.raises(ValidationFailure) as restarted:
        executors.execute_json('Frozen evidence', tmp_path / 'repo', executor=model,
                               diagnostic_scope='engineering-preparation')
    assert restarted.value.action['artifacts'] == [str(retained)]
    assert model.call_count == 2


def test_relocated_provider_failure_does_not_replay_stale_correction_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    (tmp_path / 'repo').mkdir()
    scope = {'operation': 'engineering-preparation', 'evidence': 'same governing evidence'}
    model = Mock(side_effect=['invalid', RuntimeError('provider unavailable')])
    with pytest.raises(RuntimeError, match='provider unavailable'):
        executors.execute_json('Read old source path', tmp_path / 'repo', executor=model,
                               diagnostic_scope=scope)
    with pytest.raises(ValidationFailure):
        executors.execute_json('Read relocated source path', tmp_path / 'repo', executor=model,
                               diagnostic_scope=scope)
    assert model.call_count == 2


def test_accepted_journal_fsync_failure_blocks_fresh_process(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    sync = private_state.sync_directory
    failed = []

    def sync_directory(path):
        evidence = path / 'model-exchange.json'
        if evidence.exists() and json.loads(evidence.read_text())['status'] == 'accepted' and not failed:
            failed.append(evidence)
            raise OSError('directory fsync failed after replacement')
        sync(path)

    monkeypatch.setattr(private_state, 'sync_directory', sync_directory)
    provider = Mock(side_effect=['invalid', '{}'])
    with pytest.raises(model_diagnostics.StorageFailure):
        executors.execute_json('Frozen evidence', checkout, executor=provider,
                               diagnostic_scope='engineering-preparation')
    assert provider.call_count == 2
    evidence, = failed
    retained = evidence.read_bytes()
    assert json.loads(retained)['status'] == 'accepted'
    index, = (tmp_path / 'state/dk-code-factory/workflows/model-exchanges').glob('*.json')
    assert json.loads(index.read_text())['status'] == 'running'
    script = '''import json,sys
from pathlib import Path
from shared import executors
def provider(*args, **kwargs):
    Path(sys.argv[2]).write_text("unexpected provider call")
    return '{}'
try:
    executors.execute_json('Frozen evidence', Path(sys.argv[1]), executor=provider,
                           diagnostic_scope='engineering-preparation')
except Exception as exc:
    print(json.dumps({'code': getattr(exc, 'code', None), 'message': str(exc)}))
else:
    raise AssertionError('restart unexpectedly succeeded')
'''
    call_marker = tmp_path / 'provider-called'
    restarted = subprocess.run([sys.executable, '-c', script, str(checkout), str(call_marker)],
                               env={**os.environ, 'PYTHONPATH': str(ROOT)},
                               text=True, capture_output=True, timeout=10)
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert json.loads(restarted.stdout)['code'] == 'model_diagnostics_invalid'
    assert not call_marker.exists()
    assert evidence.read_bytes() == retained


@pytest.mark.parametrize('after_replace', [False, True])
def test_optional_accepted_index_failure_preserves_validated_success(tmp_path, monkeypatch, after_replace):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    write = model_diagnostics.write
    sync = private_state.sync_directory

    def save(path, value):
        if path.parent.name == 'model-exchanges' and value['status'] == 'accepted' and not after_replace:
            raise OSError('optional marker write failed')
        write(path, value)

    def sync_directory(path):
        if path.name == 'model-exchanges' and after_replace:
            index, = path.glob('*.json')
            if json.loads(index.read_text())['status'] == 'accepted':
                raise OSError('optional marker fsync failed after replacement')
        sync(path)

    monkeypatch.setattr(model_diagnostics, 'write', save)
    monkeypatch.setattr(private_state, 'sync_directory', sync_directory)
    provider = Mock(return_value='{}')
    kwargs = {'executor': provider, 'diagnostic_scope': 'engineering-preparation'}
    assert executors.execute_json('Frozen evidence', checkout, **kwargs) == {}
    if after_replace:
        assert executors.execute_json('Frozen evidence', checkout, **kwargs) == {}
        assert provider.call_count == 2
    else:
        with pytest.raises(model_diagnostics.StorageFailure, match='eligibility'):
            executors.execute_json('Frozen evidence', checkout, **kwargs)
        assert provider.call_count == 1


@pytest.mark.parametrize('target,damage', [
    ('index', 'null'), ('index', 'empty'), ('index', 'json'), ('index', 'status'),
    ('index', 'relative'), ('index', 'outside'), ('index', 'other_exchange'),
    ('exchange', 'missing'), ('exchange', 'null'), ('exchange', 'empty'), ('exchange', 'json'),
    ('exchange', 'version'), ('exchange', 'identity'), ('exchange', 'status'),
    ('exchange', 'empty_attempts'), ('exchange', 'first_slot'), ('exchange', 'repeated_slot'),
    ('exchange', 'extra_slot'), ('exchange', 'response'), ('exchange', 'validation'),
])
def test_damaged_retained_state_never_replenishes_correction(tmp_path, monkeypatch, target, damage):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    provider = Mock(return_value='invalid')
    with pytest.raises(ValidationFailure) as exhausted:
        executors.execute_json('Frozen evidence', checkout, executor=provider,
                               diagnostic_scope='engineering-preparation')
    evidence = Path(exhausted.value.action['artifacts'][0])
    index, = (tmp_path / 'state/dk-code-factory/workflows/model-exchanges').glob('*.json')
    path = index if target == 'index' else evidence
    value = json.loads(path.read_text())
    if damage == 'missing':
        path.unlink()
    elif damage in {'null', 'empty', 'json'}:
        path.write_text({'null': 'null', 'empty': '{}', 'json': '{'}[damage])
    else:
        if damage == 'status':
            value['status'] = 'accepted'
        elif damage == 'relative':
            value['evidence'] = 'model-exchange.json'
        elif damage == 'outside':
            value['evidence'] = str(tmp_path / 'model-exchange.json')
        elif damage == 'other_exchange':
            executors.execute_json('Other evidence', checkout, executor=Mock(return_value='{}'),
                                   diagnostic_scope='engineering-preparation')
            other, = (item for item in (tmp_path / 'state').rglob('model-exchange.json') if item != evidence)
            value['evidence'] = str(other)
        elif damage == 'version':
            value['schema_version'] = True
        elif damage == 'identity':
            value['identity']['scope'] = 'different evidence'
        elif damage == 'empty_attempts':
            value['attempts'] = []
        elif damage == 'first_slot':
            value['attempts'][0]['attempt'] = 1
        elif damage == 'repeated_slot':
            value['attempts'][1]['attempt'] = 0
        elif damage == 'extra_slot':
            value['attempts'][1]['attempt'] = 2
        elif damage == 'response':
            del value['attempts'][1]['response']
        elif damage == 'validation':
            value['attempts'][1]['validation'] = {'detail': 'private invalid diagnostic'}
        path.write_text(json.dumps(value))
    with pytest.raises(model_diagnostics.StorageFailure) as caught:
        executors.execute_json('Frozen evidence', checkout, executor=provider,
                               diagnostic_scope='engineering-preparation')
    assert caught.value.code == 'model_diagnostics_invalid'
    assert 'private invalid diagnostic' not in str(caught.value)
    assert provider.call_count == 2


@pytest.mark.parametrize('interrupted', [False, True])
@pytest.mark.parametrize('stage', ['setup', 'provider'])
def test_same_key_competitors_cannot_replay_consumed_exchange(tmp_path, monkeypatch, interrupted, stage):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    context = multiprocessing.get_context('fork')
    entered, competing, release = (context.Event() for _ in range(3))
    results = context.Queue()

    def worker(first):
        calls = []
        def pause():
            entered.set()
            if not release.wait(5):
                raise AssertionError('release deadline expired')
            if interrupted:
                raise KeyboardInterrupt()
        write = model_diagnostics.write
        def save(path, value):
            if first and stage == 'setup' and path.parent.name == 'model-exchanges':
                pause()
            return write(path, value)
        model_diagnostics.write = save
        def provider(*args, **kwargs):
            calls.append(kwargs['attempt'])
            if first and stage == 'provider' and len(calls) == 1:
                pause()
            return 'invalid'
        flock = model_diagnostics.fcntl.flock
        def lock(fd, flags):
            if not first:
                competing.set()
            return flock(fd, flags)
        model_diagnostics.fcntl.flock = lock
        try:
            executors.execute_json('Frozen evidence', checkout, executor=provider,
                                   diagnostic_scope='engineering-preparation')
        except BaseException as exc:
            results.put((first, calls, type(exc).__name__))

    processes = [context.Process(target=worker, args=(first,)) for first in (True, False)]
    try:
        processes[0].start()
        assert entered.wait(5)
        processes[1].start()
        assert competing.wait(5)
        release.set()
        observed = sorted([results.get(timeout=5), results.get(timeout=5)])
        assert observed[0] == (False, [], 'StorageFailure' if interrupted and stage == 'setup'
                              else 'ValidationFailure')
        assert observed[1] == (True, ([] if stage == 'setup' else [0]) if interrupted else [0, 1],
                               'KeyboardInterrupt' if interrupted else 'ValidationFailure')
        for process in processes:
            process.join(5)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(5)


@pytest.mark.parametrize('fallback', ['external', 'unsafe', 'unavailable'])
@pytest.mark.parametrize('timeout', [False, True])
def test_cli_preserves_plain_provider_error_and_secondary_storage_action(tmp_path, monkeypatch, capsys, fallback, timeout):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    if fallback == 'unsafe':
        monkeypatch.setenv('TMPDIR', str(checkout))
        monkeypatch.setattr(model_diagnostics.tempfile, 'tempdir', None)
    spec = importlib.util.spec_from_file_location('storage_diagnostic_decisions', ROOT / 'dk-pr-decisions/scripts/decisions.py')
    decisions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decisions)
    save = model_diagnostics.write
    def write(path, value):
        if value.get('status') == 'provider_failed' and path.is_relative_to(tmp_path / 'state'):
            raise OSError('journal disk full')
        if value.get('status') == 'provider_failed' and fallback == 'unavailable':
            raise OSError('fallback disk full')
        save(path, value)
    monkeypatch.setattr(model_diagnostics, 'write', write)
    failure = subprocess.TimeoutExpired(['provider'], 5) if timeout else RuntimeError('provider unavailable')
    if timeout:
        failure.action = task('Repair the selected provider.')
        failure.action['diagnostic'] = 'Existing recovery context.'
    original_message = str(failure)
    failure.stdout = 'private provider transcript'
    provider = Mock(side_effect=failure)
    monkeypatch.setattr(decisions, 'cli', lambda: executors.execute_json(
        'Frozen evidence', checkout, executor=provider, diagnostic_scope='engineering-preparation'))
    assert decisions.main() == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['error']['code'] == type(failure).__name__
    assert original_message in receipt['error']['message']
    assert 'journal disk full' in receipt['error']['action']['diagnostic']
    if timeout:
        assert receipt['error']['message'] == original_message
        assert receipt['error']['action']['task'] == 'Repair the selected provider.'
        assert receipt['error']['action']['diagnostic'].startswith('Existing recovery context.')
    assert 'private provider transcript' not in json.dumps(receipt)
    artifacts = list(map(Path, receipt['error']['action']['artifacts']))
    assert artifacts[0].exists()
    if fallback != 'external':
        assert len(artifacts) == 1
        assert 'Fallback persistence failed' in receipt['error']['action']['diagnostic']
        detail = 'outside the plugin and repository' if fallback == 'unsafe' else 'fallback disk full'
        assert detail in receipt['error']['action']['diagnostic']
        assert list(checkout.iterdir()) == [checkout / '.git']
    else:
        assert artifacts[1].exists()
        assert json.loads(artifacts[1].read_text())['attempts'][0]['provider_error']['stdout'] == failure.stdout


@pytest.mark.parametrize('kind', ['plain', 'typed', 'timeout'])
def test_cli_correction_provider_failure_exposes_retained_exchange(tmp_path, monkeypatch, capsys, kind):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    checkout = tmp_path / 'repo'
    checkout.mkdir()
    spec = importlib.util.spec_from_file_location('correction_diagnostic_decisions', ROOT / 'dk-pr-decisions/scripts/decisions.py')
    decisions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decisions)
    original_artifact = tmp_path / 'provider.json'
    failure = (Diagnostic('provider_unavailable', 'provider unavailable',
                          task('Repair the selected provider.', original_artifact))
               if kind == 'typed' else subprocess.TimeoutExpired(['provider'], 5)
               if kind == 'timeout' else RuntimeError('provider unavailable'))
    failure.stdout = 'private provider transcript'
    rejected = 'private invalid initial response'
    provider = Mock(side_effect=[rejected, failure])
    monkeypatch.setattr(decisions, 'cli', lambda: executors.execute_json(
        'Frozen evidence', checkout, executor=provider, diagnostic_scope='engineering-preparation'))
    assert decisions.main() == 1
    receipt = json.loads(capsys.readouterr().out)
    error = receipt['error']
    assert error['code'] == ('provider_unavailable' if kind == 'typed' else type(failure).__name__)
    assert error['message'] == str(failure)
    assert rejected not in json.dumps(receipt)
    assert failure.stdout not in json.dumps(receipt)
    if kind == 'typed':
        assert error['action']['task'] == 'Repair the selected provider.'
        assert error['action']['artifacts'][0] == str(original_artifact)
    evidence = Path(error['action']['artifacts'][-1])
    saved = json.loads(evidence.read_text())
    assert saved['status'] == 'provider_failed'
    assert [item['attempt'] for item in saved['attempts']] == [0, 1]
    assert saved['attempts'][0]['response'] == rejected
    assert saved['attempts'][0]['validation']['type'] == 'JSONDecodeError'
    assert rejected in saved['attempts'][1]['prompt']
    assert saved['attempts'][1]['provider_error']['stdout'] == failure.stdout
    assert provider.call_count == 2
