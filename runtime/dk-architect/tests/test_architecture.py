"""Offline evidence, routing, policy and mandatory-gate regressions."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared import architecture as a

ROOT = Path(__file__).resolve().parents[3]
USER = {'actor': 'user', 'instruction': 'Use the inspected architecture policy for this task.',
        'source': 'user message 1'}


def run(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def model_result(path='src/core.py', rules=()):
    item = {'text': 'Inspected core boundary', 'rationale': 'Core is the shared entry point',
            'origin': 'inferred', 'sources': [{'path': path, 'start': 1, 'end': 1}]}
    return {'knowledge': {key: [deepcopy(item)] for key in a.KNOWLEDGE},
            'rules': list(rules), 'proposals': [], 'conflicts': []}


def obligation(name='core', **kwargs):
    return a.rule({'id': 'arch-' + name, 'revision': 1, 'text': 'Keep core independent.',
                   'origin': 'documented', 'sources': [{'path': 'src/core.py', 'start': 1, 'end': 1}],
                   'patterns': ['src/**'], 'agents': ['worker'], **kwargs})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    root = tmp_path / 'repo'
    root.mkdir()
    run(root, 'init', '-q')
    run(root, 'config', 'user.name', 'Fixture')
    run(root, 'config', 'user.email', 'fixture@example.test')
    (root / 'src').mkdir()
    (root / 'src/core.py').write_text('def core(): return 1\n')
    (root / 'old.py').write_text('deleted = True\n')
    run(root, 'add', '.')
    run(root, 'commit', '-qm', 'baseline')
    return root


def initialize(root, rules=()):
    return a.ensure(root, analyzer=lambda *_: model_result(rules=rules))


def deliver(root, agent='worker', paths=None, **kwargs):
    return a.packet(root, agent, paths, task='task-1', scope={'goal': 'core'}, **kwargs)


@pytest.mark.parametrize('pattern,path,expected', [
    ('**/*.py', 'core.py', True), ('**/*.py', 'src/deep/core.py', True),
    ('src/**/core.py', 'src/core.py', True), ('src/**/core.py', 'src/a/b/core.py', True),
    ('src/*.py', 'src/a/core.py', False), ('src/*.py', 'src/core.py', True),
    ('src/**', 'Src/core.py', False), ('src/[ab]?.py', 'src/a1.py', True),
    ('**/core.py', 'coreXpy', False), ('a/**/b/**/c', 'a/b/c', True),
])
def test_posix_globs(pattern, path, expected):
    assert a.matches(pattern, path) is expected


@pytest.mark.parametrize('path', ['/abs', '../bad', 'a/../b', 'a//b', './bad', 'a\\b'])
def test_bad_paths(path):
    with pytest.raises(ValueError, match='POSIX'):
        a.relative(path)


def test_routing_defaults_all_mandatory_unknown_and_empty_scopes(repo):
    rules = [obligation('default', agents=[]), obligation('all', agents=['all']),
             obligation('scoped'), obligation('unfiltered', patterns=[]),
             obligation('other', agents=['ci-repair'], patterns=['tests/**'])]
    initialize(repo, rules)
    ids = lambda packet: {r['id'] for r in a.load(packet)['rules']}
    assert ids(deliver(repo, paths=['src/core.py'])) == {'arch-all', 'arch-scoped', 'arch-unfiltered'}
    assert ids(deliver(repo, paths=['tests/test.py'])) == {'arch-unfiltered'}
    assert ids(deliver(repo, paths=[])) == {'arch-unfiltered'}
    assert ids(deliver(repo)) == {'arch-all', 'arch-scoped', 'arch-unfiltered'}
    for agent in ('coordinator', 'dk-architecture-review', 'dk-workflows:dk-architecture-review@codex',
                  'dk-workflows:dk-pr-iter@claude', 'dk-pr-review'):
        assert len(ids(deliver(repo, agent, []))) == 5
    assert ids(deliver(repo, 'ci-repair', ['tests/test.py'])) == {'arch-other'}


def test_worker_expansion_and_nested_identity_require_fresh_delivery(repo):
    initialize(repo, [obligation(), obligation('ci', agents=['ci-repair'], patterns=['tests/**'])])
    packet = deliver(repo, paths=['src/core.py'])
    a.validate_packet(packet, repo, agent='worker', paths=['src/core.py'])
    with pytest.raises(ValueError, match='scope expanded'):
        a.validate_packet(packet, repo, paths=['tests/test.py'])
    with pytest.raises(ValueError, match='recipient'):
        a.validate_packet(packet, repo, agent='ci-repair')
    refreshed = a.provider_packet(packet, repo, 'ci-repair')
    assert [r['id'] for r in a.load(refreshed)['rules']] == ['arch-ci']
    assert a.load(refreshed)['paths'] is None


def test_renames_and_deletions_include_both_paths(repo):
    base = run(repo, 'rev-parse', 'HEAD')
    run(repo, 'mv', 'src/core.py', 'src/renamed.py')
    run(repo, 'rm', 'old.py')
    run(repo, 'commit', '-qm', 'rename and delete')
    assert a.changed_paths(repo, base, 'HEAD') == ['old.py', 'src/core.py', 'src/renamed.py']


def test_clean_local_baseline_and_automatic_reuse(repo):
    catalog = initialize(repo, [obligation()])
    assert catalog['baseline'] == run(repo, 'rev-parse', 'HEAD')
    assert catalog['rules'][0]['sources'][0]['sha256']
    assert a.ensure(repo) == catalog  # No second model invocation.
    (repo / 'new.py').write_text('dirty')
    with pytest.raises(ValueError, match='clean pre-edit'):
        initialize(repo)
    # An explicitly pinned baseline remains valid while local coding proceeds.
    assert a.ensure(repo, catalog['baseline']) == catalog


def test_source_refresh_keeps_obligations_and_blocks_changed_evidence(repo):
    first = initialize(repo, [obligation()])
    (repo / 'src/core.py').write_text('def core(): return 2\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'changed governing evidence')
    updated = a.ensure(repo, analyzer=lambda *_: model_result(rules=[obligation(text='Allow core dependencies.')]))
    assert updated['rules'] == first['rules']
    assert updated['stale_rules'] == ['arch-core']
    assert updated['proposals']
    with pytest.raises(ValueError, match='user resolution'):
        deliver(repo)
    fixed = a.edit(repo, obligation(text='Explicitly approved replacement.'), user=USER, expected=updated['fingerprint'])
    assert fixed['rules'][0]['revision'] == 2
    assert not fixed['stale_rules']
    deliver(repo)


def test_uncertain_and_conflicting_conventions_are_not_adopted(repo):
    result = model_result(rules=[obligation()])
    result['proposals'] = [{'text': 'Possible layering convention', 'reason': 'Only one example'}]
    result['conflicts'] = [{'text': 'Guidance disagrees about core', 'sources': ['src/core.py']}]
    catalog = a.ensure(repo, analyzer=lambda *_: result)
    assert not catalog['rules'] and catalog['proposals'] and catalog['conflicts']
    catalog = a.edit(repo, obligation(origin='user-created', sources=[]), user=USER, expected=catalog['fingerprint'])
    assert catalog['conflicts']  # An unrelated edit cannot clear an unresolved conflict.
    catalog = a.edit(repo, obligation(origin='user-created', sources=[]), user=USER,
                     expected=catalog['fingerprint'], resolve_conflicts=True)
    deliver(repo)


def test_unchanged_obligation_refreshes_governing_evidence_without_policy_changes(repo):
    initialize(repo, [obligation()])
    old = deliver(repo, paths=['src/core.py'])
    (repo / 'src/core.py').write_text('def core(): return 2\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'same boundary with updated code evidence')
    refreshed = a.ensure(repo, analyzer=lambda *_: model_result(rules=[obligation()]))
    assert refreshed['rules'][0]['revision'] == 2
    assert refreshed['rules'][0]['text'] == obligation()['text']
    assert refreshed['rules'][0]['auto_approve'] is False
    assert not refreshed['stale_rules']
    with pytest.raises(ValueError, match='refresh rule packet'):
        a.validate_packet(old, repo)


def test_concurrent_refresh_cannot_overwrite_new_catalog(repo):
    initialize(repo)
    barrier = Barrier(2)
    def refresh():
        def analyze(*_):
            barrier.wait(timeout=10)
            return model_result()
        try:
            return a.ensure(repo, force=True, analyzer=analyze)
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any('concurrently' in str(result) for result in results)
    assert a.current(repo)['generation'] == 2


def test_user_owned_policy_and_stale_mutations(repo):
    old = initialize(repo)
    auto = obligation(auto_approve=True, approval_when='The deviation affects tests only.')
    for actor in ('worker', 'dk-architect', 'dk-architecture-enforcer'):
        with pytest.raises(ValueError, match='Explicit user'):
            a.edit(repo, auto, user={**USER, 'actor': actor}, expected=old['fingerprint'])
    with pytest.raises(ValueError, match='textual'):
        a.rule(obligation(auto_approve=True, approval_when=''))
    updated = a.edit(repo, auto, user=USER, expected=old['fingerprint'])
    with pytest.raises(ValueError, match='catalog changed'):
        a.edit(repo, auto, user=USER, expected=old['fingerprint'])
    packet = deliver(repo, paths=['src/core.py'])
    updated = a.edit(repo, auto, user=USER, expected=updated['fingerprint'])
    with pytest.raises(ValueError, match='refresh rule packet'):
        a.validate_packet(packet, repo)
    assert len(list((a.folder(repo)[0] / 'catalogs').glob('*.json'))) == 3


def request(root, packet):
    return a.exception_request(packet, root, 'arch-core', 'Allow one dependency.', ['src/core.py'],
                               [{'path': 'src/core.py', 'start': 1, 'end': 1}], 'One task needs an adapter.')


@pytest.mark.parametrize('automatic,established,approved', [(False, True, False), (True, False, False), (True, True, True)])
def test_independent_exception_policy(repo, automatic, established, approved):
    initialize(repo)
    catalog = a.current(repo)
    a.edit(repo, obligation(auto_approve=automatic, approval_when='Core has one entry point.' if automatic else ''),
           user=USER, expected=catalog['fingerprint'])
    packet = deliver(repo, paths=['src/core.py'])
    calls = []
    def evaluate(*_):
        calls.append(True)
        return {'established': established, 'rationale': 'Inspected the core entry point.',
                'evidence': [{'path': 'src/core.py', 'start': 1, 'end': 1}]}
    reference = a.enforce(request(repo, packet), repo, evaluator=evaluate)
    value = a.load(reference)
    assert value['approved'] is approved and bool(calls) is automatic
    if approved:
        a.validate_exception(reference, a.load(packet), repo)
    else:
        with pytest.raises(ValueError, match='does not cover'):
            a.validate_exception(reference, a.load(packet), repo)


def test_exception_invalidation_rule_code_scope_and_task(repo):
    catalog = initialize(repo, [obligation()])
    packet = deliver(repo, paths=['src/core.py'])
    approved = a.enforce(request(repo, packet), repo, user=USER)
    delivered = a.load(packet)
    for field in ('scope', 'task', 'tree', 'catalog'):
        with pytest.raises(ValueError, match='does not cover'):
            a.validate_exception(approved, {**delivered, field: 'changed'}, repo)
    (repo / 'src/core.py').write_text('def core(): return 2\n')
    with pytest.raises(ValueError, match='code changed'):
        a.enforce(request(repo, packet), repo, user=USER)
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'edit')
    fresh = a.load(deliver(repo, paths=['src/core.py']))
    with pytest.raises(ValueError, match='does not cover'):
        a.validate_exception(approved, fresh, repo)
    a.edit(repo, obligation(), user=USER, expected=catalog['fingerprint'])
    with pytest.raises(ValueError, match='refresh rule packet'):
        a.validate_exception(approved, delivered, repo)


def context(repo, tmp_path):
    head = run(repo, 'rev-parse', 'HEAD')
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({'body': 'Core task', 'title': 'Core change'}))
    decisions = {'head_sha': head, 'decisions': []}
    return {'checkout': str(repo), 'repository': str(repo), 'url': 'https://github.com/o/r/pull/1',
            'base_sha': head, 'head_sha': head, 'metadata': str(metadata)}, decisions


def test_pr_initialization_reads_target_not_changed_head(repo, tmp_path):
    ctx, decisions = context(repo, tmp_path)
    (repo / 'src/core.py').write_text('PR tries to replace governing source\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'head')
    ctx['head_sha'] = run(repo, 'rev-parse', 'HEAD')
    inspected = []
    def analyze(prompt, checkout, validate):
        inspected.append((checkout / 'src/core.py').read_text())
        return model_result(rules=[obligation()])
    prepared = a.prepare(ctx, decisions, analyzer=analyze)
    assert inspected == ['def core(): return 1\n']
    assert a.load(prepared['packet'])['baseline'] == ctx['base_sha']
    assert a.load(prepared['packet'])['paths'] == ['src/core.py']


def verdict(rule, status='compliant', **kwargs):
    return {'rule_id': rule['id'], 'revision': rule['revision'], 'status': status,
            'rationale': 'Inspected core dependencies.',
            'evidence': [{'path': 'src/core.py', 'start': 1, 'end': 1}], **kwargs}


def test_worker_exception_review_completion_flow(repo, tmp_path):
    ctx, decisions = context(repo, tmp_path)
    initialize(repo, [obligation()])
    prepared = a.prepare(ctx, decisions)
    worker = a.handoff(prepared, ctx, 'worker', ['src/core.py'])
    approved = a.enforce(request(repo, worker), repo, user=USER)
    prepared = a.prepare(ctx, decisions, exceptions=[approved])
    result = {'complete': True, 'reason': '', 'coverage': [verdict(obligation(), 'approved_exception',
              paths=['src/core.py'], exception=approved['fingerprint'])]}
    reviewed = a.review(prepared, ctx, decisions, reviewer=lambda *_: result)
    assert a.verify_review(reviewed, prepared, ctx, decisions)['exceptions'] == [approved]
    # Even unchanged code needs a fresh decision about materially changed scope.
    decisions['decisions'].append({'text': 'Broaden public API'})
    with pytest.raises(ValueError, match='scope or Decisions changed'):
        a.verify_review(reviewed, prepared, ctx, decisions)
    with pytest.raises(ValueError, match='does not cover'):
        a.prepare(ctx, decisions, exceptions=[approved])


@pytest.mark.parametrize('coverage', [[], [verdict(obligation()), verdict(obligation())],
    [verdict(obligation(), 'approved_exception', paths=['src/core.py'], exception='unapproved')]])
def test_review_requires_complete_coverage_and_approved_exceptions(repo, tmp_path, coverage):
    initialize(repo, [obligation()])
    ctx, decisions = context(repo, tmp_path)
    prepared = a.prepare(ctx, decisions)
    with pytest.raises(ValueError):
        a.review(prepared, ctx, decisions,
                 reviewer=lambda *_: {'complete': True, 'reason': '', 'coverage': coverage})


def test_incomplete_and_violation_never_pass_completion(repo, tmp_path):
    initialize(repo, [obligation()])
    ctx, decisions = context(repo, tmp_path)
    prepared = a.prepare(ctx, decisions)
    for result in ({'complete': False, 'reason': 'Provider unavailable', 'coverage': []},
                   {'complete': True, 'reason': '', 'coverage': [verdict(obligation(), 'violation')]}):
        reviewed = a.review(prepared, ctx, decisions, reviewer=lambda *_: result)
        with pytest.raises(ValueError, match='incomplete|violations'):
            a.verify_review(reviewed, prepared, ctx, decisions)


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_host_entrypoints_and_config_route(repo, host):
    initialize(repo, [obligation()])
    for name in ('dk-architect', 'dk-architecture-review', 'dk-architecture-enforcer'):
        text = (ROOT / host / 'skills' / name / 'SKILL.md').read_text()
        assert '--host ' + host in text
        assert 'runtime/' + name + '/procedure.md' in text
    for command in (['architecture', 'inspect'], ['config', 'architecture', 'inspect']):
        output = subprocess.check_output([sys.executable, str(ROOT / 'scripts/dk.py'),
            '--host', host, '--cwd', str(repo), *command], text=True)
        assert json.loads(output)['rules'][0]['id'] == 'arch-core'


def test_catalogs_and_exports_stay_private(repo, monkeypatch):
    catalog = initialize(repo)
    packet = deliver(repo)
    assert not Path(packet['path']).is_relative_to(repo)
    assert Path(packet['path']).stat().st_mode & 0o777 == 0o600
    assert Path(packet['path']).parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.setenv('XDG_DATA_HOME', str(repo / 'bad-private-data'))
    with pytest.raises(ValueError, match='outside'):
        a.current(repo)


def test_architect_cannot_enable_autoapproval(repo):
    with pytest.raises(ValueError, match='cannot'):
        a.ensure(repo, analyzer=lambda *_: model_result(rules=[obligation(auto_approve=True, approval_when='always')]))


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_native_execution_preserves_role_packet_and_redelivers_nested_scope(repo, monkeypatch, host):
    from shared import executors
    initialize(repo, [obligation(), obligation('ci', agents=['ci-repair'], patterns=['tests/**'])])
    worker = deliver(repo, paths=['src/core.py'])
    captured = []
    monkeypatch.setattr(executors, 'current_host', lambda: host)
    monkeypatch.setattr(executors, 'managed_update', lambda *_: None)
    def process(prompt, checkout, selected_host, environment, *args):
        captured.append((prompt, selected_host, json.loads(environment['DK_ARCHITECTURE_PACKET'])))
        return '{}'
    monkeypatch.setattr(executors, 'execute_process', process)
    executors.execute('Inspect code.', repo, architecture_packet=worker)
    delivered = a.load(captured[-1][2])
    assert captured[-1][1] == host
    assert delivered['agent'] == 'worker' and delivered['paths'] == ['src/core.py']
    assert [r['id'] for r in delivered['rules']] == ['arch-core']
    monkeypatch.setenv('DK_ARCHITECTURE_PACKET', json.dumps(worker))
    monkeypatch.setenv('DK_ARCHITECTURE_AGENT', 'ci-repair')
    executors.execute('Nested CI repair.', repo)
    child = a.load(captured[-1][2])
    assert child['agent'] == 'ci-repair' and child['paths'] is None
    assert [r['id'] for r in child['rules']] == ['arch-ci']
    with pytest.raises(ValueError, match='scope became known'):
        a.validate_packet(captured[-1][2], repo, paths=['tests/test.py'])


def test_unknown_scope_requires_declared_paths_before_coding(repo):
    initialize(repo, [obligation()])
    unknown = deliver(repo)
    with pytest.raises(ValueError, match='scope became known'):
        a.validate_packet(unknown, repo, paths=['src/core.py'])
    declared = deliver(repo, paths=['src/core.py'])
    a.validate_packet(declared, repo, paths=['src/core.py'])


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_resolved_native_invoke_delivers_binding_recipient_scope(repo, monkeypatch, host):
    from shared import executors, runtime_bundles
    recipient = 'fixture:review'
    initialize(repo, [obligation(), obligation('review', agents=[recipient])])
    parent = deliver(repo, paths=['src/core.py'])
    original = a.load(parent)
    handoff = repo.parent / 'handoff.json'
    handoff.write_text(json.dumps({'architecture_packet': parent}))
    binding = {'skill': recipient, 'host': host}
    resolved = {'binding': binding, 'executor': host, 'identity': recipient,
                'root': str(repo), 'procedure': str(repo / 'procedure.md')}
    monkeypatch.setattr(executors, 'resolve', lambda _: resolved)
    monkeypatch.setattr(runtime_bundles, 'retained_binding', lambda value: value)
    monkeypatch.setattr(executors, 'managed_update', lambda *_: None)
    monkeypatch.setenv('DK_BINDING_STACK', '[]')
    captured = []
    def process(prompt, checkout, selected_host, environment, *args):
        captured.append((selected_host, json.loads(environment['DK_ARCHITECTURE_PACKET'])))
        return '{}'
    monkeypatch.setattr(executors, 'execute_process', process)
    executors.invoke(binding, handoff, repo, 'Inspect the handoff.')
    selected_host, reference = captured.pop()
    delivered = a.load(reference)
    assert selected_host == host
    assert delivered['agent'] == recipient
    assert [rule['id'] for rule in delivered['rules']] == ['arch-review']
    for key in ('paths', 'scope', 'task', 'head', 'baseline', 'catalog'):
        assert delivered[key] == original[key]
    a.validate_packet(reference, repo, paths=['src/core.py'])
