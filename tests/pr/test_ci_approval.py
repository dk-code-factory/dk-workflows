"""Evidence-backed incoming review finalization against offline GitHub and real Git."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'runtime'))
import config
from state import Store, capture, make_spec, read, write
from shared import publication
from shared.diagnostics import Diagnostic

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load('approval_remote_fixture', ROOT/'tests/pr/fixtures/github_remote.py')


class Remote(fixture.Remote):
    def api(self, pr, path, **kwargs):
        suffix = path.removeprefix('repos/owner/repo')
        if suffix == '/pulls/7':
            pull = super().api(pr, path, **kwargs)
            pull.update(title=self.state['metadata']['title'], body=self.state['metadata']['body'])
            pull['head'].update(ref='contributor-branch', repo={'id': 2})
            pull['base']['repo'] = {'id': 1}
            return pull
        if suffix.split('?')[0] == '/issues/7/comments':
            return deepcopy(self.state.get('discussion', []))
        if suffix.startswith('/actions/runs/'):
            run_id = int(suffix.split('/')[3])
            run = self.runs[run_id]
            if suffix.endswith('/approve'):
                self.calls.append(run_id)
                if self.deny == run_id:
                    raise subprocess.CalledProcessError(1, ['gh'], stderr='HTTP 403: Actions write permission required')
                run.update(status='queued', conclusion=None)
                if self.after_write:
                    self.after_write(run_id)
                return self.changed('ci-approve', '')
            if suffix.endswith('/pending_deployments'):
                return self.deployments.get(run_id, [])
            if '/jobs?' in suffix:
                return self.jobs.get(run_id, {'total_count': 0, 'jobs': []})
            return deepcopy(run)
        return super().api(pr, path, **kwargs)

    def run(self, argv, **kwargs):
        if 'reviewThreads' in (kwargs.get('payload') or {}).get('query', ''):
            return {'data': {'repository': {'pullRequest': {
                'url': fixture.PR, 'headRefOid': self.state['metadata']['headRefOid'],
                'reviewThreads': {'nodes': self.state.get('thread_states', []),
                                  'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}
        if any(arg.endswith('/actions/runs?per_page=100') for arg in argv):
            self.list_reads += 1
            assert '--paginate' in argv and '--slurp' in argv
            return [{'workflow_runs': deepcopy(page)} for page in self.pages()]
        return super().run(argv, **kwargs)


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    cli = load('ci_approve_test_cli', ROOT/'scripts/ci_approve.py')
    remote = Remote()
    remote.calls, remote.deny, remote.after_write = [], None, None
    remote.jobs, remote.deployments, remote.list_reads = {}, {}, 0
    checkout = tmp_path/'checkout'
    checkout.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()

    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.test')
    (checkout/'code.py').write_text('VALUE = 0\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (checkout/'code.py').write_text('VALUE = 1\n')
    git('commit', '-qam', 'head')
    head = git('rev-parse', 'HEAD')
    remote.state['metadata']['headRefOid'] = head
    remote.state['pull']['base']['sha'] = base
    remote.state['branches']['main'] = base
    remote.runs = {i: {'id': i, 'head_sha': head, 'run_attempt': 1, 'event': 'pull_request',
                      'status': 'completed', 'conclusion': 'action_required', 'repository': {'id': 1},
                      'pull_requests': [{'number': 7, 'head': {'sha': head, 'repo': {'id': 2}},
                                         'base': {'ref': 'main', 'sha': base, 'repo': {'id': 1}}}]}
                   for i in (101, 202)}
    remote.pages = lambda: [[remote.runs[101]], [remote.runs[202]]]
    for key in ('metadata', 'api', 'run'):
        monkeypatch.setattr(cli.publish, key, getattr(remote, key))
    repository = 'github.com/owner/repo'
    instructions = tmp_path/'instructions.txt'
    instructions.write_text('Review the PR. PR approval and merging are disabled.\n')
    skill = tmp_path/'SKILL.md'
    skill.write_text('Complete source review and return findings.\n')
    settings = {'reviewers': [{'skill': 'fixture:a'}, {'skill': 'fixture:b'}]}
    write(config.path(repository), settings)
    store = Store(repository, fixture.PR, checkout=checkout)
    document = {'workflow': 'dk-workflows:dk-pr-review', 'repository': repository, 'pr': fixture.PR,
                'checkout': str(checkout), 'head': head, 'base': base, 'run': 'review', 'cycle': 'c1',
                'analysis_only': False, 'instructions': {'user': str(instructions)},
                'discovery': [], 'verification': None, 'hooks': [], 'publications': []}

    def task(identity, responsibility='content-review', normalized=None, options=None, inputs=None, cacheable=True):
        from native_review_observations import CONTRACT, observations
        spec = make_spec(repository=repository, pr=fixture.PR, skill_identity=identity,
                         resources={'skill': str(skill)}, responsibility=responsibility,
                         assigned_instructions='Review pinned source and original evidence.',
                         instructions=document['instructions'], inputs=inputs or {}, options=options or {},
                         git={'checkout': str(checkout), 'head': head, 'target': base, 'base': base},
                         governing_inputs_complete=True, cacheable=cacheable, native_review=CONTRACT)
        path = tmp_path/(identity.replace(':', '-') + '-' + responsibility + '.json')
        write(path, spec)
        started = store.start(spec, run='review', cycle='c1')
        if started['action'] == 'invoke':
            store.record(spec, started['claim']['id'], b'Original completed fixture report',
                         normalized or {'complete': True, 'findings': []}, 'complete',
                         native_evidence=observations(spec, started['claim'], b'Original completed fixture report'))
        return str(path)

    document['discovery'] = [task('fixture:a'), task('fixture:b')]
    evidence = tmp_path/'ci-evidence.json'
    write(evidence, document)
    cli.snapshot(evidence)

    def edit(change, *, snapshot=False):
        value = read(evidence)
        change(value)
        write(evidence, value)
        if snapshot:
            cli.snapshot(evidence)

    def prepare(run_id=101):
        return cli.publish.prepare({**cli.request(evidence), 'kind': 'ci-approve',
                                    'run_id': run_id, 'attempt': 1})['operation']

    return type('Review', (), dict(cli=cli, remote=remote, evidence=evidence, edit=staticmethod(edit),
                                  prepare=staticmethod(prepare), task=staticmethod(task), store=store,
                                  settings=settings, head=head, base=base, instructions=instructions))()


def test_ready_finalization_approves_every_page_once_without_pr_approval_or_merge(reviewed):
    r = reviewed
    policy = config.effective('github.com/owner/repo')['settings']['incoming_review']
    assert policy['approve_ci'] and not policy['approve'] and not policy['merge']
    result = r.cli.approve(r.evidence)
    assert result['status'] == 'verified' and len(result['operations']) == 2 and not result['noop']
    assert r.remote.calls == [101, 202] and r.remote.list_reads == 2
    assert r.cli.approve(r.evidence)['noop']
    assert r.remote.calls == [101, 202]
    assert all(publication.load(op['operation'])['receipt']['execution_permitted'] for op in result['operations'])


@pytest.mark.parametrize('failure', ['incomplete', 'stale', 'missing-original', 'head', 'target', 'target-ref',
                                     'draft', 'closed', 'analysis-only', 'policy', 'instructions',
                                     'own-pr', 'configured-own-pr', 'discussion', 'thread-state', 'missing-reviewer', 'hook'])
def test_unready_review_cannot_approve(reviewed, failure):
    r = reviewed
    if failure in {'incomplete', 'stale', 'missing-original'}:
        spec = read(Path(read(r.evidence)['discovery'][0]))
        if failure == 'incomplete':
            r.store.invalidate([capture(spec)['key']], 'Review was incomplete')
        elif failure == 'stale':
            Path(spec['skill']['resources']['skill']).write_text('Changed governing skill')
        else:
            with r.store.locked() as manifest:
                entry = manifest['entries'][capture(spec)['key']]
                (r.store.root/'results'/entry['result']['file']).unlink()
    if failure == 'head': r.remote.state['metadata']['headRefOid'] = 'd'*40
    if failure == 'target': r.remote.state['branches']['main'] = 'd'*40
    if failure == 'target-ref': r.remote.state['pull']['base']['ref'] = 'partition'
    if failure == 'draft': r.remote.state['pull']['draft'] = True
    if failure == 'closed': r.remote.state['pull']['state'] = 'closed'
    if failure == 'analysis-only': r.edit(lambda v: v.update(analysis_only=True))
    if failure == 'policy':
        write(config.path('github.com/owner/repo'), {**r.settings, 'incoming_review': {'approve_ci': False}})
    if failure == 'instructions': r.instructions.write_text('Analysis only. Do not mutate GitHub.')
    if failure == 'own-pr': r.remote.state['pr_author']['id'] = r.remote.state['actor_id']
    if failure == 'configured-own-pr':
        write(config.path('github.com/owner/repo'), {**r.settings, 'identities': [{'host': 'github.com', 'id': 2}]})
        r.cli.snapshot(r.evidence)
    if failure == 'discussion': r.remote.state['discussion'] = [{'id': 5, 'body': 'New review scope'}]
    if failure == 'thread-state': r.remote.state['thread_states'] = [{'id': 'thread', 'isResolved': False, 'isOutdated': False}]
    if failure == 'missing-reviewer': r.edit(lambda v: v['discovery'].pop())
    if failure == 'hook':
        write(config.path('github.com/owner/repo'), {**r.settings, 'cycle_hooks': {
            'after_cycle': [{'id': 'final', 'instructions': 'Check the final review'}]}})
        r.cli.snapshot(r.evidence)
    with pytest.raises((ValueError, KeyError)):
        r.cli.approve(r.evidence)
    assert r.remote.calls == []


@pytest.mark.parametrize('disposition', [None, 'confirmed', 'uncertain', 'rejected', 'resolved'])
def test_candidates_need_complete_evidenced_verification(reviewed, disposition):
    r = reviewed
    candidate = r.task('fixture:a', 'candidate-review', {'complete': True, 'findings': [{'body': 'Candidate'}]})
    r.edit(lambda v: v['discovery'].__setitem__(0, candidate))
    if disposition:
        key = capture(read(Path(candidate)))['key']
        original = r.store.status([read(Path(candidate))])['tasks'][0]['result']
        verified = r.task('dk-workflows:dk-pr-review', 'finding-verification', {
            'complete': True, 'findings': [], 'dispositions': [
                {'task': key, 'index': 0, 'status': disposition, 'evidence': 'Source and reproduction evidence'}]}, inputs={'candidates': original})
        r.edit(lambda v: v.update(verification=verified))
    if disposition in {'rejected', 'resolved'}:
        assert r.cli.approve(r.evidence)['status'] == 'verified'
    else:
        with pytest.raises(ValueError): r.cli.approve(r.evidence)
        assert not r.remote.calls


@pytest.mark.parametrize('case', ['unrelated', 'earlier-head', 'deployment', 'ambiguous', 'jobs', 'wrong-fork'])
def test_only_unambiguous_fork_workflow_gates_are_eligible(reviewed, case):
    r = reviewed
    run = r.remote.runs[101]
    if case == 'unrelated': run['pull_requests'][0]['number'] = 8
    if case == 'earlier-head': run['head_sha'] = 'd'*40
    if case == 'deployment': run.update(status='waiting', conclusion=None)
    if case == 'ambiguous': run['pull_requests'] = []
    if case == 'jobs': r.remote.jobs[101] = {'total_count': 1, 'jobs': [{'id': 9}]}
    if case == 'wrong-fork': run['pull_requests'][0]['head']['repo']['id'] = 99
    if case in {'unrelated', 'earlier-head', 'deployment'}:
        assert r.cli.approve(r.evidence)['status'] == 'verified'
        assert r.remote.calls == [202]
    else:
        with pytest.raises(ValueError): r.cli.approve(r.evidence)
        assert not r.remote.calls


def test_partial_completion_permission_error_retains_receipts_and_never_resends(reviewed):
    r = reviewed
    r.remote.deny = 202
    with pytest.raises(Diagnostic, match='403'): r.cli.approve(r.evidence)
    assert r.remote.calls == [101, 202]
    operations = list(publication.workflow_storage.operations_root().glob('*/*/*.json'))
    assert sorted(publication.load(p)['status'] for p in operations) == ['pending', 'verified']
    r.remote.deny = None
    with pytest.raises(Diagnostic): r.cli.approve(r.evidence)
    assert r.remote.calls == [101, 202]


def test_lost_response_is_reconciled_before_discovery_without_resending(reviewed):
    r = reviewed
    r.remote.state['lose_response'] = ['ci-approve']
    with pytest.raises(Diagnostic): r.cli.approve(r.evidence)
    assert r.remote.calls == [101]
    result = r.cli.approve(r.evidence)
    assert len(result['operations']) == 2 and r.remote.calls == [101, 202]


def test_stale_inputs_between_runs_block_later_dispatch_and_keep_first_receipt(reviewed):
    r = reviewed
    r.remote.after_write = lambda run_id: r.instructions.write_text('Analysis only now')
    with pytest.raises(Diagnostic): r.cli.approve(r.evidence)
    assert r.remote.calls == [101]
    operation = next(publication.workflow_storage.operations_root().glob('*/*/*.json'))
    assert publication.load(operation)['status'] == 'verified'


@pytest.mark.parametrize('drift', ['attempt', 'expired', 'head'])
def test_uncertain_outcomes_retain_intent_without_resending(reviewed, drift):
    r = reviewed
    operation = r.prepare()
    r.remote.state['lose_response'] = ['ci-approve']
    with pytest.raises(Diagnostic): r.cli.publish.publish(operation)
    if drift == 'attempt': r.remote.runs[101]['run_attempt'] = 2
    if drift == 'expired': r.remote.runs[101].update(status='completed', conclusion='failure')
    if drift == 'head': r.remote.runs[101]['head_sha'] = 'd'*40
    for _ in range(2):
        with pytest.raises(Diagnostic): r.cli.publish.publish(operation, reconcile=True)
    assert r.remote.calls == [101] and publication.load(operation)['status'] == 'pending'


def test_changed_evidence_path_cannot_create_second_intent_for_same_run_attempt(reviewed, tmp_path):
    r = reviewed
    first = r.prepare()
    second_path = tmp_path/'other-evidence.json'
    second_path.write_bytes(r.evidence.read_bytes())
    second = r.cli.publish.prepare({**r.cli.request(second_path), 'kind': 'ci-approve', 'run_id': 101, 'attempt': 1})
    assert second['operation'] == first
    r.cli.publish.publish(first)
    assert r.remote.calls == [101]


def test_interruption_after_durable_intent_is_read_only_on_resume(reviewed, monkeypatch):
    r = reviewed
    operation = r.prepare()
    def interrupted(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(r.cli.publish.Handler, 'send', interrupted)
    with pytest.raises(KeyboardInterrupt): r.cli.publish.publish(operation)
    assert publication.load(operation)['status'] == 'pending'
    with pytest.raises(Diagnostic): r.cli.publish.publish(operation)
    assert not r.remote.calls


@pytest.mark.parametrize('failure', ['missing-page', 'duplicate', 'deployment-gate'])
def test_incomplete_or_ambiguous_discovery_blocks_all_dispatches(reviewed, failure):
    r = reviewed
    if failure == 'missing-page':
        r.remote.pages = lambda: [[r.remote.runs[101]], None]
    if failure == 'duplicate':
        r.remote.pages = lambda: [[r.remote.runs[101]], [r.remote.runs[101]]]
    if failure == 'deployment-gate':
        r.remote.deployments[202] = [{'environment': {'id': 9}}]
    with pytest.raises((ValueError, TypeError)): r.cli.approve(r.evidence)
    assert not r.remote.calls


def test_empty_complete_listing_returns_verified_noop_without_mutation(reviewed):
    r = reviewed
    r.remote.pages = lambda: [[]]
    assert r.cli.approve(r.evidence)['noop'] is True
    assert not r.remote.calls and r.remote.list_reads == 2


@pytest.mark.parametrize('change', ['head', 'target', 'draft', 'policy'])
def test_publisher_rechecks_review_readiness_immediately_before_dispatch(reviewed, change):
    r = reviewed
    operation = r.prepare()
    if change == 'head': r.remote.state['metadata']['headRefOid'] = 'd'*40
    if change == 'target': r.remote.state['branches']['main'] = 'd'*40
    if change == 'draft': r.remote.state['pull']['draft'] = True
    if change == 'policy':
        write(config.path('github.com/owner/repo'), {**r.settings, 'incoming_review': {'approve_ci': False}})
    with pytest.raises(Diagnostic): r.cli.publish.publish(operation)
    assert not r.remote.calls and publication.load(operation)['status'] == 'prepared'


def test_current_cycle_hooks_must_be_complete_before_approval(reviewed):
    r = reviewed
    hooks = {phase: [{'id': phase, 'instructions': 'Verify current scope'}]
             for phase in ('before_cycle', 'after_cycle')}
    write(config.path('github.com/owner/repo'), {**r.settings, 'cycle_hooks': hooks})
    paths = []
    for phase in hooks:
        paths.append(r.task('dk-workflows:dk-pr-review', 'cycle-hook-' + phase,
                            options={'cycle_hook': {'run': 'review', 'cycle': 'c1', 'phase': phase, 'id': phase}}))
    r.edit(lambda value: value.update(hooks=paths), snapshot=True)
    assert r.cli.approve(r.evidence)['status'] == 'verified'


def test_ready_boolean_without_original_task_coverage_is_insufficient(reviewed):
    r = reviewed
    r.edit(lambda value: value.update(ready=True, discovery=[]))
    with pytest.raises(ValueError, match='discovery coverage'): r.cli.approve(r.evidence)
    assert not r.remote.calls


def test_complete_noncacheable_review_evidence_is_valid_only_in_current_cycle(reviewed):
    r = reviewed
    path = r.task('fixture:a', 'current-noncacheable', cacheable=False)
    r.edit(lambda value: value['discovery'].__setitem__(0, path))
    assert r.cli.approve(r.evidence)['status'] == 'verified'
    r.edit(lambda value: value.update(cycle='c2'))
    with pytest.raises(ValueError): r.cli.approve(r.evidence)
    assert r.remote.calls == [101, 202]


def test_head_change_after_dispatch_retains_confirmed_execution_receipt(reviewed):
    r = reviewed
    r.remote.after_write = lambda run_id: r.remote.state['metadata'].update(headRefOid='d'*40)
    operation = r.prepare()
    with pytest.raises(Diagnostic): r.cli.publish.publish(operation)
    saved = publication.load(operation)
    assert saved['status'] == 'verified' and saved['receipt']['head'] == r.head
    assert r.remote.calls == [101]


def test_undispatched_approval_can_refresh_after_valid_reassessment(reviewed):
    r = reviewed
    operation = r.prepare()
    original = Path(operation).read_bytes()
    r.remote.state['discussion'] = [{'id': 5, 'body': 'Clarified scope'}]
    with pytest.raises(Diagnostic):
        r.cli.publish.publish(operation)
    assert publication.load(operation)['status'] == 'prepared' and not r.remote.calls
    r.cli.snapshot(r.evidence)  # Source and findings are unchanged by this clarification.
    assert r.prepare() == operation
    refreshed = publication.load(operation)
    assert Path(refreshed['preparations'][0]).read_bytes() == original
    for discussion in ([], [{'id': 5, 'body': 'Clarified scope'}]):
        r.remote.state['discussion'] = discussion
        r.cli.snapshot(r.evidence)
        assert r.prepare() == operation
    refreshed = publication.load(operation)
    assert len(set(refreshed['preparations'])) == 3
    assert Path(refreshed['preparations'][0]).read_bytes() == original
    assert r.cli.publish.publish(operation)['status'] == 'verified'
    assert r.remote.calls == [101]


def test_noncacheable_hook_claims_without_reviewer_cycle_are_complete(reviewed, tmp_path):
    r = reviewed
    write(config.path('github.com/owner/repo'), {**r.settings, 'cycle_hooks': {
        'after_cycle': [{'id': 'check', 'instructions': 'Check final scope'}]}})
    definition = read(Path(read(r.evidence)['discovery'][0]))
    spec = config.make_hook_spec(repository='github.com/owner/repo', pr=fixture.PR,
        run='review', cycle='c1', phase='after_cycle', hook_id='check', definition={
            'skill_identity': 'dk-workflows:dk-pr-review', 'resources': definition['skill']['resources'],
            'instructions': definition['instructions'], 'inputs': {},
            'git': definition['git'], 'cacheable': False, 'governing_inputs_complete': True})
    path = tmp_path/'hook.json'
    write(path, spec)
    claim = r.store.start(spec, run='review')['claim']
    assert claim['cycle'] is None
    r.store.record(spec, claim['id'], b'Final scope checked', {'complete': True, 'findings': []}, 'complete')
    r.edit(lambda value: value.update(hooks=[str(path)]), snapshot=True)
    assert r.cli.approve(r.evidence)['status'] == 'verified'
