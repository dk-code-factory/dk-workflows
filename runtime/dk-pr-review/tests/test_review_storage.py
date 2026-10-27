"""Private review writes stay outside both Git roots, including derived paths."""
import json
import multiprocessing
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from test_review_sessions import pinned, original_pinned
from test_review_entrypoints import main_call, iteration_record
from test_stats_capture import runner
from shared import review_coverage as coverage
from shared.private_state import read, write
from shared.review_sessions import Session, result_artifact


@pytest.fixture
def storage_layout(pinned, tmp_path):
    context, decisions, prepared, names, git = pinned
    repository = tmp_path / 'repository'
    subprocess.check_call(['git', 'clone', '-q', context['checkout'], str(repository)])
    context['repository'] = str(repository)
    path = Path(prepared['sources']['context']['path'])
    write(path, context)
    prepared['sources']['context']['sha256'] = runner.preparation_api.file_hash(path)
    prepared['preparation_id'] = runner.preparation_api.digest(
        {k: v for k, v in prepared.items() if k != 'preparation_id'})
    return context, decisions, prepared, names


def assert_clean(context):
    for key in ('repository', 'checkout'):
        assert coverage.git(context[key], 'status', '--porcelain') == ''


@pytest.mark.parametrize('name', ['review-results', 'review-checkpoints'])
@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_derived_directory_name_collision_rejected_before_execution(storage_layout, tmp_path, name, root):
    context, decisions, prepared, names = storage_layout
    destination = tmp_path / name
    Path(context[root]).rename(destination)
    context[root] = str(destination)
    session_path = tmp_path / 'session.json'
    with patch.object(runner, 'run_skill_reviewer') as provider:
        with pytest.raises(ValueError, match='outside'):
            session = Session(context, prepared, names, path=session_path)
            with session.execution():
                runner.review(context, names, '', decisions, '', prepared, session=session)
    provider.assert_not_called()
    assert not session_path.exists()
    assert not session_path.with_name('session.json.lock').exists()
    assert_clean(context)


@pytest.mark.parametrize('name', ['review-results', 'review-checkpoints'])
@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_symlinked_derived_directory_rejected_before_session_creation(storage_layout, tmp_path, name, root):
    context, decisions, prepared, names = storage_layout
    (tmp_path / name).symlink_to(context[root], target_is_directory=True)
    path = tmp_path / 'session.json'
    with pytest.raises(ValueError, match='outside'):
        Session(context, prepared, names, path=path)
    assert not path.exists()
    assert_clean(context)


@pytest.mark.parametrize('name', ['session.json.lock', 'session.json.execution.lock'])
@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_initial_lock_destination_rejected_before_private_writes(storage_layout, tmp_path, name, root):
    context, decisions, prepared, names = storage_layout
    (tmp_path / name).symlink_to(Path(context[root]) / 'private-lock')
    path = tmp_path / 'session.json'
    with pytest.raises(ValueError, match='outside'):
        Session(context, prepared, names, path=path)
    assert not path.exists()
    assert_clean(context)


@pytest.mark.parametrize('name', ['session.json.lock', 'session.json.execution.lock',
                                 'review-checkpoints', 'review-results'])
def test_changed_destination_preserves_session_before_execution(storage_layout, tmp_path, name):
    context, decisions, prepared, names = storage_layout
    session = Session(context, prepared, names, path=tmp_path / 'session.json')
    before = session.path.read_bytes()
    destination = tmp_path / name
    destination.unlink(missing_ok=True)
    target = (Path(context['checkout']) if name.startswith('review-')
              else Path(context['checkout']) / 'private-lock')
    destination.symlink_to(target)
    with patch.object(runner, 'run_skill_reviewer') as provider:
        with pytest.raises(ValueError, match='outside'):
            with session.execution():
                runner.review(context, names, '', decisions, '', prepared, session=session)
    provider.assert_not_called()
    assert session.path.read_bytes() == before
    assert_clean(context)


