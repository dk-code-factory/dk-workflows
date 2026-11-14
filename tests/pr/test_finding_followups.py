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


def linear_history(members):
    items = [{'sha': sha, 'parents': [{'sha': members[index - 1]}] if index else []}
             for index, sha in enumerate(members)]
    return followups.github_history(items, members[-1], members)


def context(history=None, retained=None):
    history = [A, B, C] if history is None else history
    return followups.inspect(*inputs(), linear_history(history) if isinstance(history, list) else history,
                             retained or {})


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
    value = followups.inspect(inline, top, linear_history([A, B, C]), {})
    assert outcome(value)['status'] == 'excluded'


@pytest.mark.parametrize('author', ['robot[bot]', 'maintainer', 'previous-workflow'])
def test_recent_equivalent_request_resets_age_for_every_author(author):
    inline, top = inputs()
    recent = {**top['comments'][0], 'reference': 'issuecomment-902', 'id': 902,
              'url': PR + '#issuecomment-902', 'author': author,
              'text': f'Please handle empty input at [head]({PR}/commits/{B}).'}
    top['items'].append(recent)
    value = followups.inspect(inline, top, linear_history([A, B, C]), {})
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
    before = followups.inspect(inline, top, linear_history([A, B, C]), {})
    top['comments'][0].update(author='robot[bot]', updatedAt='2026-11-13T23:59:59Z')
    top['comments'][0]['text'] = '**Summary**\n\n' + top['comments'][0]['text']
    after = followups.inspect(inline, top, linear_history([A, B, C]), {})
    assert before['discussion'] != after['discussion']  # semantic reassessment is required
    assert outcome(after, source='issuecomment-901')['newCommits'] == 2
    with pytest.raises(ValueError, match='inputs changed'):
        followups.plan(after, assessment(before, source='issuecomment-901'), {})


def test_one_summary_edit_does_not_reset_every_findings_substantive_age():
    inline, top = inputs()
    top['comments'][0]['text'] += f'\nNew request about generators at [head]({PR}/commits/{B}).'
    top['comments'][0]['updatedAt'] = '2026-11-13T23:59:59Z'
    value = followups.inspect(inline, top, linear_history([A, B, C]), {})
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


@pytest.mark.parametrize('local', [False, True])
@pytest.mark.parametrize('latest,expected', [('C99', 'eligible'),
                         ('/old/operation.json', 'uncertain_age')])
def test_only_latest_verified_unavailable_receipt_blocks_age(local, latest, expected):
    saved = {'/old/operation.json': retained('verified', D)}
    history = {'commits': [A, B, C], 'mode': 'local', 'target': 'e' * 40,
               'newCommits': {A: 2}} if local else linear_history([A, B, C])
    value = context(history, saved)
    original = deepcopy(saved)
    native = assessment(value, operations=list(saved), latestRequest=latest)
    assert followups.plan(value, native, saved)['findings'][0]['status'] == expected
    assert saved == original


@pytest.mark.parametrize('source', ['C99', 'pullrequestreview-789', 'issuecomment-901'])
def test_source_repository_casing_preserves_exact_publication_url(source):
    inline, top = inputs()
    items = [*inline['threads'][0]['comments'], *top['items']]
    for item in items:
        item['url'] = item['url'].replace('/owner/repo/', '/Owner/Repo/')
    value = followups.inspect(inline, top, linear_history([A, B, C]), {})
    result = outcome(value, source=source)
    url = value['sources'][source]['url']
    assert result['request']['source']['url'] == url
    assert url in result['request']['body']


@pytest.mark.parametrize('url', [PR.replace('/owner/', '/other/') + '#discussion_r99',
             PR + '0#discussion_r99', PR + '?other=1#discussion_r99',
             PR.replace('github.com', 'other.test') + '#discussion_r99'])
def test_source_url_rejects_different_identity(url):
    value = context()
    value['sources']['C99']['url'] = url
    with pytest.raises(ValueError, match='source URL'):
        outcome(value)


@pytest.mark.parametrize('source', ['C99', 'pullrequestreview-789', 'issuecomment-901'])
@pytest.mark.parametrize('kind,equivalent,expected', [
    ('request-review', False, 'eligible'), ('request-review', True, 'recover_required'),
    ('reply-inline', False, 'recover_required'), ('reply-comment', False, 'recover_required'),
    ('edit-pr', False, 'recover_required')])
