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
    kwargs.setdefault('baseline', run(root, 'rev-parse', 'HEAD'))
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


def test_same_baseline_refresh_keeps_obligations_and_user_policy(repo):
    first = initialize(repo, [obligation()])
    auto = obligation(auto_approve=True, approval_when='The boundary is unchanged.')
    first = a.edit(repo, auto, user=USER, expected=first['fingerprint'])
    old = deliver(repo)
    updated = a.ensure(repo, force=True,
        analyzer=lambda *_: model_result(rules=[obligation(text='Allow core dependencies.')]))
    assert updated['rules'] == first['rules']
    assert updated['proposals'] and not updated['stale_rules']
    with pytest.raises(ValueError, match='refresh rule packet'):
        a.validate_packet(old, repo)
    fixed = a.edit(repo, obligation(text='Explicitly approved replacement.'), user=USER, expected=updated['fingerprint'])
    assert fixed['rules'][0]['revision'] == 3
    deliver(repo)


def test_uncertain_and_conflicting_conventions_are_not_adopted(repo):
    result = model_result(rules=[obligation()])
    result['proposals'] = [{'text': 'Possible layering convention', 'reason': 'Only one example'}]
    result['conflicts'] = [{'text': 'Guidance disagrees about core', 'sources': ['src/core.py']}]
    catalog = a.ensure(repo, analyzer=lambda *_: result)
    assert not catalog['rules'] and catalog['proposals'] and catalog['conflicts']
    held = catalog['proposals'][-1]
    assert held['rule']['id'] == 'arch-core' and 'resolution' in held['reason']
    assert held['rule']['sources'][0]['sha256']
    pending = deepcopy(catalog['proposals'])
    for refreshed in (model_result(rules=[obligation()]), result):
        catalog = a.ensure(repo, catalog['baseline'], force=True, analyzer=lambda *_: refreshed)
        assert not catalog['rules']
        assert catalog['conflicts'] == result['conflicts']
        assert catalog['proposals'] == pending
        with pytest.raises(ValueError, match='user resolution'):
            deliver(repo)
    catalog = a.edit(repo, obligation(origin='user-created', sources=[]), user=USER, expected=catalog['fingerprint'])
    assert catalog['conflicts']  # An unrelated edit cannot clear an unresolved conflict.
    catalog = a.edit(repo, obligation(origin='user-created', sources=[]), user=USER,
                     expected=catalog['fingerprint'], resolve_conflicts=True)
    assert a.ensure(repo) == catalog
    assert held in catalog['proposals']
    deliver(repo)


