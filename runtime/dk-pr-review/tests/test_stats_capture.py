"""Pinned checkout integration with controlled reviewer responses; no models/network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dk-pr-review/scripts'))
sys.path.insert(0, str(ROOT / 'dk-pr-decisions/tests'))
import run_reviewers as runner
from stats_capture import Capture, for_iteration, api
from preparation_fixtures import artifact
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
    context = dict(url='https://GitHub.com/O/R/pull/2', repository=str(checkout), checkout=str(checkout),
                   head_sha=sha, base_sha='b' * 40, patch=str(patchfile), metadata=str(metadata))
    decisions = dict(pr_url=context['url'], head_sha=sha, body_hash=runner.hashlib.sha256(b'').hexdigest(),
                     live_decisions='', discussion_decisions=[], local_decisions=[], verified=True)
    names = ['code-review-ad', 'code-review-matpocock']
    prep = artifact(context, decisions, {'production/0': {
        'path': str(metadata), 'sha256': runner.hashlib.sha256(metadata.read_bytes()).hexdigest()}}, names)
    monkeypatch.setattr(runner, 'describe_reviewer', lambda name: prep['reviewers'][runner.bindings.key(name)])
    procedure=tmp_path/'SKILL.md';procedure.write_text('Read-only review fixture')
    monkeypatch.setattr(runner, 'skill_path', lambda name: procedure)
    from shared import executors
    monkeypatch.setattr(executors, 'resolve', lambda name: {'binding':runner.bindings.normalize(name), 'executor':'codex', 'root':str(tmp_path), 'procedure':str(procedure)})
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
    assert result == {'complete': True, 'outcome': 'completed', 'findings': [{**finding(), 'reviewers': names}], 'skipped_reviewers': [], 'executed_reviewers': names, 'reused_reviewers': []}
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
    prep['preparation_id'] = runner.preparation_api.digest({k: v for k, v in prep.items() if k != 'preparation_id'})
    names.append(third)
    stats = Capture(context, prep, names, cycle=1)
    with patch.object(runner, 'run_skill_reviewer', side_effect=responses(names, [
            {'findings': [finding()]}, {'bad': True}, {'findings': []}])):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats)
    assert not result['complete']
    reviews = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
    assert [r['status'] for r in reviews.values()] == ['completed', 'failed', 'completed']
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
    assert result['complete']
    assert result['skipped_reviewers'] == [{'reviewer': names[0], 'reason': 'usage limit reached'}]
    assert api.summary()['total']['reviews']['exhausted'] == 1
    assert api.summary()['total']['reviews']['clean'] == 1
    stats2 = Capture(context, prep, names, cycle=2)
    monkeypatch.setattr(api, 'record_review', lambda *a, **k: (_ for _ in ()).throw(OSError('disk error')))
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': [finding()]}):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats2)
    assert result['complete']
    assert len(result['findings']) == 1
    assert 'missing data' in capsys.readouterr().err
    assert list(api.ledger_path(stats.repo).parent.glob('review-stats-artifacts/results/*.json'))


def test_execution_corrections_retained_without_nested_double_counting(pinned, monkeypatch):
    context, decisions, prep, names, git = pinned
    stats = Capture(context, prep, names[:1], cycle=1)
    responses = ['not json', json.dumps({'findings': [finding()]})]
    def fake(args, input, **kwargs):
        Path(args[args.index('-o') + 1]).write_text(responses.pop(0))
        return subprocess.CompletedProcess(
            args, 0, '', 'model: exposed-model\nuser\nreview\ntokens used\n7\n')
    # Replace the owned provider transport; git validation stays real.
    with patch.object(executors, 'run_provider', side_effect=fake):
        result = runner.review(context, names[:1], '', decisions, '', prep, stats=stats)
    assert result['complete']
    rev = api.ledger(stats.repo)['runs'][stats.run_id]['reviews'][names[0]]
    assert rev['correction_retries'] == 1
    assert rev['usage'] == [{'total_tokens': 7, 'source': 'codex_cli_summary'}] * 2
    assert rev['model'] == 'exposed-model'
    trace = json.loads(next(stats.directory.glob('*-execution.json')).read_text())
    assert len(trace['attempts']) == 2
    assert trace['attempts'][0]['output'] == 'not json'
    assert rev['duration_seconds'] >= sum(a['duration_seconds'] for a in trace['attempts'])
    assert api.summary()['total']['reviews']['correction_retries'] == 1


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
    assert result['complete']
    recovery = Path(context['patch']).parent / 'review-stats-recovery'
    begin = json.loads(next(recovery.glob('begin-*.json')).read_text())
    reviews = [json.loads(p.read_text()) for p in recovery.glob('record-review-*.json')]
    assert len(reviews) == 2
    assert all(r['run_id'] == begin['run_id'] for r in reviews)
    assert all(r['status'] == 'completed' and r['findings'] == [finding()] for r in reviews)
    assert 'missing data' in capsys.readouterr().err
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
