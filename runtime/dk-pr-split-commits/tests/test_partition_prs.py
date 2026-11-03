import copy
import json
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlparse

import pytest

from shared import pr_partition as p, pr_size, pr_dependencies, partition_gate, publication, pr_partition_policy as policy
from shared.diagnostics import Diagnostic
from shared.review_loop import encode

PR = 'https://github.com/base/repo/pull/1'
HEAD_REPO = 'contributor/repo'
SPLIT = encode('split', 1, True)
DESCRIPTION = encode('dk-pr-description', 1, True)


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.PIPE, text=True).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    cwd = tmp_path / 'repo'
    cwd.mkdir()
    git(cwd, 'init', '-qb', 'main')
    git(cwd, 'config', 'user.name', 'Author')
    git(cwd, 'config', 'user.email', 'author@example.test')
    (cwd / 'base').write_text('base\n')
    git(cwd, 'add', '.')
    git(cwd, 'commit', '-qm', 'base')
    base = git(cwd, 'rev-parse', 'HEAD')
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    git(cwd, 'push', '-q', str(remote), 'main')
    git(cwd, 'checkout', '-qb', 'topic')
    units = []
    for name in ('a', 'b', 'c'):
        (cwd / name).write_text((name + '\n') * 3)
        git(cwd, 'add', name)
        git(cwd, 'commit', '-qm', 'add ' + name)
        units.append({'id': name, 'commit': git(cwd, 'rev-parse', 'HEAD'),
                      'scope': 'related', 'reason': 'Issue requires ' + name})
    git(cwd, 'push', '-q', str(remote), 'topic')
    remote_url = 'https://github.com/contributor/repo.git'
    git(cwd, 'remote', 'add', 'origin', remote_url)
    original_run = p.run
    def transport(work, *args):
        return original_run(work, *(str(remote) if arg == remote_url else arg for arg in args)) if args[:2] != (
            'git', 'remote') else original_run(work, *args)
    monkeypatch.setattr(p, 'run', transport)
    state = {'title': 'Primary deliverable', 'body': 'Fixes #9\n\n## Decisions\nKeep a.',
             'base': 'main', 'drafts': {}, 'writes': [], 'interrupt': None}
    def head(branch):
        return git(remote, 'rev-parse', 'refs/heads/' + branch)
    def metadata(work, url):
        entry = state if url == PR else next(item for item in state['drafts'].values() if item['html_url'] == url)
        branch = 'topic' if url == PR else entry['head']['ref']
        return {'url': url, 'number': int(url.rsplit('/', 1)[1]), 'title': entry['title'], 'body': entry['body'],
                'headRefOid': head(branch), 'headRefName': branch, 'headRepository': {'nameWithOwner': HEAD_REPO},
                'isCrossRepository': True, 'baseRefName': entry.get('base_name', 'main'),
                'baseRefOid': base, 'state': 'OPEN'}
    monkeypatch.setattr(p, 'metadata', metadata)
    def api(work, host, endpoint, *args):
        assert host == 'github.com'
        if endpoint == 'repos/base/repo':
            return {'default_branch': 'main'}
        if '/git/ref/heads/main' in endpoint:
            return {'object': {'sha': head('main')}}
        method = args[args.index('--method') + 1] if '--method' in args else 'GET'
        if '/pulls?' in endpoint:
            query = parse_qs(urlparse(endpoint).query)
            branch = query['head'][0].split(':', 1)[1]
            found = [item for item in state['drafts'].values() if item['head']['ref'] == branch]
            for item in found:
                item['head']['sha'] = head(branch)
            return [found]
        payload = json.loads(Path(args[args.index('--input') + 1]).read_text())
        state['writes'].append((method, endpoint))
        if method == 'POST':
            assert payload['draft'] is True and payload['base'] == 'main'
            assert payload['head'].startswith('contributor:')
            assert payload['head_repo'] == HEAD_REPO.split('/')[1]
            branch = payload['head'].split(':', 1)[1]
            number = len(state['drafts']) + 2
            item = {'title': payload['title'], 'body': payload['body'], 'number': number,
                    'html_url': PR.rsplit('/', 1)[0] + '/' + str(number), 'state': 'open', 'draft': True,
                    'head': {'ref': branch, 'sha': head(branch), 'repo': {'full_name': HEAD_REPO}},
                    'base': {'ref': 'main', 'repo': {'full_name': 'base/repo'}}}
            state['drafts'][number] = item
            result = item
        else:
            number = int(endpoint.rsplit('/', 1)[1])
            entry = state if number == 1 else state['drafts'][number]
            entry.update(title=payload['title'], body=payload['body'], base_name=payload['base'])
            result = entry
        if state['interrupt'] == method:
            state['interrupt'] = None
            raise RuntimeError('injected interruption after ' + method)
        return result
    monkeypatch.setattr(p, 'api', api)
    evidence = {}
    for key in ('scope', 'decisions', 'rules', 'dependencies'):
        target = tmp_path / (key + '.json')
        target.write_text(json.dumps({'requirements': [key]}))
        evidence[key] = str(target)
    chain = ['remote:github.com/contributor/repo', 'remote:github.com/base/repo']
    pr_size.configure(chain, 'set', settings={'added_lines_threshold': 6})
    def piece(name, depends=None):
        return {'id': 'original' if name == 'a' else name, 'units': [name], 'depends_on': depends or [],
                'title': 'Deliver ' + name, 'body': ('Fixes #9\n\n' if name == 'a' else '') +
                'Scope: ' + name + '\n\n{{dependencies}}\n\n{{related}}', 'reason': 'Independent behavior ' + name,
                'checks': [['git', 'diff', '--check', base]], 'oversized_reason': 'Prerequisite is indivisible.'}
    plan = {'units': units, 'pieces': [piece('a'), piece('b'), piece('c')],
            'assessments': {key: {'reason': 'Inspected issue, scope and Decisions: all three behaviors are required.'}
                            for key in p.BEHAVIORS}}
    return cwd, remote, state, evidence, plan, base, chain