def test_new_baseline_derives_its_own_obligations_without_invalidating_old(repo):
    original = initialize(repo, [obligation()])
    old = deliver(repo, paths=['src/core.py'])
    (repo / 'src/core.py').write_text('def core(): return 2\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'different baseline evidence')
    refreshed = initialize(repo, [obligation(text='A distinct boundary.')])
    assert refreshed['parent'] is None and refreshed['generation'] == 1
    assert refreshed['rules'][0]['revision'] == 1
    assert refreshed['rules'][0]['text'] == 'A distinct boundary.'
    assert not refreshed['stale_rules']
    assert a.current(repo, baseline=original['baseline']) == original
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
    assert a.current(repo, baseline='HEAD')['generation'] == 2


@pytest.mark.parametrize('order', [('a', 'b'), ('b', 'a')])
def test_baseline_catalogs_isolate_rules_policy_and_all_live_evidence(repo, tmp_path, order):
    base = run(repo, 'rev-parse', 'HEAD')
    (repo / 'src/core.py').write_text('def core(): return 2\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'second baseline')
    head = run(repo, 'rev-parse', 'HEAD')
    contexts, catalogs, evidence = {}, {}, {}
    for name, baseline in [('a', base), ('b', head)]:
        ctx, decisions = context(repo, tmp_path)
        ctx['base_sha'] = baseline
        ctx['url'] = 'https://github.com/o/r/pull/' + name
        contexts[name] = (ctx, decisions)

    def capture(name):
        ctx, decisions = contexts[name]
        prepared = a.prepare(ctx, decisions)
        worker = a.handoff(prepared, ctx, 'worker', ['src/core.py'])
        approval = a.enforce(request(repo, worker), repo, user=USER)
        prepared = a.prepare(ctx, decisions, exceptions=[approval])
        covered = [verdict(r) for r in a.load(prepared['packet'])['rules']]
        reviewed = a.review(prepared, ctx, decisions, reviewer=lambda *_: {
            'complete': True, 'reason': '', 'coverage': covered})
        return prepared, worker, approval, reviewed

    def verify(name):
        ctx, decisions = contexts[name]
        prepared, worker, approval, reviewed = evidence[name]
        a.validate_packet(worker, repo)
        a.validate_preparation(prepared, ctx, decisions)
        a.validate_exception(approval, a.load(worker), repo)
        a.verify_review(reviewed, prepared, ctx, decisions)
        child = a.handoff(prepared, ctx, 'worker', ['src/core.py'])
        assert a.load(child)['catalog'] == prepared['catalog']

    for name in order:
        ctx, _ = contexts[name]
        fresh = a.ensure(repo, ctx['base_sha'], analyzer=lambda *_: model_result(
            rules=[obligation(text='Boundary ' + name)]))
        assert fresh['parent'] is None and fresh['generation'] == 1
        assert len(fresh['rules']) == 1 and fresh['rules'][0]['auto_approve'] is False
        automatic = name == 'a'
        configured = a.edit(repo, obligation(text='Boundary ' + name, auto_approve=automatic,
            approval_when='Condition A.' if automatic else ''), user=USER, expected=fresh['fingerprint'])
        catalogs[name] = a.edit(repo, obligation(name, text='User rule ' + name,
            origin='user-created', sources=[]), user=USER, expected=configured['fingerprint'])
        evidence[name] = capture(name)
        for previous in evidence:
            verify(previous)
        assert a.ensure(repo, ctx['base_sha']) == catalogs[name]  # Reuse makes no model call.
    assert {r['id'] for r in catalogs['a']['rules']} == {'arch-core', 'arch-a'}
    assert {r['id'] for r in catalogs['b']['rules']} == {'arch-core', 'arch-b'}
    assert catalogs['a']['rules'][0]['approval_when'] == 'Condition A.'
    assert catalogs['b']['rules'][0]['approval_when'] == ''

    for name, sibling in [('a', 'b'), ('b', 'a')]:
        ctx, decisions = contexts[name]
        old_prepared, old_worker, old_approval, old_review = evidence[name]
        catalogs[name] = a.edit(repo, obligation(text='Edited boundary ' + name),
            user=USER, expected=catalogs[name]['fingerprint'])
        verify(sibling)
        for operation in (lambda: a.validate_packet(old_worker, repo),
                          lambda: a.validate_preparation(old_prepared, ctx, decisions),
                          lambda: a.validate_exception(old_approval, a.load(old_worker), repo),
                          lambda: a.verify_review(old_review, old_prepared, ctx, decisions)):
            with pytest.raises(ValueError, match='refresh rule packet'):
                operation()
        evidence[name] = capture(name)
        verify(name)


def test_concurrent_different_baselines_initialize_independently(repo):
    base = run(repo, 'rev-parse', 'HEAD')
    (repo / 'old.py').write_text('second = True\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'second baseline')
    head = run(repo, 'rev-parse', 'HEAD')
    barrier = Barrier(2)
    def initialize_at(baseline):
        def analyze(prompt, *_):
            assert 'Existing catalog: null' in prompt
            barrier.wait(timeout=10)
            return model_result(rules=[obligation(text=baseline)])
        return a.ensure(repo, baseline, analyzer=analyze)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize_at, [base, head]))
    for result in results:
        assert a.current(repo, baseline=result['baseline']) == result
        assert result['generation'] == 1 and result['parent'] is None
        assert a.ensure(repo, result['baseline']) == result


def test_legacy_shared_live_pointer_requires_fresh_initialization_and_preserves_history(repo):
    from shared.private_state import write
    old = initialize(repo)
    old = a.edit(repo, obligation('legacy', origin='user-created', sources=[], auto_approve=True,
        approval_when='Legacy condition.'), user=USER, expected=old['fingerprint'])
    packet = deliver(repo)
    directory, _ = a.folder(repo)
    reference = {'path': str(directory / 'catalogs' / (old['fingerprint'] + '.json')),
                 'fingerprint': old['fingerprint']}
    write(directory / 'current.json', reference)
    a.baseline_pointer(directory, old['baseline']).unlink()
    historical = Path(reference['path']).read_bytes()
    for select in ({'baseline': old['baseline']}, {'catalog': old['fingerprint']}):
        with pytest.raises(ValueError, match='initialization required'):
            a.current(repo, **select)
    with pytest.raises(ValueError, match='initialization required'):
        a.validate_packet(packet, repo)
    prompts = []
    def analyze(prompt, *_):
        prompts.append(prompt)
        return model_result(rules=[obligation()])
    fresh = a.ensure(repo, old['baseline'], analyzer=analyze)
    assert len(prompts) == 1 and 'Legacy condition.' not in prompts[0]
    assert fresh['parent'] is None and fresh['generation'] == 1
    assert [r['id'] for r in fresh['rules']] == ['arch-core']
    assert a.load(reference) == old and Path(reference['path']).read_bytes() == historical
    assert a.load(packet)['catalog'] == old['fingerprint']
    with pytest.raises(ValueError, match='refresh rule packet'):
        a.validate_packet(packet, repo)


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
    catalog = a.current(repo, baseline='HEAD')
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
    fresh = a.load(deliver(repo, paths=['src/core.py'], baseline=catalog['baseline']))
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
    head_catalog = initialize(repo, [obligation('head', text='Head-only obligation.')])
    head_catalog = a.edit(repo, obligation('private', origin='user-created', sources=[], auto_approve=True,
        approval_when='Head-only condition.'), user=USER, expected=head_catalog['fingerprint'])
    inspected = []
    def analyze(prompt, checkout, validate):
        assert 'Head-only' not in prompt
        inspected.append((checkout / 'src/core.py').read_text())
        return model_result(rules=[obligation()])
    prepared = a.prepare(ctx, decisions, analyzer=analyze)
    assert inspected == ['def core(): return 1\n']
    assert a.load(prepared['packet'])['baseline'] == ctx['base_sha']
    assert a.load(prepared['packet'])['paths'] == ['src/core.py']
    assert [r['id'] for r in a.load(prepared['packet'])['rules']] == ['arch-core']
    assert a.current(repo, catalog=head_catalog['fingerprint']) == head_catalog


def verdict(rule, status='compliant', **kwargs):
    return {'rule_id': rule['id'], 'revision': rule['revision'], 'status': status,
            'rationale': 'Inspected core dependencies.',
            'evidence': [{'path': 'src/core.py', 'start': 1, 'end': 1}], **kwargs}


@pytest.mark.parametrize('cli', [None, 'retained', 'explicit'])
def test_worker_exception_review_completion_flow(repo, tmp_path, monkeypatch, capsys, cli):
    ctx, decisions = context(repo, tmp_path)
    initialize(repo, [obligation()])
    prepared = a.prepare(ctx, decisions)
    worker = a.handoff(prepared, ctx, 'worker', ['src/core.py'])
    approved = a.enforce(request(repo, worker), repo, user=USER)
    prepared = a.prepare(ctx, decisions, exceptions=[approved])
    result = {'complete': True, 'reason': '', 'coverage': [verdict(obligation(), 'approved_exception',
              paths=['src/core.py'], exception=approved['fingerprint'])]}
    if cli:
        spec = importlib.util.spec_from_file_location('architecture_cli', ROOT / 'runtime/dk-architect/scripts/architecture.py')
        command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(command)
        monkeypatch.setattr(a, 'analyze', lambda *_: result)
        arguments = ['review']
        for name, value in [('preparation', prepared), ('context', ctx), ('decisions', decisions)]:
            path = tmp_path / (name + '.json')
            path.write_text(json.dumps(value))
            arguments.extend(['--' + name, str(path)])
        if cli == 'explicit':
            path = tmp_path / 'exceptions.json'
            path.write_text(json.dumps([approved]))
            arguments.extend(['--exceptions-file', str(path)])
        assert command.dispatch(arguments, cwd=str(repo)) == 0
        reviewed = json.loads(capsys.readouterr().out)
    else:
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
    catalog = initialize(repo, [obligation()])
    for name in ('dk-architect', 'dk-architecture-review', 'dk-architecture-enforcer'):
        text = (ROOT / host / 'skills' / name / 'SKILL.md').read_text()
        assert '--host ' + host in text
        assert 'runtime/' + name + '/procedure.md' in text
    (repo / 'old.py').write_text('second baseline = True\n')
    run(repo, 'add', '.')
    run(repo, 'commit', '-qm', 'other baseline')
    other = initialize(repo, [obligation('other')])
    user_file, rule_file = repo.parent / 'user.json', repo.parent / 'rule.json'
    user_file.write_text(json.dumps(USER))
    rule_file.write_text(json.dumps(obligation('cli', origin='user-created', sources=[])))
    def invoke(route, arguments, success=True):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/dk.py'),
            '--host', host, '--cwd', str(repo), *route, *arguments], text=True, capture_output=True)
        if success:
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        assert result.returncode != 0
        return result.stderr
    for route in (['architecture'], ['config', 'architecture']):
        assert invoke(route, ['inspect', '--baseline', catalog['baseline']]) == catalog
        assert invoke(route, ['list', '--catalog', other['fingerprint']]) == other
        exported = invoke(route, ['export', '--catalog', catalog['fingerprint'],
            '--agent', 'worker', '--task', 'external task'])
        assert a.load(exported)['baseline'] == catalog['baseline']
        a.validate_packet(exported, repo)
        assert 'selection required' in invoke(route, ['inspect'], success=False)
        assert 'selection required' in invoke(route,
            ['packet', '--agent', 'worker', '--task', 'external task'], success=False)
        assert 'Invalid architecture catalog' in invoke(route,
            ['inspect', '--catalog', '../invalid'], success=False)
        assert 'Unknown architecture catalog selector' in invoke(route,
            ['inspect', '--catalog', '0' * 64], success=False)
        assert 'Invalid architecture baseline selector' in invoke(route,
            ['inspect', '--baseline', 'does-not-exist'], success=False)
        assert 'different baseline' in invoke(route,
            ['inspect', '--baseline', catalog['baseline'], '--catalog', other['fingerprint']], success=False)
        assert 'different baseline' in invoke(route, ['edit', '--baseline', other['baseline'],
            '--expected', catalog['fingerprint'], '--file', str(rule_file),
            '--user-file', str(user_file)], success=False)
        updated = invoke(route, ['edit', '--expected', catalog['fingerprint'],
            '--file', str(rule_file), '--user-file', str(user_file)])
        assert updated['baseline'] == catalog['baseline']
        assert 'catalog changed' in invoke(route, ['inspect', '--catalog', catalog['fingerprint']], success=False)
        catalog = invoke(route, ['delete', 'arch-cli', '--expected', updated['fingerprint'],
            '--user-file', str(user_file)])
        assert a.current(repo, catalog=other['fingerprint']) == other


