"""Failure injection at the shared publication boundary and publisher integrations."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from shared import publication as pub
from shared.diagnostics import Diagnostic
from shared.command_results import ResultFile


def module(name, path):
    path = ROOT / path
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    return tmp_path


@pytest.mark.parametrize('kind', ['edit-pr', 'post-review', 'branch-publish', 'ci-rerun', 'request-review'])
@pytest.mark.parametrize('fault', ['before_dispatch', 'after_application', 'response_persistence', 'verification', 'receipt_persistence'])
def test_single_dispatch_under_every_failure(storage, monkeypatch, kind, fault):
    value = pub.prepare(kind, 'target', {'head': 'a' * 40}, {'approved': 'output'},
                        ['python3', 'publisher.py', 'reconcile'])
    path = value['operation']
    applied, sends = False, []
    normal_write = pub.write
    active_fault = True
    def write(path, value):
        if active_fault and ((fault == 'before_dispatch' and value['status'] == 'pending' and 'response' not in value)
                or fault == 'response_persistence' and 'response' in value
                or fault == 'receipt_persistence' and value['status'] == 'verified'):
            raise OSError('storage exhausted')
        normal_write(path, value)
    monkeypatch.setattr(pub, 'write', write)
    def send(value):
        nonlocal applied
        sends.append(value['operation_id'])
        applied = True
        if fault == 'after_application' and active_fault:
            raise OSError('connection lost after application')
        return {'id': 42}
    def observe(value):
        if fault == 'verification' and active_fault:
            raise ValueError('malformed verification output')
        if not applied:
            raise ValueError('no publication found')
        return {'id': 42}
    def execute(reconcile=False):
        return pub.execute(path, kind, check=lambda v, done: None,
                           send=send, observe=observe, reconcile=reconcile)
    with pytest.raises(Diagnostic) as failure:
        execute()
    if fault == 'before_dispatch':
        assert not sends and not failure.value.dispatched
        active_fault = False
        execute()
    else:
        assert failure.value.dispatched and failure.value.action['argv'][-1] == path
        assert pub.load(path)['status'] == 'pending'
        active_fault = False
        execute(True)
    assert len(sends) == 1
    assert execute()['reused'] is True and len(sends) == 1
    assert pub.load(path)['status'] == 'verified'


def test_no_receipt_can_replace_current_readiness_or_changed_approved_inputs(storage):
    prepared = pub.prepare('fixture', 'target', {}, {'text': 'reviewed'}, ['reconcile'])
    path = prepared['operation']
    pub.execute(path, 'fixture', check=lambda v, done: None, send=lambda v: {}, observe=lambda v: {'id': 1})
    def stale(value, done):
        raise ValueError('governing source changed')
    with pytest.raises(Diagnostic, match='governing source changed'):
        pub.execute(path, 'fixture', check=stale, send=lambda v: pytest.fail('duplicate'), observe=lambda v: {'id': 1})
    value = json.loads(Path(path).read_text())
    value['request']['approved']['text'] = 'changed'
    Path(path).write_text(json.dumps(value))
    with pytest.raises(ValueError, match='changed'):
        pub.load(path)


@pytest.mark.parametrize('kind,script,prefix', [
    ('post-review', 'runtime/dk-pr-post-review/scripts/post_review.py', []),
    ('branch-publish', 'runtime/dk-pr-iter/scripts/branch_publish.py', []),
    ('ci-rerun', 'runtime/dk-pr-iter/scripts/publication_actions.py', ['ci-rerun']),
])
def test_retained_publication_cannot_override_common_pr_context(storage, monkeypatch, kind, script, prefix):
    mod = module('scoped_' + kind.replace('-', '_'), script)
    operation = pub.prepare(kind, 'https://github.com/o/r/pull/42', {}, {}, ['reconcile'])
    monkeypatch.setattr(mod, 'publish', lambda *a: pytest.fail('conflicting target was dispatched'))
    monkeypatch.setattr(sys, 'argv', [script, '--pr', 'https://github.com/o/r/pull/43',
                                    *prefix, 'publish', '--operation', operation['operation']])
    with pytest.raises(ValueError, match='PR context conflicts'):
        mod.main()


def test_edit_captures_reviewed_output_and_detects_concurrent_scope_change(storage, monkeypatch):
    mod = module('recovery_edit', 'runtime/dk-pr-description/scripts/pr_edit_guard.py')
    from shared.review_loop import encode
    token = encode('dk-pr-description', 1, True)
    pr = 'https://github.com/o/r/pull/42'
    live = {'url': pr, 'headRefOid': 'a' * 40, 'title': 'old', 'body': 'old body'}
    monkeypatch.setattr(mod, 'snapshot', lambda pr: copy.deepcopy(live))
    title, body = storage / 'title', storage / 'body'
    title.write_text('new title'); body.write_text('reviewed body')
    prepared = mod.prepare(pr, token, title, body)
    title.unlink(); body.unlink()
    Path(prepared['request']['approved']['body_artifact']).unlink()
    sent = []
    def edit(argv, **kwargs):
        sent.append(argv)
        live['title'] = argv[argv.index('--title') + 1]
        live['body'] = Path(argv[argv.index('--body-file') + 1]).read_text()
        raise OSError('lost response')
    monkeypatch.setattr(mod.subprocess, 'run', edit)
    with pytest.raises(Diagnostic):
        mod.publish(prepared['operation'])
    assert mod.publish(prepared['operation'], True)['status'] == 'verified'
    assert len(sent) == 1 and live['body'] == 'reviewed body'
    live['body'] = 'external update'
    with pytest.raises(Diagnostic):
        mod.publish(prepared['operation'])
    assert len(sent) == 1


def review_fixture(storage, monkeypatch):
    mod = module('recovery_post', 'runtime/dk-pr-post-review/scripts/post_review.py')
    pr, head = 'https://github.com/o/r/pull/42', 'a' * 40
    meta = {'node_id': 'NODE', 'head': {'sha': head}, 'base': {'sha': 'b' * 40}}
    monkeypatch.setattr(mod, 'pr_snapshot', lambda pr: copy.deepcopy(meta))
    monkeypatch.setattr(mod, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0,
        'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+new\n', ''))
    live, writes = [], []
    monkeypatch.setattr(mod, 'reviews', lambda pr: copy.deepcopy(live))
    def api(host, endpoint, **kwargs):
        if endpoint == 'user':
            return {'login': 'me'}
        writes.append(kwargs['payload'])
        live.append({'id': 123, 'html_url': pr + '#review-123', 'user': {'login': 'me'},
            'commit_id': head, 'state': 'COMMENTED', 'body': '',
            'comments': [{'path': 'x.py', 'line': 1, 'side': 'RIGHT', 'body': 'Finding.'}]})
        raise OSError('response lost')
    monkeypatch.setattr(mod, 'api', api)
    payload = {'query': 'ignored', 'variables': {'input': {'pullRequestId': 'NODE',
        'commitOID': head, 'event': 'COMMENT', 'threads': [
            {'path': 'x.py', 'line': 1, 'side': 'RIGHT', 'body': 'Finding.'}]}}}
    return mod, pr, head, payload, live, writes


def test_review_reconciles_complete_locations_and_rejects_ambiguity(storage, monkeypatch):
    mod, pr, head, payload, live, writes = review_fixture(storage, monkeypatch)
    prepared = mod.prepare(pr, head, payload)
    with pytest.raises(Diagnostic):
        mod.publish(prepared['operation'])
    live[0]['comments'].append({'path': 'x.py', 'line': 1, 'side': 'RIGHT', 'body': 'extra'})
    with pytest.raises(Diagnostic):
        mod.publish(prepared['operation'], True)
    live[0]['comments'].pop()
    duplicate = copy.deepcopy(live[0]); duplicate['id'] = 124
    live.append(duplicate)
    with pytest.raises(Diagnostic):
        mod.publish(prepared['operation'], True)
    live.pop()
    assert mod.publish(prepared['operation'], True)['receipt']['comments'] == 1
    assert mod.publish(prepared['operation'])['reused']
    assert len(writes) == 1


@pytest.mark.parametrize('change', ['head', 'summary', 'line', 'range', 'pending'])
def test_review_validation_and_current_snapshot_fail_closed(storage, monkeypatch, change):
    mod, pr, head, payload, live, writes = review_fixture(storage, monkeypatch)
    value = payload['variables']['input']
    if change == 'head': value['commitOID'] = 'b' * 40
    if change == 'summary': value['body'] = 'unrequested summary'
    if change == 'line': value['threads'][0]['line'] = 99
    if change == 'range': value['threads'][0].update(startLine=0, startSide='RIGHT')
    if change == 'pending':
        prepared = mod.prepare(pr, head, payload)
        live.append({'id': 3, 'state': 'PENDING', 'comments': []})
        with pytest.raises(Diagnostic, match='existing reviews changed'):
            mod.publish(prepared['operation'])
    else:
        with pytest.raises(ValueError):
            mod.prepare(pr, head, payload)
    assert not writes


def test_complete_pagination_and_partial_collection_failures(storage, monkeypatch):
    from shared import publication_github as github
    calls = []
    def gh(argv):
        calls.append(argv)
        return [[{'id': n} for n in range(1, 101)], [{'id': 101}]]
    monkeypatch.setattr(github, 'gh', gh)
    assert len(github.pages('github.com', 'reviews')) == 101 and '--paginate' in calls[0]
    monkeypatch.setattr(github, 'gh', lambda *a: [{'total_count': 3, 'jobs': [{'id': 1}]}])
    with pytest.raises(ValueError, match='partial'):
        github.pages('github.com', 'jobs', 'jobs')
    monkeypatch.setattr(github, 'gh', lambda *a: {'message': 'malformed'})
    with pytest.raises(ValueError, match='incomplete'):
        github.pages('github.com', 'reviews')


@pytest.mark.parametrize('kind', ['ci-rerun', 'request-review'])
def test_attempt_reservation_and_uncertain_actions_never_repeat(storage, monkeypatch, kind):
    mod = module('recovery_actions', 'runtime/dk-pr-iter/scripts/publication_actions.py')
    monkeypatch.setattr(mod, 'completion_ready', lambda *a: {'gate': 'clean'})
    pr, head = 'https://github.com/o/r/pull/42', 'a' * 40
    monkeypatch.setattr(mod.run_state, 'work_gate', lambda *a: {'identity': {'pr_url': pr}})
    record = storage / 'run.json'
    record.write_text(json.dumps({'pr': 42, 'cycles': []}))
    meta = {'head': {'sha': head}, 'user': {'login': 'author'}}
    attempt = {'id': 77, 'head_sha': head, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure'}
    monkeypatch.setattr(mod, 'pr_snapshot', lambda pr: meta)
    monkeypatch.setattr(mod, 'rerun_ready', lambda *a: copy.deepcopy(attempt))
    monkeypatch.setattr(mod, 'run_snapshot', lambda *a: copy.deepcopy(attempt))
    reviews = [{'id': 1, 'state': 'COMMENTED', 'user': {'login': 'reviewer'}, 'commit_id': 'b' * 40}]
    pending = []
    monkeypatch.setattr(mod, 'request_snapshot', lambda pr: (meta, reviews, pending[:]))
    prepared = mod.prepare(kind, pr, head, record, run_id='77', attempt=1, reviewer='reviewer',
                           gate_arguments=['gate'], cwd=storage)
    same = mod.prepare(kind, pr, head, record, run_id='77', attempt=1, reviewer='reviewer',
                       gate_arguments=['gate'], cwd=storage)
    assert prepared['operation'] == same['operation']
    sends = []
    def apply(*args, **kwargs):
        sends.append(args)
        if kind == 'ci-rerun': attempt['run_attempt'] = 2
        else: pending.append('reviewer')
        raise OSError('server applied then disconnected')
    monkeypatch.setattr(mod, 'api', apply)
    with pytest.raises(Diagnostic):
        mod.publish(kind, prepared['operation'])
    if kind == 'ci-rerun':
        attempt['run_attempt'] = 1
        with pytest.raises(Diagnostic, match='not been observed'):
            mod.publish(kind, prepared['operation'], True)
        attempt['run_attempt'] = 2
    assert mod.publish(kind, prepared['operation'], True)['status'] == 'verified'
    assert mod.publish(kind, prepared['operation'])['reused']
    assert len(sends) == 1
    retained = json.loads(record.read_text())
    assert len(retained['reruns' if kind == 'ci-rerun' else 'review_requests']) == 1


def test_intent_and_artifact_storage_failures_preserve_previous_record(storage, monkeypatch):
    result = ResultFile(storage / 'result.json', storage, ROOT)
    original = result.path.read_bytes()
    monkeypatch.setattr('shared.private_state.os.fsync', lambda fd: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        result.save({'outcome': 'complete'})
    assert result.path.read_bytes() == original
    assert not list(storage.glob('.state-*'))
    result.close()


def test_cleanup_cannot_replace_the_original_storage_error(storage, monkeypatch):
    from shared import private_state
    fdopen = private_state.os.fdopen
    class Broken:
        def __init__(self, stream): self.stream = stream
        def write(self, content): raise OSError('original write failure')
        def close(self):
            self.stream.close()
            raise OSError('cleanup failure')
    monkeypatch.setattr(private_state.os, 'fdopen', lambda *args, **kwargs: Broken(fdopen(*args, **kwargs)))
    with pytest.raises(OSError, match='original write failure'):
        private_state.write(storage / 'state.json', {'data': 'value'})
    assert not list(storage.glob('.state-*'))
    def failed_fdopen(fd, *args, **kwargs):
        os.close(fd)
        raise OSError('original reservation failure')
    monkeypatch.setattr(private_state.os, 'fdopen', failed_fdopen)
    with pytest.raises(OSError, match='original reservation failure'):
        ResultFile(storage / 'result.json', storage, ROOT)
    assert not (storage / 'result.json').exists()


def test_real_rebase_retains_refs_and_reconciles_a_lost_push_response(storage, monkeypatch):
    mod = module('recovery_branch', 'runtime/dk-pr-iter/scripts/branch_publish.py')
    work = storage / 'work'
    work.mkdir()
    remote = storage / 'remote.git'
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=work, text=True, stderr=subprocess.DEVNULL).strip()
    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'Tester'); git('config', 'user.email', 'tester@example.com')
    (work / 'base').write_text('base')
    git('add', '.'); git('commit', '-qm', 'base')
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    git('remote', 'add', 'origin', str(remote)); git('push', '-q', 'origin', 'main')
    git('checkout', '-qb', 'topic')
    (work / 'feature').write_text('feature')
    git('add', '.'); git('commit', '-qm', 'feature'); git('push', '-q', 'origin', 'topic')
    original = git('rev-parse', 'HEAD')
    git('checkout', '-q', 'main')
    (work / 'target-change').write_text('target')
    git('add', '.'); git('commit', '-qm', 'advance target'); git('push', '-q', 'origin', 'main')
    target = git('rev-parse', 'HEAD')
    git('checkout', '-q', 'topic')
    pr = 'https://github.com/o/r/pull/42'
    def snapshot(pr):
        return {'url': pr, 'title': 'scope', 'body': 'body', 'head': {'ref': 'topic',
            'sha': git('ls-remote', '--heads', str(remote), 'topic').split()[0],
            'repo': {'full_name': 'o/r'}}, 'base': {'ref': 'main', 'sha': target,
            'repo': {'full_name': 'o/r'}}}
    monkeypatch.setattr(mod, 'pr_snapshot', snapshot)
    monkeypatch.setattr(mod, 'remote', lambda *a: {'fetch': str(remote), 'push': str(remote)})
    monkeypatch.setattr(mod.run_state, 'work_gate', lambda *a: {'identity': {'pr_url': pr}})
    record = storage / 'run.json'; record.write_text('{}')
    prepared = mod.prepare(pr, work, record)
    rebased = mod.rebase(prepared['state'])
    state = json.loads(Path(prepared['state']).read_text())
    assert git('rev-parse', state['recovery_refs']['original']) == original
    assert git('rev-parse', state['recovery_refs']['target']) == target
    assert git('rev-parse', state['recovery_refs']['candidate']) == state['candidate'] != original
    real_git = mod.git
    sends = []
    def lost_response(cwd, *args):
        result = real_git(cwd, *args)
        if args[0] == 'push':
            sends.append(args)
            raise subprocess.CalledProcessError(1, args, stderr='lost response')
        return result
    monkeypatch.setattr(mod, 'git', lost_response)
    with pytest.raises(Diagnostic) as failure:
        mod.publish(rebased['operation'])
    assert failure.value.action['argv'][-1] == rebased['operation']
    assert mod.publish(rebased['operation'], True)['receipt']['head'] == state['candidate']
    assert mod.publish(rebased['operation'])['reused'] and len(sends) == 1
    assert '--force-with-lease=refs/heads/topic:' + original in sends[0]
    assert (work / 'feature').read_text() == 'feature' and (work / 'target-change').read_text() == 'target'


def test_branch_dirty_work_and_remote_identity_produce_explicit_tasks(storage, monkeypatch):
    mod = module('recovery_branch_dirty', 'runtime/dk-pr-iter/scripts/branch_publish.py')
    monkeypatch.setattr(mod, 'git', lambda *a: '?? unique-work')
    with pytest.raises(Diagnostic) as failure:
        mod.prepare('https://github.com/o/r/pull/42', storage, storage / 'record')
    assert failure.value.code == 'local_changes' and failure.value.action['kind'] == 'agent_task'
    assert mod.remote_identity('git@github.com:O/R.git') == ('github.com', 'o/r')
    with pytest.raises(ValueError, match='exact GitHub repository'):
        mod.remote_identity(str(storage))


def test_stage_adapter_never_allocates_review_budget(storage, monkeypatch):
    mod = module('recovery_next', 'runtime/dk-pr-iter/scripts/run_state.py')
    head = 'a' * 40
    record = {'pr': 42, '_repo': 'github.com/o/r', 'cycles': [], 'iteration_run': {
        'id': 'run', 'status': 'active', 'contract_version': 2}}
    original = copy.deepcopy(record)
    monkeypatch.setattr(mod, 'work_gate', lambda *a: (_ for _ in ()).throw(ValueError('preparation missing')))
    assert mod.next_stage(record, head, storage / 'run')['stage'] == 'preparation'
    monkeypatch.setattr(mod, 'work_gate', lambda *a: {})
    monkeypatch.setattr(mod, 'current_review', lambda *a: (_ for _ in ()).throw(ValueError('no review')))
    record['preparation'] = {'path': 'prepared.json'}
    expected = copy.deepcopy(record)
    action = mod.next_stage(record, head, storage / 'run')
    assert action['stage'] == 'review' and action['action']['argv'][-3:] == ['review-session', '--head', head]
    assert record == expected and record['cycles'] == original['cycles']
    operation = pub.prepare('fixture', 'target', {}, {}, ['python3', 'reconcile'])
    saved = pub.load(operation['operation']); saved['status'] = 'pending'
    saved['intent'] = {'operation_id': saved['operation_id'], 'request_hash': saved['request_hash']}
    pub.write(Path(operation['operation']), saved)
    record['publication_attempts'] = {'key': operation['operation']}
    assert mod.next_stage(record, head, storage / 'run')['stage'] == 'publication_reconciliation'
    record['iteration_run'].pop('contract_version')
    assert mod.next_stage(record, head, storage / 'run')['stage'] == 'legacy_runtime'
