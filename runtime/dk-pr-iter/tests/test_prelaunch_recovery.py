"""Bounded recovery uses mandatory session evidence, without external providers."""
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

from test_review_coverage import state, Package, prep, analysis, descriptor, read, write, refresh_prepared
from shared import review_sessions
from shared import private_state, review_coverage

spec = importlib.util.spec_from_file_location('prelaunch_runner',
    Path(__file__).parents[2] / 'dk-pr-review/scripts/run_reviewers.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def allocated(tmp_path, monkeypatch):
    package = Package(tmp_path)
    package.manifest['mode'] = 'iteration'
    package.write('manifest.json', package.manifest)
    artifact = package.prepare(analysis())['artifact']
    prepared = prep.read(artifact)
    record = {}
    state.start_run(record)
    state.attach_preparation(record, artifact, package.context['url'], package.head)
    state.next_cycle(record, package.head)
    path = tmp_path / 'run.json'
    binding = state.review_session(record, package.head, path)
    write(path, record)
    monkeypatch.setattr(runner, 'describe_reviewer', lambda _: prepared['reviewers']['example'])
    monkeypatch.setattr(runner, 'skill_path', lambda _: Path('command'))
    actual_review = runner.architecture.review
    monkeypatch.setattr(runner.architecture, 'review', lambda packet, context, decisions, **kwargs:
        actual_review(packet, context, decisions, **kwargs, reviewer=lambda *_:
            {'complete': True, 'reason': '', 'coverage': []}))
    return package, prepared, record, path, binding


def session_for(allocated, *, resume=False, scope=''):
    package, prepared, record, path, binding = allocated
    return review_sessions.Session(package.context, prepared, ['example'],
        path=binding['session_file'], resume=resume, scope=scope)


def prelaunch_failure(allocated, monkeypatch):
    package, prepared, record, path, _ = allocated
    session = session_for(allocated)
    monkeypatch.setattr(runner, 'describe_reviewer', lambda _: (_ for _ in ()).throw(OSError('descriptor unavailable')))
    with session.execution():
        result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=session)
        item = session.finish(result)
    assert result['guard_failure'] and not result['executed_reviewers']
    state.register_review(record, {'review_artifact': item}, package.head)
    return session, item


def recover(allocated, *, scope='', reconcile=False):
    package, prepared, record, path, _ = allocated
    write(path, record)
    item = state.recover_cycle_file(path, package.context['head_sha'], scope=scope, reconcile=reconcile)
    record.clear()
    record.update(read(path, {}))
    return item


@pytest.mark.parametrize('proof_damage', [None, 'missing', 'changed'])
def test_prelaunch_failure_resumes_same_session_and_preserves_failed_result(allocated, monkeypatch, proof_damage):
    package, prepared, record, path, binding = allocated
    original, failed_item = prelaunch_failure(allocated, monkeypatch)
    before = read(original.path, {})
    with pytest.raises(ValueError, match='bounded cycle-recover'), session_for(allocated, resume=True).execution():
        pass
    assert state.next_stage(record, package.head, path)['stage'] == 'prelaunch_recovery'
    recovery = recover(allocated)
    assert recovery['resume'] and recovery['session_file'] == str(original.path)
    assert len(record['cycles']) == 1 and recovery['attempt'] == 2
    assert read(Path(recovery['proof']['path']), {}) == before
    assert read(original.path, {})['results'] == [failed_item]
    before_guidance = copy.deepcopy(record)
    ready = state.next_stage(record, package.head, path)
    assert ready['stage'] == 'review' and ready['resume']
    assert ready['session_file'] == str(original.path) and record == before_guidance
    with pytest.raises(ValueError, match='changed'), original.execution():
        pass
    monkeypatch.setattr(runner, 'describe_reviewer', lambda _: prepared['reviewers']['example'])
    def provider(*_):
        saved = read(original.path, {})
        assert 'example' in saved['execution_history'][-1]['launch_intents']
        return {'findings': []}
    monkeypatch.setattr(runner, 'run_skill_reviewer', provider)
    resumed = session_for(allocated, resume=True)
    with resumed.execution():
        result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=resumed)
        item = resumed.finish(result)
    state.register_review(record, {'review_artifact': item}, package.head)
    assert state.current_review(record, prepared)[1]['result']['complete']
    assert read(original.path, {})['results'] == [failed_item, item]
    status = state.cycle_status(record)
    assert (status['allocated'], status['substantive'], status['infrastructure_attempts']) == (1, 1, 1)
    assert status['cycles'][0]['sessions'] == [
        {'attempt': 1, 'session_file': str(original.path), 'state': 'recovered_unlaunched',
         'launch_intent': False, 'proved_unlaunched': True},
        {'attempt': 2, 'session_file': str(original.path), 'state': 'completed',
         'launch_intent': True, 'proved_unlaunched': False}]
    assert [entry['attempt'] for entry in record['review_sessions']] == [1, 2]
    proof_path = Path(recovery['proof']['path'])
    assert read(proof_path, {}) == before
    if proof_damage:
        if proof_damage == 'missing':
            proof_path.unlink()
        else:
            write(proof_path, {})
        uncertain = state.cycle_status(record)
        assert uncertain['cycles'][0]['sessions'][0] == {
            'attempt': 1, 'session_file': str(original.path), 'state': 'unknown',
            'launch_intent': False, 'proved_unlaunched': False}
        assert uncertain['cycles'][0]['sessions'][1] == status['cycles'][0]['sessions'][1]