def prepare(repo, plan=None):
    cwd, _, _, evidence, default, _, _ = repo
    return Path(p.prepare(cwd, PR, evidence, plan or default, 'origin')['state'])


def publish(path, **kwargs):
    return p.publish(path, review_state=SPLIT, description_review_state=DESCRIPTION,
                     candidate_hash=p.load(path)['candidate_hash'], **kwargs)


def test_default_branch_fork_publication_and_complete_change_preservation(repo):
    cwd, remote, state, evidence, plan, base, _ = repo
    original = git(cwd, 'rev-parse', 'HEAD')
    path = prepare(repo)
    data = p.verify(path)
    assert data['snapshot']['added_lines'] == 9
    assert data['recomposed_tree'] == git(cwd, 'rev-parse', original + '^{tree}')
    assert len(data['candidates']) == 3
    assert git(remote, 'rev-parse', 'topic') == original  # Construction has no remote writes.
    result = publish(path)
    assert result['outcome'] == 'published' and len(result['drafts']) == 2
    assert git(remote, 'rev-parse', 'topic') == data['candidates']['original']['head']
    assert 'Fixes #9' in state['body'] and all('Fixes #9' not in entry['body'] for entry in state['drafts'].values())
    assert all(PR in entry['body'] for entry in state['drafts'].values())
    assert all('Related PRs:' in entry['body'] for entry in state['drafts'].values())
    writes = list(state['writes'])
    assert publish(path)['outcome'] == 'published'
    assert state['writes'] == writes  # Exact resume reuses all verified operations.


def test_transitive_prerequisites_are_unique_ordered_and_have_real_provenance(repo):
    cwd, _, state, _, plan, base, _ = repo
    plan['pieces'][1]['depends_on'] = ['original']
    plan['pieces'][2]['depends_on'] = ['b', 'original']
    path = prepare(repo)
    data = p.verify(path)
    candidate = data['candidates']['c']
    assert len(candidate['prerequisites']) == 2
    assert [row['piece'] for row in candidate['prerequisites']] == ['original', 'b']
    assert candidate['added_lines'] == 9  # Copied prerequisites count toward the gate.
    assert publish(path)['outcome'] == 'published'
    url = p.load(path)['drafts']['c']
    body = next(item['body'] for item in state['drafts'].values() if item['html_url'] == url)
    rows = pr_dependencies.parse(body)
    prefix = pr_dependencies.validate_prefix(cwd, base, candidate['head'], rows)
    assert len(prefix) == 2
    bad = copy.deepcopy(rows)
    bad.reverse()
    with pytest.raises(ValueError, match='leading commits'):
        pr_dependencies.validate_prefix(cwd, base, candidate['head'], bad)


@pytest.mark.parametrize('kind', ['POST', 'PATCH'])
def test_interrupted_remote_creations_and_description_edits_reconcile_without_duplicates(repo, kind):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = kind
    with pytest.raises(Diagnostic, match='read-only reconciliation'):
        publish(path)
    writes = list(state['writes'])
    p.publish(path, reconcile=True)
    assert state['writes'] == writes
    assert publish(path)['outcome'] == 'published'
    assert len(state['drafts']) == 2
    assert sum(method == 'POST' for method, _ in state['writes']) == 2


@pytest.mark.parametrize('branch_name', ['b', 'original'])
def test_interrupted_branch_pushes_reconcile_without_repeating_any_push(repo, monkeypatch, branch_name):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    real = p.run
    pushes = []
    interrupted = False
    def run(cwd, *argv):
        nonlocal interrupted
        result = real(cwd, *argv)
        if argv[:2] == ('git', 'push'):
            pushes.append(argv[-1])
            match = argv[-1].endswith(':refs/heads/topic') if branch_name == 'original' else argv[-1].endswith('-b')
            if match and not interrupted:
                interrupted = True
                raise RuntimeError('lost push response')
        return result
    monkeypatch.setattr(p, 'run', run)
    with pytest.raises(Diagnostic, match='read-only reconciliation'):
        publish(path)
    before = list(pushes)
    p.publish(path, reconcile=True)
    assert pushes == before
    assert publish(path)['outcome'] == 'published'
    assert len(pushes) == len(set(pushes)) == 3


def test_evidence_changes_block_publication_before_remote_mutation(repo):
    _, _, state, evidence, _, _, chain = repo
    path = prepare(repo)
    Path(evidence['rules']).write_text('new obligation')
    with pytest.raises(ValueError, match='evidence changed'):
        publish(path)
    assert not state['writes']


@pytest.mark.parametrize('change', ['base', 'body', 'settings', 'lease'])
def test_stale_base_scope_settings_and_leases_block_publication(repo, change):
    cwd, remote, state, _, _, _, chain = repo
    path = prepare(repo)
    if change == 'base':
        git(remote, 'update-ref', 'refs/heads/main', git(cwd, 'rev-parse', 'HEAD'))
    elif change == 'body':
        state['body'] += '\nNew Decision'
    elif change == 'settings':
        pr_size.configure(chain, 'set', settings={'split_related': 'manual'})
    else:
        git(remote, 'update-ref', 'refs/heads/topic', git(cwd, 'rev-parse', 'HEAD~1'))
    with pytest.raises(ValueError):
        publish(path)
    assert not state['writes']


def test_manual_approval_is_action_bound_and_decline_does_not_move_work_via_other_action(repo):
    cwd, remote, state, evidence, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual', 'split_related': 'automatic'})
    path = prepare(repo)
    result = publish(path)
    assert result['outcome'] == 'awaiting_approval' and not state['writes']
    assert p.prepare(cwd, PR, evidence, plan, 'origin')['state'] == str(path)
    assert p.check(cwd, PR, evidence)['outcome'] == 'partition_pending'
    with pytest.raises(ValueError, match='exact proposed action'):
        publish(path, decisions={'evacuate_unrelated': {'action_id': 'wrong', 'decision': 'approved'}})
    result = publish(path, decisions={'evacuate_unrelated': {
        'action_id': result['action_ids']['evacuate_unrelated'], 'decision': 'declined'}})
    assert result['outcome'] == 'selection_ready'
    assert not state['writes']
    assert publish(path)['outcome'] == 'published'
    assert len(state['drafts']) == 1  # Related b moved; declined unrelated c stays.
    retained = git(remote, 'ls-tree', '--name-only', 'topic').splitlines()
    assert 'a' in retained and 'c' in retained and 'b' not in retained


