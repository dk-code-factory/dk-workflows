import importlib.util
import json
from pathlib import Path

import pytest

from shared import review_coverage as coverage, review_sessions
from shared.private_state import read, write


def load(name):
    spec = importlib.util.spec_from_file_location('coverage_' + name,
        Path(__file__).parents[1] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = load('run_state')
completion = load('completion')



def retain_review(path, context, prepared, result, number):
    record = read(path, {})
    binding = state.review_session(record, context['head_sha'], path)
    write(path, record)
    session_path = Path(binding['session_file'])
    if not session_path.exists():
        session = review_sessions.Session(context, prepared, list(prepared['reviewers']), path=session_path)
        item = session.finish(result)
    else:
        saved = read(session_path, {})
        item = coverage.retain(session_path, context, prepared, result, saved['id'])
        saved['results'].append(item)
        saved['status'] = 'completed' if result['complete'] else 'incomplete'
        write(session_path, saved)
    return state.mutate(path, {}, lambda record: state.register_review(record, {'review_artifact': item}, context['head_sha']))

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
    item = retain_review(record_path, context, prepared, result, 5)
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
        state.verify_review(path, prepared)
    record['review_results'] = [item]
    write(path, record)
    with pytest.raises(ValueError, match='lacks review coverage'):
        state.verify_review(path, prepared)


@pytest.mark.parametrize('stage', ['review', 'complete'])
def test_final_cycle_fix_blocks_passing_ci_completion(reviewed, monkeypatch, stage):
    path, context, prepared, result, item, git = reviewed
    assert state.verify_review(path, prepared)['result']['skipped_reviewers'] == result['skipped_reviewers']
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
    assert state.verify_review(path, prepared)['head'] == context['head_sha']
    prepared['sources']['rules/0'] = {'sha256': 'new rules'}
    with pytest.raises(ValueError, match='scope lacks'):
        state.verify_review(path, prepared)
    del prepared['sources']['rules/0']
    decisions['decisions'] = ['new scope']
    decisions_path.write_text(json.dumps(decisions))
    with pytest.raises(ValueError, match='scope lacks'):
        state.verify_review(path, prepared)


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
        retain_review(path, context, prepared, result, 5)
    with pytest.raises((ValueError, OSError)):
        state.verify_review(path, prepared)


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
    retain_review(path, context, prepared, result, 4)
    assert read(path, {})['review_results'][-1]['cycle'] == 4
    state.verify_review(path, prepared)
    record = read(path, {})
    state.next_cycle(record, context['head_sha'])
    write(path, record)
    with pytest.raises(ValueError, match='lacks review coverage'):
        state.verify_review(path, prepared)


@pytest.mark.parametrize('change', [None, 'run', 'cycle', 'tree', 'scope', 'incomplete'])
def test_legacy_coverage_keeps_original_guards_without_creating_sessions(reviewed, change):
    path, context, prepared, result, item, git = reviewed
    record = read(path, {})
    artifact = coverage.load(item)
    artifact.pop('session_id')
    artifact.update(version=1, iteration_run_id=record['iteration_run']['id'], cycle=5)
    if change == 'run': artifact['iteration_run_id'] = 'older-run'
    if change == 'cycle': artifact['cycle'] = 4
    if change == 'tree': artifact['tree'] = 'a' * 40
    if change == 'scope': artifact['scope'] = 'older-scope'
    if change == 'incomplete': artifact['result']['complete'] = False
    legacy_path = path.parent / 'legacy-review.json'
    write(legacy_path, artifact)
    reference = {'path': str(legacy_path), 'sha256': coverage.digest(artifact)}
    record['review_results'], record['review_sessions'] = [reference], []
    write(path, record)
    if change:
        with pytest.raises(ValueError):
            state.verify_review(path, prepared)
    else:
        assert state.verify_review(path, prepared)['version'] == 1
        assert read(path, {})['review_sessions'] == []
        assert len(read(path, {})['cycles']) == 5
    with pytest.raises(ValueError, match='latest registered'):
        review_sessions.result_artifact(item['session_file'], reference)


def test_legacy_runner_adapter_reuses_only_its_bound_completed_session(reviewed):
    path, context, prepared, result, item, git = reviewed
    options = state.legacy_review_options(path, context, prepared, list(prepared['reviewers']), '', '', 5)
    assert options['result']['executed_reviewers'] == []
    assert options['result']['reused_reviewers'] == ['one']
    assert options['result']['review_artifact']['path'] == item['path']
    state.legacy_register_review(path, options['result'], context['head_sha'])
    assert len(read(path, {})['review_results']) == 1
    with pytest.raises(ValueError, match='stats-cycle'):
        state.legacy_review_options(path, context, prepared, list(prepared['reviewers']), '', '', 4)
    with pytest.raises(ValueError, match='evidence changed'):
        state.legacy_review_options(path, context, prepared, list(prepared['reviewers']), 'new scope', '', 5)