def test_repeated_prelaunch_failure_is_bounded_in_runner_and_allocator(allocated, monkeypatch):
    package, prepared, record, path, binding = allocated
    original, _ = prelaunch_failure(allocated, monkeypatch)
    recover(allocated)
    resumed = session_for(allocated, resume=True)
    with resumed.execution():
        result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=resumed)
        resumed.finish(result)
    with pytest.raises(ValueError, match='already recorded'):
        state.recover_cycle(record, package.head, path)
    with pytest.raises(ValueError, match='bounded cycle-recover'), session_for(allocated, resume=True).execution():
        pass
    assert len(record['cycles']) == len(record['infrastructure_recoveries']) == 1
    assert len(read(original.path, {})['execution_history']) == 2
    assert state.next_stage(record, package.head, path)['stage'] == 'prelaunch_limitation'
    sessions = state.cycle_status(record)['cycles'][0]['sessions']
    assert sessions[0]['state'] == 'recovered_unlaunched'
    assert all(item['proved_unlaunched'] and not item['launch_intent'] for item in sessions)
    assert sessions[-1]['state'] != 'provider_failed'


def test_reserved_restart_freezes_old_path_and_preserves_allocation(allocated):
    package, prepared, record, path, binding = allocated
    before = read(Path(binding['session_file']), {})
    recovery = recover(allocated)
    assert not recovery['resume'] and recovery['session_file'] != binding['session_file']
    assert read(Path(recovery['proof']['path']), {}) == before
    with pytest.raises(ValueError, match='already exists'):
        session_for(allocated)
    replacement = {**binding, 'session_file': recovery['session_file']}
    target = Path(recovery['session_file'])
    before_upgrade = target.read_bytes()
    with pytest.raises(ValueError, match='changed evidence'):
        session_for((*allocated[:4], replacement), scope='Wrong requested scope')
    assert target.read_bytes() == before_upgrade
    fresh = session_for((*allocated[:4], replacement))
    assert fresh.path != Path(binding['session_file'])
    with fresh.execution():
        fresh.launch('example')
        item = fresh.finish({'complete': True, 'findings': []})
    assert state.register_review(record, {'review_artifact': item}, package.head)['attempt'] == 2
    assert record['cycles'] == [{'number': 1, 'head': package.head}]