def test_declined_work_cannot_be_copied_as_a_prerequisite(repo):
    _, _, state, _, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][1]['depends_on'] = ['c']
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual'})
    path = prepare(repo)
    result = publish(path)
    with pytest.raises(ValueError, match='declined work is a prerequisite'):
        publish(path, decisions={'evacuate_unrelated': {
            'action_id': result['action_ids']['evacuate_unrelated'], 'decision': 'declined'}})
    assert not state['writes']


def test_indivisible_assessment_and_declines_are_cached_across_history_rewrites(repo):
    cwd, remote, state, evidence, plan, _, chain = repo
    plan['pieces'][0]['units'] = ['a', 'b', 'c']
    plan['pieces'] = plan['pieces'][:1]
    path = prepare(repo)
    assert p.check(cwd, PR, evidence)['outcome'] == 'no_op'
    git(cwd, 'commit', '--amend', '-qm', 'equivalent history')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    assert p.check(cwd, PR, evidence)['state'] == str(path)
    assert p.check(cwd, PR, evidence)['outcome'] == 'no_op'
    Path(evidence['decisions']).write_text(json.dumps({'requirements': ['decisions'], 'head': 'new-head'}))
    assert p.check(cwd, PR, evidence)['outcome'] == 'no_op'
    Path(evidence['scope']).write_text('new issue obligation')
    assert p.check(cwd, PR, evidence)['outcome'] == 'assessment_required'


@pytest.mark.parametrize('defect', ['omission', 'duplication', 'cycle', 'no_primary', 'closing'])
def test_unsafe_plans_fail_before_remote_changes(repo, defect):
    _, _, state, _, plan, _, _ = repo
    if defect == 'omission':
        plan['pieces'].pop()
    elif defect == 'duplication':
        plan['pieces'][1]['units'].append('a')
    elif defect == 'cycle':
        plan['pieces'][0]['depends_on'] = ['b']
        plan['pieces'][1]['depends_on'] = ['original']
    elif defect == 'no_primary':
        plan['units'][0]['scope'] = 'unrelated'
    else:
        plan['pieces'][1]['body'] += '\nCloses #9'
    with pytest.raises(ValueError):
        prepare(repo)
    assert not state['writes']


def test_final_tree_preservation_detects_wrong_unit_content(repo):
    cwd, _, state, _, plan, base, _ = repo
    plan['units'][2]['commit'] = base
    with pytest.raises((ValueError, RuntimeError)):
        prepare(repo)
    assert not state['writes']


def test_non_alphabetical_unit_order_survives_json_serialization(repo):
    _, _, _, _, plan, _, _ = repo
    for unit, name in zip(plan['units'], ('z', 'y', 'x')):
        unit['id'] = name
    plan['pieces'][0]['units'] = ['z', 'y', 'x']
    plan['pieces'] = plan['pieces'][:1]
    path = prepare(repo)
    assert p.verify(path)['status'] == 'no_op'


def test_cached_assessment_verifies_after_equivalent_history_rewrite(repo):
    cwd, remote, _, evidence, plan, _, _ = repo
    plan['pieces'][0]['units'] = ['a', 'b', 'c']
    plan['pieces'] = plan['pieces'][:1]
    path = prepare(repo)
    git(cwd, 'commit', '--amend', '-qm', 'history only')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    assert p.check(cwd, PR, evidence)['state'] == str(path)
    assert p.verify(path)['status'] == 'no_op'


def test_changed_source_branch_blocks_before_draft_publication(repo, monkeypatch):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    original = p.metadata
    def metadata(cwd, pr):
        value = original(cwd, pr)
        if pr == PR:
            value['headRefName'] = 'different-topic'
        return value
    monkeypatch.setattr(p, 'metadata', metadata)
    with pytest.raises(ValueError, match='stale'):
        publish(path)
    assert not state['writes']


def test_post_evacuation_recount_includes_prerequisites(repo):
    _, _, _, _, plan, _, chain = repo
    pr_size.configure(chain, 'set', settings={'added_lines_threshold': 8})
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][0]['depends_on'] = ['c']
    path = prepare(repo)
    assert p.load(path)['after_evacuation_added_lines'] == 9


def test_interrupted_decline_rebuild_preserves_artifacts_and_can_resume(repo, monkeypatch):
    _, _, state, _, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual'})
    path = prepare(repo)
    pending = publish(path)
    decision = {'evacuate_unrelated': {'action_id': pending['action_ids']['evacuate_unrelated'], 'decision': 'declined'}}
    original = p.build
    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        raise RuntimeError('interrupted after local construction')
    monkeypatch.setattr(p, 'build', interrupted)
    with pytest.raises(RuntimeError, match='local construction'):
        publish(path, decisions=decision)
    monkeypatch.setattr(p, 'build', original)
    assert publish(path, decisions=decision)['outcome'] == 'selection_ready'
    assert len(list(path.parent.glob('selected-*'))) == 2
    assert not state['writes']
    assert publish(path)['outcome'] == 'published'


def test_generated_recovery_command_is_accepted_by_the_cli(repo):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    data = p.load(path)
    operation = next(publication.load(path) for key, path in data['operations'].items() if key.startswith('create-'))
    argv = operation['reconcile_argv']
    # Execute the parser with --help so the real subprocess never accesses GitHub.
    result = subprocess.run([*argv, '--help'], capture_output=True, text=True)
    assert result.returncode == 0 and '--operation' in result.stdout
    assert '--cwd' in argv and str(repo[0]) in argv