def test_pending_operations_follow_publisher_independent_reply_policy(source, kind, equivalent, expected):
    saved = {'/pending/operation.json': retained('pending', B)}
    saved['/pending/operation.json']['request']['kind'] = kind
    value = context(retained=saved)
    original = deepcopy(saved)
    native = assessment(value, source=source, operations=list(saved) if equivalent else [])
    result = followups.plan(value, native, saved)['findings'][0]
    assert result['status'] == expected
    assert saved == original


def test_default_inspection_bounds_membership_without_weakening_fingerprint():
    members = [A, B, *[f'{index:040x}' for index in range(10000)], C]
    value = context({'commits': members, 'mode': 'local', 'target': D,
                     'targetRef': 'main', 'newCommits': {A: 10002}})
    before = deepcopy(value)
    compact = followups.inspection_output(value)
    full = followups.inspection_output(value, full_commits=True)
    assert 'commits' not in compact and 'commits' not in compact['commitEvidence']
    assert compact['commitEvidence'] == {'mode': 'local', 'count': len(members),
             'target': D, 'targetRef': 'main', 'newCommits': {A: 10002}}
    assert compact['context'] == full['context'] == evidence.digest(value)
    assert len(json.dumps(compact)) < 4000
    assert full['commits'] == members and value == before
    assert followups.plan(value, assessment(value), {})['findings'][0]['newCommits'] == 10002


def test_default_inspection_lists_candidate_ages_and_preserves_missing_anchors():
    saved = {'/original/operation.json': retained('verified', B)}
    value = context(retained=saved)
    compact = followups.inspection_output(value)
    assert compact['commitEvidence']['newCommits'] == {A: 2, B: 1}
    assert compact['context'] == evidence.digest(value)


@pytest.mark.parametrize('change', ['head', 'body', 'intent'])
def test_changed_inputs_reject_only_plan_without_modifying_originals(change):
    saved = {'/original/operation.json': retained('verified', A)}
    inline, top = inputs()
    old = followups.inspect(inline, top, linear_history([A, B, C]), saved)
    native = assessment(old, operations=list(saved))
    if change == 'head':
        inline['pullRequest']['headRefOid'] = D
    elif change == 'body':
        top['items'][0]['text'] += ' Actually, preserve the exception.'
    else:
        saved['/original/operation.json']['status'] = 'pending'
    new = followups.inspect(inline, top, linear_history([A, B, C]), saved)
    with pytest.raises(ValueError, match='changed'):
        followups.plan(new, native, saved)


def test_commit_capture_requires_stable_complete_pr_list():
    history = [A, B, C]
    metadata = {'commits': 3, 'head': {'sha': C}}
    items = [{'sha': sha, 'parents': [{'sha': history[index - 1]}] if index else []}
             for index, sha in enumerate(history)]
    responses = [metadata, [items], {'headRefOid': C}] * 2
    with patch.object(followups.conversation, 'gh_json', side_effect=responses) as read:
        assert followups.commits(PR, C, Path('.'), anchors=history) == linear_history(history)
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
    pages = [[{'sha': sha, 'parents': [{'sha': parent}] if parent else []}
              for sha, parent in [(A, None), (B, A), (C, B)]]]
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
        responses += [metadata, [[pages[0][1], pages[0][0], pages[0][2]]], {'headRefOid': C}]
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


@pytest.mark.parametrize('full', [False, True])
def test_inspect_cli_defaults_to_compact_with_full_evidence_opt_in(monkeypatch, capsys, full):
    argv = ['finding_followups.py', 'inspect', '--handoff', '/evidence',
            '--sha256', 'digest', '--pr', PR, '--head', C]
    monkeypatch.setattr(sys, 'argv', argv + (['--full-commits'] if full else []))
    with patch.object(evidence, 'load_handoff', return_value=({}, *inputs())), \
         patch.object(evidence, 'verify'), patch.object(followups, 'operations', return_value={}), \
         patch.object(followups, 'commits', return_value=linear_history([A, B, C])):
        assert followups.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert ('commits' in result) is full
    assert result['context'] == evidence.digest(context())


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


@pytest.mark.parametrize('caller', ['https://github.com/Owner/Repo/pull/7', PR + '/',
                                    'https://GitHub.com/Owner/Repo/pull/7/'])
