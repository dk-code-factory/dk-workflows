"""Request admission, private Git history, exact writes and interrupted recovery."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
import config_requests as requests
import preferences
from shared.private_state import write

REPO = 'github.com/owner/repo'
CI = 'dk-workflows:dk-ci'


@pytest.fixture
def service(tmp_path):
    checkout = tmp_path/'checkout'
    checkout.mkdir()
    return requests.Requests(), {'checkout': str(checkout), 'repository': REPO}


def operation(seconds=41, scope='repo'):
    return {'domain': 'skill', 'skill': CI, 'action': 'set', 'scope': scope,
            'value': {'timeout_seconds': seconds}}


def preview(service, ask='Use 41 seconds.', operations=None):
    store, context = service
    request_id = store.start(ask, context)['id']
    ops = operations or [operation()]
    result = store.prepare(request_id, {'operations': ops})
    return request_id, ops, result


def admit(store, request_id, ops, result, *, relations=(), unresolved=()):
    assessment = {'fingerprint': result['fingerprint'], 'summary': 'Concrete requested settings; checked governing instructions.',
                  'reviewed': [c['request'] + '/' + c['id'] for c in result['candidates']],
                  'relations': list(relations), 'unresolved': list(unresolved)}
    return store.prepare(request_id, {'operations': ops, 'assessment': assessment})


def relation(previous, result, choice='replace'):
    return {'request': previous['request'], 'decision': previous['id'],
            'new_decision': result['decisions'][0]['id'], 'choice': choice,
            'evidence': 'User explicitly requested this replacement.'}


def test_request_roundtrip_fresh_process_and_no_repeated_writes(service, monkeypatch):
    store, context = service
    request_id, ops, result = preview(service)
    assert result['legacy_intent'].startswith('unknown')
    assert admit(store, request_id, ops, result)['status'] == 'prepared'
    command = [sys.executable, str(ROOT/'scripts/config_requests.py'), 'resume', '--request-id', request_id]
    files = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    output = json.loads(subprocess.check_output(command, text=True))
    assert output['admission'] == 'reusable' and 'ask' not in output
    assert files == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    assert store.apply(request_id)['status'] == 'applied'
    target = config.path(REPO)
    original = target.stat().st_mtime_ns
    monkeypatch.setattr(requests, 'atomic_text', lambda *_: pytest.fail('Repeated write'))
    assert requests.Requests().apply(request_id)['status'] == 'applied'
    assert target.stat().st_mtime_ns == original
    assert json.loads(target.read_text())['skill_defaults'][CI]['options']['timeout_seconds'] == 41
    assert store._git('remote') == ''
    assert len(store._git('log', '--oneline').splitlines()) >= 5
    assert store.root.stat().st_mode & 0o077 == 0


def test_clarification_cancel_and_history_are_discoverable(service):
    store, _ = service
    request_id = store.start('Make my settings better', service[1])['id']
    with pytest.raises(ValueError, match='admission'):
        store.apply(request_id)
    store.clarify(request_id, 'Use 41 seconds.', question='What timeout?')
    assert store.show(request_id, 'ask')['clarifications'][0]['excerpt'] == 'Use 41 seconds.'
    store.cancel(request_id, 'User cancelled.')
    assert store.history(status='cancelled')['requests'][0]['id'] == request_id
    with pytest.raises(ValueError):
        store.prepare(request_id, {'operations': [operation()]})
    assert 'User cancelled.' in store._git('show', 'HEAD:' + request_id + '.json')


def test_partial_supersession_explicit_replacement_and_scoped_exception(service):
    store, context = service
    first = operation()
    first['value']['poll_interval_seconds'] = 3
    request_id, ops, result = preview(service, operations=[first])
    admit(store, request_id, ops, result)
    store.apply(request_id)
    new = operation(52)
    new['value']['poll_interval_seconds'] = 3
    second, ops, result = preview(service, 'Replace 41 seconds with 52 seconds.', [new])
    with pytest.raises(ValueError, match='explicit conflict'):
        admit(store, second, ops, result)
    old = next(c for c in result['candidates'] if c['path'][-1] == 'timeout_seconds')
    admit(store, second, ops, result, relations=[relation(old, result)])
    store.apply(second)
    third, ops, result = preview(service, operations=[operation(100, scope='user')])
    active = {(c['request'], c['path'][-1]) for c in result['candidates']}
    assert (request_id, 'poll_interval_seconds') in active
    assert (request_id, 'timeout_seconds') not in active
    assert (second, 'timeout_seconds') in active
    # A repository exception must not retire a broader user decision.
    user = next(c for c in result['candidates'] if c['path'][-1] == 'timeout_seconds')
    with pytest.raises(ValueError, match='exception'):
        admit(store, third, ops, result, relations=[relation(user, result)])
    admit(store, third, ops, result, relations=[relation(user, result, 'exception')])
    store.apply(third)
    fourth, _, result = preview(service)
    assert {second, third} <= {c['request'] for c in result['candidates']}


def test_unresolved_and_stale_assessments_never_write(service):
    store, context = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result, unresolved=['Which scope?'])
    with pytest.raises(ValueError, match='unresolved'):
        store.apply(request_id)
    assert not config.path(REPO).exists()
    store.clarify(request_id, 'Repository scope.')
    with pytest.raises(ValueError, match='fingerprint'):
        admit(store, request_id, ops, result)
    result = store.prepare(request_id, {'operations': ops})
    admit(store, request_id, ops, result)
    (Path(context['checkout'])/'AGENTS.md').write_text('New governing instruction')
    assert store.resume(request_id)['admission'] == 'stale'
    with pytest.raises(ValueError, match='changed'):
        store.apply(request_id)
    assert not config.path(REPO).exists()


def test_exact_operation_context_and_missing_request_enforcement(service):
    store, context = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    with pytest.raises(ValueError, match='require --request-id'):
        requests.apply_bound(None, operation(), context)
    with pytest.raises(ValueError, match='exact single operation'):
        requests.apply_bound(request_id, operation(99), context)
    with pytest.raises(ValueError, match='context'):
        requests.apply_bound(request_id, operation(), {**context, 'repository': 'github.com/other/repo'})
    command = [sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPO,
               '--checkout', context['checkout'], 'skill-set', '--skill', CI, '--file', str(Path(context['checkout']).parent/'options.json')]
    write(Path(context['checkout']).parent/'options.json', {'timeout_seconds': 41})
    denied = subprocess.run(command, text=True, capture_output=True)
    assert denied.returncode == 2 and '--request-id' in denied.stdout
    accepted = subprocess.run([*command, '--request-id', request_id], text=True, capture_output=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('operation', [
    {'domain': 'external', 'action': 'set', 'value': {}},
    {'domain': 'workflow', 'action': 'set', 'scope': 'upstream', 'value': {}},
    {'domain': 'workflow', 'action': 'set', 'path': '/tmp/evil', 'value': {}},
    {'domain': 'skill', 'action': 'set', 'skill': 'other:skill', 'value': {}},
])
def test_typed_adapters_reject_unsupported_scope_path_and_domain(service, operation):
    with pytest.raises(ValueError):
        preview(service, operations=[operation])


def test_concurrent_edit_invalidates_admission_preserving_settings(service):
    store, _ = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    write(config.path(REPO), {'extensions': {'another': 'original'}})
    with pytest.raises(ValueError, match='changed'):
        store.apply(request_id)
    assert json.loads(config.path(REPO).read_text()) == {'extensions': {'another': 'original'}}


@pytest.mark.parametrize('failure', ['after-write', 'before-second-write', 'receipt-commit'])
def test_partial_application_and_uncertain_receipt_recover_without_rewrite(service, monkeypatch, failure):
    store, _ = service
    ops = [operation(), {'domain': 'completion', 'scope': 'user', 'action': 'set', 'value': 'Do complete work.'}]
    request_id, ops, result = preview(service, operations=ops)
    admit(store, request_id, ops, result)
    original_atomic = requests.atomic_text
    original_save = store._save
    written = []
    def atomic(path, content):
        if failure == 'before-second-write' and written:
            raise OSError('Interrupted before second write')
        original_atomic(path, content)
        written.append(path)
        if failure == 'after-write':
            raise OSError('Interrupted after write')
    def save(record, event):
        if failure == 'receipt-commit' and event.startswith('readback receipt'):
            write(store._path(request_id), record)
            raise OSError('Git receipt interrupted')
        original_save(record, event)
    monkeypatch.setattr(requests, 'atomic_text', atomic)
    monkeypatch.setattr(store, '_save', save)
    with pytest.raises(OSError):
        store.apply(request_id)
    assert store.resume(request_id)['admission'] == 'reusable'
    def remaining(path, content):
        assert path not in written
        original_atomic(path, content)
    monkeypatch.setattr(requests, 'atomic_text', remaining)
    assert requests.Requests().apply(request_id)['status'] == 'applied'
    assert requests.Requests().apply(request_id)['status'] == 'applied'


def test_unexpected_value_blocks_partial_recovery(service, monkeypatch):
    store, _ = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    original = requests.atomic_text
    def interrupt(path, content):
        original(path, content)
        raise OSError('Interrupted')
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id)
    write(config.path(REPO), {'extensions': {'unexpected': 'edit'}})
    with pytest.raises(ValueError, match='changed'):
        requests.Requests().apply(request_id)


def test_preference_ids_duplicate_reuse_and_unchanged_read_commands(service):
    store, context = service
    record = {'owner': {'type': 'user'}, 'prompt': 'Use clear names.',
              'scope': {'include': [{'type': 'directory', 'path': '.'}]}}
    proposal = preferences.prepare(context['checkout'], {'upsert': [record]})
    op = {'domain': 'preferences', 'action': 'commit', 'proposal': proposal}
    request_id, ops, result = preview(service, operations=[op])
    admit(store, request_id, ops, result)
    store.apply(request_id)
    current = preferences.list_records(context['checkout'])
    assert current['records'][0]['id'] == proposal['changes']['upsert'][0]['id']
    duplicate = preferences.prepare(context['checkout'], {'upsert': [record]})
    assert duplicate['reused'] and not duplicate['changes']['upsert']
    request_id, ops, result = preview(service, operations=[{'domain': 'preferences', 'action': 'commit', 'proposal': duplicate}])
    assert result['decisions'] == []
    admit(store, request_id, ops, result)
    store.apply(request_id)
    assert preferences.list_records(context['checkout']) == current
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.root.rglob('*') if p.is_file()}
    command = [sys.executable, str(ROOT/'scripts/preferences.py'), '--checkout', context['checkout'], 'list']
    assert json.loads(subprocess.check_output(command, text=True)) == current
    assert files == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('domain,value', [
    ('workflow', {'extensions': {'keep': 'literal'}}),
    ('rules', {'rules': [{'id': 'x', 'text': 'Preserve this exact rule.'}], 'conflicts': []}),
    ('drafting', {'pr_description': {'skill': 'team:writer', 'instructions': 'Exact prompt.\n'}}),
    ('completion', 'Original completion prompt.\n'),
    ('commit-policy', {'prompt': 'Keep independent commits.'}),
    ('pr-size', {'added_lines_threshold': 701}),
])
def test_all_setting_adapters_set_and_reset_with_legacy_readback(service, monkeypatch, domain, value):
    import config_adapters
    import settings
    from shared import pr_size
    store, context = service
    monkeypatch.setattr(config_adapters, 'identities', lambda *_: ['remote:' + REPO])
    op = {'domain': domain, 'action': 'set', 'scope': 'user', 'value': value}
    request_id, ops, result = preview(service, operations=[op])
    admit(store, request_id, ops, result)
    store.apply(request_id)
    target = config_adapters.destination(context, config_adapters.normalize(op))[0]
    if domain == 'workflow':
        assert json.loads(target.read_text()) == value
    elif domain == 'pr-size':
        assert pr_size.load(['remote:' + REPO], 'user')['pr_size']['added_lines_threshold'] == 701
    else:
        readback = settings.domain_load(domain, ['remote:' + REPO], 'user')['value']
        assert readback == ({'prompt': value} if domain == 'completion' else value)
    reset = {'domain': domain, 'action': 'reset', 'scope': 'user'}
    second, ops, result = preview(service, 'Restore inheritance.', [reset])
    relations = []
    for old in result['candidates']:
        new = next(d for d in result['decisions'] if d['path'] == old['path'])
        relations.append({**relation(old, result), 'new_decision': new['id']})
    admit(store, second, ops, result, relations=relations)
    store.apply(second)
    assert not target.exists()


def test_ci_and_partition_adapters_preserve_existing_domain_history(service, monkeypatch):
    import config_adapters
    from shared import ci_exceptions, pr_partition_policy
    store, context = service
    monkeypatch.setattr(config_adapters, 'identities', lambda *_: ['remote:' + REPO])
    pr = 'https://' + REPO + '/pull/4'
    context['pr'] = pr
    original = pr_partition_policy.configure(pr, 'disable', reason='Original decision.')
    pr_partition_policy.decline(pr, 'split_related', 'earlier', reason='Prior decline.', source='user')
    value = {'kind': 'pipeline', 'workflow': '.github/workflows/test.yml', 'job': None,
             'test': None, 'signature': None, 'reason': 'Explicit exception.'}
    ops = [{'domain': 'ci-exceptions', 'action': 'add', 'value': value},
           {'domain': 'pr-partition', 'action': 'instructions', 'reason': 'Scope requested.',
            'instructions': 'Keep the migration together.\n'}]
    request_id, ops, result = preview(service, operations=ops)
    admit(store, request_id, ops, result)
    store.apply(request_id)
    assert len(ci_exceptions.load('remote:' + REPO)['entries']) == 1
    readback = pr_partition_policy.load(pr)
    assert readback['history'][0] == original['history'][0]
    assert readback['declines'][0]['action_id'] == 'earlier'
    assert readback['instructions'] == ops[1]['instructions']
    assert len(readback['history']) == 2


def test_missing_request_pr_and_preference_writers_fail_before_network_or_writes(service):
    _, context = service
    command = [sys.executable, str(ROOT/'scripts/pr_config.py'), '--cwd', context['checkout'],
               'pr-size', 'set', '--added-lines-threshold', '500']
    denied = subprocess.run(command, capture_output=True, text=True)
    assert denied.returncode == 2 and '--request-id' in denied.stderr
    command = [sys.executable, str(ROOT/'scripts/preferences.py'), '--checkout', context['checkout'],
               'remove', '--id', 'pref-unknown', '--revision', '0']
    denied = subprocess.run(command, capture_output=True, text=True)
    assert denied.returncode == 2 and '--request-id' in denied.stdout
    assert not requests.Requests().root.exists()


def test_conflicting_uncertain_request_must_recover_first(service, monkeypatch):
    store, _ = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    original = requests.atomic_text
    def uncertain(path, content):
        original(path, content)
        raise OSError('Interrupted')
    monkeypatch.setattr(requests, 'atomic_text', uncertain)
    with pytest.raises(OSError):
        store.apply(request_id)
    with pytest.raises(ValueError, match='Reconcile uncertain'):
        preview(service, operations=[operation(99)])


def test_new_earlier_decision_stales_other_prepared_admission(service):
    store, _ = service
    first, ops, result = preview(service, operations=[operation(scope='user')])
    admit(store, first, ops, result)
    second, ops, result = preview(service)
    admit(store, second, ops, result)
    store.apply(first)
    with pytest.raises(ValueError, match='Earlier decisions changed'):
        store.apply(second)


def test_history_must_be_private_and_have_no_remote(service, monkeypatch):
    store, context = service
    monkeypatch.setenv('XDG_STATE_HOME', context['checkout'])
    with pytest.raises(ValueError, match='outside'):
        requests.Requests().start('Set timeout.', context)
    monkeypatch.setenv('XDG_STATE_HOME', str(Path(context['checkout']).parent/'state'))
    store.start('Set timeout.', context)
    store._git('remote', 'add', 'origin', '/unused')
    with pytest.raises(ValueError, match='no remote'):
        store.start('Another request.', context)


def test_preference_history_candidates_respect_actual_record_owner(service):
    store, context = service
    owner = {'type': 'repo', 'repository': preferences.repository(context['checkout'])}
    # The request's workflow identity must match its actual path fallback here.
    context['repository'] = owner['repository']
    proposal = preferences.prepare(context['checkout'], {'upsert': [
        {'owner': owner, 'prompt': 'Use local names.', 'scope': {'include': [{'type': 'directory', 'path': '.'}]}}
    ]})
    request_id, ops, result = preview(service, operations=[{'domain': 'preferences', 'action': 'commit', 'proposal': proposal}])
    admit(store, request_id, ops, result)
    store.apply(request_id)
    assert len(store.show(request_id, 'decisions')['decisions']) == 1
    other_context = {**context, 'repository': 'github.com/other/repo'}
    other = store.start('Use another timeout.', other_context)['id']
    result = store.prepare(other, {'operations': [operation()]})
    assert result['candidates'] == []


def test_partial_recovery_refreshes_admission_and_keeps_completed_siblings(service, monkeypatch):
    store, context = service
    ops = [operation(), {'domain': 'completion', 'action': 'set', 'scope': 'user', 'value': 'Complete selected work.'}]
    request_id, ops, result = preview(service, operations=ops)
    admit(store, request_id, ops, result)
    original = requests.atomic_text
    written = []
    def interrupt(path, content):
        if written:
            raise OSError('Interrupted second store')
        original(path, content)
        written.append(path)
    monkeypatch.setattr(requests, 'atomic_text', interrupt)
    with pytest.raises(OSError):
        store.apply(request_id)
    completed = deepcopy(store.show(request_id, 'progress')['progress'][0])
    (Path(context['checkout'])/'AGENTS.md').write_text('New relevant admission instruction.')
    assert store.resume(request_id)['admission'] == 'stale'
    store.clarify(request_id, 'Preserve the applied timeout; complete the remaining original change.')
    result = store.prepare(request_id, {'operations': ops})
    admit(store, request_id, ops, result, unresolved=['Confirm the remaining instruction.'])
    with pytest.raises(ValueError, match='unresolved'):
        store.apply(request_id)
    store.clarify(request_id, 'The remaining original completion instruction is still requested.')
    result = store.prepare(request_id, {'operations': ops})
    admit(store, request_id, ops, result)
    def remaining(path, content):
        assert path not in written
        original(path, content)
    monkeypatch.setattr(requests, 'atomic_text', remaining)
    assert store.apply(request_id)['status'] == 'applied'
    assert store.show(request_id, 'progress')['progress'][0] == completed


def test_workflow_history_retains_sparse_changes_and_reuses_exact_operation(service):
    store, context = service
    unrelated = 'Unrelated settings remain solely in their live store.'
    before = {'extensions': {'untouched': unrelated, 'changed': 'old', 'deleted': {'nested': 'value'}}}
    write(config.path(REPO), before)
    after = {'extensions': {'untouched': unrelated, 'changed': 'new', 'deleted': {}}}
    op = {'domain': 'workflow', 'action': 'set', 'value': after}
    request_id, ops, result = preview(service, operations=[op])
    admit(store, request_id, ops, result)
    saved = store._path(request_id).read_text()
    assert unrelated not in saved
    retained = store.prepare(request_id, {'operations': 'retained'})
    assert retained['fingerprint'] == result['fingerprint']
    requests.apply_bound(request_id, op, context)
    assert json.loads(config.path(REPO).read_text()) == after


def test_explicit_repository_storage_identity_keeps_existing_address(service):
    store, context = service
    context['repository'] = 'remote:' + REPO
    write(config.path(context['repository']), {'extensions': {'unchanged': 'original'}})
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    store.apply(request_id)
    assert not config.path(REPO).exists()
    value = json.loads(config.path(context['repository']).read_text())
    assert value['extensions'] == {'unchanged': 'original'}
    assert value['skill_defaults'][CI]['options']['timeout_seconds'] == 41


def test_interrupted_completion_commit_is_repaired_without_configuration_write(service, monkeypatch):
    store, _ = service
    request_id, ops, result = preview(service)
    admit(store, request_id, ops, result)
    original = store._save
    def fail(record, event):
        if event == 'complete':
            write(store._path(request_id), record)
            raise OSError('Outcome commit interrupted')
        original(record, event)
    monkeypatch.setattr(store, '_save', fail)
    with pytest.raises(OSError):
        store.apply(request_id)
    monkeypatch.setattr(requests, 'atomic_text', lambda *_: pytest.fail('Repeated configuration write'))
    recovered = requests.Requests()
    assert recovered.apply(request_id)['status'] == 'applied'
    assert recovered._git('status', '--porcelain') == ''