@pytest.mark.parametrize('evacuate,split', [(a, b) for a in pr_size.MODES for b in pr_size.MODES])
def test_all_independent_behavior_combinations(repo, evacuate, split):
    cwd, _, state, evidence, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': evacuate, 'split_related': split})
    if evacuate == split == 'disabled':
        assert p.check(cwd, PR, evidence)['outcome'] == 'disabled'
        assert not state['writes']
        return
    original = plan['pieces'][0]
    if evacuate == 'disabled':
        original['units'].append('c')
        plan['pieces'] = [piece for piece in plan['pieces'] if piece['id'] != 'c']
    if split == 'disabled':
        original['units'].append('b')
        plan['pieces'] = [piece for piece in plan['pieces'] if piece['id'] != 'b']
    path = prepare(repo)
    result = publish(path)
    manual = [key for key, value in (('evacuate_unrelated', evacuate), ('split_related', split)) if value == 'manual']
    if manual:
        assert result['outcome'] == 'awaiting_approval' and not state['writes']
        result = publish(path, decisions={key: {'action_id': result['action_ids'][key], 'decision': 'approved'}
                                          for key in manual})
    assert result['outcome'] == 'published'
    assert len(state['drafts']) == (evacuate != 'disabled') + (split != 'disabled')


def test_every_manual_action_declined_is_cached_without_publication(repo):
    cwd, _, state, evidence, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual', 'split_related': 'manual'})
    path = prepare(repo)
    result = publish(path)
    decisions = {key: {'action_id': action_id, 'decision': 'declined'} for key, action_id in result['action_ids'].items()}
    assert publish(path, decisions=decisions)['outcome'] == 'declined'
    assert p.check(cwd, PR, evidence)['outcome'] == 'declined'
    assert publish(path)['outcome'] == 'declined' and not state['writes']


def test_iteration_gate_requires_current_assessment_and_blocks_pending_work(repo, monkeypatch):
    cwd, _, _, evidence, plan, base, chain = repo
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base},
                'engineering': 'engineering-one'}
    context = {'checkout': str(cwd), 'repository_identity': chain[0]}
    monkeypatch.setattr(partition_gate, 'engineering_id', lambda value: value['engineering'])
    monkeypatch.setattr(p, 'preparation', lambda *_: prepared['engineering'])
    with pytest.raises(ValueError, match='assessment required'):
        partition_gate.require({}, prepared, context)
    path = Path(p.prepare(cwd, PR, evidence, plan, 'origin', preparation_file=Path('/private/preparation'))['state'])
    record = {'cycles': [{'number': 5}]}
    partition_gate.attach(record, prepared, context, path)
    with pytest.raises(ValueError, match='pending'):
        partition_gate.require(record, prepared, context)
    assert record['cycles'] == [{'number': 5}]


def test_iteration_gate_reuses_indivisible_assessment_only_for_equivalent_engineering(repo, monkeypatch):
    cwd, remote, _, evidence, plan, base, chain = repo
    plan['pieces'][0]['units'] = ['a', 'b', 'c']
    plan['pieces'] = plan['pieces'][:1]
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base},
                'engineering': 'engineering-one'}
    context = {'checkout': str(cwd), 'repository_identity': chain[0]}
    monkeypatch.setattr(partition_gate, 'engineering_id', lambda value: value['engineering'])
    monkeypatch.setattr(p, 'preparation', lambda *_: 'engineering-one')
    path = Path(p.prepare(cwd, PR, evidence, plan, 'origin', preparation_file=Path('/private/preparation'))['state'])
    record = {}
    partition_gate.attach(record, prepared, context, path)
    assert partition_gate.require(record, prepared, context)['outcome'] == 'no_op'
    git(cwd, 'commit', '--amend', '-qm', 'equivalent rewrite')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    prepared['identity']['head_sha'] = git(cwd, 'rev-parse', 'HEAD')
    assert partition_gate.require(record, prepared, context)['outcome'] == 'no_op'
    prepared['engineering'] = 'new obligation'
    with pytest.raises(ValueError, match='assessment required'):
        partition_gate.require(record, prepared, context)
    with pytest.raises(ValueError, match='engineering preparation'):
        partition_gate.attach(record, prepared, context, path)


def test_below_threshold_and_disabled_iteration_gate_needs_no_assessment(repo):
    cwd, _, _, _, _, base, chain = repo
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base}}
    context = {'checkout': str(cwd), 'repository_identity': chain[0]}
    pr_size.configure(chain, 'reset')
    assert partition_gate.require({}, prepared, context)['outcome'] == 'below_threshold'
    pr_size.configure(chain, 'set', settings={'added_lines_threshold': 1,
                                           'evacuate_unrelated': 'disabled', 'split_related': 'disabled'})
    assert partition_gate.require({}, prepared, context)['outcome'] == 'disabled'


def test_reconciliation_records_dispatched_write_even_when_rules_changed(repo):
    _, _, state, evidence, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    Path(evidence['rules']).write_text('new governing obligation')
    writes = list(state['writes'])
    with pytest.raises(Diagnostic, match='current readiness failed'):
        p.publish(path, reconcile=True)
    assert state['writes'] == writes
    data = p.load(path)
    operation = next(publication.load(path) for key, path in data['operations'].items() if key.startswith('create-'))
    assert operation['status'] == 'verified'
    with pytest.raises(ValueError, match='evidence changed'):
        publish(path)


def test_partial_source_removal_blocks_iteration_even_without_record_registration(repo):
    cwd, _, _, _, _, base, chain = repo
    path = prepare(repo)
    data = p.load(path)
    # Simulate interruption after any journaled publication but before record attach.
    data['operations']['push-b'] = publication.prepare('partition-branch', PR, {}, {}, ['reconcile'],
        directory=path.parent)['operation']
    from shared.private_state import write
    write(path, data)
    pr_size.configure(chain, 'set', settings={'added_lines_threshold': 1000})
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base}}
    with pytest.raises(ValueError, match='publication is pending'):
        partition_gate.require({}, prepared, {'checkout': str(cwd), 'repository_identity': chain[0]})