def test_equivalent_caller_identity_finds_recent_receipt_during_discussion_lag(tmp_path, monkeypatch, caller):
    exercise = load_exercise()
    root = tmp_path / 'fixture'
    exercise.initialize(root)
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    request = outcome()['request']
    # Remote was at B when this request was published, but the complete
    # discussion fixture still only exposes the older report at A.
    state = json.loads((root / 'remote.json').read_text())
    state['metadata']['headRefOid'] = B
    (root / 'remote.json').write_text(json.dumps(state))
    request['head'] = B
    published = exercise.operate(root, 'submit', request)
    path = published['operation']
    before = Path(path).read_bytes()
    discovered = followups.operations(caller, root)
    assert str(Path(path).resolve()) in discovered
    value = context(retained=discovered)
    result = followups.plan(value, assessment(value, operations=list(discovered)), discovered)
    assert result['findings'][0]['status'] == 'recent_request'
    assert result['findings'][0]['newCommits'] == 1
    assert Path(path).read_bytes() == before


def test_real_superseded_receipt_and_pending_review_allow_one_independent_followup(tmp_path, monkeypatch):
    exercise = load_exercise()
    root = tmp_path / 'fixture'
    exercise.initialize(root)
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    state = json.loads((root / 'remote.json').read_text())
    state['metadata']['headRefOid'] = D
    (root / 'remote.json').write_text(json.dumps(state))
    old = outcome()['request']
    old['head'] = D
    old['body'] = old['body'].replace(C, D).replace(C[:12], D[:12])
    published = exercise.operate(root, 'submit', old)
    path = published['operation']
    original = Path(path).read_bytes()
    state = json.loads((root / 'remote.json').read_text())
    state['metadata']['headRefOid'] = C
    (root / 'remote.json').write_text(json.dumps(state))
    pending = exercise.operate(root, 'submit', {'kind': 'request-review', 'pr': PR,
               'head': C, 'reviewers': ['reviewer'], 'team_reviewers': []}, lose_response=True)
    pending_path = pending['operation']
    assert publication.load(pending_path)['status'] == 'pending'
    pending_original = Path(pending_path).read_bytes()
    saved = followups.operations(PR, root)
    value = context(retained=saved)
    native = assessment(value, operations=[str(Path(path).resolve())], latestRequest='C99')
    result = followups.plan(value, native, saved)['findings'][0]
    assert result['status'] == 'eligible'
    # Both follow-up planning and actual publisher dispatch permit this reply.
    sent = exercise.operate(root, 'submit', result['request'])
    assert publication.load(sent['operation'])['status'] == 'verified'
    assert exercise.operate(root, 'inspect')['writes'] == {'reply-inline': 2, 'request-review': 1}
    assert Path(path).read_bytes() == original
    assert Path(pending_path).read_bytes() == pending_original


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


