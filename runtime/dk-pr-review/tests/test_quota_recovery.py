"""Real session/CLI recovery with controlled providers and no paid model calls."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_review_entrypoints import main_call, pinned, original_pinned
from test_stats_capture import runner, finding
from shared import reviewer_recovery as recovery, review_coverage
from shared.review_sessions import Session


@pytest.fixture(autouse=True)
def immediate_backoff(monkeypatch):
    monkeypatch.setattr(recovery, 'BACKOFF_SECONDS', 0)
    monkeypatch.setattr(recovery, 'live_evidence', lambda context: None)


def quota(message='usage limit reached'):
    return {'status': 'tokens_exhausted', 'findings': [], 'message': message}


def test_quota_then_success_reuses_completed_checkpoints_and_retains_original(main_call, pinned):
    context, decisions, prepared, names, git = pinned
    counts = dict.fromkeys(names, 0)
    deliveries = []
    def provider(name, skill, handoff, checkout):
        counts[name] += 1
        if name == names[0]:
            deliveries.append(Path(handoff).read_bytes())
            return quota() if counts[name] == 1 else {'findings': []}
        return {'findings': [finding()]}
    with patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        result = main_call()
    assert result['complete'] and counts == {names[0]: 2, names[1]: 1}
    assert deliveries[0] == deliveries[1]
    assert result['reused_reviewers'] == names[1:]
    assert result['findings'] == [{**finding(), 'reviewers': names[1:]}]
    assert result['quota_recovery']['status'] == 'completed'
    session = json.loads(Path(result['review_session']['path']).read_text())
    assert session['invocations'] == 2 and len(session['results']) == 2
    original = review_coverage.load(session['results'][0])
    assert not original['result']['complete'] and original['result']['outcome'] == 'usage_exhausted'
    assert review_coverage.verify(result['review_artifact'], prepared)['result']['complete']
    with patch.object(runner, 'run_skill_reviewer') as providers:
        with pytest.raises(ValueError, match='finished'):
            main_call('--session-file', result['review_session']['path'], '--resume')
    providers.assert_not_called()


def test_persistent_quota_is_bounded_and_never_clean(main_call, pinned):
    context, decisions, prepared, names, git = pinned
    with patch.object(runner, 'run_skill_reviewer', return_value=quota()) as providers:
        result = main_call()
    assert providers.call_count == 2 * len(names)
    assert not result['complete'] and result['quota_recovery']['status'] == 'exhausted'
    assert result['quota_recovery']['restarts'] == 1
    assert len(result['quota_recovery']['history']) == 1
    assert '--resume' in result['quota_recovery']['resume_argv']
    with pytest.raises(ValueError):
        review_coverage.verify(result['review_artifact'], prepared)


@pytest.mark.parametrize('message', [
    'usage limit reached; try again at 2099-11-07T23:53:00Z',
    'You’ve hit your usage limit. Try again at Nov 7th, 2026 11:53 PM.',
])
def test_future_or_ambiguous_reset_returns_durable_checkpoint_without_retry(main_call, pinned, message):
    context, decisions, prepared, names, git = pinned
    with patch.object(runner, 'run_skill_reviewer', return_value=quota(message)) as providers:
        result = main_call()
        resumed = main_call('--session-file', result['review_session']['path'], '--resume')
    assert providers.call_count == len(names)
    assert not result['complete'] and result['quota_recovery']['status'] == 'waiting'
    assert result['review_artifact'] == resumed['review_artifact']
    assert result['quota_recovery']['restarts'] == 0
    session = json.loads(Path(result['review_session']['path']).read_text())
    assert session['invocations'] == 1 and len(session['results']) == 1


def test_verified_timezone_resolves_checkpoint_without_resetting_budget(main_call, pinned):
    with patch.object(runner, 'run_skill_reviewer', return_value=quota('usage limit reached; try again at noon')):
        first = main_call()
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        result = main_call('--session-file', first['review_session']['path'], '--resume',
                           '--quota-reset-at', '2000-01-01T00:00:00Z')
    assert result['complete'] and result['quota_recovery']['restarts'] == 1
    assert result['quota_recovery']['resolved_reset'] == '2000-01-01T00:00:00Z'
    assert providers.call_count == 2


def test_remote_evidence_change_blocks_dispatch_and_retains_prior_result(main_call, pinned, monkeypatch):
    context, decisions, prepared, names, git = pinned
    monkeypatch.setattr(recovery, 'live_evidence', lambda context: (_ for _ in ()).throw(ValueError('PR evidence changed')))
    with patch.object(runner, 'run_skill_reviewer', return_value=quota()) as providers:
        with pytest.raises(ValueError, match='PR evidence changed'):
            main_call()
    assert providers.call_count == len(names)
    sessions = list((Path(prepared['workflow_dir']) / 'review-sessions').glob('*.json'))
    assert len(sessions) == 1
    saved = json.loads(sessions[0].read_text())
    assert saved['quota_recovery']['restarts'] == 0 and len(saved['results']) == 1
    assert not review_coverage.load(saved['results'][0])['result']['complete']


def test_architecture_quota_restarts_only_architecture(main_call, pinned):
    original = runner.architecture.review
    attempts = 0
    def architecture(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise runner.ProviderExhausted('architecture usage limit reached')
        return original(*args, **kwargs)
    with patch.object(runner.architecture, 'review', side_effect=architecture), \
            patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        result = main_call()
    assert result['complete'] and attempts == 2 and providers.call_count == 2
    assert result['reused_reviewers'] == pinned[3]
    assert result['quota_recovery']['history'][0]['roles'] == ['architecture-review']


def test_structured_quota_preserves_correction_slot(pinned, tmp_path):
    context, decisions, prepared, names, git = pinned
    handoff = tmp_path / 'handoff.json'
    handoff.write_text(json.dumps({'patch': context['patch'], 'response_budget': 'same-role'}))
    attempts = []
    replies = iter(['invalid JSON', json.dumps(quota()), '{"findings":[]}'])
    def invoke(*args, **kwargs):
        attempts.append(kwargs['attempt'])
        return next(replies)
    with patch.object(runner, 'invoke', side_effect=invoke):
        first = runner.run_skill_reviewer(names[0], Path('SKILL.md'), handoff, context['checkout'])
        second = runner.run_skill_reviewer(names[0], Path('SKILL.md'), handoff, context['checkout'])
    assert first['status'] == 'tokens_exhausted' and second == {'findings': []}
    assert attempts == [0, 1, 1]


def test_reset_parser_and_interruptible_wait_preserve_checkpoint(pinned, monkeypatch):
    assert recovery.reset_time('Retry-After: 120', 100) == 220
    assert recovery.reset_time('try again in 2 minutes', 100) == 220
    assert recovery.reset_time('try again at 2099-01-01T00:00:00Z', 100) > 100
    assert recovery.reset_time('try again at Nov 7th, 2026 11:53 PM', 100) is None
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names)
    with session.execution(), patch.object(runner, 'run_skill_reviewer', return_value=quota()):
        result = runner.review(context, names, '', decisions, '', prepared, session=session)
        session.finish(result)
        recovery.plan(session, result, clock=lambda: 100)
    with pytest.raises(KeyboardInterrupt):
        recovery.restart(session, clock=lambda: 99,
                         sleep=lambda delay: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert recovery.pending(session)['status'] == 'waiting'
    assert recovery.pending(session)['restarts'] == 0


def test_mixed_quota_requires_explicit_acceptance_and_does_not_retry_other_failures(main_call, pinned):
    context, decisions, prepared, names, git = pinned
    def provider(name, *args):
        if name == names[0]:
            return quota('usage limit reached; try again at 2099-01-01T00:00:00Z')
        return {'findings': []}
    with patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        result = main_call()
    with pytest.raises(ValueError, match='incomplete'):
        review_coverage.verify(result['review_artifact'], prepared)
    accepted = review_coverage.verify(result['review_artifact'], prepared, accepted_failures=names[:1])
    assert accepted['accepted_failures'] == names[:1]
    assert not accepted['result']['complete']
    def failure(name, *args):
        return quota() if name == names[0] else {'status': 'incomplete', 'findings': [], 'message': 'no evidence'}
    with patch.object(runner, 'run_skill_reviewer', side_effect=failure) as providers:
        failed = main_call()
    assert providers.call_count == len(names)
    assert not failed['complete'] and failed['outcome'] == 'provider_failed'
    assert 'quota_recovery' not in failed


def test_live_evidence_compares_remote_scope_and_reset_parser_rejects_overflow(pinned, monkeypatch):
    # The automatic fixture suppresses GitHub reads for orchestration tests. Call
    # the original implementation explicitly with a controlled read response.
    from importlib import util
    spec = util.spec_from_file_location('quota_live_check', Path(recovery.__file__))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = pinned[0]
    metadata = json.loads(Path(context['metadata']).read_text())
    live = {'headRefOid': context['head_sha'], 'baseRefOid': context['base_sha'],
            'title': metadata.get('title'), 'body': metadata['body']}
    import subprocess
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, json.dumps(live), ''))
    module.live_evidence(context)
    for key, value in (('headRefOid', 'f' * 40), ('baseRefOid', 'e' * 40),
                       ('title', 'Changed title'), ('body', 'Changed Decisions')):
        original = live[key]
        live[key] = value
        with pytest.raises(ValueError, match='evidence changed'):
            module.live_evidence(context)
        live[key] = original
    assert recovery.reset_time('Retry-After: ' + '9' * 1000, 100) is None


def test_unknown_reset_resolution_preserves_other_roles_known_deadline(main_call, pinned):
    names = pinned[3]
    def provider(name, *args):
        return quota('usage limit reached; try again at ' +
                     ('2099-01-01T00:00:00Z' if name == names[0] else 'noon'))
    with patch.object(runner, 'run_skill_reviewer', side_effect=provider) as providers:
        first = main_call()
        result = main_call('--session-file', first['review_session']['path'], '--resume',
                           '--quota-reset-at', '2000-01-01T00:00:00Z')
    assert providers.call_count == len(names)
    assert result['quota_recovery']['status'] == 'waiting'
    assert result['quota_recovery']['resume_after'] == '2099-01-01T00:00:00+00:00'
    assert result['quota_recovery']['restarts'] == 0


@pytest.mark.parametrize('message,expected', [
    ('try again in 2 days', 172900), ('try again in 1 week', 604900),
    ('try again in 2 fortnights', None), ('try again in tomorrow', None),
    ('Retry-After: 1000000000000', None), ('Retry-After: 120', 220),
])
def test_relative_reset_units_and_finite_timestamp_bounds(message, expected):
    assert recovery.reset_time(message, 100) == expected


def test_finite_out_of_range_reset_returns_resolvable_checkpoint(main_call):
    with patch.object(runner, 'run_skill_reviewer', return_value=quota('Retry-After: 1000000000000')):
        result = main_call()
    assert not result['complete'] and result['quota_recovery']['status'] == 'waiting'
    assert result['quota_recovery']['resume_after'] is None
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}):
        resumed = main_call('--session-file', result['review_session']['path'], '--resume',
                            '--quota-reset-at', '2000-01-01T00:00:00Z')
    assert resumed['complete']


def test_deferred_legacy_resume_keeps_registration(main_call, pinned, tmp_path, monkeypatch):
    from test_review_entrypoints import iteration_record, state
    from test_stats_capture import prepare_current
    context, decisions, prepared, names, git = pinned
    prepared['mode'] = 'iteration'
    prepare_current(context, decisions, prepared)
    path = iteration_record(tmp_path, context, prepared)
    original = json.loads(path.read_text())
    with patch.object(runner, 'run_skill_reviewer', return_value=quota('try again at noon')):
        first = main_call('--run-record', path)
    argv = first['quota_recovery']['resume_argv']
    assert '--run-record' in argv and '--session-file' not in argv and '--resume' not in argv
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}):
        result = main_call(*argv[2:], '--quota-reset-at', '2000-01-01T00:00:00Z')
    record = json.loads(path.read_text())
    assert result['complete'] and record['cycles'] == original['cycles']
    assert record['review_sessions'] == original['review_sessions']
    assert len(record['review_results']) == 2
    reference, artifact = state.current_review(record, prepared)
    assert reference == result['review_artifact'] and artifact['result']['complete']
    assert not review_coverage.load(first['review_artifact'])['result']['complete']


def test_interrupted_started_recovery_rechecks_remote_scope_on_resume(main_call, pinned, tmp_path, monkeypatch):
    context, decisions, prepared, names, git = pinned
    path = tmp_path / 'interrupted-quota.json'
    calls = 0
    def provider(name, *args):
        nonlocal calls
        if name == names[0]:
            calls += 1
            if calls == 1:
                return quota()
            raise KeyboardInterrupt()
        return {'findings': []}
    with patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        with pytest.raises(KeyboardInterrupt):
            main_call('--session-file', path)
    saved = json.loads(path.read_text())
    assert saved['quota_recovery']['status'] == 'started'
    assert saved['quota_recovery']['restarts'] == 1 and len(saved['results']) == 1
    monkeypatch.setattr(recovery, 'live_evidence', lambda context:
                        (_ for _ in ()).throw(ValueError('remote PR evidence changed')))
    with patch.object(runner, 'run_skill_reviewer') as providers:
        with pytest.raises(ValueError, match='remote PR evidence changed'):
            main_call('--session-file', path, '--resume')
    providers.assert_not_called()
    assert json.loads(path.read_text())['results'] == saved['results']
    monkeypatch.setattr(recovery, 'live_evidence', lambda context: None)
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}):
        result = main_call('--session-file', path, '--resume')
    assert result['complete'] and result['quota_recovery']['restarts'] == 1