def test_later_commit_regrouping_freezes_validated_prerequisite_prefix(repo, monkeypatch, capsys):
    import importlib.util
    cwd, _, _, _, plan, base, _ = repo
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][0]['depends_on'] = ['c']
    path = prepare(repo)
    assert publish(path)['outcome'] == 'published'
    candidate = p.load(path)['candidates']['original']
    git(cwd, 'reset', '--hard', candidate['head'])
    git(cwd, 'update-ref', 'refs/remotes/origin/topic', candidate['head'])
    git(cwd, 'branch', '--set-upstream-to', 'origin/topic', 'topic')
    script = Path(__file__).resolve().parents[1] / 'scripts/split_guard.py'
    spec = importlib.util.spec_from_file_location('partition_frozen_split_guard', script)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.chdir(cwd)
    def run(*argv, **kwargs):
        if argv[:3] == ('gh', 'pr', 'view'):
            return json.dumps(p.metadata(cwd, PR))
        return p.run(cwd, *argv)
    monkeypatch.setattr(guard, 'run', run)
    guard.prepare(base, PR)
    data = json.loads(guard.state_path().read_text())
    assert len(data['prerequisite_prefix']) == 1
    guard.verify(data)
    prefix = data['prerequisite_prefix'][0]
    git(cwd, 'reset', '--hard', prefix)
    git(cwd, 'commit', '--amend', '-qm', 'changed prerequisite message')
    p.replay(cwd, list(candidate['own_commits'].values()))
    assert git(cwd, 'rev-parse', 'HEAD^{tree}') == data['original_tree']
    with pytest.raises(ValueError, match='leading commits'):
        guard.verify(data)


def test_copy_only_declared_minimum_prerequisite_units(repo):
    _, _, _, _, plan, _, _ = repo
    plan['pieces'][0]['depends_on'] = ['c']
    plan['pieces'][0]['prerequisite_units'] = {'c': ['b']}
    plan['pieces'][2]['units'] = ['b', 'c']
    plan['pieces'].pop(1)
    path = prepare(repo)
    candidate = p.verify(path)['candidates']['original']
    assert candidate['added_lines'] == 6
    assert len(candidate['prerequisites'][0]['commits']) == 1
    assert publish(path)['outcome'] == 'published'


def test_disabled_evacuation_keeps_unassessed_work_out_of_draft_dependencies(repo):
    _, _, _, _, plan, _, chain = repo
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'disabled'})
    plan['units'][2]['scope'] = 'retained'
    plan['pieces'][0]['units'] = ['a', 'c']
    plan['pieces'].pop(2)
    plan['pieces'][1]['depends_on'] = ['original']
    with pytest.raises(ValueError, match='cannot copy retained'):
        prepare(repo)
    plan['pieces'][1]['prerequisite_units'] = {'original': ['a']}
    path = prepare(repo)
    assert publish(path)['outcome'] == 'published'


def test_retained_indivisible_original_also_targets_default_branch(repo):
    _, _, state, _, plan, _, _ = repo
    state['base_name'] = 'release'
    plan['pieces'][0]['units'] = ['a', 'b', 'c']
    plan['pieces'] = plan['pieces'][:1]
    path = prepare(repo)
    assert p.load(path)['status'] == 'prepared'
    assert publish(path)['outcome'] == 'published'
    assert state['base_name'] == 'main' and not state['drafts']


@pytest.mark.parametrize('retain_foreign', [False, True])
def test_foreign_authorship_requires_intact_pinned_units(repo, retain_foreign):
    cwd, remote, _, _, plan, base, _ = repo
    original_units = copy.deepcopy(plan['units'])
    git(cwd, 'reset', '--hard', base)
    p.replay(cwd, [original_units[0]['commit'], original_units[1]['commit']])
    git(cwd, 'commit', '--amend', '--no-edit', '--author', 'Other <other@example.test>')
    foreign = git(cwd, 'rev-parse', 'HEAD')
    p.replay(cwd, [original_units[2]['commit']])
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    if retain_foreign:
        plan['units'][1]['commit'] = foreign
        path = prepare(repo)
        head = p.verify(path)['candidates']['b']['head']
        assert git(cwd, 'show', '-s', '--format=%ae', head) == 'other@example.test'
    else:
        # The old b unit has identical changes but credits the current author.
        with pytest.raises(ValueError, match='foreign-author source commits require intact units'):
            prepare(repo)


def test_pending_manual_assessment_reuses_action_ids_after_equivalent_rewrite(repo):
    cwd, remote, state, evidence, plan, _, chain = repo
    pr_size.configure(chain, 'set', settings={'split_related': 'manual'})
    path = prepare(repo)
    pending = publish(path)
    action_ids = pending['action_ids']
    old_head = p.load(path)['snapshot']['head']
    git(cwd, 'commit', '--amend', '-qm', 'equivalent pending history')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    assert p.check(cwd, PR, evidence)['outcome'] == 'partition_pending'
    assert p.prepare(cwd, PR, evidence, plan, 'origin')['state'] == str(path)
    data = p.verify(path)
    assert data['snapshot']['head'] != old_head and old_head in data['equivalent_heads']
    assert publish(path)['action_ids'] == action_ids and not state['writes']
    decision = {key: {'action_id': action_id, 'decision': 'approved'} for key, action_id in action_ids.items()}
    assert publish(path, decisions=decision)['outcome'] == 'published'


def test_declining_a_dependency_folds_it_and_its_checks_into_original(repo):
    _, _, _, _, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][0]['depends_on'] = ['c']
    plan['pieces'][2]['checks'].append(['git', 'show', 'HEAD:c'])
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual'})
    path = prepare(repo)
    pending = publish(path)
    decision = {'evacuate_unrelated': {'action_id': pending['action_ids']['evacuate_unrelated'], 'decision': 'declined'}}
    assert publish(path, decisions=decision)['outcome'] == 'selection_ready'
    data = p.verify(path)
    original = next(piece for piece in data['pieces'] if piece['id'] == 'original')
    assert original['units'] == ['a', 'c'] and not original['depends_on']
    assert ['git', 'show', 'HEAD:c'] in original['checks']
    assert publish(path)['outcome'] == 'published'