@pytest.mark.parametrize('change', ['scope', 'head'])
def test_changed_evidence_requires_fresh_session_and_fences_stale_worker(allocated, monkeypatch, change):
    package, prepared, record, path, binding = allocated
    original, item = prelaunch_failure(allocated, monkeypatch)
    scope = 'Changed requested scope' if change == 'scope' else ''
    if change == 'head':
        write(path, record)
        (package.repo / 'transform.py').write_text('def transform(value):\n    return value + 1\n')
        prep.git(package.repo, 'add', '.')
        prep.git(package.repo, 'commit', '-qm', 'Change supported transform')
        package.write('reviewer.json', descriptor('example', package.repo / 'transform.py'))
        refresh_prepared(path, package.context, prepared)
        record = read(path, {})
        assert state.next_stage(record, package.context['head_sha'], path)['stage'] == 'prelaunch_recovery'
    recovery = recover((package, prepared, record, path, binding), scope=scope)
    assert not recovery['resume'] and recovery['session_file'] != str(original.path)
    assert read(original.path, {})['results'] == [item]
    with pytest.raises(ValueError, match='changed'), original.execution():
        pass
    fresh_binding = state.review_session(record, package.context['head_sha'], path)
    fresh = session_for((package, prepared, record, path, fresh_binding), scope=scope)
    assert fresh.id != original.id
    assert len(record['cycles']) == 1
    assert state.next_stage(record, package.context['head_sha'], path)['stage'] == 'review'
    if change == 'head':
        decisions = read(Path(prepared['sources']['decisions']['path']), {})
        monkeypatch.setattr(runner, 'describe_reviewer', lambda _: prepared['reviewers']['example'])
        monkeypatch.setattr(runner, 'run_skill_reviewer', lambda *_:
            {'status': 'tokens_exhausted', 'findings': [], 'message': 'usage limit'})
        with fresh.execution():
            exhausted = runner.review(package.context, ['example'], scope, decisions, '', prepared, session=fresh)
            exhausted_item = fresh.finish(exhausted)
        state.register_review(record, {'review_artifact': exhausted_item}, package.context['head_sha'])
        retry = state.retry_exhausted_cycle(record, package.context['head_sha'])
        assert exhausted['outcome'] == 'usage_exhausted'
        assert retry['prior_head'] == retry['head'] == package.context['head_sha']
        assert retry['prior_head'] != record['cycles'][0]['head']
        assert retry['attempt'] == 3 and len(record['cycles']) == 1
        assert record['review_results'][0]['path'] == item['path']


@pytest.mark.parametrize('uncertain', ['missing', 'legacy', 'active', 'completed_checkpoint', 'completed_result'])
def test_missing_uncertain_and_contradictory_evidence_denies_recovery(allocated, uncertain):
    package, prepared, record, path, binding = allocated
    if uncertain == 'missing':
        Path(binding['session_file']).unlink()
    else:
        session = session_for(allocated)
        with session.execution():
            if uncertain == 'completed_checkpoint':
                session.record('example', 'completed', {'findings': []})
            if uncertain == 'completed_result':
                session.finish({'complete': True, 'findings': []})
        saved = read(session.path, {})
        if uncertain == 'legacy':
            saved.pop('launch_tracking_version')
        if uncertain == 'active':
            saved['execution_history'][-1]['status'] = 'active'
        write(session.path, saved)
    before = copy.deepcopy(record)
    with pytest.raises(ValueError, match='uncertain|contradict|allocation evidence'):
        state.recover_cycle(record, package.head, path)
    assert record.get('infrastructure_recoveries', []) == []
    assert record['cycles'] == before['cycles']


@pytest.mark.parametrize('intent', ['example', 'architecture-review'])
def test_any_provider_dispatch_intent_disallows_recovery_even_after_failure(allocated, intent):
    package, prepared, record, path, binding = allocated
    session = session_for(allocated)
    with pytest.raises(OSError, match='provider failed'), session.execution():
        session.launch(intent)
        raise OSError('provider failed')
    assert state.cycle_status(record)['substantive'] == 1
    with pytest.raises(ValueError, match='launch intent'):
        state.recover_cycle(record, package.head, path)


def test_active_execution_owns_recovery_lock(allocated):
    package, prepared, record, path, binding = allocated
    session = session_for(allocated)
    with session.execution():
        with pytest.raises(ValueError, match='already executing'):
            state.recover_cycle(record, package.head, path)


