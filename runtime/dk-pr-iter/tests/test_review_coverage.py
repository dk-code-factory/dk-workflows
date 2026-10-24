import importlib.util
import json
import sys
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
    assert binding['cycle'] == number
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


def failed_review(reviewed, all_failed=False):
    path, context, prepared, result, item, git = reviewed
    names = ['one', 'two'] if all_failed else ['two']
    result = dict(result, complete=False, skipped_reviewers=[],
                  executed_reviewers=[] if all_failed else ['one'],
                  failed_reviewers=[{'reviewer': name, 'status': 'incomplete', 'reason': 'API DNS error'}
                                    for name in names])
    item = retain_review(path, context, prepared, result, 5)
    return path, context, prepared, result, item, git


def accept_failures(path, context, names):
    record = read(path, {})
    entry = state.accept_review_failures(record, context['head_sha'], names,
        'User: continue despite these reviewer failures.', 'Accepted API DNS failure for this result.')
    write(path, record)
    return entry


@pytest.mark.parametrize('all_failed', [False, True])
@pytest.mark.parametrize('stage', ['review', 'complete'])
def test_explicit_acceptance_reaches_completion_without_falsifying_reviews(reviewed, monkeypatch,
                                                                         all_failed, stage):
    path, context, prepared, result, item, git = failed_review(reviewed, all_failed)
    with pytest.raises(ValueError, match='incomplete'):
        state.verify_review(path, prepared)
    names = [f['reviewer'] for f in result['failed_reviewers']]
    entry = accept_failures(path, context, names)
    assert entry['source'].startswith('User:') and entry['review_artifact']['sha256'] == item['sha256']
    verified = state.verify_review(path, prepared)
    assert verified['result']['complete'] is False
    assert verified['accepted_failures'] == names
    assert coverage.load(item)['result']['complete'] is False
    assert len(read(path, {})['cycles']) == 5
    monkeypatch.setattr(completion, 'current_preparation', lambda *a: prepared)
    monkeypatch.setattr(completion.preparation_api, 'verify_completion', lambda *a: None)
    monkeypatch.setattr(completion.series_assessment, 'check', lambda *a: 'clean')
    args = (context['head_sha'],) * 3
    kwargs = dict(stage=stage, preparation='saved', run_record=path,
                  pr_url=context['url'], verification='saved')
    # No false --review-complete assertion is required for an accepted incomplete result.
    assert completion.completion_gate(*args, False, False, 'success', **kwargs) == 'clean'
    assert completion.completion_gate(*args, False, False, 'failure', **kwargs) == 'ci_not_green'
    assert completion.completion_gate(*args, False, True, 'success', **kwargs) == 'actionable_work'


@pytest.mark.parametrize('change', ['source', 'reason', 'reviewer', 'guard', 'missing_reviewer'])
def test_acceptance_cannot_bypass_missing_evidence_or_unrun_reviewers(reviewed, change):
    path, context, prepared, result, item, git = failed_review(reviewed)
    if change in {'guard', 'missing_reviewer'}:
        if change == 'guard':
            result['guard_failure'] = True
        else:
            result['executed_reviewers'] = []
        retain_review(path, context, prepared, result, 5)
    record = read(path, {})
    with pytest.raises(ValueError):
        state.accept_review_failures(record, context['head_sha'],
            ['one'] if change == 'reviewer' else ['two'],
            '' if change == 'source' else 'User explicitly accepted failure',
            '' if change == 'reason' else 'API unavailable')


