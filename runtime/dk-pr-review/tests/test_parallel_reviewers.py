"""Real command-provider rendezvous and durable concurrent review evidence."""
import json
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from test_stats_capture import pinned, runner, finding, Capture, for_iteration, api
from shared import bindings, executors
from shared.review_sessions import Session
from shared.reviewer_telemetry import ENV


PROVIDER = '''import json, os, sys, time
from pathlib import Path
handoff_path = Path(sys.argv[1])
name, behavior = sys.argv[2:4]
root = Path(sys.argv[4])
handoff = json.loads(handoff_path.read_text())
assert os.environ.get("DK_REVIEW_STATS_TRACE") is None
attempt = int(os.environ['DK_PROVIDER_ATTEMPT'])
(root / (name + '-started')).write_text('ready')
deadline = time.monotonic() + 5
while len(list(root.glob('*-started'))) < 2:
    if time.monotonic() > deadline:
        raise RuntimeError('reviewers did not overlap')
    time.sleep(.01)
original = handoff_path.read_bytes()
if name == 'first':
    while not (root / 'second-finished').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('second reviewer did not finish')
        time.sleep(.01)
assert handoff_path.read_bytes() == original
record = {'handoff': handoff, 'path': str(handoff_path), 'attempt': attempt,
          'prompt': sys.stdin.read()}
(root / (name + '-' + str(attempt) + '.json')).write_text(json.dumps(record))
if behavior == 'correct' and attempt == 0:
    print('not json')
elif behavior == 'fail':
    print('provider unavailable', file=sys.stderr)
    sys.exit(1)
elif behavior == 'exhaust':
    print('usage limit reached', file=sys.stderr)
    sys.exit(1)
else:
    print(json.dumps({'findings': [{'severity':'Major', 'path':'x.py', 'line':1,
                                 'message':'bug', 'fix':'fix it'}]}))
(root / (name + '-finished')).write_text('done')
'''


@pytest.fixture
def commands(pinned, tmp_path, monkeypatch):
    context, decisions, prep, old_names, git = pinned
    script = tmp_path / 'provider.py'
    script.write_text(PROVIDER)
    monkeypatch.setattr(executors, 'resolve', bindings.resolve)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    def setup(behaviors=('correct', 'ok')):
        providers = [{'command': [sys.executable, str(script), '{handoff}', name, behavior, str(tmp_path)]}
                     for name, behavior in zip(('first', 'second'), behaviors)]
        names = [bindings.key(provider) for provider in providers]
        prep['reviewers'] = {name: {**prep['reviewers'][old_names[i]], 'reviewer': name}
                             for i, name in enumerate(names)}
        prep['analysis']['reviewer_addenda'] = {name: 'addendum for ' + name for name in names}
        prep['preparation_id'] = runner.preparation_api.digest({k: v for k, v in prep.items() if k != 'preparation_id'})
        monkeypatch.setattr(runner, 'skill_path', lambda provider: None)
        return context, decisions, prep, providers, names, tmp_path
    return setup


@pytest.mark.parametrize('offline', [False, True])
def test_real_commands_overlap_correct_and_keep_handoffs_and_traces_separate(commands, monkeypatch, offline):
    context, decisions, prep, providers, names, root = commands()
    if offline:
        monkeypatch.setattr(api, 'begin_review', lambda **kw: (_ for _ in ()).throw(OSError('offline')))
    stats = for_iteration(context, prep, names, cycle=1, attempt='1')
    session = Session(context, prep, names)
    # Environment compatibility stays intact while workers select their own traces.
    monkeypatch.setenv(ENV, str(root / 'ambient.json'))
    with session.execution():
        result = runner.review(context, providers, '', decisions, '', prep, stats=stats, session=session)
        session.finish(result)
    assert result['complete'], result
    assert result['executed_reviewers'] == names
    assert result['findings'] == [{**finding(), 'reviewers': names}]
    first, corrected, second = [json.loads((root / path).read_text()) for path in
                               ('first-0.json', 'first-1.json', 'second-0.json')]
    assert first['path'] == corrected['path'] != second['path']
    assert first['handoff'] == corrected['handoff']
    for record, name in ((first, names[0]), (second, names[1])):
        assert record['handoff']['reviewer_addendum'] == prep['analysis']['reviewer_addenda'][name]
        assert record['handoff']['preparation'] == prep
    assert 'Correction:' in corrected['prompt']
    traces = [json.loads(path.read_text()) for path in stats.directory.glob('*-execution.json')]
    assert len(traces) == 2
    assert sorted(len(trace['attempts']) for trace in traces) == [1, 2]
    assert json.loads((root / 'ambient.json').read_text())['attempts'] == []
    assert all(session.cached(name) == {'findings': [finding()]} for name in names)
    if not offline:
        records = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
        assert [records[name]['correction_retries'] for name in names] == [1, 0]
        assert all(records[name]['status'] == 'completed' for name in names)


@pytest.mark.parametrize('behaviors,expected', [
    (('ok', 'fail'), 'provider_failed'), (('ok', 'exhaust'), 'completed'),
    (('exhaust', 'exhaust'), 'usage_exhausted')])