def test_real_reviewer_failure_retains_dispatch_and_cannot_recover(allocated, monkeypatch):
    package, prepared, record, path, binding = allocated
    session = session_for(allocated)
    called = []
    def provider(*args):
        called.append(args[0])
        raise RuntimeError('provider started and failed')
    monkeypatch.setattr(runner, 'run_skill_reviewer', provider)
    with session.execution():
        result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=session)
        session.finish(result)
    assert called == ['example'] and result['outcome'] == 'provider_failed'
    assert state.cycle_status(record)['cycles'][0]['sessions'][0]['state'] == 'provider_failed'
    with pytest.raises(ValueError, match='launch intent'):
        state.recover_cycle(record, package.head, path)


def test_run_wide_infrastructure_cap_and_five_cycle_cap_preserve_history(allocated):
    package, prepared, record, path, binding = allocated
    recover(allocated)
    state.next_cycle(record, package.head)
    state.review_session(record, package.head, path)
    recover(allocated)
    for number in range(3, 6):
        state.next_cycle(record, package.head)
        current = state.review_session(record, package.head, path)
        assert state.next_stage(record, package.head, path)['stage'] == 'review'
        with session_for((*allocated[:4], current)).execution():
            pass
        limitation = state.next_stage(record, package.head, path)
        assert limitation['stage'] == 'prelaunch_limitation'
        assert limitation['action']['kind'] == ('human_input' if number == 5 else 'agent_task')
        with pytest.raises(ValueError, match='two infrastructure'):
            state.recover_cycle(record, package.head, path)
    with pytest.raises(ValueError, match='five review cycles'):
        state.next_cycle(record, package.head)
    assert len(record['cycles']) == 5 and len(record['infrastructure_recoveries']) == 2


def test_partial_recovery_write_failure_remains_blocked(allocated, monkeypatch):
    package, prepared, record, path, binding = allocated
    original = review_sessions.mutate
    def interrupted(target, default, operation):
        result = original(target, default, operation)
        if target == Path(binding['session_file']):
            raise OSError('run record not written')
        return result
    monkeypatch.setattr(review_sessions, 'mutate', interrupted)
    with pytest.raises(OSError, match='not written'):
        state.recover_cycle(record, package.head, path)
    assert not record['infrastructure_recoveries']
    with pytest.raises(ValueError, match='uncertain'):
        state.recover_cycle(record, package.head, path)
    with pytest.raises(ValueError, match='already exists'):
        session_for(allocated)


@pytest.mark.parametrize('field', [None, 'iteration_run_id', 'cycle', 'attempt'])
def test_recovery_rejects_missing_or_mismatched_original_allocation(allocated, field):
    package, prepared, record, path, binding = allocated
    target = Path(binding['session_file'])
    saved = read(target, {})
    if field is None:
        saved.pop('iteration_allocation')
    else:
        saved['iteration_allocation'][field] = 'different' if field == 'iteration_run_id' else 99
    write(target, saved)
    before = target.read_bytes()
    with pytest.raises(ValueError, match='allocation evidence'):
        state.recover_cycle(record, package.head, path)
    assert target.read_bytes() == before and not record['infrastructure_recoveries']
    assert not list(target.parent.glob('prelaunch-proofs/*.json'))


