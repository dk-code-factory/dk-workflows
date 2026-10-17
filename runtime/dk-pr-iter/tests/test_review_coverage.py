import importlib.util
import json
from pathlib import Path

import pytest

from shared import review_coverage as coverage
from shared.private_state import read, write


def load(name):
    spec = importlib.util.spec_from_file_location('coverage_' + name,
        Path(__file__).parents[1] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = load('run_state')
completion = load('completion')


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return coverage.git(repo, *args)
    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.test')
    (repo / 'code.py').write_text('old\n')
    git('add', '.')
    git('commit', '-qm', 'Initial')
    head = git('rev-parse', 'HEAD')
    context = {'checkout': str(repo), 'repository': str(repo), 'head_sha': head,
               'url': 'https://github.com/o/r/pull/1'}
    context_path = tmp_path / 'context.json'
    context_path.write_text(json.dumps(context))
    decisions = tmp_path / 'decisions.json'
    decisions.write_text(json.dumps({'head_sha': head, 'body_hash': 'body', 'decisions': []}))
    prepared = {'identity': {'head_sha': head, 'base_sha': head, 'pr_url': context['url']},
                'sources': {'context': {'path': str(context_path)}, 'decisions': {'path': str(decisions)}},
                'analysis': {'boundaries': 'original'}, 'reviewers': {'one': {}, 'two': {}},
                'preparation_id': 'original-preparation'}
    monkeypatch.setattr(state, 'work_gate', lambda *a: prepared)
    record = {}
    run = state.start_run(record)
    for _ in range(5):
        state.next_cycle(record, head)
    record_path = tmp_path / 'run.json'
    write(record_path, record)
    result = {'complete': True, 'findings': [], 'executed_reviewers': ['one'],
              'reused_reviewers': [], 'skipped_reviewers': [{'reviewer': 'two', 'reason': 'usage limit'}]}
    item = coverage.retain(record_path, context, prepared, result, run['id'], 5)
    return record_path, context, prepared, result, item, git


def test_completed_five_cycle_request_starts_distinct_run_and_preserves_history(reviewed):
    path, context, prepared, result, item, git = reviewed
    record = read(path, {})
    record['events'] = [{'key': 'published', 'kind': 'push'}]
    record['reply_workflow'] = {'verified': ['reply-url']}
    record['reruns'] = [{'head': context['head_sha'], 'number': 3}]
    old = state.start_run(record, resume=True)['id']
    assert len(record['cycles']) == 5
    with pytest.raises(ValueError, match='active'):
        state.start_run(record)
    state.finish_run(record, 'completed', 'Previous request completed')
    with pytest.raises(ValueError, match='no active'):
        state.start_run(record, resume=True)
    new = state.start_run(record)['id']
    assert new != old and record['cycles'] == [] and record['review_results'] == []
    assert record['run_history'][0]['review_results'] == [item]
    assert len(record['run_history'][0]['cycles']) == 5
    assert record['events'][0]['key'] == 'published'
    assert record['reply_workflow']['verified'] == ['reply-url']
    assert record['reruns'][0]['number'] == 3
    state.next_cycle(record, context['head_sha'])
    write(path, record)
    with pytest.raises(ValueError, match='no configured-reviewer'):
        coverage.verify(path, prepared)
    record['review_results'] = [item]
    write(path, record)
    with pytest.raises(ValueError, match='lacks review coverage'):
        coverage.verify(path, prepared)


@pytest.mark.parametrize('stage', ['review', 'complete'])
def test_final_cycle_fix_blocks_passing_ci_completion(reviewed, monkeypatch, stage):
    path, context, prepared, result, item, git = reviewed
    assert coverage.verify(path, prepared)['result']['skipped_reviewers'] == result['skipped_reviewers']
    monkeypatch.setattr(completion, 'current_preparation', lambda *a: prepared)
    monkeypatch.setattr(completion.preparation_api, 'verify_completion', lambda *a: None)
    monkeypatch.setattr(completion.series_assessment, 'check', lambda *a: 'clean')
    def gate():
        head = prepared['identity']['head_sha']
        return completion.completion_gate(head, head, head, True, False, 'success',
            stage=stage, preparation='saved', run_record=path, pr_url=context['url'], verification='saved')
    assert gate() == 'clean'
    (Path(context['checkout']) / 'code.py').write_text('fixed after cycle five\n')
    git('add', '.')
    git('commit', '-qm', 'Final fix')
    prepared['identity']['head_sha'] = git('rev-parse', 'HEAD')
    assert gate() == 'review_coverage_invalid'
    with pytest.raises(ValueError, match='five review cycles'):
        state.next_cycle(read(path, {}), prepared['identity']['head_sha'])


def test_tree_equivalent_regroup_preserves_coverage_but_scope_changes_do_not(reviewed):
    path, context, prepared, result, item, git = reviewed
    git('commit', '--amend', '-qm', 'Regrouped message')
    regrouped = git('rev-parse', 'HEAD')
    assert regrouped != context['head_sha']
    prepared['identity']['head_sha'] = regrouped
    decisions_path = Path(prepared['sources']['decisions']['path'])
    decisions = json.loads(decisions_path.read_text())
    decisions['head_sha'] = regrouped
    decisions_path.write_text(json.dumps(decisions))
    assert coverage.verify(path, prepared)['head'] == context['head_sha']
    prepared['sources']['rules/0'] = {'sha256': 'new rules'}
    with pytest.raises(ValueError, match='scope lacks'):
        coverage.verify(path, prepared)
    del prepared['sources']['rules/0']
    decisions['decisions'] = ['new scope']
    decisions_path.write_text(json.dumps(decisions))
    with pytest.raises(ValueError, match='scope lacks'):
        coverage.verify(path, prepared)


@pytest.mark.parametrize('change', ['tamper', 'missing', 'all_skipped', 'incomplete', 'unassessed', 'actionable'])
def test_invalid_results_cannot_authorize_completion(reviewed, change):
    path, context, prepared, result, item, git = reviewed
    if change == 'tamper':
        Path(item['path']).write_text('{}')
    elif change == 'missing':
        Path(item['path']).unlink()
    else:
        if change == 'all_skipped':
            result['executed_reviewers'] = []
            result['skipped_reviewers'].append({'reviewer': 'one', 'reason': 'limit'})
        if change == 'incomplete':
            result['complete'] = False
        if change in {'unassessed', 'actionable'}:
            finding = {'path': 'code.py', 'line': 1, 'message': 'bug', 'reviewers': ['one']}
            result['findings'] = [finding]
            if change == 'actionable':
                record = read(path, {})
                ref = 'review-finding:' + coverage.digest(finding)
                record['threads'] = {ref: {context['head_sha']: {'disposition': 'fix'}}}
                record['assessments'] = [{'reference': ref, 'head': context['head_sha'],
                    'preparation_id': prepared['preparation_id'], 'disposition': 'within_scope'}]
                write(path, record)
        coverage.retain(path, context, prepared, result, read(path, {})['iteration_run']['id'], 5)
    with pytest.raises((ValueError, OSError)):
        coverage.verify(path, prepared)


def test_legacy_exhausted_counters_require_explicit_adoption():
    record = {'cycles': [{'number': n, 'head': 'a' * 40} for n in range(1, 6)]}
    with pytest.raises(ValueError, match='legacy'):
        state.start_run(record)
    state.adopt_run(record)
    assert len(record['cycles']) == 5
    state.finish_run(record, 'limited', 'Prior review budget exhausted; no fresh review')
    state.start_run(record)
    assert record['cycles'] == [] and len(record['run_history'][0]['cycles']) == 5


def test_starting_another_cycle_invalidates_previous_cycle_completion(reviewed):
    path, context, prepared, result, item, git = reviewed
    record = read(path, {})
    record['cycles'].pop()
    write(path, record)
    coverage.retain(path, context, prepared, result, record['iteration_run']['id'], 4)
    assert coverage.verify(path, prepared)['cycle'] == 4
    record = read(path, {})
    state.next_cycle(record, context['head_sha'])
    write(path, record)
    with pytest.raises(ValueError, match='lacks review coverage'):
        coverage.verify(path, prepared)
