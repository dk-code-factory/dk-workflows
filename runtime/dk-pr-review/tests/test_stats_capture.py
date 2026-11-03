"""Pinned checkout integration with controlled reviewer responses; no models/network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dk-pr-review/scripts'))
sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
import run_reviewers as runner
from stats_capture import Capture, for_iteration, api
from preparation_fixtures import artifact, rebuild
from shared.reviewer_telemetry import ENV, capture, trace_path
from shared.review_sessions import Session
from shared import executors


def responses(names, values):
    by_name = dict(zip(names, values))
    def respond(name, *args):
        value = by_name[runner.bindings.key(name)]
        if isinstance(value, BaseException):
            raise value
        return value
    return respond


def prepare_current(context, decisions, prepared):
    rebuild(prepared, context, decisions)
    context.update(runner.preparation_api.context_for(prepared))


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'private'))
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=checkout, text=True).strip()
    git('init', '-q')
    (checkout / 'x.py').write_text('new\n')
    git('add', 'x.py')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'test')
    sha = git('rev-parse', 'HEAD')
    patchfile = tmp_path / 'diff.patch'
    patchfile.write_text('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n')
    metadata = tmp_path / 'metadata.json'
    metadata.write_text('{"body":""}')
    context = dict(url='https://github.com/O/R/pull/2', repository=str(checkout), checkout=str(checkout),
                   head_sha=sha, base_sha='b' * 40, patch=str(patchfile), metadata=str(metadata))
    decisions = dict(pr_url=context['url'], head_sha=sha, body_hash=runner.hashlib.sha256(b'').hexdigest(),
                     live_decisions='', discussion_decisions=[], local_decisions=[], verified=True)
    # These mocked providers exercise orchestration/analytics, without claiming
    # execution of a real installed reviewer's independent specialist method.
    names = ['fixture-reviewer-one', 'fixture-reviewer-two']
    prep = artifact(context, decisions, {'production/0': {
        'path': str(metadata), 'sha256': runner.hashlib.sha256(metadata.read_bytes()).hexdigest()}}, names)
    prepare_current(context, decisions, prep)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=context['checkout'], text=True).strip()
    monkeypatch.setattr(runner, 'describe_reviewer', lambda name: prep['reviewers'][runner.bindings.key(name)])
    provider_root = tmp_path / 'provider-package'
    provider_root.mkdir()
    procedure=provider_root/'SKILL.md';procedure.write_text('Read-only review fixture')
    monkeypatch.setattr(runner, 'skill_path', lambda name: procedure)
    from shared import executors
    monkeypatch.setattr(executors, 'resolve', lambda name: {'binding':runner.bindings.normalize(name), 'identity':runner.bindings.key(name), 'executor':'codex', 'root':str(provider_root), 'procedure':str(procedure)})
    return context, decisions, prep, names, git


def finding():
    return dict(severity='Major', path='x.py', line=1, message='bug', fix='fix it')


def test_capture_before_aggregation_and_independent_review_session(pinned):
    context, decisions, prep, names, git = pinned
    stats = Capture(context, prep, names, cycle=1)
    def fake(name, skill, handoff_path, checkout):
        assert trace_path().endswith('-execution.json')
        # A configured parent owns all observations, including nested skills.
        assert json.loads(handoff_path.read_text())['reviewer_addendum']
        return {'findings': [finding()]}
    with patch.object(runner, 'run_skill_reviewer', side_effect=fake) as calls:
        result = runner.review(context, names, '', decisions, '', prep, stats=stats)
    assert calls.call_count == 2
    assert runner.architecture.load(result['architecture_review'])['result']['complete']
    assert {key: value for key, value in result.items() if key not in {'architecture_review', 'architecture_accounting', 'role_failures'}} == {'complete': True, 'outcome': 'completed', 'findings': [{**finding(), 'reviewers': names}], 'skipped_reviewers': [], 'executed_reviewers': names, 'reused_reviewers': []}
    data = api.ledger('github.com/o/r')
    reviews = data['runs'][stats.run_id]['reviews']
    assert len({rev['findings'][0]['id'] for rev in reviews.values()}) == 2
    assert all(rev['duration_seconds'] >= 0 and rev['prompt_fingerprint'] for rev in reviews.values())
    assert api.summary()['total']['unverified']['total'] == 2
    assert (Path(context['patch']).parent / 'review-stats.json').exists()
    assert not git('status', '--porcelain')
    assert ENV not in os.environ
    # Completed optional observations cannot authorize reuse in a fresh session.
    session = Session(context, prep, names)
    with session.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as calls:
        fresh = runner.review(context, names, '', decisions, '', prep, stats=stats, session=session)
        session.finish(fresh)
    assert calls.call_count == 2
    assert fresh['complete'] and fresh['findings'] == []
    assert fresh['executed_reviewers'] == names and fresh['reused_reviewers'] == []
    assert all(session.cached(name) == {'findings': []} for name in names)
    assert api.summary()['total']['reported']['total'] == 2


def test_partial_failure_retains_completed_and_later_reviewers(pinned, monkeypatch):
    context, decisions, prep, names, git = pinned
    # One completed reviewer, malformed second, third still runs.
    third = 'dk-pr-read-comments'
    prep['reviewers'][third] = {**prep['reviewers'][names[0]], 'reviewer': third}
    prep['analysis']['reviewer_addenda'][third] = prep['analysis']['reviewer_addenda'][names[0]]
    prepare_current(context, decisions, prep)
    names.append(third)
    stats = Capture(context, prep, names, cycle=1)
    with patch.object(runner, 'run_skill_reviewer', side_effect=responses(names, [
            {'findings': [finding()]}, {'bad': True}, {'findings': []}])):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats)
    assert not result['complete']
    reviews = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
    assert {name: reviews[name]['status'] for name in names} == dict(zip(
        names, ['completed', 'failed', 'completed']))
    assert len(result['findings']) == 1
    assert api.summary()['total']['rejected']['total'] == 0
    assert api.summary()['total']['reviews']['failures'] == 1
    assert api.summary()['total']['reviews']['not_run'] == 0
    assert api.summary()['warnings']


def test_exhaustion_and_analytics_failure_do_not_change_review_contract(pinned, monkeypatch, capsys):
    context, decisions, prep, names, git = pinned
    stats = Capture(context, prep, names, cycle=1)
    exhausted = {'status': 'tokens_exhausted', 'findings': [], 'message': 'usage limit reached'}
    with patch.object(runner, 'run_skill_reviewer', side_effect=responses(names, [exhausted, {'findings': []}])):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats)
    assert not result['complete']
    assert result['skipped_reviewers'] == [{'reviewer': names[0], 'reason': 'usage limit reached'}]
    assert api.summary()['total']['reviews']['exhausted'] == 1
    assert api.summary()['total']['reviews']['clean'] == 1
    stats2 = Capture(context, prep, names, cycle=2)
    monkeypatch.setattr(api, 'record_review', lambda *a, **k: (_ for _ in ()).throw(OSError('disk error')))
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': [finding()]}):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats2)
    assert result['complete'], result.get('reason', result)
    assert len(result['findings']) == 1
    assert 'missing data' in capsys.readouterr().err
    assert list(api.ledger_path(stats.repo).parent.glob('review-stats-artifacts/results/*.json'))


def test_execution_corrections_retained_without_nested_double_counting(pinned, monkeypatch):
    context, decisions, prep, names, git = pinned
    prep['reviewers'] = {names[0]: prep['reviewers'][names[0]]}
    prep['analysis']['reviewer_addenda'] = {names[0]: prep['analysis']['reviewer_addenda'][names[0]]}
    prepare_current(context, decisions, prep)
    stats = Capture(context, prep, names[:1], cycle=1)
    responses = ['not json', json.dumps({'findings': [finding()]})]
    def fake(args, input, **kwargs):
        Path(args[args.index('-o') + 1]).write_text(responses.pop(0))
        return subprocess.CompletedProcess(
            args, 0, '', 'model: exposed-model\nuser\nreview\ntokens used\n7\n')
    # Replace the owned provider transport; git validation stays real.
    with patch.object(executors, 'run_provider', side_effect=fake):
        result = runner.review(context, names[:1], '', decisions, '', prep, stats=stats)
    assert result['complete'], result.get('reason', result)
    rev = api.ledger(stats.repo)['runs'][stats.run_id]['reviews'][names[0]]
    assert rev['correction_retries'] == 1
    assert rev['usage'] == [{'total_tokens': 7, 'source': 'codex_cli_summary'}] * 2
    assert rev['model'] == 'exposed-model'
    trace = json.loads(next(stats.directory.glob('*-execution.json')).read_text())
    assert len(trace['attempts']) == 2
    assert trace['attempts'][0]['output'] == 'not json'
    assert rev['duration_seconds'] >= sum(a['duration_seconds'] for a in trace['attempts'])
    assert api.summary()['total']['reviews']['correction_retries'] == 1


def test_json_cli_capture_preserves_response_file_and_known_usage_on_missing_correction(pinned):
    context, decisions, prep, names, git = pinned
    prep['reviewers'] = {names[0]: prep['reviewers'][names[0]]}
    prep['analysis']['reviewer_addenda'] = {names[0]: prep['analysis']['reviewer_addenda'][names[0]]}
    prepare_current(context, decisions, prep)
    stats = Capture(context, prep, names[:1], cycle=1)
    responses = ['not json', json.dumps({'findings': []})]
    usage = dict(input_tokens=100, cached_input_tokens=70, output_tokens=20, reasoning_output_tokens=5)
    def fake(args, input, **kwargs):
        assert '--json' in args
        first = len(responses) == 2
        Path(args[args.index('-o') + 1]).write_text(responses.pop(0))
        # JSON stdout is transport evidence. It never substitutes for -o content.
        return subprocess.CompletedProcess(args, 0,
            json.dumps({'type': 'turn.completed', 'usage': usage}) if first else '', '')
    with patch.object(executors, 'run_provider', side_effect=fake):
        result = runner.review(context, names[:1], '', decisions, '', prep, stats=stats)
    assert result['complete'] and result['findings'] == []
    review = api.ledger(stats.repo)['runs'][stats.run_id]['reviews'][names[0]]
    assert review['usage'] == [{**usage, 'source': 'codex_cli_json'}, None]
    assert len(review['executions']) == 2
    assert review['executions'][0]['execution_id'] != review['executions'][1]['execution_id']
    summary = api.summary()['usage']
    assert summary['tokens']['total_tokens']['known_subtotal'] == 120
    assert summary['missing_attempts'] == 1 and not summary['complete']
    assert summary['tokens']['reasoning_output_tokens']['known_subtotal'] == 5
    trace = json.loads(next(stats.directory.glob('*-execution.json')).read_text())
    assert trace['attempts'][0]['output'] == 'not json'
    assert json.loads(trace['attempts'][0]['stdout'])['usage'] == usage
    assert not git('status', '--porcelain')


def test_standalone_no_capture_and_automatic_iteration_cycle_lookup(pinned):
    context, decisions, prep, names, git = pinned
    assert for_iteration(context, {**prep, 'mode': 'standalone'}, names) is None
    repo, pr = api.pr_identity(context['url'])
    from shared.private_state import mutate, root
    path = root(repo) / f'pr-{pr}.json'
    mutate(path, {'cycles': [{'number': 3, 'head': context['head_sha']}], 'cycle_retries': []}, lambda d: None)
    before = path.read_bytes()
    stats = for_iteration(context, prep, names)
    assert stats.run['cycle'] == 3
    assert stats.run['repository'] == 'github.com/o/r'
    assert before == path.read_bytes()


def test_private_initialization_failure_retains_replayable_results(pinned, monkeypatch, capsys):
    context, decisions, prep, names, git = pinned
    monkeypatch.setattr(api, 'begin_review', lambda **k: (_ for _ in ()).throw(OSError('storage unavailable')))
    stats = for_iteration(context, prep, names, cycle=1)
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': [finding()]}):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats)
    assert result['complete'], result.get('reason', result)
    recovery = Path(context['patch']).parent / 'review-stats-recovery'
    begin = json.loads(next(recovery.glob('begin-*.json')).read_text())
    reviews = [json.loads(p.read_text()) for p in recovery.glob('record-review-*.json')]
    assert len(reviews) == 2
    assert all(r['run_id'] == begin['run_id'] for r in reviews)
    assert all(r['status'] == 'completed' and r['findings'] == [finding()] for r in reviews)
    assert 'missing data' in capsys.readouterr().err
    assert not git('status', '--porcelain')


def test_offline_replay_preserves_capture_date_for_bounded_queries(pinned):
    context, decisions, prep, names, git = pinned
    captured_at = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    replayed_at = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    with patch('stats_capture.datetime', wraps=datetime) as clock:
        clock.now.return_value = captured_at
        with patch.object(api, 'begin_review', side_effect=OSError('storage unavailable')):
            stats = for_iteration(context, prep, names, cycle=1)
    stats.failure(names[0], 'interrupted while storage unavailable')
    recovery = Path(context['patch']).parent / 'review-stats-recovery'
    begin = json.loads(next(recovery.glob('begin-*.json')).read_text())
    review = json.loads(next(recovery.glob('record-review-*.json')).read_text())
    assert begin['started_at'] == captured_at.isoformat()
    with patch.object(api, 'datetime', wraps=datetime) as clock:
        clock.now.return_value = replayed_at
        run = api.begin_review(**begin)
        api.record_review(run['repository'], **review)
    assert run['id'] == stats.run_id
    assert run['started_at'] == captured_at.isoformat()
    original = api.summary(since='2026-11-01', until='2026-11-02')
    replay = api.summary(since='2026-11-03', until='2026-11-04')
    assert original['review_runs']['failed'] == 1
    assert replay['review_runs']['failed'] == 0
    assert not git('status', '--porcelain')


def test_nested_provider_execution_does_not_duplicate_telemetry(pinned, monkeypatch):
    context, decisions, prep, names, git = pinned
    trace = Path(context['patch']).parent / 'trace.json'
    monkeypatch.setenv(ENV, str(trace))
    def provider():
        assert os.environ[ENV] == str(trace)
        assert trace_path() is None
        capture(lambda: None, telemetry_prompt="nested", telemetry_attempt=0)
        return subprocess.CompletedProcess(['provider'], 0, 'result', '')
    capture(provider, telemetry_prompt='review', telemetry_attempt=0)
    assert os.environ[ENV] == str(trace)
    assert len(json.loads(trace.read_text())['attempts']) == 1


def test_transport_failure_preserves_both_streams_and_no_false_positive(pinned):
    context, decisions, prep, names, git = pinned
    stats = Capture(context, prep, names[:1], cycle=1)
    with stats.invocation(names[0], {'head_sha': context['head_sha']}, prep['reviewers'][names[0]]):
        with patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['external'], 1, 'partial output', 'transport failed')):
            with pytest.raises(RuntimeError, match='transport failed'):
                runner.run(['external'])
    stats.finish('failed', reason='transport failed')
    trace = json.loads(next(stats.directory.glob('*-execution.json')).read_text())
    assert trace['transports'] == [{'returncode': 1, 'stdout': 'partial output', 'stderr': 'transport failed'}]
    assert api.summary()['total']['rejected']['total'] == 0


def test_legacy_record_casing_is_read_without_moving_counters(pinned):
    context, decisions, prep, names, git = pinned
    from shared.private_state import mutate, root
    path = root('github.com/O/R') / 'pr-2.json'
    mutate(path, {'cycles': [{'number': 2, 'head': context['head_sha']}], 'cycle_retries': []}, lambda d: None)
    before = path.read_bytes()
    stats = for_iteration(context, prep, names)
    assert stats.run['cycle'] == 2
    assert stats.repo == 'github.com/o/r'
    assert path.read_bytes() == before
    assert not (root('github.com/o/r') / 'pr-2.json').exists()