def test_real_commands_keep_mixed_outcomes(commands, behaviors, expected):
    context, decisions, prep, providers, names, root = commands(behaviors)
    # Failures exit before the normal finish marker used to reverse completion.
    (root / 'second-finished').write_text('release')
    result = runner.review(context, providers, '', decisions, '', prep)
    assert result['outcome'] == expected, result
    if expected != 'usage_exhausted':
        assert result['findings'] == [{**finding(), 'reviewers': names[:1]}]
    else:
        assert result['findings'] == [] and len(result['skipped_reviewers']) == 2


@pytest.mark.parametrize('invalid', ['descriptor', 'checkpoint', 'findings'])
def test_every_reusable_checkpoint_and_descriptor_is_checked_before_launch(pinned, invalid):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    session.record(names[1], 'completed', {'findings': [] if invalid != 'findings' else [{**finding(), 'line': 99}]})
    stats = for_iteration(context, prep, names, cycle=1, attempt='guard')
    if invalid == 'checkpoint':
        data = json.loads(session.path.read_text())
        Path(data['reviews'][names[1]]['checkpoint']['path']).write_text('{}')
    describe = runner.describe_reviewer
    def descriptor(name):
        return {} if invalid == 'descriptor' and name == names[1] else describe(name)
    with session.execution(), patch.object(runner, 'describe_reviewer', side_effect=descriptor), \
            patch.object(runner, 'run_skill_reviewer') as provider:
        result = runner.review(context, names, '', decisions, '', prep, stats=stats, session=session)
    provider.assert_not_called()
    assert result['outcome'] == 'guard_failed'
    if invalid == 'findings':
        reviews = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
        assert all(review['status'] == 'not_run' for review in reviews.values())


@pytest.mark.parametrize('failure', ['interrupt', 'storage', 'provider'])
def test_all_workers_drain_and_successful_checkpoints_survive(pinned, failure):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    rendezvous = threading.Barrier(2)
    pending = threading.Event()
    release = threading.Event()
    shutdown = threading.Event()
    finished = threading.Event()
    original_record = session.record
    completed = runner.as_completed
    def record(name, status, **kwargs):
        if failure == 'storage' and name == names[0] and status == 'completed':
            raise OSError('mandatory checkpoint unavailable')
        return original_record(name, status, **kwargs)
    def completions(futures):
        for future in completed(futures):
            if failure != 'interrupt' and not release.is_set():
                assert pending.is_set() and not finished.is_set()
                release.set()
            yield future
    class Pool(runner.ThreadPoolExecutor):
        def shutdown(self, wait=True, **kwargs):
            if failure == 'interrupt':
                assert pending.is_set() and not finished.is_set()
                shutdown.set()
                release.set()
                assert wait
            return super().shutdown(wait=wait, **kwargs)
    def provider(name, *args):
        rendezvous.wait(timeout=5)
        if name == names[0]:
            assert pending.wait(timeout=5)
            if failure == 'interrupt':
                raise KeyboardInterrupt()
            if failure == 'provider':
                raise RuntimeError('provider unavailable')
            return {'findings': []}
        pending.set()
        assert release.wait(timeout=5)
        finished.set()
        return {'findings': [finding()]}
    with session.execution(), patch.object(session, 'record', side_effect=record), \
            patch.object(runner, 'ThreadPoolExecutor', Pool), \
            patch.object(runner, 'as_completed', side_effect=completions), \
            patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        if failure == 'interrupt':
            with pytest.raises(KeyboardInterrupt):
                runner.review(context, names, '', decisions, '', prep, session=session)
        else:
            result = runner.review(context, names, '', decisions, '', prep, session=session)
            assert result['outcome'] == ('guard_failed' if failure == 'storage' else 'provider_failed')
            assert result['findings'] == [{**finding(), 'reviewers': names[1:]}]
    assert finished.is_set()
    assert shutdown.is_set() == (failure == 'interrupt')
    assert session.cached(names[1]) == {'findings': [finding()]}
    resumed = Session(context, prep, names, path=session.path, resume=True)
    with resumed.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as provider:
        result = runner.review(context, names, '', decisions, '', prep, session=resumed)
    assert result['complete']
    assert result['reused_reviewers'] == names[1:]
    assert [call.args[0] for call in provider.call_args_list] == names[:1]


def test_workers_inherit_pinned_bindings_and_registry_snapshot(pinned):
    context, decisions, prep, names, git = pinned
    observed, pinned_contexts = [], []
    skill_path = runner.skill_path
    def skill(name):
        pinned_contexts.append(bindings._PINNED_BINDINGS.get())
        return skill_path(name)
    def provider(*args):
        observed.append((bindings._PINNED_BINDINGS.get(), bindings._REGISTRY_SNAPSHOT.get()))
        return {'findings': []}
    with bindings.registry_snapshot(), patch.object(runner, 'skill_path', side_effect=skill), \
            patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        snapshot = bindings._REGISTRY_SNAPSHOT.get()
        runner.review(context, names, '', decisions, '', prep)
    assert len(observed) == 2
    assert all(pinned is pinned_contexts[0] and registry is snapshot for pinned, registry in observed)