@pytest.mark.parametrize('kind', ['checkpoint', 'result'])
@pytest.mark.parametrize('lock', [False, True])
def test_actual_artifact_and_lock_destinations_are_rechecked(storage_layout, tmp_path, kind, lock):
    context, decisions, prepared, names = storage_layout
    session = Session(context, prepared, names, path=tmp_path / 'session.json')
    result = {'complete': False, 'findings': [], 'executed_reviewers': names[:1],
              'reused_reviewers': [], 'skipped_reviewers': []}
    session.record(names[0], 'completed', {'findings': []})
    item = session.finish(result)
    before = session.path.read_bytes()
    checkpoint = read(session.path, {})['reviews'][names[0]]['checkpoint']
    artifact = Path((checkpoint if kind == 'checkpoint' else item)['path'])
    other_artifact = Path((item if kind == 'checkpoint' else checkpoint)['path'])
    earlier_evidence = other_artifact.read_bytes()
    guarded = artifact.with_name(artifact.name + '.lock') if lock else artifact
    guarded.unlink()
    target = Path(context['repository']) / ('private-lock' if lock else 'x.py')
    guarded.symlink_to(target)
    with pytest.raises(ValueError, match='outside'):
        if kind == 'checkpoint':
            session.record(names[0], 'completed', {'findings': []})
        else:
            # The shared result writer also guards direct callers.
            coverage.retain(session.path, context, prepared, result, session.id)
    assert session.path.read_bytes() == before
    assert other_artifact.read_bytes() == earlier_evidence
    assert_clean(context)


def test_external_layout_executes_and_retains_reusable_evidence(storage_layout, tmp_path):
    context, decisions, prepared, names = storage_layout
    session = Session(context, prepared, names, path=tmp_path / 'evidence/session.json')
    with session.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as provider:
        result = runner.review(context, names, '', decisions, '', prepared, session=session)
        item = session.finish(result)
    assert provider.call_count == len(names)
    assert result['complete'] and result['executed_reviewers'] == names
    assert result_artifact(session.path, item) == coverage.verify(item, prepared)
    assert all(session.cached(name) == {'findings': []} for name in names)
    assert json.loads(session.path.read_text())['invocations'] == 1
    assert_clean(context)


def execution_outcome(session):
    """Bound the actual flock/update interaction so a regression cannot hang pytest."""
    process = multiprocessing.get_context('fork')
    receiver, sender = process.Pipe(duplex=False)
    def invoke():
        try:
            with session.execution():
                pass
            sender.send(None)
        except ValueError as exc:
            sender.send(str(exc))
        finally:
            sender.close()
    child = process.Process(target=invoke)
    child.start()
    sender.close()
    try:
        child.join(timeout=3)
        assert not child.is_alive(), 'session execution blocked on its own lock'
        assert child.exitcode == 0
        assert receiver.poll(), 'session execution returned no outcome'
        return receiver.recv()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=3)
        receiver.close()


@pytest.mark.parametrize('filename', ['review.json', 'review.execution'])
def test_custom_session_filename_does_not_deadlock(pinned, tmp_path, filename):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names, path=tmp_path / filename)
    assert execution_outcome(session) is None
    assert read(session.path, {})['invocations'] == 1
    assert not git('status', '--porcelain')


@pytest.mark.parametrize(('source', 'target', 'hardlink'), [
    ('execution', 'mutation', False), ('execution', 'mutation', True),
    ('execution', 'session', False), ('mutation', 'session', False)])
def test_session_lock_alias_rejected_without_corrupting_evidence(pinned, tmp_path, source, target, hardlink):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names, path=tmp_path / 'session.json')
    before = session.path.read_bytes()
    paths = {'session': session.path, 'mutation': tmp_path / 'session.json.lock',
             'execution': tmp_path / 'session.json.execution.lock'}
    paths[source].unlink(missing_ok=True)
    if hardlink:
        paths[source].hardlink_to(paths[target])
    else:
        paths[source].symlink_to(paths[target])
    assert 'must be distinct' in execution_outcome(session)
    assert session.path.read_bytes() == before
    assert not git('status', '--porcelain')


@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_legacy_main_rejects_record_lock_before_allocation_even_with_wrong_cycle(
        storage_layout, main_call, tmp_path, root):
    context, decisions, prepared, names = storage_layout
    path = iteration_record(tmp_path, context, prepared, cycle=2)
    context = runner.preparation_api.context_for(prepared)
    before_record = path.read_bytes()
    before_roots = {key: (Path(context[key]) / 'x.py').read_bytes()
                    for key in ('repository', 'checkout')}
    path.with_name(path.name + '.lock').symlink_to(Path(context[root]) / 'x.py')
    with patch.object(runner, 'run_skill_reviewer') as provider:
        with pytest.raises(ValueError, match='outside'):
            main_call('--run-record', path, '--stats-cycle', 99)
    provider.assert_not_called()
    assert path.read_bytes() == before_record
    for key, content in before_roots.items():
        assert (Path(context[key]) / 'x.py').read_bytes() == content
    assert_clean(context)


