"""Passive workflow accounting preserves execution, reuse and unknown evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import reviewer_telemetry, workflow_usage as telemetry


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    for name in ('DK_WORKFLOW_USAGE', 'DK_UPDATE_PARENT', 'DK_REVIEW_STATS_TRACE'):
        monkeypatch.delenv(name, raising=False)
    def start(token='root-token', skill='dk-pr-iter', parent=None):
        receipt = {'token': token, 'skill': skill, 'host': 'codex',
                   'runtime_digest': 'digest', 'runtime_root': str(ROOT),
                   'invocation': str(tmp_path / (token + '.json')),
                   'lifecycle': {'identity': {'name': 'dk-workflows', 'version': '1.7.3'}}}
        ctx = telemetry.register(receipt, tmp_path, parent)
        assert ctx is not None
        return receipt, ctx
    return start


def records(path, kind):
    return [item for item in telemetry.query(path)['events'] if item['kind'] == kind]


@pytest.mark.parametrize('skill,stage', [('dk-pr-iter', 'coordination'), ('dk-pr-review', 'review')])
def test_standalone_and_iteration_register_private_stable_workflow(lifecycle, skill, stage):
    receipt, ctx = lifecycle(skill=skill)
    assert telemetry.register(receipt, Path(receipt['invocation']).parent) == ctx
    report = telemetry.query(ctx['path'])
    assert report['workflow_id'] == ctx['workflow_id']
    assert report['completed_workflows'] == 0 and report['workflow_outcome'] == 'unknown'
    assert len(records(ctx['path'], 'invocation')) == 1
    invocation = records(ctx['path'], 'invocation')[0]
    assert invocation['stage'] == stage
    assert invocation['plugin_version'] == '1.7.3' and invocation['runtime_digest'] == 'digest'
    assert invocation['host_usage'] is invocation['host_model'] is invocation['host_effort'] is None
    assert stat.S_IMODE(Path(ctx['path']).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(ctx['path']).parent.stat().st_mode) == 0o700
    assert telemetry.context({'DK_UPDATE_PARENT': receipt['token']}) == ctx
    assert telemetry.context({telemetry.ENV: json.dumps(ctx)}) == ctx


def test_children_share_observed_root_without_merging_unrelated_workflows(lifecycle):
    root, ctx = lifecycle()
    child, child_ctx = lifecycle('child-token', 'dk-pr-review', root['token'])
    _, other_ctx = lifecycle('unrelated-token', 'dk-pr-review')
    assert child_ctx['workflow_id'] == ctx['workflow_id']
    assert child_ctx['parent_invocation_id'] == root['token']
    assert other_ctx['workflow_id'] != ctx['workflow_id']
    assert {item['invocation_id'] for item in records(ctx['path'], 'invocation')} == {root['token'], child['token']}


def test_repository_group_keeps_unmeasured_host_with_its_measured_provider(lifecycle):
    _, ctx = lifecycle()
    assert ctx['repository'] is not None
    telemetry.execution(ctx, {'execution_id': 'measured-provider', 'status': 'returned',
        'provider': 'codex', 'inclusive': True, 'scope': 'execution',
        'usage': {'input_tokens': 90, 'output_tokens': 10, 'total_tokens': 100}})
    report = telemetry.query(ctx['path'], groups=('repository',))
    assert len(report['usage']['groups']) == 1
    group = report['usage']['groups'][0]
    assert group['key'] == {'repository': ctx['repository']}
    assert group['complete'] is False
    assert group['missing_attempts'] == 1
    total = group['tokens']['total_tokens']
    assert total['known_subtotal'] == 100
    assert total['measured_attempts'] == total['missing_attempts'] == 1
    assert total['complete'] is False


def test_inclusive_parent_and_nested_child_preserve_known_partial_usage(lifecycle, monkeypatch):
    _, ctx = lifecycle()
    telemetry.execution(ctx, {'execution_id': 'parent', 'status': 'returned',
        'provider': 'codex', 'inclusive': True, 'scope': 'execution',
        'usage': {'input_tokens': 90, 'output_tokens': 10, 'total_tokens': 100}})
    telemetry.execution({**ctx, 'parent_execution_id': 'parent', 'role': 'spec'}, {
        'execution_id': 'child', 'parent_execution_id': 'parent', 'status': 'returned',
        'provider': 'codex', 'inclusive': True, 'scope': 'execution',
        'usage': {'input_tokens': 25, 'output_tokens': 5, 'total_tokens': 30}})
    monkeypatch.setenv(telemetry.ENV, json.dumps({**ctx, 'parent_execution_id': 'parent'}))
    child_receipt, _ = lifecycle('nested-host', 'dk-pr-review', 'root-token')
    report = telemetry.query(ctx['path'])
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] == 100
    assert report['usage']['excluded_records']['inclusive_children'] == 2
    assert report['usage']['complete'] is False  # The original host itself remains unmeasured.
    assert report['usage']['tokens']['total_tokens']['missing_attempts'] == 1
    assert child_receipt['token'] in report['unknown_host_invocations']


def test_interrupted_resume_reconciles_same_execution_and_keeps_status_history(lifecycle):
    _, ctx = lifecycle()
    running = {'execution_id': 'attempt-1', 'provider': 'codex', 'status': 'running',
               'scope': 'execution', 'started_at': '2026-01-01T00:00:00+00:00'}
    telemetry.execution(ctx, running)
    assert telemetry.query(ctx['path'])['execution_statuses']['running'] == 1
    telemetry.execution(ctx, {**running, 'status': 'interrupted', 'usage': {'total_tokens': 15},
                             'observed_at': '2026-01-01T00:00:01+00:00'})
    telemetry.execution(ctx, {**running, 'status': 'returned', 'usage': {'total_tokens': 20},
                             'observed_at': '2026-01-01T00:00:02+00:00'})
    report = telemetry.query(ctx['path'])
    assert report['execution_statuses']['running'] == 0
    assert report['execution_statuses']['returned'] == 1
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] == 20
    assert report['usage']['excluded_records']['duplicates'] == 2
    assert [item['status'] for item in report['events'] if item['kind'] == 'execution'] == [
        'running', 'interrupted', 'returned']


def test_checkpoint_reuse_and_incomplete_coverage_never_create_execution(lifecycle):
    _, ctx = lifecycle()
    telemetry.observe('checkpoint_reuse', context=ctx, status='reused', role='toolkit',
                      evidence={'session': 'session-1', 'head_sha': 'a' * 40})
    telemetry.observe('review_coverage', context=ctx, status='incomplete',
                      evidence={'executed_reviewers': [], 'reused_reviewers': ['toolkit']})
    report = telemetry.query(ctx['path'])
    assert report['checkpoint_reuses'] == 1
    assert report['review_coverage']['status'] == 'incomplete'
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] is None
    assert not records(ctx['path'], 'execution')
    assert report['completed_workflows'] == 0


def test_lifecycle_close_does_not_claim_workflow_completion(lifecycle):
    receipt, ctx = lifecycle()
    telemetry.close(receipt, 'finished responding')
    report = telemetry.query(ctx['path'])
    assert report['lifecycle_closed'] is True
    assert report['completed_workflows'] == 0
    assert report['workflow_outcome'] == 'unknown'
    telemetry.observe('run', context=ctx, status='limited', source='run_state',
                      evidence={'run_id': 'actual-run'})
    assert telemetry.query(ctx['path'])['workflow_outcome'] == 'limited'
    telemetry.observe('run', context=ctx, status='completed', source='run_state',
                      evidence={'run_id': 'actual-run'})
    report = telemetry.query(ctx['path'])
    assert report['completed_workflows'] == 1
    assert report['usage']['complete'] is False
    assert report['completion_source'] == 'run_state'


def test_helper_records_real_interruption_and_restores_role_context(lifecycle, monkeypatch):
    _, ctx = lifecycle()
    env = {'DK_UPDATE_PARENT': ctx['invocation_id']}
    with pytest.raises(KeyboardInterrupt):
        with telemetry.helper('ci-wait', [], ROOT, env) as event:
            supplied = telemetry.context(event['env'])
            assert supplied['stage'] == 'ci'
            raise KeyboardInterrupt()
    helper = records(ctx['path'], 'helper')[-1]
    assert helper['status'] == 'interrupted' and helper['duration_seconds'] >= 0
    assert env == {'DK_UPDATE_PARENT': ctx['invocation_id']}
    monkeypatch.setenv(telemetry.ENV, json.dumps(ctx))
    with telemetry.role('spec', evidence={'head_sha': 'b' * 40}):
        assert telemetry.context()['role'] == 'spec'
    assert telemetry.context()['role'] == ctx['role']


def test_concurrent_observations_are_not_lost(lifecycle):
    _, ctx = lifecycle()
    def worker(number):
        telemetry.observe('helper_call', context=ctx, status='returned', evidence={'number': number})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, range(30)))
    observed = records(ctx['path'], 'helper_call')
    assert sorted(item['evidence']['number'] for item in observed) == list(range(30))
    assert len({item['id'] for item in observed}) == 30


def test_missing_or_failed_collection_cannot_change_workflow_result(lifecycle, monkeypatch, capsys):
    _, ctx = lifecycle()
    def fail(*args, **kwargs):
        raise OSError('read-only evidence store')
    monkeypatch.setattr(telemetry, 'save', fail)
    assert telemetry.observe('run', context=ctx, status='completed') is None
    assert telemetry.execution(ctx, {'execution_id': 'lost', 'status': 'returned'}) is None
    with telemetry.helper('reviewers', [], ROOT, {telemetry.ENV: json.dumps(ctx)}) as event:
        assert event['status'] == 'returned'
    assert 'read-only evidence store' in capsys.readouterr().err
    assert telemetry.query(ctx['path'])['completed_workflows'] == 0


def test_repository_local_telemetry_is_rejected_without_creating_files(tmp_path, monkeypatch, capsys):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    monkeypatch.setenv('XDG_STATE_HOME', str(repo / 'private'))
    receipt = {'token': 'unsafe', 'skill': 'dk-pr-review', 'host': 'codex'}
    assert telemetry.register(receipt, repo) is None
    assert not (repo / 'private').exists()
    assert 'outside the plugin and repository' in capsys.readouterr().err


def test_invalid_context_never_redirects_evidence_to_arbitrary_path(lifecycle, tmp_path, capsys):
    _, ctx = lifecycle()
    invalid = {**ctx, 'path': str(tmp_path / 'unowned.json')}
    assert telemetry.context({telemetry.ENV: json.dumps(invalid)}) is None
    assert not Path(invalid['path']).exists()
    assert 'invalid workflow usage context' in capsys.readouterr().err


def test_capture_observes_standalone_usage_and_retains_available_metadata(lifecycle):
    _, ctx = lifecycle(skill='dk-pr-review')
    provider = SimpleNamespace(returncode=0, stdout='response', stderr='', model='observed-model',
        usage={'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14}, effort='high',
        compactions=2, tool_output_bytes=5,
        settings={'context_window': 200000}, settings_source='provider_result')
    seen = []
    def call(*args, **kwargs):
        seen.append(kwargs['env'])
        return provider
    result = reviewer_telemetry.capture(call, ['fake-provider'], env={telemetry.ENV: json.dumps(ctx)},
        telemetry_prompt='go', telemetry_attempt=1, telemetry_provider='codex')
    assert result is provider
    assert json.loads(seen[0][telemetry.ENV])['parent_execution_id']
    events = records(ctx['path'], 'execution')
    assert [event['status'] for event in events] == ['running', 'returned']
    final = events[-1]
    assert final['attempt'] == 1 and final['model'] == 'observed-model'
    assert final['compactions'] == 2 and final['tool_output_bytes'] == 5
    assert final['transport_output_bytes'] == len('response')
    assert final['effort'] == 'high' and final['duration_seconds'] >= 0
    assert final['settings'] == {'context_window': 200000}
    assert final['settings_source'] == 'provider_result'
    assert telemetry.query(ctx['path'])['usage']['tokens']['total_tokens']['known_subtotal'] == 14
    assert 'prompt' not in final and 'stdout' not in final and 'stderr' not in final


def test_nested_actual_captures_retain_parent_execution_identity(lifecycle):
    _, ctx = lifecycle()
    def inner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='', stderr='', usage={'total_tokens': 7})
    def outer(*args, **kwargs):
        reviewer_telemetry.capture(inner, ['nested-provider'], env=kwargs['env'],
            telemetry_prompt='child', telemetry_attempt=0, telemetry_provider='codex')
        return SimpleNamespace(returncode=0, stdout='', stderr='', usage={'total_tokens': 20})
    reviewer_telemetry.capture(outer, ['provider'], env={telemetry.ENV: json.dumps(ctx)},
        telemetry_prompt='root', telemetry_attempt=0, telemetry_provider='codex')
    events = [event for event in records(ctx['path'], 'execution') if event['status'] == 'returned']
    child, parent = events
    assert child['parent_execution_id'] == parent['execution_id']
    report = telemetry.query(ctx['path'])
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] == 20
    assert report['usage']['excluded_records']['inclusive_children'] == 1


def test_capture_preserves_interruption_and_unknown_provider_usage(lifecycle):
    _, ctx = lifecycle()
    interrupted = KeyboardInterrupt('stop')
    def fail(*args, **kwargs):
        raise interrupted
    with pytest.raises(KeyboardInterrupt) as error:
        reviewer_telemetry.capture(fail, ['provider'], env={telemetry.ENV: json.dumps(ctx)},
            telemetry_prompt='go', telemetry_attempt=2, telemetry_provider='codex')
    assert error.value is interrupted
    final = records(ctx['path'], 'execution')[-1]
    assert final['status'] == 'interrupted' and final['attempt'] == 2
    assert final['usage'] is None and final['compactions'] is None
    report = telemetry.query(ctx['path'])
    assert report['execution_statuses']['interrupted'] == 1
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] is None


def test_query_is_read_only_and_never_launches_external_processes(lifecycle, monkeypatch):
    _, ctx = lifecycle()
    before = Path(ctx['path']).read_bytes()
    def fail(*args, **kwargs):
        pytest.fail('Query attempted a write or external execution')
    monkeypatch.setattr(telemetry, 'save', fail)
    monkeypatch.setattr(telemetry, 'write', fail)
    monkeypatch.setattr(subprocess, 'run', fail)
    report = telemetry.query(ctx['path'], groups=('stage', 'role'))
    assert report['workflow_outcome'] == 'unknown'
    assert Path(ctx['path']).read_bytes() == before
    assert report['usage']['groups']


def test_capture_collection_failure_preserves_provider_result_and_exception(lifecycle, monkeypatch):
    _, ctx = lifecycle()
    def storage_fail(*args, **kwargs):
        raise OSError('store unavailable')
    monkeypatch.setattr(telemetry, 'save', storage_fail)
    result = SimpleNamespace(returncode=0, stdout='done', stderr='')
    def succeed(*args, **kwargs):
        return result
    assert reviewer_telemetry.capture(succeed, ['provider'], env={telemetry.ENV: json.dumps(ctx)},
        telemetry_prompt='go', telemetry_attempt=0, telemetry_provider='codex') is result
    error = RuntimeError('original provider error')
    def fail(*args, **kwargs):
        raise error
    with pytest.raises(RuntimeError) as observed:
        reviewer_telemetry.capture(fail, ['provider'], env={telemetry.ENV: json.dumps(ctx)},
            telemetry_prompt='go', telemetry_attempt=0, telemetry_provider='codex')
    assert observed.value is error
    assert records(ctx['path'], 'execution') == []


def test_contended_telemetry_lock_has_a_local_bound(lifecycle, monkeypatch, capsys):
    import fcntl
    import time
    _, ctx = lifecycle()
    with open(ctx['path'] + '.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(telemetry, 'LOCK_TIMEOUT_SECONDS', 0.02)
        started = time.monotonic()
        assert telemetry.observe('helper', context=ctx, status='returned') is None
        assert time.monotonic() - started < 0.5
    assert 'Workflow usage evidence unavailable' in capsys.readouterr().err


def test_helper_pins_supplied_evidence_and_provider_attempt_to_stage(lifecycle, tmp_path):
    _, ctx = lifecycle()
    evidence = tmp_path / 'context.json'
    evidence.write_text('{"head_sha":"pinned-head"}')
    with telemetry.helper('decisions', ['prepare', '--context', str(evidence)], tmp_path,
                          {telemetry.ENV: json.dumps(ctx)}) as item:
        child = telemetry.context(item['env'])
        telemetry.execution(child, {'execution_id': 'attempt', 'status': 'returned'})
    report = telemetry.query(ctx['path'])
    helper = [event for event in report['events'] if event['kind'] == 'helper'][-1]
    execution = [event for event in report['events'] if event['kind'] == 'execution'][-1]
    assert helper['stage'] == execution['stage'] == 'preparation'
    assert helper['helper_id'] == execution['helper_id']
    import hashlib
    assert execution['evidence']['pinned'][0]['sha256'] == hashlib.sha256(evidence.read_bytes()).hexdigest()


@pytest.mark.parametrize('source', ['environment', 'token'])
def test_context_repository_timeout_preserves_helper_and_provider_execution(lifecycle, monkeypatch, capsys, source):
    _, ctx = lifecycle()
    env = ({telemetry.ENV: json.dumps(ctx)} if source == 'environment'
           else {'DK_UPDATE_PARENT': ctx['invocation_id']})
    from shared import repository_scope
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(['git', 'rev-parse'], 15)
    monkeypatch.setattr(repository_scope, 'checkout_roots', fail)
    with telemetry.helper('review-loop', ['check'], ROOT, env) as observed:
        assert observed['env'] == env
    result = SimpleNamespace(returncode=0, stdout='unchanged', stderr='')
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs['env'])
        return result
    assert reviewer_telemetry.capture(provider, ['provider'], env=env,
        telemetry_prompt='go', telemetry_attempt=0, telemetry_provider='codex') is result
    assert len(calls) == 1
    assert 'Workflow usage evidence unavailable' in capsys.readouterr().err


@pytest.mark.parametrize('failure', ['corrupt', 'unreadable'])
def test_failed_token_index_lookup_warns_without_stopping_work(lifecycle, monkeypatch, capsys, failure):
    _, ctx = lifecycle()
    path = telemetry.token_path(ctx['invocation_id'])
    if failure == 'corrupt':
        path.write_text('broken JSON')
    else:
        read = telemetry.read
        def denied(target, default):
            if Path(target) == path:
                raise PermissionError('unreadable usage index')
            return read(target, default)
        monkeypatch.setattr(telemetry, 'read', denied)
    env = {'DK_UPDATE_PARENT': ctx['invocation_id']}
    with telemetry.helper('review-loop', ['check'], ROOT, env) as observed:
        assert observed['env'] == env
    assert telemetry.context(env) is None
    assert 'Workflow usage evidence unavailable' in capsys.readouterr().err


def test_selected_child_invocation_overrides_inherited_context_and_preserves_its_ancestry(lifecycle, monkeypatch):
    parent, parent_ctx = lifecycle()
    inherited = {**parent_ctx, 'parent_execution_id': 'parent-provider'}
    monkeypatch.setenv(telemetry.ENV, json.dumps(inherited))
    child, child_ctx = lifecycle('child-token', 'dk-pr-review', parent['token'])
    # The current provider's environment still belongs to the parent when its
    # caller explicitly selects a different retained invocation.
    env = {telemetry.ENV: json.dumps(inherited), 'DK_UPDATE_PARENT': child['token']}
    assert telemetry.context(env) == child_ctx
    assert child_ctx['parent_execution_id'] == 'parent-provider'
    # Same-invocation transport ancestry and helper metadata remain authoritative.
    current = {**child_ctx, 'parent_execution_id': 'child-provider',
               'helper_id': 'active-helper', 'pinned_evidence': [{'sha256': 'retained'}]}
    assert telemetry.context({**env, telemetry.ENV: json.dumps(current)}) == current
    with telemetry.helper('reviewers', [], ROOT, env) as observed:
        def provider(*args, **kwargs):
            supplied = json.loads(kwargs['env'][telemetry.ENV])
            assert supplied['invocation_id'] == child['token']
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        reviewer_telemetry.capture(provider, ['provider'], env=observed['env'],
            telemetry_prompt='go', telemetry_attempt=0, telemetry_provider='codex')
    events = records(parent_ctx['path'], 'execution')
    assert len(events) == 2 and all(event['invocation_id'] == child['token'] for event in events)
    assert all(event['parent_invocation_id'] == parent['token'] for event in events)


def test_oversized_artifact_candidates_remain_unknown_and_native_work_continues(lifecycle, tmp_path, monkeypatch, capsys):
    _, ctx = lifecycle()
    path = tmp_path / 'oversized.json'
    path.write_bytes(b'x' * 129)
    monkeypatch.setattr(telemetry, 'MAX_EVIDENCE_BYTES', 128)
    with telemetry.helper('reviewers', ['--context', str(path)], tmp_path,
                          {telemetry.ENV: json.dumps(ctx)}) as observed:
        assert json.loads(observed['env'][telemetry.ENV])['pinned_evidence'] == []
    assert 'regular file within the size budget' in capsys.readouterr().err