def test_declining_a_dependent_preserves_its_external_prerequisite(repo):
    _, _, _, _, plan, _, chain = repo
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][2]['depends_on'] = ['b']
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'manual'})
    path = prepare(repo)
    pending = publish(path)
    decision = {'evacuate_unrelated': {'action_id': pending['action_ids']['evacuate_unrelated'], 'decision': 'declined'}}
    assert publish(path, decisions=decision)['outcome'] == 'selection_ready'
    data = p.verify(path)
    original = next(piece for piece in data['pieces'] if piece['id'] == 'original')
    assert original['depends_on'] == ['b']
    assert data['candidates']['original']['added_lines'] == 9
    assert publish(path)['outcome'] == 'published'


def test_decline_that_exposes_dependency_cycle_requires_a_new_decision(repo):
    _, _, state, _, plan, _, _ = repo
    plan['units'][2]['scope'] = 'unrelated'
    plan['pieces'][1]['depends_on'] = ['original']
    plan['pieces'][2]['depends_on'] = ['b']
    with pytest.raises(ValueError, match='cyclic prerequisite graph'):
        p.selected_pieces({'plan': plan, 'decisions': {'evacuate_unrelated': 'declined'}})
    assert not state['writes']


def policy_assessment(decision, *, reason='Applied the exact human conditions to the current scope.'):
    return {'decision': decision, 'policy_fingerprint': policy.load(PR)['fingerprint'],
            'reason': reason, 'evidence': ['partition_policy', 'scope', 'diff']}


def test_pr_disable_skips_assessment_and_unpublished_candidates_across_heads(repo, monkeypatch):
    cwd, remote, _, evidence, _, base, chain = repo
    path = prepare(repo)
    policy.configure(PR, 'disable', reason='Never partition this PR.', source='Keep this PR as one deliverable.')
    def unexpected(*args, **kwargs):
        raise AssertionError('disabled PR must not inspect remote metadata or line counts')
    monkeypatch.setattr(p, 'metadata', unexpected)
    monkeypatch.setattr(pr_size, 'additions', unexpected)
    assert p.check(cwd, PR, None)['outcome'] == 'pr_disabled'
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base}}
    record = {'partition_assessment': {'path': str(path)}, 'cycles': [{'number': 5}]}
    context = {'checkout': str(cwd), 'repository_identity': chain[0]}
    assert partition_gate.require(record, prepared, context)['outcome'] == 'pr_disabled'
    git(cwd, 'commit', '--amend', '-qm', 'new history')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    prepared['identity']['head_sha'] = git(cwd, 'rev-parse', 'HEAD')
    assert partition_gate.require(record, prepared, context)['outcome'] == 'pr_disabled'
    assert record['cycles'] == [{'number': 5}]


def test_reenabling_pr_discards_local_old_preference_assessment(repo):
    cwd, _, _, evidence, _, base, chain = repo
    path = prepare(repo)
    old = p.load(path)
    policy.configure(PR, 'disable', reason='Keep this PR together.')
    policy.configure(PR, 'enable', reason='Partitioning is appropriate now.')
    assert p.check(cwd, PR, evidence)['outcome'] == 'assessment_required'
    prepared = {'identity': {'pr_url': PR, 'head_sha': old['snapshot']['head'], 'base_sha': base}}
    with pytest.raises(ValueError, match='assessment required'):
        partition_gate.require({'partition_assessment': {'path': str(path)}}, prepared,
                               {'checkout': str(cwd), 'repository_identity': chain[0]})


def test_conditions_reach_worker_unchanged_and_agent_can_retain_without_building(repo, monkeypatch):
    cwd, _, state, evidence, _, base, chain = repo
    instructions = 'Keep schema and adapters together.\nPartition only after their interaction is stable.'
    policy.configure(PR, 'instructions', reason='Human conditions.', instructions=instructions)
    result = p.check(cwd, PR, evidence)
    assert result['outcome'] == 'assessment_required'
    assert result['partition_policy']['instructions'] == instructions
    assert result['binding']['partition_policy']['instructions'] == instructions
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base},
                'engineering': 'prepared-engineering'}
    context = {'checkout': str(cwd), 'repository_identity': chain[0]}
    monkeypatch.setattr(partition_gate, 'engineering_id', lambda value: value['engineering'])
    monkeypatch.setattr(p, 'preparation', lambda *_: prepared['engineering'])
    assessment = policy_assessment('retain', reason='Adapters and schema still share an unstable integration boundary.')
    recorded = p.assess_policy(cwd, PR, evidence, assessment, preparation_file=Path('/private/preparation'))
    path = Path(recorded['state'])
    repeated = p.assess_policy(cwd, PR, evidence, assessment, preparation_file=Path('/private/preparation'))
    assert repeated['reused'] is True and repeated['state'] == str(path)
    data = p.verify(path)
    assert data['assessment'] == assessment and 'candidates' not in data
    assert not state['writes']
    record = {'cycles': [{'number': 5}]}
    partition_gate.attach(record, prepared, context, path)
    assert partition_gate.require(record, prepared, context)['outcome'] == 'no_op'
    assert p.check(cwd, PR, evidence, preparation_file=Path('/private/preparation'))['state'] == str(path)
    assert p.publish(path)['outcome'] == 'no_op' and not state['writes']
    assert record['cycles'] == [{'number': 5}]


def test_custom_conditions_can_trigger_partition_below_default_threshold(repo):
    cwd, _, _, evidence, plan, _, chain = repo
    pr_size.configure(chain, 'reset')
    policy.configure(PR, 'instructions', reason='Split independently testable behavior.',
                     instructions='Partition when there are more than two independently testable behaviors, regardless of line count.')
    assert p.check(cwd, PR, evidence)['outcome'] == 'assessment_required'
    with pytest.raises(ValueError, match='agent assessment'):
        prepare(repo)
    plan['policy_assessment'] = policy_assessment('partition', reason='Three independent behaviors have separate files and checks.')
    path = prepare(repo)
    assert p.load(path)['snapshot']['added_lines'] == 9
    assert pr_size.load(chain)['pr_size']['added_lines_threshold'] == 1000
    assert publish(path)['outcome'] == 'published'


