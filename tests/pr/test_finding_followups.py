"""Offline age, source routing and retained-write recovery boundaries."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

import finding_followups as followups
import pr_evidence as evidence
from shared import publication

PR = 'https://github.com/owner/repo/pull/7'
A, B, C, D = (letter * 40 for letter in 'bcad')


def inputs():
    comments = [{'reference': 'C41', 'databaseId': 41, 'reviewReference': 'pullrequestreview-789',
                 'url': PR + '#discussion_r41', 'text': 'Return an empty list for empty input.',
                 'createdAt': '2026-01-01T00:00:00Z'},
                {'reference': 'C99', 'databaseId': 99, 'reviewReference': 'pullrequestreview-789',
                 'url': PR + '#discussion_r99', 'text': 'This also affects generators.',
                 'createdAt': '2026-01-01T00:00:01Z'}]
    inline = {'pullRequest': {'url': PR, 'headRefOid': C},
              'threads': [{'id': 'PRRT_fixture', 'isResolved': False, 'comments': comments}]}
    review = {'kind': 'review', 'reference': 'pullrequestreview-789', 'id': 789,
              'url': PR + '#pullrequestreview-789', 'commitId': A,
              'createdAt': '2026-01-01T00:00:00Z', 'text': 'Handle empty input in code.py:42.'}
    comment = {'kind': 'issue_comment', 'reference': 'issuecomment-901', 'id': 901,
               'url': PR + '#issuecomment-901', 'createdAt': '2026-01-01T00:00:00Z',
               'updatedAt': '2026-01-01T00:00:00Z',
               'text': f'Empty input fails at [head]({PR}/commits/{A}).'}
    top = {'reviews': [review], 'comments': [comment], 'items': [review, comment]}
    return inline, top


def context(history=None, retained=None):
    return followups.inspect(*inputs(), [A, B, C] if history is None else history, retained or {})


def assessment(value, source='C99', **changes):
    request = {'reference': source}
    if source == 'issuecomment-901':
        request.update(commit=A, anchorEvidence='The report explicitly labels its report head.')
    finding = {'key': 'empty-input', 'source': source, 'disposition': 'confirmed',
               'evidence': 'Independent current-head reproduction: empty input raises ValueError.',
               'requests': [request], 'operations': [],
               'latestRequest': source,
               'correction': 'Please return an empty list before indexing input in code.py:42.'}
    finding.update(changes)
    return {'head': value['head'], 'context': evidence.digest(value), 'findings': [finding]}


def outcome(value=None, **changes):
    value = value or context()
    return followups.plan(value, assessment(value, **changes), {})['findings'][0]


@pytest.mark.parametrize('history,age,status', [([C], 0, 'uncertain_age'),
                         ([A, C], 1, 'recent_request'), ([A, B, C], 2, 'eligible'),
                         ([A, B, D, C], 3, 'eligible')])
def test_only_actual_pr_commit_membership_counts(history, age, status):
    result = outcome(context(history))
    assert result['status'] == status
    if status != 'uncertain_age':
        assert result['newCommits'] == age


@pytest.mark.parametrize('source,kind,parent', [('C99', 'reply-inline', 41),
            ('pullrequestreview-789', 'reply-comment', None), ('issuecomment-901', 'reply-comment', None)])
def test_original_source_and_destination_are_preserved(source, kind, parent):
    result = outcome(source=source)
    request = result['request']
    assert request['kind'] == kind
    assert request.get('parent') == parent
    assert request['source']['url'] == context()['sources'][source]['url']
    assert C in request['body'] and request['source']['url'] in request['body']
    if kind == 'reply-inline':
        assert request['source'] == {'kind': 'inline_comment', 'id': 99,
              'url': PR + '#discussion_r99', 'thread_id': 'PRRT_fixture'}
    else:
        assert request['body'].startswith('In Reply To: [Earlier finding](')


@pytest.mark.parametrize('disposition', ['fixed', 'rejected', 'uncertain', 'accepted', 'deferred'])
def test_nonactionable_dispositions_never_remind(disposition):
    assert outcome(disposition=disposition)['status'] == 'excluded'


def test_resolved_thread_never_reminds():
    inline, top = inputs()
    inline['threads'][0]['isResolved'] = True
    value = followups.inspect(inline, top, [A, B, C], {})
    assert outcome(value)['status'] == 'excluded'


@pytest.mark.parametrize('author', ['robot[bot]', 'maintainer', 'previous-workflow'])
def test_recent_equivalent_request_resets_age_for_every_author(author):
    inline, top = inputs()
    recent = {**top['comments'][0], 'reference': 'issuecomment-902', 'id': 902,
              'url': PR + '#issuecomment-902', 'author': author,
              'text': f'Please handle empty input at [head]({PR}/commits/{B}).'}
    top['items'].append(recent)
    value = followups.inspect(inline, top, [A, B, C], {})
    requests = [{'reference': 'C99'}, {'reference': recent['reference'], 'commit': B,
                                     'anchorEvidence': 'Equivalent substantive author request at B.'}]
    assert outcome(value, requests=requests, latestRequest=recent['reference'])['newCommits'] == 1
    assert outcome(value, requests=requests, latestRequest=recent['reference'])['status'] == 'recent_request'


def test_unknown_equivalent_request_and_unsupported_link_are_uncertain():
    value = context()
    assert outcome(value, requests=[{'reference': 'C99'}, {'reference': 'issuecomment-901'}],
                   latestRequest='issuecomment-901')['status'] == 'uncertain_age'
    assert outcome(value, source='issuecomment-901', requests=[{
        'reference': 'issuecomment-901', 'commit': B, 'anchorEvidence': 'Unproven guess'}])['status'] == 'uncertain_age'
    assert outcome(value, requests=[{'reference': 'C99', 'commit': D}])['status'] == 'uncertain_age'


def test_later_evidenced_request_can_supersede_an_unanchored_original():
    value = context()
    result = outcome(value, source='issuecomment-901', requests=[{'reference': 'issuecomment-901'},
                     {'reference': 'C99'}], latestRequest='C99')
    assert result['status'] == 'eligible' and result['newCommits'] == 2


def test_formatting_only_bot_summary_edit_keeps_each_findings_anchor():
    inline, top = inputs()
    before = followups.inspect(inline, top, [A, B, C], {})
    top['comments'][0].update(author='robot[bot]', updatedAt='2026-11-13T23:59:59Z')
    top['comments'][0]['text'] = '**Summary**\n\n' + top['comments'][0]['text']
    after = followups.inspect(inline, top, [A, B, C], {})
    assert before['discussion'] != after['discussion']  # semantic reassessment is required
    assert outcome(after, source='issuecomment-901')['newCommits'] == 2
    with pytest.raises(ValueError, match='inputs changed'):
        followups.plan(after, assessment(before, source='issuecomment-901'), {})


def test_one_summary_edit_does_not_reset_every_findings_substantive_age():
    inline, top = inputs()
    top['comments'][0]['text'] += f'\nNew request about generators at [head]({PR}/commits/{B}).'
    top['comments'][0]['updatedAt'] = '2026-11-13T23:59:59Z'
    value = followups.inspect(inline, top, [A, B, C], {})
    native = assessment(value, source='issuecomment-901')
    second = deepcopy(native['findings'][0])
    second.update(key='generator-consumption', correction='Please materialize the generator only once.')
    second['requests'][0].update(commit=B, anchorEvidence='This distinct generator request was newly added at B.')
    native['findings'].append(second)
    result = followups.plan(value, native, {})
    assert [(item['status'], item['newCommits']) for item in result['findings']] == [
        ('eligible', 2), ('recent_request', 1)]


@pytest.mark.parametrize('history', [[A, C], [C], [A, B, C]])
def test_reminder_age_never_clears_unresolved_findings_or_grants_permission(history):
    value = context(history)
    result = followups.plan(value, assessment(value), {})
    assert result['publicationAuthority'] is False
    assert result['findings'][0]['disposition'] == 'confirmed'
    assert not {'ready', 'approved', 'mergeable', 'reviewComplete'} & result.keys()


def retained(status, head=C):
    return {'status': status, 'request': {'kind': 'reply-inline', 'target': PR,
             'approved': {'head': head, 'source': {'id': 99}, 'body': 'Prior substantive fix request.'}}}


@pytest.mark.parametrize('status,expected', [('verified', 'recent_request'),
                   ('pending', 'recover_required'), ('prepared', 'uncertain_age')])
def test_original_journal_prevents_unchanged_fresh_session_reminders(status, expected):
    saved = {'/original/operation.json': retained(status)}
    value = context(retained=saved)
    native = assessment(value, operations=list(saved))
    original = deepcopy(saved)
    for _ in range(2):
        assert followups.plan(deepcopy(value), deepcopy(native), saved)['findings'][0]['status'] == expected
    assert saved == original


def test_two_commits_after_latest_verified_receipt_can_be_reassessed():
    saved = {'/original/operation.json': retained('verified', A)}
    value = context(retained=saved)
    result = followups.plan(value, assessment(value, operations=list(saved)), saved)['findings'][0]
    assert result['status'] == 'eligible' and result['newCommits'] == 2


@pytest.mark.parametrize('change', ['head', 'body', 'intent'])
def test_changed_inputs_reject_only_plan_without_modifying_originals(change):
    saved = {'/original/operation.json': retained('verified', A)}
    inline, top = inputs()
    old = followups.inspect(inline, top, [A, B, C], saved)
    native = assessment(old, operations=list(saved))
    if change == 'head':
        inline['pullRequest']['headRefOid'] = D
    elif change == 'body':
        top['items'][0]['text'] += ' Actually, preserve the exception.'
    else:
        saved['/original/operation.json']['status'] = 'pending'
    new = followups.inspect(inline, top, [A, B, C], saved)
    with pytest.raises(ValueError, match='changed'):
        followups.plan(new, native, saved)


def test_commit_capture_requires_stable_complete_pr_list():
    history = [A, B, C]
    metadata = {'commits': 3, 'head': {'sha': C}}
    responses = [metadata, [[{'sha': sha} for sha in history]], {'headRefOid': C}] * 2
    with patch.object(followups.conversation, 'gh_json', side_effect=responses) as read:
        assert followups.commits(PR, C, Path('.')) == history
    assert read.call_count == 6
    assert '--paginate' in read.call_args_list[1].args[0]


@pytest.fixture(scope='module')
def large_history(tmp_path_factory):
    root = tmp_path_factory.mktemp('finding-history')
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.test',
           'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.test'}

    def git(*args, data=None):
        return subprocess.check_output(['git', *args], cwd=root, env=env, input=data, text=True).strip()

    git('init', '-q')
    tree = git('mktree', data='')

    def commit(label, *parents):
        return git('commit-tree', tree, *[arg for parent in parents for arg in ('-p', parent)], data=label)

    base = commit('base')
    target = commit('target only 2', commit('target only 1', base))
    features = []
    prior = base
    for index in range(251):
        prior = commit(f'PR {index}', prior)
        features.append(prior)
    merged = commit('merge target', prior, target)
    next_head = commit('new PR work', merged)
    return root, target, features, merged, next_head


@pytest.mark.parametrize('extra,expected', [(False, 1), (True, 2)])
def test_capped_list_uses_pinned_local_membership_without_target_only_commits(large_history, extra, expected):
    root, target, features, merged, next_head = large_history
    head = next_head if extra else merged
    metadata = {'commits': 252 + int(extra), 'head': {'sha': head},
                'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}
    listed = features[:250]

    def read(args, cwd):
        if '/git/ref/heads/' in args[-1]:
            return {'object': {'type': 'commit', 'sha': target}}
        if args[:2] == ['pr', 'view']:
            return {'headRefOid': head}
        if '/commits' in args:
            raise AssertionError('Use exact route')
        if '--paginate' in args:
            return [[{'sha': sha} for sha in listed[:100]],
                    [{'sha': sha} for sha in listed[100:200]],
                    [{'sha': sha} for sha in listed[200:]]]
        return deepcopy(metadata)

    with patch.object(followups.conversation, 'gh_json', side_effect=read):
        captured = followups.commits(PR, head, root, anchors=[features[-1], 'e' * 40])
    assert captured['newCommits'][features[-1]] == expected
    assert target not in captured['commits']
    assert 'e' * 40 not in captured['newCommits']  # rewritten/unavailable source is uncertain
    inline, top = inputs()
    inline['pullRequest']['headRefOid'] = head
    top['reviews'][0]['commitId'] = features[-1]
    value = followups.inspect(inline, top, captured, {})
    result = outcome(value)
    assert result['newCommits'] == expected
    assert result['status'] == ('eligible' if expected == 2 else 'recent_request')


@pytest.mark.parametrize('failure', ['missing_objects', 'count', 'listed', 'target_changed', 'shallow'])
def test_local_fallback_requires_consistent_full_objects_and_live_target(large_history, tmp_path, failure):
    root, target, features, head, _ = large_history
    metadata = {'commits': 252, 'head': {'sha': head},
                'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}
    listed = features[:250]
    cwd = root
    if failure == 'missing_objects':
        cwd = tmp_path
    elif failure == 'count':
        metadata['commits'] += 1
    elif failure == 'listed':
        listed = ['e' * 40, *listed[1:]]
    elif failure == 'shallow':
        cwd = tmp_path / 'shallow'
        subprocess.check_call(['git', 'clone', '-q', '--no-checkout', str(root), str(cwd)],
                              stderr=subprocess.DEVNULL)
        (cwd / '.git' / 'shallow').write_text(features[0] + '\n')
    answers = [{'object': {'type': 'commit', 'sha': target}},
               {'object': {'type': 'commit', 'sha': features[0] if failure == 'target_changed' else target}}]
    with patch.object(followups.conversation, 'gh_json', side_effect=answers):
        with pytest.raises(ValueError, match='restore|disagrees|changed'):
            followups.local_history(PR, metadata, listed, [features[-1]], cwd)


@pytest.mark.parametrize('failure', ['cap', 'missing', 'duplicate', 'head', 'pages', 'unstable'])
def test_incomplete_or_unstable_commit_capture_never_produces_age(failure):
    metadata = {'commits': 3, 'head': {'sha': C}}
    pages = [[{'sha': sha} for sha in [A, B, C]]]
    if failure == 'cap':
        metadata['commits'] = 251
    elif failure == 'missing':
        pages[0].pop(0)
    elif failure == 'duplicate':
        pages[0][0]['sha'] = B
    elif failure == 'head':
        metadata['head']['sha'] = D
    elif failure == 'pages':
        pages = {'incomplete': True}
    responses = [metadata, pages, {'headRefOid': C}]
    if failure == 'unstable':
        responses += [metadata, [[{'sha': sha} for sha in [B, A, C]]], {'headRefOid': C}]
        responses += [metadata, pages, {'headRefOid': C}]
    with patch.object(followups.conversation, 'gh_json', side_effect=responses):
        with pytest.raises(ValueError):
            followups.commits(PR, C, Path('.'))


def test_plan_cli_rechecks_live_evidence_before_age_or_payload(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['finding_followups.py', 'plan', '--handoff', '/old/evidence',
                         '--sha256', 'digest', '--pr', PR, '--head', C, '--assessment', '/unused'])
    with patch.object(evidence, 'load_handoff', return_value=({}, *inputs())), \
         patch.object(evidence, 'verify', side_effect=ValueError('PR evidence changed')), \
         patch.object(followups, 'commits') as read:
        assert followups.main() == 1
    read.assert_not_called()
    assert 'PR evidence changed' in capsys.readouterr().err


def load_exercise():
    spec = importlib.util.spec_from_file_location('finding_followup_exercise',
            Path(__file__).with_name('fixtures') / 'feedback_reply_exercise.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('source', ['C99', 'pullrequestreview-789', 'issuecomment-901'])
def test_followup_payload_uses_existing_single_dispatch_journal(tmp_path, monkeypatch, source):
    exercise = load_exercise()
    root = tmp_path / 'fixture'
    exercise.initialize(root)
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    request = outcome(source=source)['request']
    first = exercise.operate(root, 'submit', request, lose_response=True)
    operation = first['operation']
    original = publication.load(operation)
    assert original['status'] == 'pending'
    assert exercise.operate(root, 'submit', request)['operation'] == operation
    assert exercise.operate(root, 'reconcile', operation=operation)['status'] == 'verified'
    assert exercise.operate(root, 'inspect')['writes'] == {request['kind']: 1}
    assert publication.load(operation)['request'] == original['request']
    # New-session discovery reads existing original intents rather than adding
    # a second follow-up registry or relying on discussion visibility.
    discovered = followups.operations(PR, root)
    assert str(Path(operation).resolve()) in discovered
    value = context(retained=discovered)
    result = followups.plan(value, assessment(value, source=source, operations=list(discovered)), discovered)
    assert result['findings'][0]['status'] == 'recent_request'


def test_native_assessment_round_trip_in_new_process_preserves_results(tmp_path):
    value = context()
    path = tmp_path / 'inputs.json'
    path.write_text(json.dumps({'context': value, 'assessment': assessment(value)}))
    before = path.read_bytes()
    code = ('import json,sys; import finding_followups as f; '
            'v=json.load(open(sys.argv[1])); print(json.dumps(f.plan(v["context"], v["assessment"], {})))')
    outputs = [subprocess.check_output([sys.executable, '-c', code, str(path)], text=True) for _ in range(2)]
    assert outputs[0] == outputs[1]
    assert path.read_bytes() == before
