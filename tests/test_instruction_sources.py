"""Source adoption, retained handoff freshness and request-bound offline recovery."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import config_adapters
import config
import config_requests
import instruction_sources as sources
from shared.private_state import write
from state import Store, capture, make_spec
from tasks import Tasks
from test_state import case, REPO, PR
from test_batch_state import definition

ROOT = Path(__file__).resolve().parents[1]


def policy(mode='human', include=(), exclude=()):
    return {'mode': mode, 'include': list(include), 'exclude': list(exclude)}


def save(value):
    write(sources.policy_path(REPO), sources.envelope(REPO, value))


def files(checkout):
    for name, text in {'AGENTS.md': 'Post bot status and model attribution.',
                       '.github/copilot-instructions.md': 'Automation only.',
                       '.github/instructions/python.instructions.md': 'Nested AI rules.',
                       'CONTRIBUTING.md': 'Use meaningful tests.',
                       'docs/development/testing.md': 'Run offline tests.',
                       'docs/team.md': 'Custom contributor source.'}.items():
        path = checkout/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def build(spec, **overrides):
    return make_spec(repository=REPO, pr=PR, **{**definition(spec), **overrides})


def test_initialization_discovers_rules_without_adopting_or_writing(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    output = sources.inspect(checkout, REPO)
    assert output['status'] == 'choice-needed' and not output['initialized']
    assert not any(item['selected'] for item in output['sources'])
    assert {item['path'] for item in output['sources']} == {
        'AGENTS.md', '.github/copilot-instructions.md', '.github/instructions/python.instructions.md',
        'CONTRIBUTING.md', 'docs/development/testing.md'}
    assert 'cannot unload host context' in output['host_limitations']
    assert not sources.policy_path(REPO).exists()
    with pytest.raises(ValueError, match='not adopted'):
        sources.read_source(checkout, 'AGENTS.md', identity=REPO)
    evidence = sources.read_source(checkout, 'AGENTS.md', discovery=True, identity=REPO)
    assert not evidence['adopted'] and 'model attribution' in evidence['text']
    assert sources.read_source(checkout, 'docs/team.md', discovery=True, identity=REPO)['kind'] == 'custom'
    assert sources.inspect(checkout, REPO)['status'] == 'choice-needed'


@pytest.mark.parametrize('value,expected', [
    (policy(), {'CONTRIBUTING.md', 'docs/development/testing.md'}),
    (policy('all', exclude=['AGENTS.md']), {'.github/copilot-instructions.md', '.github/instructions/python.instructions.md',
                                         'CONTRIBUTING.md', 'docs/development/testing.md'}),
    (policy('custom', include=['docs/team.md']), {'docs/team.md'})])
def test_saved_modes_custom_paths_and_exact_exclusions(case, value, expected):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(value)
    output = sources.inspect(checkout, REPO)
    assert output['status'] == 'ready'
    assert {item['path'] for item in output['sources'] if item['selected']} == expected
    for source in expected:
        assert sources.read_source(checkout, source, identity=REPO)['adopted']
    with pytest.raises(ValueError, match='not adopted'):
        sources.read_source(checkout, 'AGENTS.md', identity=REPO)
    # Fresh processes reuse the saved choice without adopting new bot guidance.
    command = [sys.executable, str(ROOT/'scripts/instruction_sources.py'), '--checkout', str(checkout),
               '--repository', REPO, 'show']
    assert json.loads(subprocess.check_output(command)) == output


@pytest.mark.parametrize('value', [policy('unknown'), policy(include=['../AGENTS.md']),
                                 policy(include=['/AGENTS.md']), policy(include=['AGENTS.md'], exclude=['AGENTS.md'])])
def test_invalid_policy_cannot_be_admitted(value):
    with pytest.raises(ValueError):
        config_adapters.normalize({'domain': 'instruction-sources', 'action': 'set', 'value': value})


def test_selected_missing_or_escaping_sources_fail_closed(case, tmp_path):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    outside = tmp_path/'outside.md'
    outside.write_text('External instructions')
    (checkout/'AGENTS.md').symlink_to(outside)
    for name in ['AGENTS.md', 'missing.md']:
        save(policy('custom', include=[name]))
        with pytest.raises(ValueError, match='missing or unsafe'):
            sources.binding(checkout, REPO)
    with pytest.raises(ValueError):
        sources.read_source(checkout, 'AGENTS.md', discovery=True, identity=REPO)


def test_fresh_process_reuse_ignores_excluded_changes_preserves_original_results(case, tmp_path):
    spec, store = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    bound = build(spec)
    claim = store.start(bound)['claim']['id']
    store.record(bound, claim, b'Original completed analysis', {'complete': True}, 'complete')
    original_manifest = store.manifest.read_bytes()
    (checkout/'AGENTS.md').write_text('Different ignored bot rules')
    (checkout/'CLAUDE.md').write_text('New unadopted bot rules')
    saved = tmp_path/'spec.json'
    saved.write_text(json.dumps(bound))
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR,
               'start', '--spec', str(saved)]
    assert json.loads(subprocess.check_output(command))['action'] == 'reuse'
    assert store.manifest.read_bytes() == original_manifest


@pytest.mark.parametrize('change', ['selected-content', 'choice', 'reset'])
def test_changed_sources_choice_reset_reject_old_handoff_without_erasing_analysis(case, change):
    spec, store = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    bound = build(spec)
    claim = store.start(bound)['claim']['id']
    store.record(bound, claim, b'Original source-based analysis', {'complete': True}, 'complete')
    before = store.manifest.read_bytes()
    if change == 'selected-content':
        (checkout/'CONTRIBUTING.md').write_text('New governing human instructions')
    else:
        save(policy('custom') if change == 'choice' else None)
    with pytest.raises(ValueError, match='rebuild|choice needed'):
        store.start(bound)
    with pytest.raises(ValueError, match='rebuild|choice needed'):
        build(spec, instruction_policy=bound['instruction_policy'])
    assert store.manifest.read_bytes() == before


def test_excluded_sources_cannot_return_as_governing_artifacts(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    (checkout/'nested').mkdir()
    (checkout/'alias.md').symlink_to(checkout/'AGENTS.md')
    for field in ['instructions', 'inputs', 'resources']:
        for source in [checkout/'AGENTS.md', checkout/'nested/../AGENTS.md', checkout/'alias.md', checkout]:
            bound = build(spec, **{field: {'cached-guidance': str(source)}})
            with pytest.raises(ValueError, match='Excluded repository instruction'):
                capture(bound)


@pytest.mark.parametrize('field', ['instructions', 'inputs', 'resources'])
def test_custom_excluded_sources_remain_in_handoff_validation(case, field):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy('custom', exclude=['docs/team.md']))
    source = checkout/'docs/team.md'
    alias = checkout/'team-alias.md'
    alias.symlink_to(source)
    assert {'path': 'docs/team.md', 'kind': 'custom', 'selected': False} in sources.inspect(checkout, REPO)['sources']
    for path in [source, alias, source.parent]:
        with pytest.raises(ValueError, match='Excluded repository instruction'):
            capture(build(spec, **{field: {'old-guidance': str(path)}}))


def test_directory_alias_cannot_adopt_an_explicitly_excluded_source(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    (checkout/'alias').symlink_to(checkout, target_is_directory=True)
    save(policy('custom', include=['alias/AGENTS.md'], exclude=['AGENTS.md']))
    context = sources.inspect(checkout, REPO)
    assert not any(item['selected'] for item in context['sources'])
    with pytest.raises(ValueError, match='not adopted'):
        sources.read_source(checkout, 'alias/AGENTS.md', identity=REPO)
    assert not sources.read_source(checkout, 'alias/AGENTS.md', discovery=True, identity=REPO)['adopted']
    before = sources.binding(checkout, REPO)
    (checkout/'AGENTS.md').write_text('Changed excluded guidance through both names')
    assert sources.binding(checkout, REPO) == before


@pytest.mark.parametrize('field', ['instructions', 'inputs', 'resources'])
@pytest.mark.parametrize('value', [policy('all'), policy('custom', include=['CLAUDE.md'])])
def test_unsafe_alias_does_not_exclude_an_independently_selected_source(case, field, value):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    source = checkout/'CLAUDE.md'
    source.write_text('Selected repository instructions')
    alias = checkout/'AGENTS.md'
    alias.symlink_to(source)
    save(value)
    assert sources.read_source(checkout, 'CLAUDE.md', identity=REPO)['adopted']
    bound = build(spec, **{field: {'selected-guidance': str(source)}})
    capture(bound)
    nested = checkout/'nested'
    nested.mkdir()
    selected_alias = nested/'selected.md'
    selected_alias.symlink_to(source)
    capture(build(spec, **{field: {'selected-alias': str(selected_alias)}}))
    unsafe_alias = nested/'unsafe.md'
    unsafe_alias.symlink_to(alias)
    for path in [alias, nested/'../AGENTS.md', unsafe_alias, checkout]:
        with pytest.raises(ValueError, match='Excluded repository instruction.*AGENTS.md'):
            capture(build(spec, **{field: {'unsafe-guidance': str(path)}}))
    with pytest.raises(ValueError, match='missing or unsafe'):
        sources.read_source(checkout, 'AGENTS.md', identity=REPO)
    save(policy('all', exclude=['AGENTS.md']))
    with pytest.raises(ValueError, match='not adopted'):
        sources.read_source(checkout, 'CLAUDE.md', identity=REPO)
    with pytest.raises(ValueError, match='Excluded repository instruction'):
        capture(build(spec, **{field: {'excluded-guidance': str(source)}}))


@pytest.mark.parametrize('field', ['instructions', 'inputs', 'resources'])
def test_nested_resource_links_cannot_reintroduce_excluded_sources(case, tmp_path, field):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    bundle = tmp_path/'bundle'
    bundle.mkdir()
    linked = tmp_path/'linked'
    linked.mkdir()
    (bundle/'nested').symlink_to(linked, target_is_directory=True)
    (linked/'instructions.md').symlink_to(checkout/'AGENTS.md')
    with pytest.raises(ValueError, match='Excluded repository instruction.*AGENTS.md'):
        capture(build(spec, **{field: {'cached-guidance': str(bundle)}}))


@pytest.mark.parametrize('field', ['instructions', 'inputs', 'resources'])
@pytest.mark.parametrize('target', ['internal', 'external'])
def test_excluded_source_symlink_is_never_a_governing_artifact(case, tmp_path, field, target):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    source = checkout/'docs/team.md' if target == 'internal' else tmp_path/'external.md'
    source.parent.mkdir(exist_ok=True)
    source.write_text('Post automation attribution')
    (checkout/'AGENTS.md').symlink_to(source)
    save(policy())
    assert {'path': 'AGENTS.md', 'kind': 'automation', 'selected': False} in sources.inspect(checkout, REPO)['sources']
    for path in [checkout/'AGENTS.md', source]:
        with pytest.raises(ValueError, match='Excluded repository instruction.*AGENTS.md'):
            capture(build(spec, **{field: {'old-guidance': str(path)}}))
    for discovery in [False, True]:
        with pytest.raises(ValueError, match='missing or unsafe'):
            sources.read_source(checkout, 'AGENTS.md', discovery=discovery, identity=REPO)


def test_legacy_managed_handoff_requires_rebuilding_after_first_choice(case):
    spec, store = case
    tasks = Tasks(REPO, PR, checkout=spec['git']['checkout'])
    first = tasks.prepare({'tasks': [{'definition': definition(spec)}]})['tasks'][0]
    files(Path(spec['git']['checkout']))
    save(policy())
    resumed = tasks.resume()['tasks'][0]
    assert resumed['reason'] == 'refresh_required' and 'rebuilt assignment' in resumed['detail']
    with pytest.raises(ValueError, match='rebuilt assignment'):
        tasks.prepare({'tasks': [{'key': first['key']}]})
    assert store.manifest.exists()


def test_managed_saved_binding_reuses_then_blocks_changed_summary(case):
    spec, _ = case
    files(Path(spec['git']['checkout']))
    save(policy())
    tasks = Tasks(REPO, PR, checkout=spec['git']['checkout'])
    first = tasks.prepare({'tasks': [{'definition': definition(spec)}]})['tasks'][0]
    assert tasks.saved_spec(first['key']) == first['assignment']
    save(policy('custom'))
    result = Tasks(REPO, PR).resume()['tasks'][0]
    assert result['reason'] == 'refresh_required' and 'rebuild assignment' in result['detail']


def test_request_application_and_uncertain_write_recovery_keep_choice_and_siblings(case, monkeypatch):
    spec, _ = case
    checkout = spec['git']['checkout']
    files(Path(checkout))
    context = {'checkout': checkout, 'repository': REPO}
    store = config_requests.Requests()
    request_id = store.start('Use human contributor rules for this repository.', context)['id']
    operation = {'domain': 'instruction-sources', 'action': 'set', 'scope': 'repo', 'value': policy()}
    sibling = {'domain': 'skill', 'action': 'set', 'scope': 'repo', 'skill': 'dk-workflows:dk-ci',
               'value': {'timeout_seconds': 42}}
    operations = [sibling, operation]
    result = store.prepare(request_id, {'operations': operations})
    assessment = {'fingerprint': result['fingerprint'], 'summary': 'User selected human rules; inspected source provenance.',
                  'reviewed': [], 'relations': [], 'unresolved': []}
    store.prepare(request_id, {'operations': operations, 'assessment': assessment})
    original = config_requests.atomic_text
    def interrupted(path, content):
        original(path, content)
        if path == sources.policy_path(REPO):
            raise OSError('Lost response after write')
    monkeypatch.setattr(config_requests, 'atomic_text', interrupted)
    with pytest.raises(OSError, match='Lost response'):
        store.apply(request_id)
    completed_sibling = store.show(request_id, 'progress')['progress'][0]
    assert completed_sibling['receipt']
    monkeypatch.setattr(config_requests, 'atomic_text', lambda *_: pytest.fail('Repeated uncertain write'))
    recovered = config_requests.Requests()
    assert recovered.resume(request_id)['admission'] == 'reusable'
    assert recovered.apply(request_id)['status'] == 'applied'
    assert recovered.show(request_id, 'progress')['progress'][0] == completed_sibling
    assert sources.inspect(checkout, REPO)['policy'] == policy()
    assert recovered.apply(request_id)['status'] == 'applied'


def test_reset_is_request_bound_and_leaves_choice_needed_marker(case):
    spec, _ = case
    checkout = spec['git']['checkout']
    save(policy())
    store = config_requests.Requests()
    request_id = store.start('Reset repository instruction source choice.', {'checkout': checkout, 'repository': REPO})['id']
    operation = {'domain': 'instruction-sources', 'action': 'reset', 'scope': 'repo'}
    result = store.prepare(request_id, {'operations': [operation]})
    store.prepare(request_id, {'operations': [operation], 'assessment': {
        'fingerprint': result['fingerprint'], 'summary': 'Explicit reset; require a new source choice.',
        'reviewed': [], 'relations': [], 'unresolved': []}})
    store.apply(request_id)
    assert sources.inspect(checkout, REPO)['status'] == 'choice-needed'
    with pytest.raises(ValueError, match='choice needed'):
        sources.binding(checkout, REPO)


@pytest.mark.parametrize('changed_source,expected', [('AGENTS.md', 'reusable'),
                                                   ('CONTRIBUTING.md', 'stale'),
                                                   ('DEVELOPMENT.md', 'stale')])
def test_configuration_admission_reuses_only_selected_unchanged_sources(case, changed_source, expected):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    store = config_requests.Requests()
    request_id = store.start('Use a 42 second CI timeout.', {'checkout': str(checkout), 'repository': REPO})['id']
    operation = {'domain': 'skill', 'action': 'set', 'scope': 'repo', 'skill': 'dk-workflows:dk-ci',
                 'value': {'timeout_seconds': 42}}
    result = store.prepare(request_id, {'operations': [operation]})
    store.prepare(request_id, {'operations': [operation], 'assessment': {
        'fingerprint': result['fingerprint'], 'summary': 'Concrete timeout with selected contributor sources.',
        'reviewed': [], 'relations': [], 'unresolved': []}})
    (checkout/changed_source).write_text('Changed or newly discovered instructions')
    assert config_requests.Requests().resume(request_id)['admission'] == expected


def test_configuration_admission_tracks_ancestor_instructions_across_processes(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    ancestor = checkout.parent/'AGENTS.md'
    ancestor.write_text('Host-loaded configuration admission rules.')
    store = config_requests.Requests()
    request_id = store.start('Use a 42 second CI timeout.', {'checkout': str(checkout), 'repository': REPO})['id']
    operation = {'domain': 'skill', 'action': 'set', 'scope': 'repo', 'skill': 'dk-workflows:dk-ci',
                 'value': {'timeout_seconds': 42}}
    result = store.prepare(request_id, {'operations': [operation]})
    store.prepare(request_id, {'operations': [operation], 'assessment': {
        'fingerprint': result['fingerprint'], 'summary': 'Checked host and selected repository rules.',
        'reviewed': [], 'relations': [], 'unresolved': []}})
    command = [sys.executable, str(ROOT/'scripts/config_requests.py')]
    resume = [*command, 'resume', '--request-id', request_id]
    assert json.loads(subprocess.check_output(resume))['admission'] == 'reusable'
    ancestor.write_text('Changed host-loaded configuration admission rules.')
    assert json.loads(subprocess.check_output(resume))['admission'] == 'stale'
    applied = subprocess.run([*command, 'apply', '--request-id', request_id], text=True, capture_output=True)
    assert applied.returncode == 2 and 'governing inputs changed' in applied.stdout
    assert not config.path(REPO).exists()
    assert store.resume(request_id)['status'] == 'prepared'


def test_repository_only_policy_and_canonical_identity(case):
    spec, _ = case
    save(policy())
    assert sources.load('remote:' + REPO.upper()) == sources.load(REPO)
    assert sources.load('github.com/other/repository') is None
    with pytest.raises(ValueError, match='repository-only'):
        config_adapters.normalize({'domain': 'instruction-sources', 'action': 'set', 'scope': 'user', 'value': policy()})


def test_managed_hook_binding_survives_resume(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    write(config.path(REPO), {'processes': {'pr-fix': {'hooks': {'before_cycle': [
        {'instructions': 'Inspect selected contributor sources.'}]}}}})
    task_definition = definition(spec)
    task_definition['skill_identity'] = 'dk-workflows:dk-pr-fix'
    task_definition.pop('responsibility')
    task_definition.pop('assigned_instructions')
    request = {'run': 'run', 'cycle': 'cycle', 'tasks': [{'kind': 'hook', 'phase': 'before_cycle',
               'index': 0, 'definition': task_definition}]}
    tasks = Tasks(REPO, PR, checkout=checkout)
    first = tasks.prepare(request)['tasks'][0]
    assert tasks.saved_spec(first['key']) == first['assignment']
    assert 'detail' not in tasks.resume()['tasks'][0]


def test_retained_checkout_without_git_pins_survives_changed_session_cwd(case, monkeypatch, tmp_path):
    spec, store = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    task_definition = definition(spec)
    task_definition.pop('git')
    monkeypatch.chdir(tmp_path)
    tasks = Tasks(REPO, PR, checkout=checkout)
    first = tasks.prepare({'tasks': [{'definition': task_definition}]})['tasks'][0]
    assert first['assignment']['instruction_policy'] == sources.binding(checkout, REPO)
    monkeypatch.chdir(ROOT)
    assert Tasks(REPO, PR).saved_spec(first['key']) == first['assignment']
    assert capture(first['assignment'])['fingerprint']
    retained = store.manifest.read_bytes()
    (checkout/'CONTRIBUTING.md').write_text('Changed selected target repository guidance')
    with pytest.raises(ValueError, match='rebuild assignment'):
        capture(first['assignment'])
    assert Tasks(REPO, PR).resume()['tasks'][0]['reason'] == 'refresh_required'
    assert store.manifest.read_bytes() == retained


def test_no_git_task_honors_explicit_checkout_for_exclusions(case, tmp_path, monkeypatch):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    task_definition = definition(spec)
    task_definition.pop('git')
    task_definition['instructions'] = {'old-guidance': str(checkout/'AGENTS.md')}
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='Excluded repository instruction'):
        Tasks(REPO, PR, checkout=checkout).prepare({'tasks': [{'definition': task_definition}]})


def test_no_policy_no_git_task_preserves_legacy_fingerprint(case, tmp_path, monkeypatch):
    spec, _ = case
    task_definition = definition(spec)
    task_definition.pop('git')
    monkeypatch.chdir(tmp_path)
    legacy = make_spec(repository=REPO, pr=PR, **task_definition)
    task = Tasks(REPO, PR, checkout=spec['git']['checkout']).prepare(
        {'tasks': [{'definition': task_definition}]})['tasks'][0]
    assert 'instruction_policy' not in task['assignment']
    assert capture(task['assignment'])['fingerprint'] == capture(legacy)['fingerprint']


def test_source_contract_changes_stale_configuration_admission(case, monkeypatch):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    store = config_requests.Requests()
    request_id = store.start('Use a 42 second CI timeout.', {'checkout': str(checkout), 'repository': REPO})['id']
    operation = {'domain': 'skill', 'action': 'set', 'scope': 'repo', 'skill': 'dk-workflows:dk-ci',
                 'value': {'timeout_seconds': 42}}
    result = store.prepare(request_id, {'operations': [operation]})
    store.prepare(request_id, {'operations': [operation], 'assessment': {
        'fingerprint': result['fingerprint'], 'summary': 'Concrete timeout under the current source contract.',
        'reviewed': [], 'relations': [], 'unresolved': []}})
    assert config_requests.Requests().resume(request_id)['admission'] == 'reusable'
    original = config_requests.read_text
    contract = ROOT/'docs/instruction-sources.md'
    monkeypatch.setattr(config_requests, 'read_text', lambda path: original(path) + '\nChanged adoption contract.'
                        if Path(path) == contract else original(path))
    assert config_requests.Requests().resume(request_id)['admission'] == 'stale'
    with pytest.raises(ValueError, match='governing inputs changed'):
        config_requests.Requests().apply(request_id)