@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_legacy_main_rechecks_record_lock_before_registration(
        storage_layout, main_call, tmp_path, root):
    context, decisions, prepared, names = storage_layout
    path = iteration_record(tmp_path, context, prepared, cycle=2)
    context = runner.preparation_api.context_for(prepared)
    before_roots = {key: (Path(context[key]) / 'x.py').read_bytes()
                    for key in ('repository', 'checkout')}
    allocated_record = None
    def provider(name, *args):
        nonlocal allocated_record
        if name == names[0]:
            allocated_record = path.read_bytes()
            lock = path.with_name(path.name + '.lock')
            lock.unlink()
            lock.symlink_to(Path(context[root]) / 'x.py')
        return {'findings': []}
    with patch.object(runner, 'run_skill_reviewer', side_effect=provider) as calls:
        with pytest.raises(ValueError, match='outside'):
            main_call('--run-record', path)
    assert calls.call_count == len(names)
    assert path.read_bytes() == allocated_record
    record = read(path, {})
    assert record['review_results'] == [] and len(record['review_sessions']) == 1
    session_path = record['review_sessions'][0]['session_file']
    session = read(Path(session_path), {})
    assert session['status'] == 'completed'
    assert result_artifact(session_path, session['results'][-1])['result']['complete']
    for key, content in before_roots.items():
        assert (Path(context[key]) / 'x.py').read_bytes() == content
    assert_clean(context)


def interrupted_evidence(context, prepared, names, path):
    session = Session(context, prepared, names, path=path)
    result = {'complete': False, 'reason': 'controlled interruption', 'findings': [],
              'executed_reviewers': names[:1], 'reused_reviewers': [], 'skipped_reviewers': []}
    with session.execution():
        session.record(names[0], 'completed', {'findings': []})
        item = session.finish(result)
    return session, item


@pytest.mark.parametrize('suffix', ['.lock', '.execution.lock'])
@pytest.mark.parametrize('existing_first', [False, True])
def test_lock_named_sessions_reject_in_either_creation_order(
        storage_layout, main_call, tmp_path, suffix, existing_first):
    context, decisions, prepared, names = storage_layout
    path = tmp_path / 'review'
    if existing_first:
        session, item = interrupted_evidence(context, prepared, names, path)
        checkpoint = read(session.path, {})['reviews'][names[0]]['checkpoint']
        before = {entry: entry.read_bytes() for entry in
                  (session.path, Path(item['path']), Path(checkpoint['path']))}
        locks = set(tmp_path.glob('review*.lock'))
    with patch.object(runner, 'run_skill_reviewer') as provider:
        with pytest.raises(ValueError, match='ending in .lock are reserved'):
            main_call('--session-file', tmp_path / ('review' + suffix))
    provider.assert_not_called()
    if existing_first:
        assert all(entry.read_bytes() == content for entry, content in before.items())
        assert set(tmp_path.glob('review*.lock')) == locks
    else:
        assert not path.exists() and not path.with_name('review' + suffix).exists()
        session, item = interrupted_evidence(context, prepared, names, path)
    assert result_artifact(path, item)['session_id'] == session.id
    resumed = Session(context, prepared, names, path=path, resume=True)
    assert resumed.cached(names[0]) == {'findings': []}
    assert_clean(context)


@pytest.mark.parametrize('suffix', ['.lock', '.execution.lock'])
def test_earlier_lock_named_evidence_blocks_later_sibling_without_truncation(
        storage_layout, main_call, tmp_path, suffix):
    context, decisions, prepared, names = storage_layout
    session, item = interrupted_evidence(context, prepared, names, tmp_path / 'earlier.json')
    earlier_path = tmp_path / ('review' + suffix)
    # Session identity is independent of its JSON filename. This is the valid
    # on-disk state produced by an earlier version accepting the reserved name.
    session.path.rename(earlier_path)
    assert result_artifact(earlier_path, item)['session_id'] == session.id
    checkpoint = read(earlier_path, {})['reviews'][names[0]]['checkpoint']
    before = {path: path.read_bytes() for path in
              (earlier_path, Path(item['path']), Path(checkpoint['path']))}
    with patch.object(runner, 'run_skill_reviewer') as provider:
        with pytest.raises(ValueError, match='lock destination contains existing data'):
            main_call('--session-file', tmp_path / 'review')
    provider.assert_not_called()
    assert not (tmp_path / 'review').exists()
    assert all(path.read_bytes() == content for path, content in before.items())
    assert result_artifact(earlier_path, item)['session_id'] == session.id
    assert coverage.digest(read(Path(checkpoint['path']), {})) == checkpoint['sha256']
    recovered = tmp_path / 'recovered.json'
    earlier_path.rename(recovered)
    resumed = Session(context, prepared, names, path=recovered, resume=True)
    assert resumed.cached(names[0]) == {'findings': []}
    assert result_artifact(recovered, item)['session_id'] == session.id
    assert_clean(context)