def test_catalogs_and_exports_stay_private(repo, monkeypatch):
    catalog = initialize(repo)
    packet = deliver(repo)
    assert not Path(packet['path']).is_relative_to(repo)
    assert Path(packet['path']).stat().st_mode & 0o777 == 0o600
    assert Path(packet['path']).parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.setenv('XDG_DATA_HOME', str(repo / 'bad-private-data'))
    with pytest.raises(ValueError, match='outside'):
        a.current(repo, baseline='HEAD')


def test_architect_cannot_enable_autoapproval(repo):
    with pytest.raises(ValueError, match='cannot'):
        a.ensure(repo, analyzer=lambda *_: model_result(rules=[obligation(auto_approve=True, approval_when='always')]))


@pytest.mark.parametrize('role', ['knowledge', 'rule', 'review', 'enforcer'])
@pytest.mark.parametrize('malformed', ['string', 'missing_path', 'container'])
def test_malformed_model_evidence_gets_one_counted_correction(repo, tmp_path, monkeypatch, role, malformed):
    from shared import executors
    calls = []
    def analyze(prompt, checkout, validate):
        return a.analyze(prompt, checkout, validate, on_call=lambda: calls.append(True))
    if role in ('knowledge', 'rule'):
        valid = model_result(rules=[obligation()])
        invalid = deepcopy(valid)
        refs = (invalid['knowledge']['module_map'][0] if role == 'knowledge' else invalid['rules'][0])
        field = 'sources'
        invoke = lambda: a.ensure(repo, analyzer=analyze)
    elif role == 'review':
        initialize(repo, [obligation()])
        ctx, decisions = context(repo, tmp_path)
        prepared = a.prepare(ctx, decisions)
        valid = {'complete': True, 'reason': '', 'coverage': [verdict(obligation())]}
        invalid = deepcopy(valid)
        refs, field = invalid['coverage'][0], 'evidence'
        invoke = lambda: a.review(prepared, ctx, decisions, reviewer=analyze)
    else:
        catalog = initialize(repo)
        a.edit(repo, obligation(auto_approve=True, approval_when='Core has one entry point.'),
               user=USER, expected=catalog['fingerprint'])
        pending = request(repo, deliver(repo, paths=['src/core.py']))
        valid = {'established': True, 'rationale': 'Inspected core.',
                 'evidence': [{'path': 'src/core.py'}]}
        invalid = deepcopy(valid)
        refs, field = invalid, 'evidence'
        invoke = lambda: a.enforce(pending, repo, evaluator=analyze)
    refs[field] = {'string': ['src/core.py'], 'missing_path': [{}], 'container': 1}[malformed]
    responses = iter([invalid, valid])
    prompts = []
    def execute(prompt, *_args, **kwargs):
        prompts.append((prompt, kwargs['attempt']))
        return json.dumps(next(responses))
    monkeypatch.setattr(executors, 'execute', execute)
    result = invoke()
    assert result and len(calls) == 2
    assert [attempt for _, attempt in prompts] == [0, 1]
    assert 'Correction:' in prompts[-1][0]
    if role == 'review':
        a.verify_review(result, prepared, ctx, decisions)
    elif role == 'enforcer':
        assert a.load(result)['approved']