@pytest.fixture(scope='module')
def branched_history(tmp_path_factory):
    root = tmp_path_factory.mktemp('finding-branched-history')
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.test',
           'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.test'}

    def git(*args, data=None, date=None):
        dated = {**env, 'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date} if date else env
        return subprocess.check_output(['git', *args], cwd=root, env=dated, input=data, text=True).strip()

    git('init', '-q')
    tree = git('mktree', data='')

    def commit(label, day, *parents):
        return git('commit-tree', tree, *[arg for parent in parents for arg in ('-p', parent)],
                   data=label, date=f'2026-01-{day:02d}T00:00:00Z')

    base = commit('base', 1)
    original = commit('original PR work', 2, base)
    side = commit('side branch created before request', 3, original)
    request_head = commit('head when request was posted', 4, original)
    target = commit('target only 2', 3, commit('target only 1', 2, base))
    target_merge = commit('merge only target', 5, request_head, target)
    side_merge = commit('merge old side branch and target', 6, request_head, side, target)
    histories = {}
    for head in (target_merge, side_merge):
        members = git('rev-list', head, '^' + target).splitlines()
        items = []
        for sha in sorted(members, key=lambda sha: int(git('show', '-s', '--format=%ct', sha))):
            parents = git('show', '-s', '--format=%P', sha).split()
            items.append({'sha': sha, 'parents': [{'sha': parent} for parent in parents]})
        expected = int(git('rev-list', '--count', head, '^' + request_head, '^' + target))
        histories[head] = (items, expected)
    return root, target, original, side, request_head, target_merge, side_merge, histories


def capture_small_history(fixture, head, *, items=None, cwd=None):
    root, target, original, side, request_head, _, _, histories = fixture
    actual, _ = histories[head]
    items = actual if items is None else items
    metadata = {'commits': len(actual), 'head': {'sha': head},
                'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}

    def read(args, directory):
        if '/git/ref/heads/' in args[-1]:
            return {'object': {'type': 'commit', 'sha': target}}
        if args[:2] == ['pr', 'view']:
            return {'headRefOid': head}
        if '--paginate' in args:
            return [items]
        return deepcopy(metadata)

    with patch.object(followups.conversation, 'gh_json', side_effect=read):
        return followups.commits(PR, head, cwd or root, anchors=[original, side, request_head, head, D])


@pytest.mark.parametrize('source', ['report', 'discussion', 'receipt'])
@pytest.mark.parametrize('side_branch', [False, True])
def test_small_api_graph_counts_new_commits_since_each_request_not_timestamp_or_list_suffix(
        branched_history, tmp_path, source, side_branch):
    _, target, original, side, request_head, target_merge, side_merge, histories = branched_history
    head = side_merge if side_branch else target_merge
    items, expected = histories[head]
    if side_branch:
        assert [item['sha'] for item in items] == [original, side, request_head, head]
        assert expected == 2  # The older-dated side commit was absent from request_head.
    else:
        assert expected == 1  # Target-only commits never contribute to reminder age.
    # No checkout exists here: complete short API parent evidence is sufficient.
    with patch.object(followups, 'local_history', side_effect=AssertionError('needless local fallback')):
        captured = capture_small_history(branched_history, head, cwd=tmp_path)
    assert captured['mode'] == 'github'
    assert captured['newCommits'][request_head] == expected
    assert target not in captured['commits'] and D not in captured['newCommits']
    inline, top = inputs()
    inline['pullRequest']['headRefOid'] = head
    top['reviews'][0]['commitId'] = request_head if source == 'report' else original
    requests = [{'reference': 'C99'}]
    saved = {}
    latest = 'C99'
    if source == 'discussion':
        recent = {**top['comments'][0], 'reference': 'issuecomment-902', 'id': 902,
                  'url': PR + '#issuecomment-902',
                  'text': f'Please fix this at [head]({PR}/commits/{request_head}).'}
        top['items'].append(recent)
        requests.append({'reference': recent['reference'], 'commit': request_head,
                         'anchorEvidence': 'Latest substantive request explicitly binds this head.'})
        latest = recent['reference']
    elif source == 'receipt':
        saved = {'/original/operation.json': retained('verified', request_head)}
        latest = next(iter(saved))
    value = followups.inspect(inline, top, captured, saved)
    before = deepcopy(saved)
    native = assessment(value, requests=requests, operations=list(saved), latestRequest=latest)
    result = followups.plan(value, native, saved)['findings'][0]
    assert result['newCommits'] == expected
    assert result['status'] == ('eligible' if expected == 2 else 'recent_request')
    assert followups.inspection_output(value)['commitEvidence']['newCommits'][request_head] == expected
    assert saved == before


@pytest.mark.parametrize('failure', ['missing_parents', 'disconnected'])
def test_small_incomplete_api_graph_uses_pinned_local_proof(branched_history, failure):
    *_, head, histories = branched_history
    items = deepcopy(histories[head][0])
    if failure == 'missing_parents':
        del items[0]['parents']
    else:
        items[-1]['parents'] = items[-1]['parents'][:1]
    captured = capture_small_history(branched_history, head, items=items)
    assert captured['mode'] == 'local'
    assert captured['newCommits'][branched_history[4]] == 2


def test_small_missing_api_parent_evidence_without_local_objects_blocks(branched_history, tmp_path):
    *_, head, histories = branched_history
    items = deepcopy(histories[head][0])
    del items[0]['parents']
    with pytest.raises(ValueError, match='restore full local Git objects'):
        capture_small_history(branched_history, head, items=items, cwd=tmp_path)


def test_unproved_list_positions_cannot_supply_age_for_reports_or_receipts():
    saved = {'/original/operation.json': retained('verified', A)}
    value = context(retained=saved)
    del value['commitEvidence']
    for operations, latest in [([], 'C99'), (list(saved), next(iter(saved)))]:
        result = followups.plan(value, assessment(value, operations=operations, latestRequest=latest), saved)
        assert result['findings'][0]['status'] == 'uncertain_age'
    assert followups.inspection_output(value)['commitEvidence']['newCommits'] == {}


@pytest.mark.parametrize('order', ['reversed', 'head_first'])
def test_small_api_age_does_not_depend_on_head_position(branched_history, tmp_path, order):
    *_, head, histories = branched_history
    items = histories[head][0]
    items = list(reversed(items)) if order == 'reversed' else [items[-1], *items[:-1]]
    with patch.object(followups, 'local_history', side_effect=AssertionError('API graph is complete')):
        captured = capture_small_history(branched_history, head, items=items, cwd=tmp_path)
    assert captured['newCommits'][branched_history[4]] == 2