@pytest.mark.parametrize('initialized', [False, True])
@pytest.mark.parametrize('boundary', ['record', 'receipt'])
def test_cli_recovery_commit_failures_block_until_exact_reconciliation(
        allocated, monkeypatch, capsys, initialized, boundary):
    package, prepared, record, path, binding = allocated
    failed_item = None
    if initialized:
        _, failed_item = prelaunch_failure(allocated, monkeypatch)
    path = path.parent / 'pr-1.json'
    write(path, record)
    before_session = read(Path(binding['session_file']), {})
    monkeypatch.setattr(state, 'root', lambda *_: path.parent)
    argv = ['run_state.py', '--repo', 'github.com/example/repo', '--pr', '1',
            'cycle-recover', '--head', package.head]
    monkeypatch.setattr(sys, 'argv', argv)
    original_write = private_state.write
    def unavailable(target, value):
        if ((boundary == 'record' and target == path)
                or (boundary == 'receipt' and target.parent.name == 'recovery-activations')):
            raise OSError('injected final persistence failure')
        return original_write(target, value)
    with monkeypatch.context() as fault:
        fault.setattr(private_state, 'write', unavailable)
        with pytest.raises(SystemExit) as stopped:
            state.main()
        assert stopped.value.code == 2
    capsys.readouterr()
    durable = read(path, {})
    assert len(durable.get('infrastructure_recoveries', [])) == (boundary == 'receipt')
    original = read(Path(binding['session_file']), {})
    plan = review_sessions.activation_plan(original)
    proof_path = Path(plan['proof']['path'])
    assert read(proof_path, {}) == before_session
    target = Path(plan['binding']['session_file'])
    pending = review_sessions.Session(package.context, prepared, ['example'], path=target, resume=initialized)
    generation = read(target, {})['generation']
    with pytest.raises(ValueError, match='activation'), pending.execution():
        pass
    with pytest.raises(ValueError, match='activation'):
        pending.finish({'complete': True, 'findings': []})
    if failed_item:
        with pytest.raises(ValueError, match='activation'):
            state.register_review(durable, {'review_artifact': failed_item}, package.head)
    assert state.next_stage(durable, package.head, path)['stage'] == 'recovery_reconciliation'
    monkeypatch.setattr(sys, 'argv', [*argv, '--reconcile'])
    changed_scope = path.parent / 'changed-scope.txt'
    changed_scope.write_text('Different requested scope')
    with monkeypatch.context() as changed:
        changed.setattr(sys, 'argv', [*argv, '--reconcile', '--scope-file', str(changed_scope)])
        with pytest.raises(SystemExit) as stopped:
            state.main()
        assert stopped.value.code == 2
    proof_bytes = proof_path.read_bytes()
    write(proof_path, {})
    with pytest.raises(SystemExit) as stopped:
        state.main()
    assert stopped.value.code == 2
    proof_path.write_bytes(proof_bytes)
    state.main()
    capsys.readouterr()
    durable = read(path, {})
    assert len(durable['infrastructure_recoveries']) == 1
    assert [item['attempt'] for item in durable['review_sessions']] == [1, 2]
    assert read(target, {})['generation'] == generation
    assert read(proof_path, {}) == before_session
    # Idempotent reconciliation validates the existing charge, without replaying
    # recovery or moving the generation again.
    state.main()
    assert read(path, {}) == durable
    capsys.readouterr()
    with pending.execution():
        pending.launch('example')
        item = pending.finish({'complete': True, 'findings': []})
    assert state.register_review(durable, {'review_artifact': item}, package.head)['attempt'] == 2


@pytest.mark.parametrize('damage', ['missing_receipt', 'changed_receipt', 'changed_proof'])
def test_damaged_activation_blocks_execution_finish_and_result_acceptance(allocated, monkeypatch, damage):
    package, prepared, record, path, binding = allocated
    original, _ = prelaunch_failure(allocated, monkeypatch)
    recovery = recover(allocated)
    resumed = session_for(allocated, resume=True)
    with resumed.execution():
        resumed.launch('example')
        item = resumed.finish({'complete': True, 'findings': []})
    state.register_review(record, {'review_artifact': item}, package.head)
    target = Path(recovery['proof']['path'] if damage == 'changed_proof' else recovery['activation']['path'])
    if damage == 'missing_receipt':
        target.unlink()
    else:
        write(target, {})
    with pytest.raises(ValueError, match='activation'), resumed.execution():
        pass
    with pytest.raises(ValueError, match='activation'):
        resumed.finish({'complete': True, 'findings': []})
    with pytest.raises(ValueError, match='activation'):
        state.register_review(record, {'review_artifact': item}, package.head)
    with pytest.raises(ValueError, match='activation'):
        state.current_review(record, prepared)