def test_changed_instructions_invalidate_cached_retention_and_stale_candidate_publication(repo):
    cwd, _, state, evidence, plan, _, _ = repo
    policy.configure(PR, 'instructions', reason='Initial conditions.', instructions='Keep the integrated feature together.')
    retained = p.assess_policy(cwd, PR, evidence, policy_assessment('retain'))
    policy.configure(PR, 'instructions', reason='New human conditions.', instructions='Split independently testable behavior.')
    assert p.check(cwd, PR, evidence)['outcome'] == 'assessment_required'
    with pytest.raises(ValueError, match='governing inputs changed'):
        p.verify(retained['state'])
    plan['policy_assessment'] = policy_assessment('partition')
    path = prepare(repo)
    policy.configure(PR, 'disable', reason='Do not partition this PR.')
    with pytest.raises(ValueError, match='stale'):
        publish(path)
    assert not state['writes']


def test_conditional_retention_cache_survives_history_only_rewrite_but_not_new_changes(repo):
    cwd, remote, _, evidence, _, _, _ = repo
    policy.configure(PR, 'instructions', reason='Human conditions.', instructions='Keep coupled work together.')
    state = p.assess_policy(cwd, PR, evidence, policy_assessment('retain'))['state']
    git(cwd, 'commit', '--amend', '-qm', 'equivalent history')
    git(cwd, 'push', '-q', '--force', str(remote), 'topic')
    assert p.check(cwd, PR, evidence)['state'] == state
    assert p.verify(state)['status'] == 'no_op'
    (cwd / 'new').write_text('new behavior\n')
    git(cwd, 'add', 'new')
    git(cwd, 'commit', '-qm', 'new behavior')
    git(cwd, 'push', '-q', str(remote), 'topic')
    assert p.check(cwd, PR, evidence)['outcome'] == 'assessment_required'


def test_declining_an_action_is_recorded_outside_the_tree_bound_plan_cache(repo):
    _, _, state, _, _, _, chain = repo
    pr_size.configure(chain, 'set', settings={'split_related': 'manual'})
    path = prepare(repo)
    pending = publish(path)
    before = policy.binding(PR)
    decisions = {'split_related': {'action_id': pending['action_ids']['split_related'], 'decision': 'declined',
                                  'reason': 'The current API and adapter belong together.',
                                  'source': 'Keep these related changes together in this proposal.'}}
    assert publish(path, decisions=decisions)['outcome'] == 'declined'
    recorded = policy.load(PR)
    assert recorded['enabled'] is True and len(recorded['declines']) == 1
    assert recorded['declines'][0]['source'] == decisions['split_related']['source']
    assert recorded['declines'][0]['action_id'] == decisions['split_related']['action_id']
    assert recorded['declines'][0]['proposal']['state'] == str(path)
    assert recorded['declines'][0]['proposal']['tree'] == p.load(path)['snapshot']['binding']['tree']
    assert policy.binding(PR) == before and not state['writes']


def test_disable_does_not_erase_uncertain_writes_and_reconcile_still_observes_them(repo):
    cwd, _, state, _, _, base, chain = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'disable', reason='Stop further partitioning of this PR.')
    prepared = {'identity': {'pr_url': PR, 'head_sha': git(cwd, 'rev-parse', 'HEAD'), 'base_sha': base}}
    with pytest.raises(ValueError, match='publication is pending'):
        partition_gate.require({}, prepared, {'checkout': str(cwd), 'repository_identity': chain[0]})
    assert p.check(cwd, PR, None)['outcome'] == 'partition_pending'
    writes = list(state['writes'])
    with pytest.raises(Diagnostic, match='current readiness failed'):
        p.publish(path, reconcile=True)
    assert writes == state['writes']
    operations = [publication.load(value) for key, value in p.load(path)['operations'].items() if key.startswith('create-')]
    assert operations[0]['status'] == 'verified'


@pytest.mark.parametrize('defect', ['stale_policy', 'missing_reason', 'missing_evidence'])
def test_conditional_agent_decision_requires_current_policy_and_evidence(repo, defect):
    cwd, _, state, evidence, _, _, _ = repo
    policy.configure(PR, 'instructions', reason='Conditions.', instructions='Partition only stable pieces.')
    assessment = policy_assessment('retain')
    if defect == 'stale_policy':
        assessment['policy_fingerprint'] = 'stale'
    elif defect == 'missing_reason':
        assessment['reason'] = ''
    else:
        assessment['evidence'] = ['partition_policy']
    with pytest.raises(ValueError):
        p.assess_policy(cwd, PR, evidence, assessment)
    assert not state['writes']


