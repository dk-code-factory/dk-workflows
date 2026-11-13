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


def test_excluded_sources_cannot_return_as_instruction_or_input(case):
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    (checkout/'nested').mkdir()
    (checkout/'alias.md').symlink_to(checkout/'AGENTS.md')
    for field in ['instructions', 'inputs']:
        for source in [checkout/'AGENTS.md', checkout/'nested/../AGENTS.md', checkout/'alias.md', checkout]:
            bound = build(spec, **{field: {'cached-guidance': str(source)}})
            with pytest.raises(ValueError, match='Excluded repository instruction'):
                capture(bound)


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
    result = store.prepare(request_id, {'operations': [operation]})
    assessment = {'fingerprint': result['fingerprint'], 'summary': 'User selected human rules; inspected source provenance.',
                  'reviewed': [], 'relations': [], 'unresolved': []}
    store.prepare(request_id, {'operations': [operation], 'assessment': assessment})
    original = config_requests.atomic_text
    def interrupted(path, content):
        original(path, content)
        raise OSError('Lost response after write')
    monkeypatch.setattr(config_requests, 'atomic_text', interrupted)
    with pytest.raises(OSError, match='Lost response'):
        store.apply(request_id)
    monkeypatch.setattr(config_requests, 'atomic_text', lambda *_: pytest.fail('Repeated uncertain write'))
    recovered = config_requests.Requests()
    assert recovered.resume(request_id)['admission'] == 'reusable'
    assert recovered.apply(request_id)['status'] == 'applied'
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
    spec, _ = case
    checkout = Path(spec['git']['checkout'])
    files(checkout)
    save(policy())
    task_definition = definition(spec)
    task_definition.pop('git')
    monkeypatch.chdir(checkout)
    tasks = Tasks(REPO, PR, checkout=checkout)
    first = tasks.prepare({'tasks': [{'definition': task_definition}]})['tasks'][0]
    monkeypatch.chdir(tmp_path)
    assert Tasks(REPO, PR).saved_spec(first['key']) == first['assignment']
    assert capture(first['assignment'])['fingerprint']