@pytest.mark.parametrize('initialized', [False, True])
@pytest.mark.parametrize('cycle', [1, 5])
def test_next_guides_unused_sessions_to_initial_launch_without_spending_recovery(allocated, initialized, cycle):
    package, prepared, record, path, binding = allocated
    for _ in range(1, cycle):
        state.next_cycle(record, package.head)
    binding = state.review_session(record, package.head, path)
    if initialized:
        unused = session_for((*allocated[:4], binding))
    before = copy.deepcopy(record)
    stage = state.next_stage(record, package.head, path)
    assert stage['stage'] == 'review'
    assert stage['action']['argv'][-3:] == ['review-session', '--head', package.head]
    assert stage['session_file'] == binding['session_file'] and stage['resume'] == initialized
    assert record == before and not record['infrastructure_recoveries']
    if initialized and cycle == 5:
        with unused.execution():
            pass
        assert state.next_stage(record, package.head, path)['stage'] == 'prelaunch_recovery'
        recovery = recover((*allocated[:4], binding))
        ready = state.next_stage(record, package.head, path)
        assert recovery['resume'] and ready['stage'] == 'review' and ready['resume']
        with session_for((*allocated[:4], binding), resume=True).execution():
            pass
        assert state.next_stage(record, package.head, path)['stage'] == 'prelaunch_limitation'


def test_usage_retry_prelaunch_failure_is_ineligible_and_has_visible_guidance(allocated, monkeypatch):
    package, prepared, record, path, binding = allocated
    monkeypatch.setattr(runner, 'run_skill_reviewer', lambda *_:
        {'status': 'tokens_exhausted', 'findings': [], 'message': 'usage limit'})
    session = session_for(allocated)
    with session.execution():
        result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=session)
        item = session.finish(result)
    state.register_review(record, {'review_artifact': item}, package.head)
    state.retry_exhausted_cycle(record, package.head)
    retry = state.review_session(record, package.head, path)
    with session_for((*allocated[:4], retry)).execution():
        pass
    assert state.cycle_status(record)['substantive'] == 1
    stage = state.next_stage(record, package.head, path)
    assert stage['stage'] == 'prelaunch_limitation' and 'usage-retried' in stage['message']
    with pytest.raises(ValueError, match='usage-retried'):
        state.recover_cycle(record, package.head, path)


@pytest.mark.parametrize('status,expected', [('completed', 'completed'), ('failed', 'provider_failed')])
def test_legacy_known_state_does_not_invent_launch_evidence(allocated, status, expected):
    package, prepared, record, path, binding = allocated
    write(Path(binding['session_file']), {'status': 'completed' if status == 'completed' else 'incomplete',
        'reviews': {'example': {'status': status}}})
    report = state.cycle_status(record)
    item = report['cycles'][0]['sessions'][0]
    assert item['state'] == expected
    assert not item['launch_intent'] and not item['proved_unlaunched'] and report['substantive'] == 0


@pytest.mark.parametrize('hardlink', [False, True])
def test_recovery_rejects_mutation_lock_alias_without_truncating_evidence(allocated, monkeypatch, hardlink):
    package, prepared, record, path, binding = allocated
    original, item = prelaunch_failure(allocated, monkeypatch)
    before = original.path.read_bytes()
    result_bytes = Path(item['path']).read_bytes()
    lock = original.path.with_name(original.path.name + '.lock')
    lock.unlink()
    if hardlink:
        lock.hardlink_to(original.path)
    else:
        lock.symlink_to(original.path)
    with pytest.raises(ValueError, match='distinct files|existing data'):
        state.recover_cycle(record, package.head, path)
    assert original.path.read_bytes() == before and Path(item['path']).read_bytes() == result_bytes
    assert not record['infrastructure_recoveries']


@pytest.mark.parametrize('damage', ['array', 'history', 'reviews', 'json', 'missing', 'unreadable'])
def test_malformed_session_has_unknown_accounting_and_restore_guidance(tmp_path, monkeypatch, damage):
    target = tmp_path / 'session.json'
    values = {'array': [], 'history': {'execution_history': ['invalid']}, 'reviews': {'reviews': []}}
    if damage in values:
        write(target, values[damage])
    elif damage == 'json':
        target.write_text('{ invalid json')
    elif damage == 'unreadable':
        read_original = state.read
        def unavailable(path, default):
            if path == target:
                raise OSError('unreadable session')
            return read_original(path, default)
        monkeypatch.setattr(state, 'read', unavailable)
    head = 'a' * 40
    record = {'iteration_run': {'id': 'run', 'contract_version': 3, 'status': 'active'},
        'cycles': [{'number': 1, 'head': head}], 'review_results': [],
        'preparation': {'path': 'prepared'}, 'review_sessions': [{
            'iteration_run_id': 'run', 'cycle': 1, 'attempt': 1, 'head': head, 'session_file': str(target)}]}
    monkeypatch.setattr(state, 'work_gate', lambda *_: {})
    before = copy.deepcopy(record)
    status = state.cycle_status(record)
    entry = status['cycles'][0]['sessions'][0]
    assert entry['state'] == 'unknown' and not entry['launch_intent'] and not entry['proved_unlaunched']
    stage = state.next_stage(record, head, tmp_path / 'run.json')
    assert stage['stage'] == 'review_evidence' and 'Restore' in stage['action']['task']
    assert record == before