def test_disabled_pr_can_use_stable_check_cli_without_evidence_or_github_reads(repo):
    import sys
    cwd, _, _, _, _, _, _ = repo
    policy.configure(PR, 'disable', reason='Keep the entire PR together.')
    script = Path(__file__).resolve().parents[3] / 'scripts/dk.py'
    result = subprocess.run([sys.executable, str(script), '--host', 'codex', '--full', '--cwd', str(cwd),
        '--pr', PR, 'partition-prs', 'check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['outcome'] == 'pr_disabled'
    assert data['partition_policy']['decision']['reason'] == 'Keep the entire PR together.'


def test_per_pr_conditions_do_not_enable_globally_disabled_behaviors(repo):
    cwd, _, state, evidence, plan, _, chain = repo
    pr_size.configure(chain, 'set', settings={'evacuate_unrelated': 'disabled', 'split_related': 'disabled'})
    policy.configure(PR, 'instructions', reason='Human conditions.', instructions='Partition independent pieces.')
    assert p.check(cwd, PR, evidence)['outcome'] == 'disabled'
    with pytest.raises(ValueError, match='both configured partition behaviors are disabled'):
        p.assess_policy(cwd, PR, evidence, policy_assessment('retain'))
    plan['policy_assessment'] = policy_assessment('partition')
    with pytest.raises(ValueError, match='disabled'):
        prepare(repo)
    assert not state['writes']


@pytest.mark.parametrize('cancel', [False, True])
def test_partial_publication_can_resume_after_recorded_disable_enable(repo, cancel):
    cwd, _, state, evidence, _, base, chain = repo
    path = prepare(repo)
    original_head = p.load(path)['snapshot']['head']
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'disable', reason='Stop partitioning this PR.')
    with pytest.raises(Diagnostic, match='current readiness failed'):
        p.publish(path, reconcile=True)
    if cancel:
        result = p.cancel_policy(path)
        assert result['outcome'] == 'cancelled' and result['original_head'] == original_head
        assert len(result['retained_drafts']) == 1
        assert not p.pending_states(cwd, PR)
        prepared = {'identity': {'pr_url': PR, 'head_sha': original_head, 'base_sha': base}}
        assert partition_gate.require({}, prepared, {'checkout': str(cwd), 'repository_identity': chain[0]})['outcome'] == 'pr_disabled'
    policy.configure(PR, 'enable', reason='Resume the verified candidate partition.')
    assert p.check(cwd, PR, evidence)['outcome'] == ('resume_required' if cancel else 'partition_pending')
    before = list(state['writes'])
    result = p.resume_policy(path, candidate_hash=p.load(path)['candidate_hash'])
    assert result['outcome'] == 'selection_ready' and state['writes'] == before
    assert publish(path)['outcome'] == 'published'
    assert len(state['drafts']) == 2
    assert sum(method == 'POST' for method, _ in state['writes']) == 2


def test_policy_resume_requires_new_manual_approval_for_exact_existing_candidates(repo):
    _, _, state, _, _, _, chain = repo
    pr_size.configure(chain, 'set', settings={'split_related': 'manual'})
    path = prepare(repo)
    approval = publish(path)
    old_action = approval['action_ids']['split_related']
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path, decisions={'split_related': {'action_id': old_action, 'decision': 'approved'}})
    policy.configure(PR, 'disable', reason='Stop for now.')
    with pytest.raises(Diagnostic):
        p.publish(path, reconcile=True)
    policy.configure(PR, 'enable', reason='Resume this partition.')
    candidate_hash = p.load(path)['candidate_hash']
    pending = p.resume_policy(path, candidate_hash=candidate_hash)
    assert pending['outcome'] == 'awaiting_approval'
    assert pending['action_ids']['split_related'] != old_action
    with pytest.raises(ValueError, match='current action'):
        p.resume_policy(path, candidate_hash=candidate_hash,
                        decisions={'split_related': {'action_id': old_action, 'decision': 'approved'}})
    result = p.resume_policy(path, candidate_hash=candidate_hash,
        decisions={'split_related': {'action_id': pending['action_ids']['split_related'], 'decision': 'approved',
                                     'source': 'Continue these exact candidates under the new preference.'}})
    assert result['outcome'] == 'selection_ready'
    assert publish(path)['outcome'] == 'published' and len(state['drafts']) == 2


def test_cancel_cannot_erase_uncertainty_or_cancel_after_source_removal(repo, monkeypatch):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'disable', reason='Stop.')
    with pytest.raises(ValueError, match='reconcile all recorded'):
        p.cancel_policy(path)
    with pytest.raises(Diagnostic):
        p.publish(path, reconcile=True)
    policy.configure(PR, 'enable', reason='Continue.')
    p.resume_policy(path, candidate_hash=p.load(path)['candidate_hash'])
    original_run = p.run
    def interrupt_source(cwd, *argv):
        result = original_run(cwd, *argv)
        if argv[:2] == ('git', 'push') and argv[-1].endswith(':refs/heads/topic'):
            raise RuntimeError('lost source rewrite response')
        return result
    monkeypatch.setattr(p, 'run', interrupt_source)
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'disable', reason='Stop after source changed.')
    with pytest.raises(Diagnostic):
        p.publish(path, reconcile=True)
    with pytest.raises(ValueError, match='cannot cancel after source removal'):
        p.cancel_policy(path)


def test_cancelled_partition_can_receive_a_fresh_conditional_retain_assessment(repo):
    cwd, _, state, evidence, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'disable', reason='Keep it whole.')
    with pytest.raises(Diagnostic):
        p.publish(path, reconcile=True)
    p.cancel_policy(path)
    policy.configure(PR, 'instructions', reason='Only split independent integration boundaries.',
                     instructions='Keep the PR whole while its integration boundary is unstable.')
    assert p.check(cwd, PR, evidence)['outcome'] == 'resume_required'
    result = p.assess_policy(cwd, PR, evidence, policy_assessment('retain'))
    assert p.check(cwd, PR, evidence)['state'] == result['state']
    assert p.check(cwd, PR, evidence)['outcome'] == 'no_op'


def test_resuming_with_changed_conditions_requires_a_current_agent_assessment(repo):
    _, _, state, _, _, _, _ = repo
    path = prepare(repo)
    state['interrupt'] = 'POST'
    with pytest.raises(Diagnostic):
        publish(path)
    policy.configure(PR, 'instructions', reason='A new condition.', instructions='Partition only independent tested pieces.')
    with pytest.raises(Diagnostic):
        p.publish(path, reconcile=True)
    candidate_hash = p.load(path)['candidate_hash']
    with pytest.raises(ValueError, match='agent assessment'):
        p.resume_policy(path, candidate_hash=candidate_hash)
    result = p.resume_policy(path, candidate_hash=candidate_hash, assessment=policy_assessment('partition'))
    assert result['outcome'] == 'selection_ready'
    assert publish(path)['outcome'] == 'published' and len(state['drafts']) == 2


def test_resuming_unpublished_manual_candidates_does_not_ask_for_approval_twice(repo):
    _, _, state, _, _, _, chain = repo
    pr_size.configure(chain, 'set', settings={'split_related': 'manual'})
    path = prepare(repo)
    policy.configure(PR, 'disable', reason='Wait for an integration decision.')
    policy.configure(PR, 'enable', reason='The integration decision is settled.')
    candidate_hash = p.load(path)['candidate_hash']
    pending = p.resume_policy(path, candidate_hash=candidate_hash)
    approval = {'split_related': {'action_id': pending['action_ids']['split_related'], 'decision': 'approved'}}
    assert p.resume_policy(path, candidate_hash=candidate_hash, decisions=approval)['outcome'] == 'selection_ready'
    assert p.load(path)['decisions']['split_related'] == 'approved'
    assert publish(path)['outcome'] == 'published' and len(state['drafts']) == 2
