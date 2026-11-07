"""Skill-owned defaults, scoped overrides, native plans and live input checks."""
from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
import config
import invocation
import skill_config
from state import Store, capture, write

REPOSITORY = 'github.com/owner/repo'
PR = 'https://' + REPOSITORY + '/pull/1'
CI = 'dk-workflows:dk-ci'
FIX = 'dk-workflows:dk-pr-fix'
SCHEMAS = [json.loads(path.read_text()) for path in [
    *ROOT.glob('schemas/*.schema.json'), *ROOT.glob('codex/skills/*/config.schema.json')]]
REGISTRY = Registry().with_resources((value['$id'], Resource.from_contents(value)) for value in SCHEMAS)


@pytest.mark.parametrize('skill', skill_config.catalog())
def test_each_skill_owns_complete_explicit_defaults_and_a_valid_schema(skill):
    directory = skill_config.directory(skill)
    schema = json.loads((directory/'config.schema.json').read_text())
    defaults = json.loads((directory/'config.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=REGISTRY).validate(defaults)
    assert set(defaults) == set(schema['properties'])
    assert skill_config.validate(skill, defaults) == skill_config.defaults(skill)
    for filename in ('config.json', 'config.schema.json'):
        assert (ROOT/'claude/skills'/directory.name/filename).read_bytes() == (directory/filename).read_bytes()


@pytest.mark.parametrize('skill,options', [
    (CI, {'poll_interval_seconds': 0}), (CI, {'timeout_seconds': True}),
    (CI, {'max_pending_extensions': 2}), (CI, {'poll_intervl_seconds': 20}),
    ('dk-workflows:dk-pr-reviewers-run', {'max_parallel': 0}),
    ('dk-workflows:dk-pr-reviewers-run', {'execution': 'automatic'}),
    ('dk-workflows:dk-pr-reviewers-run', {'reviewers': ['review']}),
    (FIX, {'tasks': [{'parallel': []}]}), (FIX, {'tasks': [{'parallel': ['review']}]}),
    (FIX, {'limits': {'review_cycles': 6}}),
    ('dk-workflows:dk-commit-split', {'policy': {'prompt': 'x', 'skill': 'team:policy'}}),
    ('dk-workflows:dk-pr-decisions', {'language_review_passes': 4}),
    ('dk-workflows:dk-pr-feedback-address', {'additional_validation_commands': ['pytest -q']}),
    ('dk-workflows:dk-pr-review-post', {'blocking_event': 'APPROVE'}),
    ('dk-workflows:dk-ci-approve', {'skip_verification': True}),
    ('dk-workflows:dk-preferences', {'default_applies_to': []}),
])
def test_owned_options_reject_malformed_values_in_runtime_and_schema(skill, options):
    schema = json.loads((skill_config.directory(skill)/'config.schema.json').read_text())
    assert not Draft202012Validator(schema, registry=REGISTRY).is_valid(options)
    with pytest.raises(ValueError):
        skill_config.validate(skill, options)
    with pytest.raises(ValueError):
        invocation.validate({'skill': skill, 'options': options})


def test_options_inherit_by_scope_then_invocation_without_writing_sources():
    upstream = 'github.com/upstream/repo'
    write(config.path(), {'skill_defaults': {CI: {'options': {'timeout_seconds': 100}}}})
    write(config.path(upstream), {'skill_defaults': {CI: {'options': {'poll_interval_seconds': 20}}}})
    write(config.path(REPOSITORY), {'skill_defaults': {CI: {'options': {'timeout_seconds': 300}}}})
    before = {path: path.read_bytes() for path in [config.path(), config.path(upstream), config.path(REPOSITORY)]}
    selected = skill_config.effective(CI, REPOSITORY, upstream, options={'timeout_seconds': 400})
    assert selected['options'] == {**skill_config.defaults(CI), 'timeout_seconds': 400, 'poll_interval_seconds': 20}
    assert selected['provenance']['timeout_seconds'] == 'invocation'
    assert selected['provenance']['poll_interval_seconds'] == str(config.path(upstream))
    assert all(path.read_bytes() == value for path, value in before.items())
    assert skill_config.scope_path(REPOSITORY) == config.path(REPOSITORY)


@pytest.mark.parametrize('options', [[], False, '', 0])
def test_empty_invalid_invocation_options_are_not_treated_as_omitted(options):
    with pytest.raises(ValueError, match='object'):
        skill_config.effective(CI, REPOSITORY, options=options)


@pytest.mark.parametrize('saved', [
    {'incoming_review': {'post': False, 'approve_ci': False}}, {'finalizers': []},
    {'reviewers': []}, {'unrecognized': {}},
])
def test_preferred_readers_reject_retired_settings_without_restoring_defaults(saved):
    source = config.path()
    write(source, saved)
    original = source.read_bytes()
    for skill in (FIX, 'dk-workflows:dk-pr-review', 'dk-workflows:dk-pr-iter'):
        with pytest.raises(ValueError, match='Unknown workflow setting'):
            skill_config.effective(skill, REPOSITORY)
        with pytest.raises(ValueError, match='Unknown workflow setting'):
            list(skill_config.calls(skill, REPOSITORY))
    with pytest.raises(ValueError, match='Unknown workflow setting'):
        skill_config.configured_call({'skill': CI}, REPOSITORY)
    for action in ('skill-show', 'skill-plan', 'skill-preflight'):
        arguments = [action, '--skill', FIX]
        if action == 'skill-plan': arguments += ['--kind', 'finalizers']
        if action == 'skill-preflight':
            available = source.parent/'available.json'
            write(available, skill_config.catalog())
            arguments += ['--available', str(available)]
        assert cli(*arguments).returncode == 2
    assert source.read_bytes() == original


def test_skill_scope_uses_the_central_location_resolver(tmp_path, monkeypatch):
    from shared import storage_paths
    calls = []
    monkeypatch.setattr(storage_paths, 'location', lambda name: calls.append(name) or tmp_path)
    assert skill_config.scope_path() == tmp_path/'user.json'
    assert calls == ['config']


@pytest.mark.parametrize('flag,limit,word', [('ci_repair', 'ci_repairs_per_cycle', 'repair attempts per cycle'),
                                           ('ci_rerun', 'ci_reruns_per_head', 'reruns per head')])
def test_lowered_ci_budget_diagnostic_reports_actual_limit(flag, limit, word):
    manifest = {'runs': {}}
    parameters = {'run': 'retained', 'cycle': None, 'retry': False, 'ci_repair': False,
                  'ci_rerun': False, 'head': 'a'*40, 'task': 'check', 'limits': {limit: 1}, flag: True}
    Store.budget(manifest, **parameters)
    with pytest.raises(ValueError, match='1 CI ' + word):
        Store.budget(manifest, **parameters)


def cli(*arguments):
    return subprocess.run([sys.executable, str(ROOT/'scripts/config.py'), '--repository', REPOSITORY,
                           *arguments], capture_output=True, text=True)


def test_cli_updates_only_selected_options_and_preserves_other_records(tmp_path):
    saved = {'skill_defaults': {CI: {'instructions': 'Keep this exact prompt.\n', 'agent': {'model': 'chosen'}},
                               'team:other': {'options': {'opaque': True}}},
             'extensions': {'untouched': 'private/reference'}}
    write(config.path(REPOSITORY), saved)
    source = tmp_path/'options.json'
    write(source, {'timeout_seconds': 42})
    result = cli('skill-set', '--skill', CI, '--scope', 'repo', '--file', str(source))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['options']['timeout_seconds'] == 42
    expected = deepcopy(saved)
    expected['skill_defaults'][CI]['options'] = {'timeout_seconds': 42}
    assert json.loads(config.path(REPOSITORY).read_text()) == expected
    write(source, {'unknown': True})
    assert cli('skill-set', '--skill', CI, '--scope', 'repo', '--file', str(source)).returncode == 2
    assert json.loads(config.path(REPOSITORY).read_text()) == expected
    assert cli('skill-reset', '--skill', CI, '--scope', 'repo').returncode == 0
    assert json.loads(config.path(REPOSITORY).read_text()) == saved


def test_repeated_invocations_and_parallel_groups_need_no_task_registry(tmp_path):
    task = {'skill': 'team:check', 'instructions': 'Additional exact prompt.\n', 'options': {}, 'required': True}
    plan = [task, {'parallel': [task, {'instructions': 'Read current Decisions.', 'options': {}, 'required': False}]}]
    write(config.path(REPOSITORY), {'skill_defaults': {FIX: {'options': {'tasks': plan}}}})
    assert skill_config.groups(plan) == [[task], [task, plan[1]['parallel'][1]]]
    resource = tmp_path/'SKILL.md'
    resource.write_text('Perform the supplied assignment; return original evidence.')
    definition = {'skill_identity': 'team:check', 'resources': {'skill': str(resource)},
                  'assigned_instructions': 'Original caller assignment.', 'instructions': {}, 'inputs': {},
                  'options': {}, 'governing_inputs_complete': True}
    first = skill_config.make_spec(repository=REPOSITORY, pr=PR, skill=FIX, kind='tasks', index=0, definition=definition)
    second = skill_config.make_spec(repository=REPOSITORY, pr=PR, skill=FIX, kind='tasks', index=1, definition=definition)
    assert capture(first)['key'] != capture(second)['key']
    assert first['task']['instructions'].startswith('Original caller assignment.')
    assert first['task']['instructions'].endswith(task['instructions'])
    store = Store(REPOSITORY, PR)
    claimed = store.start(first, run='retained')
    store.record(first, claimed['claim']['id'], b'Original result', {'complete': True}, 'complete')
    assert store.start(first, run='retained')['action'] == 'reuse'
    saved = json.loads(config.path(REPOSITORY).read_text())
    saved['skill_defaults'][CI] = {'options': {'poll_interval_seconds': 5}}
    write(config.path(REPOSITORY), saved)
    assert store.start(first, run='retained')['action'] == 'reuse'
    saved['skill_defaults'][FIX]['options']['tasks'][0]['instructions'] = 'Changed assignment.'
    write(config.path(REPOSITORY), saved)
    with pytest.raises(ValueError, match='Inputs changed'):
        capture(first)


def test_preflight_checks_optional_nested_calls_and_rejects_recursive_plans():
    selected = {'tasks': [{'parallel': [{'skill': 'team:check', 'required': False}, {'instructions': 'Read scope.'}]}], 'finalizers': []}
    calls = list(skill_config.calls(FIX, REPOSITORY, options=selected))
    result = invocation.preflight(calls, [FIX])
    assert result['missing_selected_skills'] == ['team:check']
    write(config.path(REPOSITORY), {'skill_defaults': {FIX: {'options': {'tasks': [{'skill': FIX}]}}}})
    with pytest.raises(ValueError, match='Recursive'):
        list(skill_config.calls(FIX, REPOSITORY))


@pytest.mark.parametrize('kind', ['tasks', 'finalizers', 'reviewers'])
def test_explicit_responsibilities_and_repeated_reviewers_keep_distinct_claims(tmp_path, kind):
    from native_review import required
    owner = 'dk-workflows:dk-pr-reviewers-run' if kind == 'reviewers' else FIX
    resource = tmp_path/'SKILL.md'
    resource.write_text('Perform the supplied assignment.')
    call = {'skill': 'team:check', 'instructions': 'Inspect the change.'}
    definition = {'skill_identity': 'team:check', 'resources': {'skill': str(resource)},
                  'responsibility': 'finding-verification', 'assigned_instructions': 'Verify findings.',
                  'instructions': {}, 'inputs': {}, 'options': {}, 'governing_inputs_complete': True}
    def spec(index, plan):
        return skill_config.make_spec(repository=REPOSITORY, pr=PR, skill=owner, kind=kind,
                                      index=index, definition=definition, run='retained', options={kind: plan})
    first, second = spec(0, [call, call]), spec(1, [call, call])
    assert capture(first)['key'] != capture(second)['key']
    assert required(capture(first)['components'])
    if kind == 'reviewers':
        reordered = spec(1, [{'skill': 'team:other'}, call])
        assert capture(first)['key'] == capture(reordered)['key']


def test_shared_commit_provider_settings_replace_policy_at_each_scope():
    skill = 'dk-workflows:dk-commit-split'
    write(config.path(), {'skill_defaults': {skill: {'options': {'policy': {'prompt': 'Keep boundaries.'}}}}})
    provider = {'skill': 'team:policy', 'instructions': 'Use repository conventions.'}
    write(config.path(REPOSITORY), {'skill_defaults': {skill: {'options': {'policy': provider}}}})
    assert skill_config.configured_call({'skill': skill}, REPOSITORY)['options']['policy'] == provider
    assert skill_config.configured_call({'instructions': 'Do this.'}, REPOSITORY) == {'instructions': 'Do this.'}


@pytest.mark.parametrize('kind', ['tasks', 'finalizers'])
def test_managed_skill_occurrences_replay_and_detect_live_selection_drift(tmp_path, kind):
    from tasks import Tasks
    resource = tmp_path/'SKILL.md'
    resource.write_text('Perform the selected check and report completion.')
    definition = {'skill_identity': 'team:check', 'resources': {'skill': str(resource)},
                  'assigned_instructions': 'Check current inputs.', 'instructions': {}, 'inputs': {},
                  'options': {}, 'governing_inputs_complete': True}
    selected = {'skill': 'team:check', 'instructions': 'Check repository invariants.'}
    write(config.path(REPOSITORY), {'skill_defaults': {FIX: {'options': {kind: [selected]}}}})
    request = {'run': 'retained', 'tasks': [{'kind': 'skill', 'skill': FIX, 'selection': kind,
                                           'index': 0, 'definition': definition}]}
    tasks = Tasks(REPOSITORY, PR)
    first = tasks.prepare(request)['tasks'][0]
    tasks.finish(first['key'], first['claim']['id'], b'All selected invariants checked.',
                 {'complete': True, 'findings': []})
    resumed = Tasks(REPOSITORY, PR)
    assert resumed.resume()['tasks'][0]['reusable']
    assert resumed.prepare({'run': 'retained', 'tasks': [{'key': first['key']}]})['tasks'][0]['action'] == 'reuse'
    selected['instructions'] = 'Check the changed invariants.'
    write(config.path(REPOSITORY), {'skill_defaults': {FIX: {'options': {kind: [selected]}}}})
    assert not resumed.resume()['tasks'][0]['reusable']


def test_existing_size_records_and_new_overrides_have_scope_correct_precedence():
    from shared import pr_size
    upstream = 'github.com/upstream/repo'
    pr_size.configure(['remote:' + REPOSITORY, 'remote:' + upstream], 'set', scope='user', settings={'added_lines_threshold': 700})
    write(config.path(upstream), {'skill_defaults': {'dk-workflows:dk-pr-split': {'options': {'split_related': 'manual'}}}})
    write(config.path(REPOSITORY), {'skill_defaults': {'dk-workflows:dk-pr-split': {'options': {'added_lines_threshold': 500}}}})
    result = pr_size.load(['remote:' + REPOSITORY, 'remote:' + upstream])
    assert result['pr_size'] == {'added_lines_threshold': 500, 'evacuate_unrelated': 'automatic', 'split_related': 'manual'}


def test_commit_policy_replaces_whole_prompt_or_provider_without_moving_records():
    import commit_policy
    skill = 'dk-workflows:dk-commit-split'
    write(commit_policy.path('user'), {'prompt': 'Keep independent commits.'})
    original = commit_policy.path('user').read_bytes()
    write(config.path(REPOSITORY), {'skill_defaults': {skill: {'options': {'policy': {'skill': 'team:policy'}}}}})
    assert commit_policy.load(['remote:' + REPOSITORY])['value'] == {'skill': 'team:policy'}
    assert commit_policy.path('user').read_bytes() == original
    write(config.path(REPOSITORY), {'skill_defaults': {skill: {'options': {'policy': None}}}})
    assert commit_policy.load(['remote:' + REPOSITORY])['value'] is None


def test_preference_default_modes_apply_only_to_new_admissions(tmp_path):
    import preferences
    record = {'owner': {'type': 'user'}, 'prompt': 'Use concrete names.',
              'scope': {'include': [{'type': 'directory', 'path': '.'}]}}
    first = preferences.prepare(tmp_path, {'upsert': [record]})
    persisted = preferences.commit(tmp_path, first)['records'][0]
    assert persisted['applies_to'] == ['implementation', 'review']
    write(config.path(), {'skill_defaults': {'dk-workflows:dk-preferences': {'options': {'default_applies_to': ['review']}}}})
    newer = preferences.prepare(tmp_path, {'upsert': [{**record, 'prompt': 'Use narrow exceptions.'}]})
    assert newer['changes']['upsert'][0]['applies_to'] == ['review']
    update = {key: value for key, value in persisted.items() if key != 'applies_to'}
    update['prompt'] = 'Use descriptive names.'
    changed = preferences.prepare(tmp_path, {'upsert': [update]})
    assert changed['changes']['upsert'][0]['applies_to'] == ['implementation', 'review']


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ci_cli_consumes_saved_local_and_explicit_timing_options(tmp_path, monkeypatch):
    ci = load_script('configured_ci_wait', 'runtime/dk-ci/scripts/ci_wait.py')
    write(config.path(REPOSITORY), {'skill_defaults': {CI: {'options': {'timeout_seconds': 50, 'max_pending_extensions': 0}}}})
    options = tmp_path/'options.json'
    write(options, {'poll_interval_seconds': 5, 'timeout_seconds': 40})
    observed = {}
    def observer(*args, **kwargs):
        observed['window'] = kwargs
        return nullcontext(object())
    def wait(*args, **kwargs):
        observed['wait'] = args
        return {'result': 'no_checks', 'head': 'a'*40, 'checks': []}
    monkeypatch.setattr(ci, 'for_pr', observer)
    monkeypatch.setattr(ci, 'wait', wait)
    monkeypatch.setattr(sys, 'argv', ['ci_wait.py', '--pr', PR, '--head', 'a'*40,
                                     '--options', str(options), '--timeout', '12'])
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == 1  # no checks remains a failure, even with custom timing
    assert observed['window']['timeout'] == 12
    assert observed['window']['extension'] == 0
    assert observed['wait'][3:6] == (12, 5, 60)


def test_flake_cli_consumes_saved_local_and_explicit_discovery_limits(tmp_path, monkeypatch):
    flakes = load_script('configured_flakes', 'runtime/dk-ci/scripts/flaky_tests.py')
    write(config.path(REPOSITORY), {'skill_defaults': {'dk-workflows:dk-ci-flakes': {'options': {'lookback_days': 7, 'max_runs': 8}}}})
    options = tmp_path/'options.json'
    write(options, {'max_issues': 9})
    class Saved:
        repo = REPOSITORY
        path = tmp_path/'registry.json'
        def load(self):
            return {'entries': {}}
    monkeypatch.setattr(flakes, 'Registry', lambda *args: Saved())
    monkeypatch.setattr(flakes, 'GitHub', lambda *args: object())
    observed = []
    monkeypatch.setattr(flakes, 'discover', lambda api, registry, *limits: observed.append(limits) or {})
    monkeypatch.setattr(sys, 'argv', ['flaky_tests.py', '--repo', REPOSITORY, '--options', str(options),
                                     'discover', '--max-runs', '3'])
    flakes.main()
    assert observed == [(7, 3, 9)]


def test_message_line_checker_consumes_invocation_options(tmp_path):
    draft, options = tmp_path/'message.txt', tmp_path/'options.json'
    draft.write_text('Eleven chars\n')
    write(options, {'max_line_length': 10})
    command = [sys.executable, str(ROOT/'runtime/shared/commit_message_lines.py'),
               '--file', str(draft), '--cwd', str(tmp_path), '--options', str(options)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1 and 'maximum 10' in result.stderr
    result = subprocess.run([*command, '--max-length', '20'], capture_output=True, text=True)
    assert result.returncode == 0 and 'at most 20' in result.stdout


def test_router_uses_skill_owned_invocations_and_additional_prompt(tmp_path):
    router = load_script('configured_pr_route', 'scripts/pr_route.py')
    selected = {'skill': 'team:review', 'instructions': 'Keep this additional prompt.\n', 'options': {}, 'required': True}
    write(config.path(REPOSITORY), {'skill_defaults': {'dk-workflows:dk-pr-iter': {'options': {'routes': {'other_pr': selected}}}}})
    data = {'user': {'id': 1, 'login': 'me'}, 'repos/owner/repo/pulls/1': {
        'number': 1, 'html_url': PR, 'head': {'sha': 'a'*40}, 'user': {'id': 2, 'login': 'author'}}}
    result = router.route(PR, api=data.__getitem__)
    assert result['invocation'] == selected
    assert result['skill'] == 'team:review'


def test_publisher_only_fills_an_omitted_merge_method(tmp_path, monkeypatch):
    publisher = load_script('configured_publisher', 'scripts/publish.py')
    write(config.path(REPOSITORY), {'skill_defaults': {'dk-workflows:dk-pr-publish': {'options': {'default_merge_method': 'merge'}}}})
    monkeypatch.setattr(publisher, '_prepare', lambda request, directory: request)
    prepared = publisher.prepare({'kind': 'merge-pr', 'pr': PR, 'head': 'a'*40})
    assert prepared['merge_method'] == 'merge'
    explicit = publisher.prepare({'kind': 'merge-pr', 'pr': PR, 'head': 'a'*40, 'merge_method': 'squash'})
    assert explicit['merge_method'] == 'squash'


def test_skill_spec_cli_resolves_builtin_options_and_retained_finalizer_run(tmp_path):
    definition = tmp_path/'definition.json'
    resource = tmp_path/'SKILL.md'
    resource.write_text('Inspect current CI with exact-head evidence.')
    write(config.path(REPOSITORY), {'skill_defaults': {FIX: {'options': {'finalizers': [{'skill': CI, 'options': {'timeout_seconds': 25}, 'required': True}]}}}})
    write(definition, {'skill_identity': CI, 'resources': {'skill': str(resource)},
                       'assigned_instructions': 'Check the final head.', 'instructions': {}, 'inputs': {},
                       'options': {}, 'governing_inputs_complete': True, 'cacheable': False})
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPOSITORY, '--pr', PR,
               'skill-spec', '--skill', FIX, '--kind', 'finalizers', '--index', '0', '--definition', str(definition)]
    missing = subprocess.run(command, capture_output=True, text=True)
    assert missing.returncode == 2
    first = json.loads(subprocess.check_output([*command, '--run', 'one'], text=True))
    second = json.loads(subprocess.check_output([*command, '--run', 'two'], text=True))
    assert first['options']['configured']['timeout_seconds'] == 25
    assert capture(first)['key'] != capture(second)['key']


@pytest.mark.parametrize('binary', ['codex', 'claude'])
def test_package_checker_rejects_skill_cli_advice(tmp_path, monkeypatch, binary):
    checker = load_script('skill_advice_check', 'scripts/check.py')
    monkeypatch.setattr(checker, 'ROOT', tmp_path)
    monkeypatch.setattr(checker, 'package', lambda root: {})
    skill = tmp_path/'codex/skills/example'
    skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('Run this:\n```sh\n' + binary + ' run\n```\n')
    with pytest.raises(AssertionError, match='Forbidden model CLI instructions'):
        checker.check()


@pytest.mark.parametrize('binary', ['codex', 'claude'])
def test_package_checker_rejects_model_cli_launches_without_executing_them(tmp_path, monkeypatch, binary):
    module = importlib.util.spec_from_file_location('skill_package_check', ROOT/'scripts/check.py')
    checker = importlib.util.module_from_spec(module)
    module.loader.exec_module(checker)
    monkeypatch.setattr(checker, 'ROOT', tmp_path)
    monkeypatch.setattr(checker, 'package', lambda root: {})
    (tmp_path/'runtime').mkdir()
    (tmp_path/'runtime/forbidden.py').write_text('import subprocess\nsubprocess.run([' + repr(binary) + ', "run"])\n')
    with pytest.raises(AssertionError, match='Forbidden model CLI launch'):
        checker.check()