@pytest.mark.parametrize('cycle', [1, 5])
def test_changed_head_unused_reservation_gets_fenced_recovery_guidance(allocated, cycle):
    package, prepared, record, path, binding = allocated
    for _ in range(1, cycle):
        state.next_cycle(record, package.head)
    binding = state.review_session(record, package.head, path)
    write(path, record)
    (package.repo / 'transform.py').write_text('def transform(value):\n    return value + 1\n')
    prep.git(package.repo, 'add', '.')
    prep.git(package.repo, 'commit', '-qm', 'Change prepared head')
    package.write('reviewer.json', descriptor('example', package.repo / 'transform.py'))
    refresh_prepared(path, package.context, prepared)
    record = read(path, {})
    assert state.next_stage(record, package.context['head_sha'], path)['stage'] == 'prelaunch_recovery'
    recovered = recover((package, prepared, record, path, binding))
    stage = state.next_stage(record, package.context['head_sha'], path)
    assert stage['stage'] == 'review' and not stage['resume']
    assert recovered['session_file'] != binding['session_file'] and len(record['cycles']) == cycle
    assert state.review_session(record, package.context['head_sha'], path)['session_file'] == stage['session_file']


def test_symlinked_private_root_recovery_and_retained_path_comparisons(allocated, tmp_path, monkeypatch):
    package, prepared, record, path, binding = allocated
    real_root = Path(record['iteration_run']['workflow_dir'])
    alias = tmp_path / 'private-alias'
    alias.symlink_to(real_root, target_is_directory=True)
    record['iteration_run']['workflow_dir'] = str(alias)
    binding['session_file'] = str(alias / Path(binding['session_file']).relative_to(real_root))
    historical_path = binding['session_file']
    original, failed_item = prelaunch_failure(allocated, monkeypatch)
    recovery = recover(allocated)
    assert recovery['resume'] and recovery['session_file'] == str(original.path.resolve())
    assert binding['session_file'] == historical_path
    proof_bytes = Path(recovery['proof']['path']).read_bytes()
    receipt_bytes = Path(recovery['activation']['path']).read_bytes()
    review_sessions.require_activation(read(original.path, {}), Path(historical_path))
    state.recover_cycle_file(path, package.head, reconcile=True)
    assert state.next_stage(record, package.head, path)['stage'] == 'review'
    resumed = session_for(allocated, resume=True)
    with resumed.execution():
        resumed.launch('example')
        item = resumed.finish({'complete': True, 'findings': []})
    assert state.register_review(record, {'review_artifact': item}, package.head)['attempt'] == 2
    assert state.current_review(record, prepared)[1]['result']['complete']
    assert Path(recovery['proof']['path']).read_bytes() == proof_bytes
    assert Path(recovery['activation']['path']).read_bytes() == receipt_bytes
    state.next_cycle(record, package.head)
    assert str(Path(record['review_sessions'][-1]['session_file']).resolve()) == record['review_sessions'][-1]['session_file']