@pytest.mark.parametrize('malformed_rule', [None, [], {'id': []}, {'id': 1}])
def test_malformed_rule_shapes_exhaust_only_one_correction(repo, monkeypatch, malformed_rule):
    from shared import executors
    value = model_result(rules=[malformed_rule])
    calls = []
    monkeypatch.setattr(executors, 'execute', lambda *_args, **_kwargs: json.dumps(value))
    with pytest.raises(ValueError, match='after one correction'):
        a.analyze('Analyze baseline.', repo, lambda v: a.validate_analysis(v, repo, 'HEAD'),
                  on_call=lambda: calls.append(True))
    assert len(calls) == 2
    # Injected analyzers still receive validation without an implicit provider call.
    with pytest.raises(ValueError):
        a.ensure(repo, analyzer=lambda *_: value)
    assert len(calls) == 2


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
    monkeypatch.delenv('DK_ARCHITECTURE_AGENT', raising=False)
    with pytest.raises(ValueError, match='explicit recipient identity'):
        executors.execute('Unknown nested role.', repo)
    assert len(captured) == 1
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
@pytest.mark.parametrize('handoff_recipient', ['parent', 'bare', 'qualified', 'current', 'current_concrete'])
def test_resolved_native_invoke_delivers_binding_recipient_scope(repo, monkeypatch, host, handoff_recipient):
    from shared import executors, runtime_bundles
    recipient = 'fixture:review'
    binding_host = 'current' if handoff_recipient.startswith('current') else host
    qualified = recipient + '@' + ('current' if handoff_recipient == 'current' else host)
    initialize(repo, [obligation(), obligation('review', agents=[recipient]),
                      obligation('host', agents=[qualified])])
    audience = 'worker' if handoff_recipient == 'parent' else recipient if handoff_recipient == 'bare' else qualified
    parent = deliver(repo, audience, paths=['src/core.py'])
    original = a.load(parent)
    handoff = repo.parent / 'handoff.json'
    handoff.write_text(json.dumps({'architecture_packet': parent}))
    binding = {'skill': recipient, 'host': binding_host}
    resolved = {'binding': binding, 'executor': host, 'identity': recipient + '@' + binding_host,
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
    assert delivered['agent'] == (recipient if handoff_recipient == 'bare' else qualified)
    assert [rule['id'] for rule in delivered['rules']] == (
        ['arch-review'] if handoff_recipient == 'bare' else ['arch-review', 'arch-host'])
    if handoff_recipient != 'parent':
        assert reference == parent
    for key in ('paths', 'scope', 'task', 'head', 'baseline', 'catalog'):
        assert delivered[key] == original[key]
    a.validate_packet(reference, repo, paths=['src/core.py'])
