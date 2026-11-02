"""Fresh-contract regressions with real Git and deterministic provider budgets."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import patch

import pytest

from test_review_coverage import state, Package, analysis, prep, descriptor
from shared import architecture, cleanup_precheck, preparation_refresh, review_sessions
from shared import review_reconciliation, workflow_timings
from shared.ci_observation import Observation, PermissionDenied
from shared.private_state import read, write

spec = importlib.util.spec_from_file_location('efficiency_runner',
    Path(__file__).parents[2] / 'dk-pr-review/scripts/run_reviewers.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
REAL_REVIEWER = runner.run_skill_reviewer
ci_spec = importlib.util.spec_from_file_location('efficiency_ci',
    Path(__file__).parents[1] / 'scripts/ci_wait.py')
ci = importlib.util.module_from_spec(ci_spec)
ci_spec.loader.exec_module(ci)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    p = Package(tmp_path)
    record = {}
    state.start_run(record)
    base = p.head
    prep.git(p.repo, 'commit', '--allow-empty', '-qm', 'One coherent reviewed change')
    p.head = prep.git(p.repo, 'rev-parse', 'HEAD')
    p.context['head_sha'] = p.decisions['head_sha'] = p.head
    p.write('context.json', p.context)
    p.write('decisions.json', p.decisions)
    inline = {'schemaVersion': 1, 'complete': True,
        'pullRequest': {'url': p.context['url'], 'headRefOid': p.head},
        'threads': [], 'threadCount': 0, 'threadsSha256': prep.digest([]),
        'items': [], 'itemCount': 0, 'itemsSha256': prep.digest([])}
    conversation = {'complete': True, 'pullRequest': inline['pullRequest'],
        'reviews': [], 'reviewCount': 0, 'comments': [], 'commentCount': 0, 'items': [], 'itemCount': 0}
    conversation['sha256'] = prep.digest(conversation)
    p.write('inline.json', inline)
    p.write('conversation.json', conversation)
    p.write('history.json', state.preparation_history(record))
    names = ['one', 'two']
    p.manifest.update(mode='iteration')
    p.manifest['evidence'].update(feedback=['inline.json', 'conversation.json'],
        run_history=['history.json'], choices=['decisions.json'],
        reviewers=[str(p.write(name + '.json', {**descriptor(name, p.repo / 'transform.py'),
            **({'nested_review_roles': ['Toolkit', 'Standards']} if name == 'one' else {})})) for name in names])
    p.write('manifest.json', p.manifest)
    saved = p.prepare(analysis(names))
    prepared = prep.read(saved['artifact'])
    state.attach_preparation(record, saved['artifact'], p.context['url'], p.head)
    path = p.write('run.json', record)
    calls = []
    monkeypatch.setattr(runner, 'describe_reviewer', lambda name: prepared['reviewers'][name])
    monkeypatch.setattr(runner, 'skill_path', lambda name: Path('command'))
    def provider(name, *args):
        calls.append(name)
        return {'findings': []}
    monkeypatch.setattr(runner, 'run_skill_reviewer', provider)
    return p, prepared, path, names, calls, base


def dispatch(fresh, *, resume=False):
    p, prepared, path, names, calls, base = fresh
    record = read(path, {})
    if not resume:
        state.next_cycle(record, p.head)
    binding = state.review_session(record, p.head, path)
    write(path, record)
    session = review_sessions.Session(p.context, prepared, names, path=binding['session_file'], resume=resume)
    with session.execution():
        result = runner.review(p.context, names, '', p.decisions, '', prepared, session=session)
        artifact = session.finish(result)
    state.mutate(path, {}, lambda d: state.register_review(d, {'review_artifact': artifact}, p.head))
    return result, session


def refresh(fresh):
    p, _, path, *_ = fresh
    current = read(path, {})['preparation']['path']
    return preparation_refresh.refresh(path, current, p.root / 'context.json', p.root / 'decisions.json',
        p.root / 'inline.json', p.root / 'conversation.json', state.reconciliation_api())


def cleanup_record(fresh, kind, refs):
    p, _, path, *_ = fresh
    selected = read(path, {})
    prepared = prep.read(selected['preparation']['path'])
    refs = {'preparation': selected['preparation']['path'], **refs}
    value = {'cleanup_version': 1, 'kind': kind, 'run_id': selected['iteration_run']['id'],
        'head_sha': p.head, 'worker': 'cleanup-worker', 'preparation_id': prepared['preparation_id'],
        'engineering_id': prep.engineering_id(prepared),
        'artifacts': {key: {'path': str(value), 'sha256': prep.file_hash(value)} for key, value in refs.items()}}
    source = p.write('cleanup-' + kind + '.json', value)
    return state.mutate(path, {}, lambda d: state.record_operational(d, 'cleanup', source))


def test_pr89_noop_cleanup_preserves_identity_and_zero_later_model_calls(fresh, monkeypatch):
    p, original, path, names, calls, base = fresh
    architecture_calls = []
    original_review = architecture.review
    def review(*args, **kwargs):
        architecture_calls.append('architecture')
        return original_review(*args, **kwargs)
    monkeypatch.setattr(architecture, 'review', review)
    result, session = dispatch(fresh)
    assert result['complete'] and sorted(calls) == names
    cleanup_record(fresh, 'handoff', {})
    assert refresh(fresh)['model_call_count'] == 0
    branch = prep.git(p.repo, 'symbolic-ref', '--short', 'HEAD')
    remote = p.root / 'remote.git'
    prep.git(p.repo, 'init', '--bare', '-q', str(remote))
    prep.git(p.repo, 'remote', 'add', 'origin', str(remote))
    prep.git(p.repo, 'push', '-qu', 'origin', branch)
    recovery = 'refs/pr-split-recovery/' + p.head
    prep.git(p.repo, 'update-ref', recovery, p.head)
    tree = prep.git(p.repo, 'rev-parse', 'HEAD^{tree}')
    guard = {'mode': 'regroup', 'pr_url': p.context['url'], 'branch': branch, 'remote': 'origin',
        'remote_branch': branch, 'remote_sha': p.head, 'original_head': p.head, 'original_tree': tree,
        'base': base, 'target_tip': base, 'target_branch': 'main', 'head_repo': 'example/fixture',
        'recovery_ref': recovery}
    guard_path = p.repo / '.git/pr-split-state.json'
    write(guard_path, guard)
    cleanup_record(fresh, 'prepared', {'guard': guard_path})
    assert refresh(fresh)['model_call_count'] == 0
    assessment = {'schema_version': 1, 'pr_url': p.context['url'], 'head_repo': 'example/fixture',
        'reviewed_head': p.head, 'reviewed_tree': tree, 'target_tip': base, 'target_branch': 'main',
        'final_head': p.head, 'outcome': 'no_op', 'commit_count': 1, 'reasons': ['One coherent change']}
    monkeypatch.setattr(review_reconciliation.series, 'live_snapshot', lambda *args:
        {**assessment, 'final_tree': tree})
    with patch.object(review_reconciliation, 'execute_json') as comparison:
        receipt = cleanup_precheck.precheck(path, p.head, p.head, guard_path, state.reconciliation_api())
        assert receipt['proof_kind'] == 'exact_identity'
        cleanup_precheck.validate(receipt['precheck']['path'], guard_path, p.head, state.reconciliation_api())
        comparison.assert_not_called()
    assessment_path = p.write('assessment.json', assessment)
    cleanup_record(fresh, 'assessment', {'guard': guard_path, 'assessment': assessment_path})
    updated = refresh(fresh)
    assert updated['engineering_id'] == original['engineering_id'] and updated['model_call_count'] == 0
    prepared = prep.read(updated['artifact'])
    assert state.verify_review(path, prepared, assessment=assessment, assessment_path=assessment_path)
    assert sorted(calls) == names and architecture_calls == ['architecture']
    assert len(read(path, {})['cycles']) == 1


def test_architecture_and_configured_roles_overlap_and_recovery_reuses_success(fresh, monkeypatch):
    p, prepared, path, names, calls, base = fresh
    barrier = threading.Barrier(3)
    actual = architecture.review
    def arch(*args, **kwargs):
        barrier.wait(timeout=5)
        return actual(*args, **kwargs)
    def provider(name, *args):
        calls.append(name)
        barrier.wait(timeout=5)
        if name == 'two':
            raise subprocess.TimeoutExpired(['fake-provider'], 1)
        return {'findings': []}
    monkeypatch.setattr(architecture, 'review', arch)
    monkeypatch.setattr(runner, 'run_skill_reviewer', provider)
    result, session = dispatch(fresh)
    assert not result['complete'] and result['role_failures'][0]['diagnostic']['recovery_eligible']
    original = copy.deepcopy(read(session.path, {}))
    assert state.next_stage(read(path, {}), p.head, path)['stage'] == 'infrastructure_recovery'
    recovery = state.recover_cycle_file(path, p.head)
    assert recovery['resume'] and recovery['activation']['payload']['failed_roles'] == ['two']
    assert read(Path(recovery['proof']['path']), {}) == original
    monkeypatch.setattr(architecture, 'review', lambda *a, **kw: pytest.fail('architecture reran'))
    monkeypatch.setattr(runner, 'run_skill_reviewer', lambda name, *a: (calls.append(name), {'findings': []})[1])
    result, resumed = dispatch(fresh, resume=True)
    assert result['complete'] and result['executed_reviewers'] == ['two']
    assert result['reused_reviewers'] == ['one'] and result['architecture_accounting']['reused'] == ['architecture-review']
    assert calls.count('one') == 1 and calls.count('two') == 2
    assert state.cycle_status(read(path, {}))['infrastructure_attempts'] == 1
    with pytest.raises(ValueError, match='already recorded'):
        state.recover_cycle_file(path, p.head)
    assert len(read(path, {})['cycles']) == 1


@pytest.mark.parametrize('failure', [RuntimeError('incomplete: timed out'), ValueError('malformed response')])
def test_freeform_or_semantic_role_failures_do_not_gain_recovery(fresh, monkeypatch, failure):
    p, _, path, *_ = fresh
    monkeypatch.setattr(runner, 'run_skill_reviewer', lambda *a: (_ for _ in ()).throw(failure))
    dispatch(fresh)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='structured infrastructure'):
        state.recover_cycle_file(path, p.head)
    assert path.read_bytes() == before


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


def test_ci_deadline_extension_restart_timeout_new_head_and_permission_denial(tmp_path):
    clock, path, events = Clock(), tmp_path / 'ci.json', []
    head = 'a' * 40
    result = {'result': 'pending', 'head': head, 'checks': [{'name': 'unit', 'bucket': 'pending', 'link': 'run/1'}]}
    kwargs = dict(timeout=30, extension=30, clock=clock)
    with Observation(path, 'pr', head, **kwargs) as observation:
        first = observation.update(result)
        clock.now = 30
        assert observation.update(result, events.append)['remaining_seconds'] == 30
    with Observation(path, 'pr', head, **kwargs) as resumed:
        assert resumed.update(result)['observation_id'] == first['observation_id']
        clock.now = 60
        timed = ci.wait('pr', head, timeout=30, interval=30, clock=clock, sleep=clock.sleep,
            observer=lambda *a: result, observation=resumed)
        assert timed['result'] == 'timeout' and timed['extensions'] == 1
        assert timed['pending_checks'] == result['checks'] and len(events) == 1
        with pytest.raises(BlockingIOError), Observation(path, 'pr', head, **kwargs):
            pass
        resumed.denied(PermissionDenied('HTTP 403'))
    with pytest.raises(PermissionDenied), Observation(path, 'pr', head, **kwargs):
        pass
    with Observation(path, 'pr', head, retry_denied=True, **kwargs) as repaired:
        assert repaired.update(result)['remaining_seconds'] == 0
    with Observation(path, 'pr', 'b' * 40, **kwargs) as changed:
        assert changed.current['deadline'] == 90 and changed.current['extensions'] == 0


def test_timing_union_and_transport_calls_are_not_inclusive(tmp_path):
    clock = Clock()
    with workflow_timings.span('pr', 'preparation', clock=clock):
        clock.now = 1
        with workflow_timings.span('pr', 'architecture', clock=clock):
            workflow_timings.model_call()
            clock.now = 3
        clock.now = 4
    report = workflow_timings.query('pr')
    assert report['elapsed_seconds'] == report['active_seconds'] == 4
    assert report['model_calls'] == 1 and report['unknown_spans'] == []
    assert report['overlapping_seconds'] == 0


def test_owned_refresh_rejects_structural_replacements_without_model(fresh):
    p, prepared, path, *_ = fresh
    with pytest.raises(ValueError, match='additions/removals'):
        preparation_refresh.refresh(path, read(path, {})['preparation']['path'], p.root / 'context.json',
            p.root / 'decisions.json', p.root / 'inline.json', p.root / 'conversation.json',
            state.reconciliation_api(), replacements={'unknown/0': str(p.root / 'user.json')})


def test_declared_nested_roles_receive_distinct_prematerialized_packets(fresh):
    p, _, path, names, *_ = fresh
    result, session = dispatch(fresh)
    saved = read(session.path, {})
    handoff = read(Path(saved['deliveries']['one']['path']), {})
    packets = handoff['nested_architecture_packets']
    assert set(packets) == {'Toolkit', 'Standards'}
    for role, reference in packets.items():
        delivered = architecture.validate_packet(reference, p.repo, agent=role)
        assert delivered['agent'] != architecture.load(handoff['architecture_packet'])['agent']
        with patch.object(architecture, 'retain', side_effect=AssertionError('sandbox write')):
            assert architecture.provider_packet(reference, p.repo, role, delivered['paths']) == reference
    with pytest.raises(ValueError, match='recipient'):
        architecture.provider_packet(handoff['architecture_packet'], p.repo, 'undeclared')


@pytest.mark.parametrize('damage', ['checkpoint', 'delivery'])
def test_role_recovery_rejects_corrupt_success_or_delivery(fresh, monkeypatch, damage):
    p, _, path, *_ = fresh
    monkeypatch.setattr(runner, 'run_skill_reviewer', lambda name, *a:
        (_ for _ in ()).throw(subprocess.TimeoutExpired(['fake'], 1)) if name == 'two' else {'findings': []})
    _, session = dispatch(fresh)
    saved = read(session.path, {})
    ref = saved['reviews']['one']['checkpoint'] if damage == 'checkpoint' else saved['deliveries']['one']
    Path(ref['path']).chmod(0o600)
    Path(ref['path']).write_text('{}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        state.recover_cycle_file(path, p.head)
    assert path.read_bytes() == before


def test_changed_constraint_requires_reassessment_and_leaves_parent_attached(fresh):
    p, _, path, *_ = fresh
    original = read(path, {})['preparation']
    p.write('user.json', {'instruction': 'Require a new external-service integration'})
    called = []
    def analyst(*args):
        called.append(True)
        raise RuntimeError('reassessment required')
    with pytest.raises(RuntimeError, match='reassessment required'):
        preparation_refresh.refresh(path, original['path'], p.root / 'context.json',
            p.root / 'decisions.json', p.root / 'inline.json', p.root / 'conversation.json',
            state.reconciliation_api(), analyzer=analyst)
    assert called == [True] and read(path, {})['preparation'] == original


def test_role_recovery_limits_are_shared_and_persist_across_cycles(fresh, monkeypatch):
    p, _, path, *_ = fresh
    monkeypatch.setattr(runner, 'run_skill_reviewer', lambda name, *a:
        (_ for _ in ()).throw(subprocess.TimeoutExpired(['fake'], 1)) if name == 'two' else {'findings': []})
    for cycle in (1, 2):
        dispatch(fresh)
        state.recover_cycle_file(path, p.head)
        dispatch(fresh, resume=True)
        with pytest.raises(ValueError, match='already recorded'):
            state.recover_cycle_file(path, p.head)
    dispatch(fresh)
    with pytest.raises(ValueError, match='two infrastructure recoveries exhausted'):
        state.recover_cycle_file(path, p.head)
    status = state.cycle_status(read(path, {}))
    assert status['allocated'] == 3 and status['infrastructure_attempts'] == 2


@pytest.mark.parametrize('relocate', [False, True])
def test_transport_recovery_preserves_the_consumed_correction_slot(fresh, monkeypatch, relocate):
    p, _, path, *_ = fresh
    attempts = []
    failed = False
    def transport(name, handoff, checkout, prompt, attempt=0):
        nonlocal failed
        if name == 'one':
            return '{"findings":[]}'
        attempts.append(attempt)
        if attempt == 0:
            return 'malformed response'
        if not failed:
            failed = True
            raise subprocess.TimeoutExpired(['fake'], 1)
        return '{"findings":[]}'
    monkeypatch.setattr(runner, 'run_skill_reviewer', REAL_REVIEWER)
    monkeypatch.setattr(runner, 'invoke', transport)
    first, _ = dispatch(fresh)
    assert not first['complete'] and attempts == [0, 1]
    if relocate:
        updated = refresh(fresh)
        assert updated['model_call_count'] == 0
        current = prep.read(updated['artifact'])
        fresh = (fresh[0], current, *fresh[2:])
    state.recover_cycle_file(path, p.head)
    resumed, _ = dispatch(fresh, resume=True)
    assert resumed['complete'] and attempts == [0, 1, 1]


def test_ci_verified_rerun_is_one_new_window_and_old_head_cannot_reset(tmp_path, monkeypatch):
    clock, path = Clock(), tmp_path / 'ci.json'
    kwargs = dict(timeout=30, extension=30, clock=clock)
    head = 'a' * 40
    with Observation(path, 'pr', head, **kwargs) as first:
        deadline = first.current['deadline']
    clock.now = 90
    with Observation(path, 'pr', 'b' * 40, **kwargs):
        pass
    with Observation(path, 'pr', head, **kwargs) as returned:
        assert returned.current['deadline'] == deadline
    operation = {'status': 'verified', 'request': {'target': 'pr', 'approved':
        {'head': head, 'attempt': 1, 'run_id': '42'}}, 'receipt': {'head': head, 'attempt': 2, 'run_id': 42}}
    monkeypatch.setattr('shared.ci_observation.publication.load', lambda *a: operation)
    with Observation(path, 'pr', head, rerun='verified-operation', **kwargs) as rerun:
        assert rerun.current['deadline'] == 120
    operation['diagnostic_path'] = 'relocated private receipt'
    clock.now = 150
    with Observation(path, 'pr', head, rerun='verified-operation', **kwargs) as resumed:
        assert resumed.current['deadline'] == 120
    operation['status'] = 'pending'
    with pytest.raises(ValueError, match='verified'), Observation(path, 'pr', head, rerun='pending', **kwargs):
        pass


def test_ci_read_denial_is_persisted_before_another_observer_can_run(tmp_path):
    clock, path, calls = Clock(), tmp_path / 'ci.json', []
    def denied(*args):
        calls.append(True)
        raise PermissionDenied('HTTP 403')
    with Observation(path, 'pr', 'a' * 40, clock=clock) as observation:
        with pytest.raises(PermissionDenied):
            ci.wait('pr', 'a' * 40, observer=denied, observation=observation, clock=clock)
    with pytest.raises(PermissionDenied), Observation(path, 'pr', 'a' * 40, clock=clock):
        pytest.fail('denied observer was restarted')
    assert calls == [True]
    saved = read(path, {})
    saved['current']['extensions'] = 2
    write(path, saved)
    with pytest.raises(ValueError, match='deadline evidence'), Observation(path, 'pr', 'a' * 40, clock=clock):
        pass


def test_timing_storage_failure_does_not_change_the_operation_result(monkeypatch):
    monkeypatch.setattr(workflow_timings, 'mutate', lambda *a: (_ for _ in ()).throw(KeyError('bad diagnostics')))
    with workflow_timings.span('pr', 'completion'):
        result = 'clean'
    assert result == 'clean'


def test_concurrent_timing_intervals_count_wall_time_once():
    for role in ('one', 'two', 'architecture'):
        workflow_timings.save('overlap', {'id': role, 'role': role, 'parent': None,
            'start': 0, 'end': 10, 'model_calls': 1, 'retry': False, 'reused': False})
    report = workflow_timings.query('overlap')
    assert report['active_seconds'] == report['elapsed_seconds'] == report['overlapping_seconds'] == 10
    assert report['model_calls'] == 3