@pytest.mark.parametrize('artifact', ['proof', 'receipt'])
def test_derived_artifact_lock_alias_preserves_original_failure_evidence(allocated, monkeypatch, artifact):
    package, prepared, record, path, binding = allocated
    original, item = prelaunch_failure(allocated, monkeypatch)
    result_path = Path(item['path'])
    if artifact == 'proof':
        saved = read(original.path, {})
        target = original.path.parent / 'prelaunch-proofs' / (review_coverage.digest(saved) + '.json')
        operation = lambda: state.recover_cycle(record, package.head, path)
    else:
        write(path, record)
        recovery = state.mutate(path, {}, lambda value: state.recover_cycle(value, package.head, path))
        target = Path(recovery['activation']['path'])
        operation = lambda: state.recover_cycle_file(path, package.head, reconcile=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(target.name + '.lock')
    if artifact == 'proof':
        lock.symlink_to(result_path)
    else:
        lock.hardlink_to(result_path)
    retained = [original.path, result_path]
    if artifact == 'receipt':
        retained.append(Path(recovery['proof']['path']))
    before = {p: p.read_bytes() for p in retained}
    with pytest.raises(ValueError, match='lock.*existing data|distinct files'):
        operation()
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not target.exists()


@pytest.mark.parametrize('intent', [False, True])
def test_undrained_interruption_guides_exact_resume_without_recovery_or_owner_bypass(allocated, monkeypatch, intent):
    package, prepared, record, path, binding = allocated
    session = session_for(allocated)
    with session.execution():
        if intent:
            session.launch('example')
    saved = read(session.path, {})
    saved['execution_history'][-1]['status'] = 'active'
    write(session.path, saved)
    before = copy.deepcopy(record)
    status = state.cycle_status(record)
    item = status['cycles'][0]['sessions'][0]
    assert item['state'] == 'interrupted' and item['launch_intent'] == intent
    assert not item['proved_unlaunched'] and status['substantive'] == int(intent)
    stage = state.next_stage(record, package.head, path)
    assert stage['stage'] == 'review' and stage['resume'] and stage['session_file'] == str(session.path)
    assert record == before
    with pytest.raises(ValueError, match='uncertain'):
        state.recover_cycle(record, package.head, path)
    if not intent:
        monkeypatch.setattr(runner, 'describe_reviewer', lambda *_:
            (_ for _ in ()).throw(OSError('descriptor unavailable on ordinary resume')))
        resumed = session_for(allocated, resume=True)
        with resumed.execution():
            result = runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=resumed)
            artifact = resumed.finish(result)
        state.register_review(record, {'review_artifact': artifact}, package.head)
        retained = read(session.path, {})
        assert retained['status'] == 'incomplete' and result['guard_failure']
        assert not result['executed_reviewers']
        assert [entry['status'] for entry in retained['execution_history']] == ['active', 'drained']
        status = state.cycle_status(record)
        item = status['cycles'][0]['sessions'][0]
        assert item['state'] == 'interrupted' and not item['proved_unlaunched'] and not item['launch_intent']
        stage = state.next_stage(record, package.head, path)
        assert stage['stage'] == 'review' and stage['resume'] and stage['session_file'] == str(session.path)
        with pytest.raises(ValueError, match='uncertain'):
            state.recover_cycle(record, package.head, path)
    resumed = session_for(allocated, resume=True)
    with resumed.execution():
        with pytest.raises(ValueError, match='already executing'):
            session_for(allocated, resume=True)
    assert not record['infrastructure_recoveries'] and len(record['cycles']) == 1


@pytest.mark.parametrize('missing', ['attempt', 'session_file'])
def test_missing_binding_fields_report_unknown_and_restore_without_invented_references(tmp_path, monkeypatch, missing):
    target = tmp_path / 'session.json'
    binding = {'iteration_run_id': 'run', 'cycle': 1, 'attempt': 1,
               'session_file': str(target), 'head': 'a' * 40}
    binding.pop(missing)
    record = {'iteration_run': {'id': 'run', 'contract_version': 3, 'status': 'active'},
        'cycles': [{'number': 1, 'head': 'a' * 40}], 'review_sessions': [binding],
        'review_results': [], 'preparation': {'path': 'ready'}}
    monkeypatch.setattr(state, 'work_gate', lambda *_: {})
    before = copy.deepcopy(record)
    item = state.cycle_status(record)['cycles'][0]['sessions'][0]
    assert item['state'] == 'unknown' and item[missing] is None
    assert not item['launch_intent'] and not item['proved_unlaunched']
    run_path = tmp_path / 'run.json'
    stage = state.next_stage(record, 'a' * 40, run_path)
    assert stage['stage'] == 'review_evidence'
    assert stage['required_artifacts'] == ([str(run_path)] if missing == 'session_file' else [str(run_path), str(target)])
    assert record == before
