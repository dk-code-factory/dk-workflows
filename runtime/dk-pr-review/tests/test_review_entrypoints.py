"""Real CLI routing and durable checkpoints with controlled provider responses."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_review_sessions import pinned
from test_stats_capture import responses, pinned as original_pinned, finding, runner
from shared import review_coverage as coverage
from shared import private_state
from shared.private_state import read, write
from shared.review_sessions import Session, result_artifact
from preparation_fixtures import interrupt_after_checkpoint


spec = importlib.util.spec_from_file_location('entrypoint_iteration_state',
    runner.ROOT / 'dk-pr-iter/scripts/run_state.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


@pytest.fixture
def main_call(pinned, tmp_path, monkeypatch, capsys):
    context, decisions, prepared, names, git = pinned
    preparation_path = tmp_path / 'preparation.json'
    real_run = runner.run

    def configured(args, cwd=None):
        if args[:2] == ['python3', str(runner.ROOT / 'dk-config/scripts/repo_rules.py')]:
            return json.dumps({'reviewers': names})
        return real_run(args, cwd)

    monkeypatch.setattr(runner, 'run', configured)
    # These tests exercise mandatory state; optional analytics has its own suite.
    monkeypatch.setattr(runner, 'for_iteration', lambda *args: None)

    def invoke(*options):
        prepared['preparation_id'] = runner.preparation_api.digest(
            {k: v for k, v in prepared.items() if k != 'preparation_id'})
        write(preparation_path, prepared)
        monkeypatch.setattr(sys, 'argv', ['run_reviewers.py', '--context',
            prepared['sources']['context']['path'], '--decisions-file',
            prepared['sources']['decisions']['path'], '--preparation-file',
            str(preparation_path), *map(str, options)])
        runner.main()
        return json.loads(capsys.readouterr().out)

    return invoke


def iteration_record(tmp_path, context, prepared, cycle=2):
    preparation_path = tmp_path / 'preparation.json'
    write(preparation_path, prepared)
    record = {}
    from preparation_fixtures import promote
    decisions = json.loads(Path(prepared["sources"]["decisions"]["path"]).read_text())
    promote(prepared, context, decisions)
    write(preparation_path, prepared)
    state.start_run(record)
    state.attach_preparation(record, preparation_path, context['url'], context['head_sha'])
    for _ in range(cycle):
        state.next_cycle(record, context['head_sha'])
    path = tmp_path / 'run.json'
    write(path, record)
    return path


def test_legacy_main_allocates_registers_and_returns_completed_session(main_call, pinned, tmp_path):
    context, decisions, prepared, names, git = pinned
    path = iteration_record(tmp_path, context, prepared)
    original = read(path, {})
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers, \
            patch.object(runner, 'for_iteration', return_value=None) as capture:
        result = main_call('--run-record', path)
    assert sorted(call.args[0] for call in providers.call_args_list) == sorted(names)
    assert capture.call_args.args[3] == 2
    assert result['complete'] and result['executed_reviewers'] == names
    assert result['reused_reviewers'] == [] and result['skipped_reviewers'] == []
    record = read(path, {})
    assert record['iteration_run'] == original['iteration_run']
    assert record['cycles'] == original['cycles']
    assert len(record['review_sessions']) == len(record['review_results']) == 1
    binding, registered = record['review_sessions'][0], record['review_results'][0]
    assert binding['cycle'] == registered['cycle'] == 2
    assert binding['attempt'] == registered['attempt'] == 1
    assert binding['iteration_run_id'] == registered['iteration_run_id'] == record['iteration_run']['id']
    assert result['review_session']['path'] == binding['session_file']
    assert registered['session_file'] == binding['session_file']
    assert coverage.verify(result['review_artifact'], prepared)['session_id'] == result['review_session']['id']
    before = Path(binding['session_file']).read_bytes()
    with patch.object(runner, 'run_skill_reviewer') as providers, \
            patch.object(runner, 'Session', side_effect=AssertionError('completed session reopened')), \
            patch.object(runner, 'for_iteration') as capture:
        reused = main_call('--run-record', path)
    providers.assert_not_called()
    capture.assert_not_called()
    assert reused == {**result, 'executed_reviewers': [], 'reused_reviewers': names}
    assert Path(binding['session_file']).read_bytes() == before
    assert read(path, {}) == record


@pytest.mark.parametrize(('mode', 'route'), [
    ('standalone', 'session'), ('iteration', 'session'), ('iteration', 'legacy')])
def test_main_resumes_checkpoint_after_interrupt_before_aggregate_finish(
        main_call, pinned, tmp_path, mode, route):
    context, decisions, prepared, names, git = pinned
    prepared['mode'] = mode
    prepared['preparation_id'] = runner.preparation_api.digest(
        {k: v for k, v in prepared.items() if k != 'preparation_id'})
    if route == 'legacy':
        run_path = iteration_record(tmp_path, context, prepared)
        original_record = read(run_path, {})
        options = ['--run-record', run_path]
    else:
        session_path = tmp_path / 'session.json'
        options = ['--session-file', session_path]
    sessions = []
    def create_session(*args, **kwargs):
        session = Session(*args, **kwargs)
        sessions.append(session)
        return session
    with patch.object(runner, 'Session', side_effect=create_session), \
            patch.object(runner, 'run_skill_reviewer',
            side_effect=interrupt_after_checkpoint(names[0], {'findings': [finding()]},
                lambda: sessions[0].cached(names[0]) is not None)) as providers:
        with pytest.raises(KeyboardInterrupt):
            main_call(*options)
    assert sorted(call.args[0] for call in providers.call_args_list) == sorted(names)
    if route == 'legacy':
        record = read(run_path, {})
        assert record['review_results'] == []
        assert len(record['review_sessions']) == 1
        session_path = Path(record['review_sessions'][0]['session_file'])
    interrupted = read(session_path, {})
    assert interrupted['status'] == 'active' and interrupted['invocations'] == 1
    assert interrupted['results'] == []
    assert {name: item['status'] for name, item in interrupted['reviews'].items()} == {
        names[0]: 'completed', names[1]: 'running'}
    checkpoint = interrupted['reviews'][names[0]]['checkpoint']
    assert coverage.digest(read(Path(checkpoint['path']), {})) == checkpoint['sha256']
    assert not list(session_path.parent.glob('review-results/*.json'))
    if route == 'session':
        options.append('--resume')
    with patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        result = main_call(*options)
    assert sorted(call.args[0] for call in providers.call_args_list) == sorted(names[1:])
    assert result['complete'] and result['executed_reviewers'] == names[1:]
    assert result['reused_reviewers'] == names[:1] and result['skipped_reviewers'] == []
    assert result['findings'] == [{**finding(), 'reviewers': names[:1]}]
    saved = read(session_path, {})
    assert saved['id'] == interrupted['id'] == result['review_session']['id']
    assert result['review_session']['path'] == str(session_path)
    assert saved['status'] == 'completed' and saved['invocations'] == 2
    assert saved['reviews'][names[0]]['checkpoint'] == checkpoint
    assert saved['results'] == [result['review_artifact']]
    artifact = result_artifact(session_path, result['review_artifact'])
    assert coverage.verify(result['review_artifact'], prepared) == artifact
    assert artifact['result'] == {key: value for key, value in result.items()
                                  if key not in {'review_session', 'review_artifact', 'preparation_id', 'engineering_id'}}
    if artifact['version'] == 3:
        assert result['preparation_id'] == artifact['preparation_id']
        assert result['engineering_id'] == artifact['engineering_id']
    if route == 'legacy':
        record = read(run_path, {})
        assert record['iteration_run'] == original_record['iteration_run']
        assert record['cycles'] == original_record['cycles']
        assert len(record['review_sessions']) == len(record['review_results']) == 1


@pytest.mark.parametrize(('mode', 'extra'), [
    ('iteration', ['--session-file', 'session.json']),
    ('iteration', ['--resume']), ('standalone', [])])
def test_legacy_main_rejects_incompatible_options_before_allocation(
        main_call, pinned, tmp_path, mode, extra):
    context, decisions, prepared, names, git = pinned
    prepared['mode'] = mode
    record_path = tmp_path / 'not-allocated.json'
    with patch.object(runner, 'run_skill_reviewer') as providers:
        with pytest.raises(ValueError, match='requires iteration mode.*session options'):
            main_call('--run-record', record_path, *extra)
    providers.assert_not_called()
    assert not record_path.exists()


@pytest.mark.parametrize('boundary', ['checkpoint', 'session'])
def test_mandatory_write_failure_cannot_complete_or_cache_failed_reviewer(pinned, tmp_path, boundary):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names, path=tmp_path / 'session.json')
    real_write = private_state.write
    broken = False

    def unavailable(path, value):
        nonlocal broken
        if boundary == 'checkpoint':
            status = value.get('status') if value.get('reviewer') == names[1] else None
            target = path.parent.name == 'review-checkpoints'
        else:
            status = value.get('reviews', {}).get(names[1], {}).get('status')
            target = path == session.path
        if target and status == 'completed':
            broken = True
        if target and broken and status in {'completed', 'failed'}:
            raise OSError('mandatory write unavailable')
        return real_write(path, value)

    with patch.object(private_state, 'write', side_effect=unavailable), \
            patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        with pytest.raises(OSError, match='mandatory write unavailable'), session.execution():
            runner.review(context, names, '', decisions, '', prepared, session=session)
    assert broken and sorted(call.args[0] for call in providers.call_args_list) == sorted(names)
    failed = read(session.path, {})
    assert failed['status'] == 'active' and failed['results'] == []
    assert failed['reviews'][names[0]]['status'] == 'completed'
    assert failed['reviews'][names[1]]['status'] == 'running'
    assert not list(session.path.parent.glob('review-results/*.json'))
    resumed = Session(context, prepared, names, path=session.path, resume=True)
    assert resumed.cached(names[0]) == {'findings': []}
    assert resumed.cached(names[1]) is None
    with resumed.execution(), \
            patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as providers:
        result = runner.review(context, names, '', decisions, '', prepared, session=resumed)
        item = resumed.finish(result)
    assert sorted(call.args[0] for call in providers.call_args_list) == sorted(names[1:])
    assert result['complete'] and result['executed_reviewers'] == names[1:]
    assert result['reused_reviewers'] == names[:1]
    assert read(session.path, {})['reviews'][names[0]] == failed['reviews'][names[0]]
    assert coverage.verify(item, prepared)['result'] == result
