"""Session evidence cannot be reassigned across reviewers, sessions or identities."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from test_review_sessions import pinned, original_pinned
from test_stats_capture import runner
from shared import review_coverage as coverage
from shared.private_state import read, write
from shared.review_sessions import Session, result_artifact


describe_reviewer = runner.describe_reviewer
spec = importlib.util.spec_from_file_location('provenance_iteration',
    runner.ROOT / 'dk-pr-iter/scripts/run_state.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


@pytest.mark.parametrize('replacement', ['other_reviewer', 'running', 'other_session', 'old_tree',
                                         'other_evidence', 'unsupported'])
def test_completed_checkpoint_requires_its_reviewer_status_and_session(pinned, replacement):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names)
    session.record(names[0], 'completed', {'findings': []})
    if replacement == 'other_reviewer':
        session.record(names[1], 'completed', {'findings': []})
        source, reviewer = session, names[1]
    elif replacement == 'running':
        session.record(names[0], 'running')
        source, reviewer = session, names[0]
    elif replacement in {'other_session', 'old_tree'}:
        source, reviewer = session, names[0]
        if replacement == 'old_tree':
            (Path(context['checkout']) / 'x.py').write_text('unreviewed current tree\n')
            git('add', 'x.py')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'Current tree')
            context = {**context, 'head_sha': git('rev-parse', 'HEAD')}
            decisions = {**decisions, 'head_sha': context['head_sha']}
            prepared = copy.deepcopy(prepared)
            for key, value in (('context', context), ('decisions', decisions)):
                path = Path(prepared['sources'][key]['path'])
                write(path, value)
                prepared['sources'][key]['sha256'] = runner.preparation_api.file_hash(path)
            prepared['identity'].update(head_sha=context['head_sha'],
                                        decisions_hash=runner.preparation_api.digest(decisions))
            prepared['preparation_id'] = runner.preparation_api.digest(
                {k: v for k, v in prepared.items() if k != 'preparation_id'})
        session = Session(context, prepared, names)
    else:
        source, reviewer = session, names[0]
    saved = read(session.path, {})
    item = copy.deepcopy(read(source.path, {})['reviews'][reviewer])
    if replacement in {'other_evidence', 'unsupported'}:
        checkpoint = read(Path(item['checkpoint']['path']), {})
        if replacement == 'unsupported':
            checkpoint.pop('checkpoint_version')
        else:
            checkpoint['session_identity']['scope'] = 'other evidence'
        path = session.path.parent / 'invalid-checkpoint.json'
        write(path, checkpoint)
        item['checkpoint'] = {'path': str(path), 'sha256': coverage.digest(checkpoint)}
    # Every reference points to an existing artifact with its valid hash; only
    # the mutable review slot/status is reassigned to the requested reviewer.
    item['status'] = 'completed'
    saved['reviews'][names[0]] = item
    write(session.path, saved)
    with pytest.raises(ValueError, match='does not match this session and reviewer'):
        session.cached(names[0])


@pytest.mark.parametrize('change', ['missing', 'pr_url', 'head', 'base', 'preparation_id',
                                    'tree', 'scope', 'scope_text', 'rules_text', 'reviewers'])
def test_finished_result_requires_the_complete_unchanged_session_identity(pinned, change):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names, scope='requested scope', rules='repository rules')
    result = {'complete': True, 'findings': [], 'executed_reviewers': names,
              'reused_reviewers': [], 'skipped_reviewers': []}
    item = session.finish(result)
    assert result_artifact(session.path, item)['session_identity'] == session.identity
    saved = read(session.path, {})
    if change == 'missing':
        saved.pop('identity')
    else:
        saved['identity'][change] = names[:1] if change == 'reviewers' else 'different evidence'
    write(session.path, saved)
    with pytest.raises(ValueError, match='latest registered'):
        result_artifact(session.path, item)


@pytest.mark.parametrize('change', ['missing', 'head', 'scope', 'preparation_id', 'pr_url', 'tree'])
def test_result_headers_require_the_bound_generic_identity(pinned, change):
    context, decisions, prepared, names, git = pinned
    session = Session(context, prepared, names)
    item = session.finish({'complete': True, 'findings': [], 'executed_reviewers': names,
                           'reused_reviewers': [], 'skipped_reviewers': []})
    artifact = coverage.load(item)
    if change == 'missing':
        artifact.pop('session_identity')
    else:
        artifact['session_identity'][change] = 'different evidence'
    path = session.path.parent / 'invalid-result.json'
    write(path, artifact)
    with pytest.raises(ValueError, match='session identity'):
        coverage.load({'path': str(path), 'sha256': coverage.digest(artifact)})


@pytest.mark.parametrize('selection', ['command', 'registry'])
@pytest.mark.parametrize('change', ['unchanged', 'changed', 'unresolvable'])
def test_legacy_main_validates_effective_selection_before_completed_reuse(
        pinned, monkeypatch, capsys, selection, change):
    context, decisions, prepared, names, git = pinned
    root = Path(context['patch']).parent
    original_path = os.environ.get('PATH', '')
    bins = [root / 'old-bin', root / 'new-bin']
    for directory in bins:
        directory.mkdir()
        executable = directory / ('provenance-reviewer' if selection == 'command' else 'claude')
        executable.write_text('#!/bin/sh\nexit 99\n')
        executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(bins[0]) + os.pathsep + original_path)
    if selection == 'command':
        provider = {'command': ['provenance-reviewer', '{handoff}']}
    else:
        provider = {'skill': 'fixture:review', 'host': 'claude'}
        plugins = [root / 'old-plugin', root / 'new-plugin']
        for plugin in plugins:
            (plugin / '.claude-plugin').mkdir(parents=True)
            (plugin / '.claude-plugin/plugin.json').write_text(
                json.dumps({'name': 'fixture', 'version': '1.0.0'}))
            (plugin / 'skills/review').mkdir(parents=True)
            (plugin / 'skills/review/SKILL.md').write_text('Read the pinned diff for defects.\n')
        config = root / 'claude-config'
        (config / 'plugins').mkdir(parents=True)
        registry = config / 'plugins/installed_plugins.json'
        def select(plugin):
            registry.write_text(json.dumps({'plugins': {'fixture@local': [
                {'installPath': str(plugin), 'version': '1.0.0'}]} if plugin else {}}))
        select(plugins[0])
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(config))
    monkeypatch.setattr(runner, 'describe_reviewer', describe_reviewer)
    names = [runner.bindings.key(provider)]
    prepared['reviewers'] = {names[0]: describe_reviewer(provider)}
    prepared['analysis']['reviewer_addenda'] = {names[0]: 'Review the pinned diff.'}
    prepared['preparation_id'] = runner.preparation_api.digest(
        {k: v for k, v in prepared.items() if k != 'preparation_id'})
    preparation_path = root / 'preparation.json'
    write(preparation_path, prepared)
    record = {}
    from preparation_fixtures import promote, architecture_review
    promote(prepared, context, decisions)
    write(preparation_path, prepared)
    state.start_run(record)
    state.attach_preparation(record, preparation_path, context['url'], context['head_sha'])
    state.next_cycle(record, context['head_sha'])
    record_path = root / 'run.json'
    binding = state.review_session(record, context['head_sha'], record_path)
    write(record_path, record)
    session = Session(context, prepared, names, path=binding['session_file'])
    result = {'complete': True, 'findings': [], 'executed_reviewers': names,
              'architecture_review': architecture_review(prepared, context, decisions),
              'reused_reviewers': [], 'skipped_reviewers': []}
    session.finish(result)
    # Old descriptor files remain valid even after the effective registry/PATH
    # selection moves elsewhere or becomes unresolvable.
    if change != 'unchanged':
        if selection == 'command':
            monkeypatch.setenv('PATH', (str(bins[1]) + os.pathsep if change == 'changed' else '') + original_path)
        else:
            select(plugins[1] if change == 'changed' else None)
    runner.validate_descriptor(prepared['reviewers'][names[0]])
    argv = ['run_reviewers.py', '--context', prepared['sources']['context']['path'],
            '--decisions-file', prepared['sources']['decisions']['path'],
            '--preparation-file', str(preparation_path), '--run-record', str(record_path)]
    with patch.object(sys, 'argv', argv), \
            patch.object(runner, 'run', return_value=json.dumps({'reviewers': [provider]})), \
            patch.object(runner, 'run_skill_reviewer') as launch:
        if prepared['reviewers'][names[0]].get('execution') or change == 'unchanged':
            runner.main()
            reused = json.loads(capsys.readouterr().out)
            assert reused['executed_reviewers'] == [] and reused['reused_reviewers'] == names
            assert len(read(record_path, {})['review_results']) == 1
        else:
            message = 'effective reviewer instructions changed' if change == 'changed' else 'Missing selected|expected one registry match'
            with pytest.raises(ValueError, match=message):
                runner.main()
            assert not capsys.readouterr().out
            assert read(record_path, {})['review_results'] == []
        launch.assert_not_called()