def test_coordinator_interruption_drains_workers_before_unlocking_session(pinned):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    started = threading.Barrier(3)
    release = threading.Event()
    interrupting = threading.Event()
    draining = threading.Event()
    class Pool(runner.ThreadPoolExecutor):
        def shutdown(self, wait=True, **kwargs):
            assert interrupting.is_set()
            draining.set()
            release.set()
            assert wait
            return super().shutdown(wait=wait, **kwargs)
    def provider(name, *args):
        started.wait(timeout=5)
        assert release.wait(timeout=5)
        return {'findings': [finding()]}
    def interrupted(futures):
        started.wait(timeout=5)
        assert all(not future.done() for future in futures)
        interrupting.set()
        raise KeyboardInterrupt()
    with session.execution(), patch.object(runner, 'as_completed', side_effect=interrupted), \
            patch.object(runner, 'ThreadPoolExecutor', Pool), \
            patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        with pytest.raises(KeyboardInterrupt):
            runner.review(context, names, '', decisions, '', prep, session=session)
        assert all(session.cached(name) == {'findings': [finding()]} for name in names)
        assert draining.is_set()
    resumed = Session(context, prep, names, path=session.path, resume=True)
    with resumed.execution(), patch.object(runner, 'run_skill_reviewer') as provider:
        result = runner.review(context, names, '', decisions, '', prep, session=resumed)
    provider.assert_not_called()
    assert result['complete'] and result['reused_reviewers'] == names


@pytest.mark.parametrize('reuse', [False, True])
def test_shared_checkout_guard_does_not_blame_an_observer_or_trust_tainted_results(pinned, reuse):
    context, decisions, prep, names, git = pinned
    session = Session(context, prep, names)
    if reuse:
        session.record(names[0], 'completed', {'findings': [finding()]})
    stats = for_iteration(context, prep, names, cycle=1, attempt='shared-guard')
    started = threading.Barrier(1 if reuse else 2)
    changed = threading.Event()
    def provider(name, *args):
        started.wait(timeout=5)
        if name == names[1]:
            (Path(context['checkout']) / 'provider-cache').write_text('untracked')
            changed.set()
        assert changed.wait(timeout=5)
        return {'findings': [finding()]}
    with session.execution(), patch.object(runner, 'run_skill_reviewer', side_effect=provider):
        result = runner.review(context, names, '', decisions, '', prep, stats=stats, session=session)
    assert result['outcome'] == 'guard_failed'
    assert result['findings'] == ([{**finding(), 'reviewers': names[:1]}] if reuse else [])
    assert 'failed_reviewer' not in result and 'failed_reviewers' not in result
    assert session.cached(names[0]) == ({'findings': [finding()]} if reuse else None)
    assert session.cached(names[1]) is None
    reviews = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
    assert reviews[names[0]]['status'] == ('not_run' if reuse else 'running')
    assert reviews[names[1]]['status'] == 'running'


def test_process_group_interruption_does_not_count_terminated_providers_as_failures(commands):
    context, decisions, prep, providers, names, root = commands(('ok', 'ok'))
    Path(providers[0]['command'][1]).write_text('''import sys, time
from pathlib import Path
name, root = sys.argv[2], Path(sys.argv[4])
(root / (name + '-started')).write_text('ready')
time.sleep(60)
''')
    session = Session(context, prep, names)
    stats = for_iteration(context, prep, names, cycle=1, attempt='sigint')
    process_context = multiprocessing.get_context('fork')
    outcome = process_context.Queue()
    def review():
        os.setpgrp()
        try:
            with session.execution():
                runner.review(context, providers, '', decisions, '', prep, stats=stats, session=session)
        except KeyboardInterrupt:
            outcome.put('interrupted')
        else:
            outcome.put('returned')
    process = process_context.Process(target=review)
    process.start()
    try:
        deadline = time.monotonic() + 10
        while len(list(root.glob('*-started'))) != 2:
            assert process.is_alive() and time.monotonic() < deadline
            time.sleep(.01)
        os.killpg(process.pid, signal.SIGINT)
        process.join(timeout=10)
        assert not process.is_alive() and process.exitcode == 0
        assert outcome.get(timeout=1) == 'interrupted'
    finally:
        if process.is_alive():
            os.killpg(process.pid, signal.SIGKILL)
            process.join(timeout=5)
        outcome.close()
    history = json.loads(session.path.read_text())
    assert all(review['status'] == 'running' for review in history['reviews'].values())
    assert all(session.cached(name) is None for name in names)
    reviews = api.ledger(stats.repo)['runs'][stats.run_id]['reviews']
    assert all(review['status'] == 'running' for review in reviews.values())
    traces = [json.loads(path.read_text()) for path in stats.directory.glob('*-execution.json')]
    assert len(traces) == 2
    assert all(trace['attempts'][0]['returncode'] == -signal.SIGINT for trace in traces)
