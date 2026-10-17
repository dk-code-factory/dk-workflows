"""Private review writes stay outside both Git roots, including derived paths."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from test_review_sessions import pinned, original_pinned
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


@pytest.mark.parametrize('name', ['session.json.lock', 'session.execution.lock'])
@pytest.mark.parametrize('root', ['repository', 'checkout'])
def test_initial_lock_destination_rejected_before_private_writes(storage_layout, tmp_path, name, root):
    context, decisions, prepared, names = storage_layout
    (tmp_path / name).symlink_to(Path(context[root]) / 'private-lock')
    path = tmp_path / 'session.json'
    with pytest.raises(ValueError, match='outside'):
        Session(context, prepared, names, path=path)
    assert not path.exists()
    assert_clean(context)


@pytest.mark.parametrize('name', ['session.json.lock', 'session.execution.lock',
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