@pytest.mark.parametrize('change', ['tree', 'scope', 'result', 'run', 'human_evidence'])
def test_acceptance_is_bound_to_run_result_tree_and_scope(reviewed, change):
    path, context, prepared, result, item, git = failed_review(reviewed)
    accept_failures(path, context, ['two'])
    if change == 'tree':
        (Path(context['checkout']) / 'code.py').write_text('new fix\n')
        git('add', '.')
        git('commit', '-qm', 'New fix')
        prepared['identity']['head_sha'] = git('rev-parse', 'HEAD')
    elif change == 'scope':
        prepared['analysis'] = {'boundaries': 'different'}
    elif change == 'result':
        result['failed_reviewers'][0]['reason'] = 'different failure'
        retain_review(path, context, prepared, result, 5)
    elif change == 'run':
        record = read(path, {})
        state.finish_run(record, 'limited', 'Previous request ended')
        state.start_run(record)
        state.next_cycle(record, context['head_sha'])
        write(path, record)
        retain_review(path, context, prepared, result, 1)
    else:
        record = read(path, {})
        del record['accepted_review_failures'][0]['source']
        write(path, record)
    with pytest.raises(ValueError):
        state.verify_review(path, prepared)


def test_acceptance_does_not_waive_findings_and_survives_only_equivalent_regroup(reviewed):
    path, context, prepared, result, item, git = failed_review(reviewed)
    result['findings'] = [{'path': 'code.py', 'line': 1, 'message': 'bug', 'reviewers': ['one']}]
    retain_review(path, context, prepared, result, 5)
    accept_failures(path, context, ['two'])
    with pytest.raises(ValueError, match='finding lacks'):
        state.verify_review(path, prepared)
    record = read(path, {})
    ref = 'review-finding:' + coverage.digest(result['findings'][0])
    record['threads'] = {ref: {context['head_sha']: {'disposition': 'no_change'}}}
    record['assessments'] = [{'reference': ref, 'head': context['head_sha'],
        'preparation_id': prepared['preparation_id'], 'disposition': 'outside_scope'}]
    write(path, record)
    git('commit', '--amend', '-qm', 'Equivalent regroup')
    prepared['identity']['head_sha'] = git('rev-parse', 'HEAD')
    assert state.verify_review(path, prepared)['accepted_failures'] == ['two']


def test_acceptance_cli_retains_exact_human_source(reviewed, monkeypatch):
    path, context, prepared, result, item, git = failed_review(reviewed)
    cli_record = path.parent / 'pr-1.json'
    write(cli_record, read(path, {}))
    monkeypatch.setattr(state, 'root', lambda repo: path.parent)
    source = 'I already confirmed it is OK to proceed with these reviewers failed.'
    monkeypatch.setattr(sys, 'argv', ['run_state.py', '--repo', 'github.com/o/r', '--pr', '1',
        'accept-review-failures', '--head', context['head_sha'], '--reviewer', 'two',
        '--source', source, '--reason', 'Proceed with the recorded API failure'])
    state.main()
    assert read(cli_record, {})['accepted_review_failures'][0]['source'] == source
    assert state.verify_review(cli_record, prepared)['accepted_failures'] == ['two']


@pytest.mark.parametrize('change', [None, 'run', 'cycle', 'tree', 'scope', 'incomplete',
                                  'missing_reviewer', 'unassessed', 'actionable'])
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
    if change == 'missing_reviewer': artifact['result']['skipped_reviewers'] = []
    if change in {'unassessed', 'actionable'}:
        finding = {'path': 'code.py', 'line': 1, 'message': 'bug', 'reviewers': ['one']}
        artifact['result']['findings'] = [finding]
        if change == 'actionable':
            ref = 'review-finding:' + coverage.digest(finding)
            record['threads'] = {ref: {context['head_sha']: {'disposition': 'fix'}}}
            record['assessments'] = [{'reference': ref, 'head': context['head_sha'],
                'preparation_id': prepared['preparation_id'], 'disposition': 'within_scope'}]
    legacy_path = path.parent / 'legacy-review.json'
    write(legacy_path, artifact)
    reference = {'path': str(legacy_path), 'sha256': coverage.digest(artifact)}
    record['review_results'], record['review_sessions'] = [reference], []
    write(path, record)
    if change:
        reason = {'missing_reviewer': 'configured-reviewer execution is incomplete',
                  'unassessed': 'lacks validity and scope assessment',
                  'actionable': 'remains actionable'}.get(change)
        with pytest.raises(ValueError, match=reason):
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
