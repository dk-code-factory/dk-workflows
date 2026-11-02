"""Offline profile selection, native config transport and retained-evidence guards."""
import hashlib
import importlib.util
import json
import os
import select
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import bindings, codex_profiles as profiles, executors, preparation_views
from shared.runtime_bundles import retained_binding
from shared.reviewer_telemetry import ENV

NATIVE_METADATA = profiles.native_metadata


@pytest.fixture
def profile_install(tmp_path, monkeypatch):
    home = tmp_path / 'codex'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    root = tmp_path / 'provider'
    (root / '.codex-plugin').mkdir(parents=True)
    (root / 'skills/review').mkdir(parents=True)
    (root / 'skills/review/SKILL.md').write_text('Review the pinned changes read-only.')
    (root / '.codex-plugin/plugin.json').write_text(json.dumps({
        'name': 'provider', 'version': '1.0.0', 'skills': './skills/'}))
    monkeypatch.setattr(bindings.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(bindings, 'codex_plugins', lambda: [{
        'name': 'provider', 'pluginId': 'provider@test', 'installedPath': str(root), 'version': '1.0.0'}])
    (home / 'config.toml').write_text('model = "base-model"\nmodel_reasoning_effort = "low"\n')
    selected = home / 'review.config.toml'
    selected.write_text('model = "profile-model"\nmodel_reasoning_effort = "high"\n'
                        'model_context_window = 10000\nmodel_auto_compact_token_limit = 8000\n')
    reads = []
    overrides_seen = []
    higher = []
    def config(cwd, overrides=None, **kwargs):
        overrides_seen.append(overrides or {})
        reads.append(Path(cwd).resolve())
        content = (home / 'config.toml').read_bytes()
        base = {'name': {'type': 'user', 'file': str(home / 'config.toml'), 'profile': None},
                'version': 'sha256:' + hashlib.sha256(content).hexdigest(),
                'config': tomllib.loads(content.decode())}
        layers = [*higher, base, {'name': {'type': 'system', 'file': '/etc/codex/config.toml'},
                                'version': 'sha256:empty', 'config': {}}]
        effective, origins = {}, {}
        for layer in reversed(layers):
            if not layer.get('disabledReason'):
                effective.update(layer['config'])
                origins.update({key: {k: layer[k] for k in ('name', 'version')}
                                for key in layer['config']})
        return {'config': effective, 'origins': origins, 'layers': layers, '_requirements': {}}, 'codex_cli_rs/0.164.1'
    monkeypatch.setattr(profiles, 'read_config', config)
    monkeypatch.setattr(profiles, 'validate_profile', lambda *args: None)
    def native_metadata(configuration, cwd):
        metadata = {'slug': configuration.get('model') or 'default-model',
                    'default_reasoning_level': 'medium', 'context_window': 10000,
                    'max_context_window': 20000, 'effective_context_window_percent': 95}
        effective = profiles.effective_settings(configuration, metadata['slug'], 'openai', metadata)
        return {'effective_settings': effective, 'catalog': {'sha256': 'fixture'},
                'expected_observations': {'model': effective['model'],
                    'model_provider': 'openai',
                    'model_reasoning_effort': configuration.get('model_reasoning_effort') or 'none',
                    'model_context_window': effective['model_context_window']}}
    monkeypatch.setattr(profiles, 'native_metadata', native_metadata)
    return {'binding': {'skill': 'provider:review', 'host': 'codex', 'codex_profile': 'review'},
            'home': home, 'profile': selected, 'root': root, 'reads': reads, 'higher': higher,
            'read_config': config, 'overrides_seen': overrides_seen}


def test_omission_keeps_existing_identity_and_does_not_read_profiles(profile_install):
    binding = {'skill': 'provider:review', 'host': 'codex'}
    assert bindings.key(binding) == 'provider:review@codex'
    assert 'codex_execution' not in bindings.resolve(binding)
    assert not profile_install['reads']


@pytest.mark.parametrize('value', ['', ' ', None, 7, [], {}])
def test_empty_or_nonstring_profile_rejected(value):
    with pytest.raises(ValueError, match='nonempty'):
        bindings.normalize({'skill': 'provider:review', 'codex_profile': value})


@pytest.mark.parametrize('binding', [
    {'command': ['provider', '{handoff}'], 'codex_profile': 'review'},
    {'skill': 'provider:review', 'host': 'claude', 'codex_profile': 'review'},
])
def test_wrong_executor_rejected(binding):
    with pytest.raises(ValueError, match='native Codex'):
        bindings.normalize(binding)


def test_current_host_validated_after_resolution(profile_install, monkeypatch):
    binding = {**profile_install['binding'], 'host': 'current'}
    assert bindings.resolve(binding)['executor'] == 'codex'
    monkeypatch.setenv('DK_HOST', 'claude')
    with pytest.raises(ValueError, match='resolved host: claude'):
        bindings.resolve(binding)


def test_profile_has_distinct_identity_and_effective_provenance(profile_install, tmp_path):
    binding = profile_install['binding']
    with bindings.execution_directory(tmp_path):
        value = bindings.resolve(binding)
    snapshot = value['codex_execution']
    assert snapshot['settings']['model'] == 'profile-model'
    assert snapshot['settings']['model_reasoning_effort'] == 'high'
    assert snapshot['settings']['model_context_window'] == 10000
    assert snapshot['origins']['model']['name']['profile'] == 'review'
    assert snapshot['profile_source']['version'].startswith('sha256:')
    assert snapshot['effective_settings']['model_provider'] == 'openai'
    assert bindings.key(binding) != bindings.key({**binding, 'codex_profile': 'other'})
    assert bindings.key(binding) != 'provider:review@codex'
    assert profile_install['reads'] == [tmp_path.resolve(), tmp_path.resolve()]


@pytest.mark.parametrize('name', ['missing', '../escape', '/absolute', 'bad/name', 'bad\\name'])
def test_missing_or_invalid_profile_fails(profile_install, name):
    with pytest.raises(ValueError, match='profile'):
        bindings.resolve({**profile_install['binding'], 'codex_profile': name})


def test_native_enabled_project_layer_overrides_profile_and_disabled_layer_does_not(profile_install):
    layer = {'name': {'type': 'project', 'dotCodexFolder': '/project/.codex'},
             'version': 'project-v1', 'config': {'model': 'project-model'}}
    profile_install['higher'].append(layer)
    snapshot = bindings.resolve(profile_install['binding'])['codex_execution']
    assert snapshot['settings']['model'] == 'project-model'
    assert snapshot['origins']['model']['name']['type'] == 'project'
    assert snapshot['settings']['model_reasoning_effort'] == 'high'
    layer['disabledReason'] = 'project is untrusted'
    assert bindings.resolve(profile_install['binding'])['codex_execution']['settings']['model'] == 'profile-model'


@pytest.mark.parametrize('change', ['profile', 'inherited', 'higher', 'missing'])
def test_retained_preparation_rejects_setting_drift_before_reuse(profile_install, change):
    binding = profile_install['binding']
    # Leave effort inherited to prove drift outside the selected file governs.
    profile_install['profile'].write_text('model = "profile-model"\n')
    retained = retained_binding(bindings.resolve(binding))
    descriptors = {bindings.key(binding): {'execution': retained}}
    with bindings.pinned_bindings(descriptors):
        assert bindings.resolve(binding) == retained
    if change == 'profile':
        profile_install['profile'].write_text('model = "changed-model"\n')
    elif change == 'inherited':
        (profile_install['home'] / 'config.toml').write_text('model_reasoning_effort = "high"\n')
    elif change == 'higher':
        profile_install['higher'].append({'name': {'type': 'legacyManagedConfigTomlFromFile', 'file': '/managed.toml'},
            'version': 'new', 'config': {'model_context_window': 20000}})
    else:
        profile_install['profile'].unlink()
    with pytest.raises(ValueError, match='changed|Cannot read'):
        with bindings.pinned_bindings(descriptors):
            pytest.fail('changed settings accepted for checkpoint reuse')


def test_profile_selection_change_cannot_reuse_retained_binding(profile_install):
    binding = profile_install['binding']
    retained = retained_binding(bindings.resolve(binding))
    wrong = {**retained, 'binding': {**binding, 'codex_profile': 'other'}}
    with pytest.raises(ValueError, match='invalid retained'):
        with bindings.pinned_bindings({bindings.key(binding): {'execution': wrong}}):
            pytest.fail('different profile accepted')


@pytest.mark.parametrize('change', ['projects', 'project_root_markers', 'profiles', 'profile'])
def test_native_profile_layer_selection_is_not_blanket_rejected(profile_install, change):
    profile_install['profile'].write_text(change + ' = {}\n')
    value = bindings.resolve(profile_install['binding'])
    assert value['codex_execution']['effective_settings']['model'] == 'base-model'
    if change in {'projects', 'project_root_markers'}:
        assert profile_install['overrides_seen'][-1][change] == {}


@pytest.mark.parametrize('shape', ['version', 'unknown-layer', 'bad-order', 'duplicate-user'])
def test_unproven_native_layering_is_rejected(profile_install, monkeypatch, shape):
    response, agent = profile_install['read_config'](Path.cwd())
    if shape == 'version':
        agent = 'codex_cli_rs/0.133.0'
    elif shape == 'unknown-layer':
        response['layers'][1]['name']['type'] = 'future-layer'
    elif shape == 'bad-order':
        response['layers'].reverse()
    else:
        response['layers'].append(response['layers'][0])
    monkeypatch.setattr(profiles, 'read_config', lambda cwd, *args, **kwargs: (response, agent))
    with pytest.raises(ValueError, match='support|precedence|ambiguous|modern'):
        bindings.resolve(profile_install['binding'])


def test_profile_execution_forwards_cli_and_retains_settings_telemetry(profile_install, tmp_path, monkeypatch):
    binding = profile_install['binding']
    resolved = bindings.resolve(binding)
    trace = tmp_path / 'trace.json'
    monkeypatch.setenv(ENV, str(trace))
    def run(argv, **kwargs):
        assert argv[0:4] == ['codex', 'exec', '--profile', 'review']
        assert argv[argv.index('--sandbox') + 1] == 'read-only'
        Path(argv[argv.index('-o') + 1]).write_text('{"findings":[]}')
        return subprocess.CompletedProcess(argv, 0, '', 'model: profile-model\nreasoning effort: high\n'
            'context window: 10000\nuser\nmodel: prompt-content\n')
    monkeypatch.setattr(executors, 'run_provider', run)
    result = executors.execute_process('Review', tmp_path, 'codex', {'DK_HOST': 'codex'}, resolved, 0, 10)
    assert json.loads(result) == {'findings': []}
    attempt, = json.loads(trace.read_text())['attempts']
    assert attempt['codex_execution']['prepared'] == resolved['codex_execution']
    assert attempt['codex_execution']['observed'] == {'model': 'profile-model',
        'model_reasoning_effort': 'high', 'model_context_window': 10000}


@pytest.mark.parametrize('detail', ['model: wrong-model', 'reasoning effort: low', 'context window: 9999'])
def test_observed_setting_mismatch_blocks_response_without_json_correction(profile_install, tmp_path, monkeypatch, detail):
    resolved = bindings.resolve(profile_install['binding'])
    def run(argv, **kwargs):
        Path(argv[argv.index('-o') + 1]).write_text('{"findings":[]}')
        return subprocess.CompletedProcess(argv, 0, '', detail + '\nuser\n')
    monkeypatch.setattr(executors, 'run_provider', run)
    with pytest.raises(ValueError, match='setting mismatch'):
        executors.execute_process('Review', tmp_path, 'codex', {'DK_HOST': 'codex'}, resolved, 0, 10)


def test_profile_drift_during_execution_blocks_response(profile_install, tmp_path, monkeypatch):
    resolved = bindings.resolve(profile_install['binding'])
    def run(argv, **kwargs):
        profile_install['profile'].write_text('model = "changed"\n')
        Path(argv[argv.index('-o') + 1]).write_text('{"findings":[]}')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(executors, 'run_provider', run)
    with pytest.raises(ValueError, match='settings changed'):
        executors.execute_process('Review', tmp_path, 'codex', {'DK_HOST': 'codex'}, resolved, 0, 10)


def test_known_profile_fields_keep_compact_delivery_and_unknown_extensions_govern(profile_install):
    execution = retained_binding(bindings.resolve(profile_install['binding']))
    descriptor = {'descriptor_version': 1, 'reviewer': execution['identity'], 'complete': True,
        'missing': [], 'sources': [{'path': execution['procedure'], 'sha256': 'fixture'}],
        'execution': execution, 'effective_prompt': 'review', 'aspects': ['correctness']}
    prepared = {'reviewers': {execution['identity']: descriptor},
                'analysis': {'reviewer_addenda': {execution['identity']: 'specific'}}}
    assert preparation_views.reviewer_view(prepared, execution['identity'])['reviewers'] == {}
    descriptor['execution']['codex_execution']['future'] = 'must remain'
    assert preparation_views.reviewer_view(prepared, execution['identity'])['reviewers'] == prepared['reviewers']


@pytest.mark.parametrize('paged', [False, True, 'repeat'])
def test_native_config_transport_waits_for_initialize_and_cleans_up(tmp_path, monkeypatch, paged):
    # A real child enforces the request boundary; model turns are rejected.
    script = tmp_path / 'codex'
    script.write_text('#!' + sys.executable + '\n' + """
import json, os, sys
initialized = False
ready = False
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialize':
        assert not initialized and not ready
        assert request['params']['clientInfo']['name'] == 'codex_exec'
        initialized = True
        result = {'userAgent': 'codex_exec/0.164.1'}
    elif method == 'initialized':
        assert initialized and not ready and 'id' not in request
        ready = True
        continue
    elif method == 'config/read':
        assert ready
        assert request['params'] == {'cwd': os.getcwd(), 'includeLayers': True}
        result = {'config': {}, 'origins': {}, 'layers': []}
    elif method == 'configRequirements/read':
        assert ready and request['params'] == {}
        result = {'requirements': None}
    elif method == 'model/list':
        assert ready and request['params'].get('includeHidden') is True
        if os.environ.get('DK_TEST_PAGED') and 'cursor' not in request['params']:
            result = {'data': [{'model': 'hidden-model', 'isDefault': False}], 'nextCursor': 'second'}
        elif os.environ.get('DK_TEST_PAGED'):
            assert request['params']['cursor'] == 'second'
            result = {'data': [{'model': 'visible-model', 'isDefault': True}],
                      'nextCursor': 'second' if os.environ.get('DK_TEST_PAGED') == 'repeat' else None}
        else:
            assert request['params'] == {'includeHidden': True}
            result = {'data': []}
    else:
        raise AssertionError('unexpected operation or model turn: ' + method)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
""")
    script.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    if paged:
        monkeypatch.setenv('DK_TEST_PAGED', 'repeat' if paged == 'repeat' else '1')
    if paged == 'repeat':
        with pytest.raises(ValueError, match='repeated cursor'):
            profiles.read_config(tmp_path, requirements=True, models=True)
        return
    result, agent, listing = profiles.read_config(tmp_path, requirements=True, models=True)
    assert result == {'config': {}, 'origins': {}, 'layers': [], '_requirements': {}}
    if paged:
        assert listing == {'data': [{'model': 'hidden-model', 'isDefault': False},
                                    {'model': 'visible-model', 'isDefault': True}]}
        catalog = {'models': [metadata(slug='hidden-model', visibility='hidden'),
                              metadata(slug='visible-model', visibility='list')]}
        monkeypatch.setattr(profiles, 'read_catalog', lambda *args: catalog)
        monkeypatch.delenv('CODEX_API_KEY', raising=False)
        monkeypatch.delenv('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', raising=False)
        assert NATIVE_METADATA({}, tmp_path)['effective_settings']['model'] == 'visible-model'
    else:
        assert listing == {'data': []}
    assert agent == 'codex_exec/0.164.1'


@pytest.mark.parametrize('kind', ['reviewers', 'drafting'])
def test_existing_binding_files_accept_and_persist_profile_selection(
        profile_install, tmp_path, monkeypatch, capsys, kind):
    path = Path(__file__).resolve().parents[1] / 'runtime/dk-config/scripts/repo_rules.py'
    spec = importlib.util.spec_from_file_location('profile_config_rules', path)
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    monkeypatch.setattr(rules, 'DATA', tmp_path / 'private-rules')
    monkeypatch.setattr(rules, 'identity', lambda cwd: (str(tmp_path), 'remote:github.com/o/r', tmp_path / 'index'))
    monkeypatch.setattr(rules, 'identities', lambda *args: ['remote:github.com/o/r'])
    binding = profile_install['binding']
    file = tmp_path / 'binding.json'
    file.write_text(json.dumps([binding] if kind == 'reviewers' else binding))
    args = ['repo_rules.py', '--cwd', str(tmp_path), kind, 'set']
    args += ['--bindings-file', str(file)] if kind == 'reviewers' else [
        'commit_message', '--binding-file', str(file)]
    monkeypatch.setattr(sys, 'argv', args)
    rules.main()
    result = json.loads(capsys.readouterr().out)
    selected = result['reviewers'][0] if kind == 'reviewers' else result['commit_message']['binding']
    assert selected == binding
    assert profile_install['reads'] == [tmp_path.resolve(), tmp_path.resolve()]


def test_drafting_profile_corrections_retain_same_execution_settings(profile_install, tmp_path, monkeypatch):
    from shared import drafting
    evidence = tmp_path / 'evidence'
    evidence.write_text('Verified changes')
    handoff = tmp_path / 'handoff.json'
    handoff.write_text(json.dumps({'contract': 'dk-draft-v1', 'role': 'commit_message',
        'sources': [{'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]}))
    calls = []
    def process(prompt, checkout, host, env, resolved, attempt, timeout):
        calls.append((resolved['codex_execution'], attempt))
        return '{"content": 1}' if attempt == 0 else '{"content":"Describe verified changes"}'
    monkeypatch.setattr(executors, 'execute_process', process)
    result = drafting.draft(profile_install['binding'], 'commit_message', handoff, tmp_path)
    assert result['content'] == 'Describe verified changes'
    assert not result['publication_authorized']
    assert [attempt for _, attempt in calls] == [0, 1]
    assert calls[0][0] == calls[1][0]
    assert all(cwd == tmp_path.resolve() for cwd in profile_install['reads'])


@pytest.mark.parametrize('field', ['model_catalog_json', 'model_instructions_file'])
@pytest.mark.parametrize('source', ['profile', 'inherited'])
def test_governing_file_content_drift_invalidates_retained_profile(
        profile_install, tmp_path, field, source):
    content = tmp_path / ('catalog.json' if field == 'model_catalog_json' else 'instructions.md')
    content.write_text('{"models": []}' if field == 'model_catalog_json' else 'Original instructions')
    configuration = profile_install['profile'] if source == 'profile' else profile_install['home'] / 'config.toml'
    configuration.write_text(configuration.read_text() + field + ' = ' + json.dumps(str(content)) + '\n')
    binding = profile_install['binding']
    retained = retained_binding(bindings.resolve(binding))
    snapshot = retained['codex_execution']
    assert snapshot['setting_sources'][field]['sha256'] == hashlib.sha256(content.read_bytes()).hexdigest()
    descriptors = {bindings.key(binding): {'execution': retained}}
    with bindings.pinned_bindings(descriptors):
        pass
    content.write_text('Changed content at the identical path')
    with pytest.raises(ValueError, match='execution settings changed'):
        with bindings.pinned_bindings(descriptors):
            pytest.fail('changed governing file reused')


@pytest.mark.parametrize('field', ['model_catalog_json', 'model_instructions_file'])
@pytest.mark.parametrize('value', ['relative/path', '/missing-governing-file'])
def test_unverifiable_governing_file_fails_visibly(profile_install, field, value):
    profile_install['profile'].write_text(field + ' = ' + json.dumps(value) + '\n')
    with pytest.raises(ValueError, match='Cannot read governing'):
        bindings.resolve(profile_install['binding'])


def test_native_config_startup_failure_is_not_masked_by_cleanup(tmp_path, monkeypatch):
    script = tmp_path / 'codex'
    script.write_text('#!' + sys.executable + '\nimport sys\n'
                      'sys.stdin.readline()\nprint("native startup error",file=sys.stderr)\nsys.exit(1)\n')
    script.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    with pytest.raises(ValueError, match='ended without a response'):
        profiles.read_config(tmp_path)



def test_base_user_config_symlink_remains_a_valid_native_layer(profile_install, tmp_path):
    base = profile_install['home'] / 'config.toml'
    managed = tmp_path / 'dotfiles-config.toml'
    base.replace(managed)
    base.symlink_to(managed)
    snapshot = bindings.resolve(profile_install['binding'])['codex_execution']
    assert snapshot['settings']['model'] == 'profile-model'


@pytest.mark.parametrize('reference', ['instructions.md', 'link/../instructions.md', '~/instructions.md'])
def test_relative_profile_references_use_lexical_profile_parent(profile_install, tmp_path, monkeypatch, reference):
    home = profile_install['home']
    user_home = tmp_path / 'user-home'
    user_home.mkdir()
    monkeypatch.setenv('HOME', str(user_home))
    (home / 'instructions.md').write_text('Home instructions')
    (user_home / 'instructions.md').write_text('User instructions')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (home / 'link').symlink_to(outside, target_is_directory=True)
    profile_install['profile'].write_text('model_instructions_file = ' + json.dumps(reference) + '\n')
    # The selected profile is itself a symlink; its lexical parent still governs.
    actual = outside / 'actual.config.toml'
    profile_install['profile'].replace(actual)
    profile_install['profile'].symlink_to(actual)
    snapshot = bindings.resolve(profile_install['binding'])['codex_execution']
    expected = user_home / 'instructions.md' if reference.startswith('~/') else home / 'instructions.md'
    assert snapshot['setting_sources']['model_instructions_file']['path'] == str(expected)
    assert snapshot['setting_sources']['model_instructions_file']['sha256'] == hashlib.sha256(expected.read_bytes()).hexdigest()


def test_higher_layer_reference_keeps_its_native_normalized_origin(profile_install, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    target = project / 'instructions.md'
    target.write_text('Project instructions')
    profile_install['profile'].write_text('model_instructions_file = []\n')
    profile_install['higher'].append({'name': {'type': 'project', 'dotCodexFolder': str(project)},
        'version': 'project-instructions', 'config': {'model_instructions_file': str(target)}})
    snapshot = bindings.resolve(profile_install['binding'])['codex_execution']
    assert snapshot['setting_sources']['model_instructions_file']['path'] == str(target)
    assert snapshot['origins']['model_instructions_file']['name']['type'] == 'project'


def test_authentic_profile_validation_cannot_be_masked_by_synthetic_discovery(profile_install, monkeypatch):
    native_calls = []
    monkeypatch.setattr(profiles, 'native_metadata', lambda *args: native_calls.append(args))
    def reject(*args):
        raise ValueError('Native selected-profile permission validation failed')
    monkeypatch.setattr(profiles, 'validate_profile', reject)
    with pytest.raises(ValueError, match='permission validation'):
        bindings.resolve(profile_install['binding'])
    assert native_calls == []


def metadata(**changes):
    return {'slug': 'fixture-model', 'default_reasoning_level': 'low',
            'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'max'}, {'effort': 'ultra'}],
            'multi_agent_reasoning_effort': 'max', 'context_window': 1000,
            'max_context_window': 2000, 'auto_compact_token_limit': None,
            'effective_context_window_percent': 95, **changes}


@pytest.mark.parametrize('config,expected', [
    ({}, (1000, 950, 900, 'low')),
    ({'model_context_window': 3000}, (2000, 1900, 1800, 'low')),
    ({'model_auto_compact_token_limit': 9999}, (1000, 950, 900, 'low')),
    ({'model_auto_compact_token_limit': 9999, 'model_auto_compact_token_limit_scope': 'body_after_prefix'},
     (1000, 950, 9999, 'low')),
    ({'model_reasoning_effort': 'ultra'}, (1000, 950, 900, 'max')),
    ({'model_reasoning_effort': 'persistent'}, (1000, 950, 900, 'disabled')),
])
def test_native_effective_context_and_request_effort(config, expected):
    result = profiles.effective_settings(config, 'fixture-model', 'openai', metadata())
    assert tuple(result[key] for key in ('model_context_window', 'model_usable_context_window',
        'model_auto_compact_token_limit', 'model_reasoning_effort')) == expected


@pytest.mark.parametrize('levels,preferred,expected', [
    (['low', 'high', 'ultra'], 'high', 'high'),
    (['low', 'max', 'ultra'], 'missing', 'max'),
    (['low', 'high', 'ultra'], 'missing', 'high'),
    (['ultra'], 'ultra', 'medium'),
])
def test_ultra_uses_native_supported_effort_fallback(levels, preferred, expected):
    row = metadata(supported_reasoning_levels=[{'effort': item} for item in levels],
                   multi_agent_reasoning_effort=preferred)
    assert profiles.effective_settings({'model_reasoning_effort': 'ultra'}, 'm', 'p', row)[
        'model_reasoning_effort'] == expected


def test_known_native_absence_is_distinct_from_header_none_and_unobserved_fields():
    result = profiles.effective_settings({}, 'm', 'p', metadata(default_reasoning_level=None))
    assert result['model_reasoning_effort'] is None
    expected = {'effective_settings': result, 'expected_observations': {'model_reasoning_effort': 'none'}}
    profiles.verify_observed(expected, {})
    profiles.verify_observed(expected, {'model_reasoning_effort': 'none'})
    with pytest.raises(ValueError, match='mismatch'):
        profiles.verify_observed(expected, {'model_reasoning_effort': 'low'})


def test_native_context_max_fallback_and_signed_integer_semantics():
    result = profiles.effective_settings({}, 'm', 'p', metadata(context_window=None))
    assert result['model_context_window'] == 2000
    negative = profiles.effective_settings({}, 'm', 'p', metadata(context_window=-101))
    assert negative['model_usable_context_window'] == -95
    assert negative['model_auto_compact_token_limit'] == -90
    saturated = profiles.effective_settings({}, 'm', 'p', metadata(
        context_window=profiles.I64_MAX, max_context_window=profiles.I64_MAX))
    assert saturated['model_usable_context_window'] == profiles.I64_MAX // 100
    with pytest.raises(ValueError, match='signed-i64'):
        profiles.effective_settings({'model_context_window': 2**63}, 'm', 'p', metadata())


@pytest.mark.parametrize('model,route,origin,slug', [
    ('fixture-model-latest', 'default', 'active_catalog', 'fixture-model'),
    ('custom/fixture-model-latest', 'static', 'active_catalog', 'fixture-model'),
    ('custom/fixture-model-latest', 'explicit_endpoint', 'native_synthetic', 'custom/fixture-model-latest'),
    ('a/b/fixture-model', 'default', 'native_synthetic', 'a/b/fixture-model'),
    ('missing', 'static', 'native_synthetic', 'missing'),
])
def test_route_aware_native_matching_and_public_synthetic_fallback(model, route, origin, slug):
    row, digest, actual_origin = profiles.model_metadata(model, {'models': [metadata()]}, route)
    assert row['slug'] == slug and actual_origin == origin and len(digest) == 64
    if origin == 'native_synthetic':
        assert row['context_window'] == 272000 and row['default_reasoning_level'] is None


@pytest.fixture
def native_metadata_io(tmp_path, monkeypatch):
    catalog = {'models': [metadata(priority=1, visibility='list', supported_in_api=True)]}
    listing = {'data': [{'model': 'fixture-model', 'isDefault': True}]}
    calls = []
    def read_catalog(cwd, projection):
        calls.append(('catalog', projection))
        if 'model_catalog_json' in projection and Path(projection['model_catalog_json']).exists():
            return json.loads(Path(projection['model_catalog_json']).read_text())
        return json.loads(json.dumps(catalog))
    def read_config(cwd, projection, **kwargs):
        assert kwargs == {'models': True}
        assert json.loads(Path(projection['model_catalog_json']).read_text()) == catalog
        calls.append(('models', projection))
        return {}, 'codex_exec/0.164.1', listing
    monkeypatch.setattr(profiles, 'read_catalog', read_catalog)
    monkeypatch.setattr(profiles, 'read_config', read_config)
    for key in ('CODEX_API_KEY', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE',
                'OPENAI_FEDERATION_RULE_ID', 'OPENAI_IDENTITY_TOKEN_FILE'):
        monkeypatch.delenv(key, raising=False)
    return catalog, listing, calls


def test_native_catalog_default_selection_uses_same_private_snapshot(native_metadata_io, tmp_path):
    value = NATIVE_METADATA({}, tmp_path)
    assert value['effective_settings']['model'] == 'fixture-model'
    assert value['catalog']['selection'] == 'native_catalog_default'
    assert value['effective_settings']['model_reasoning_effort'] == 'low'
    assert value['expected_observations']['model_reasoning_effort'] == 'none'
    # The temporary immutable catalogue is never a retained descriptor path.
    assert 'dk-codex-catalog-' not in json.dumps(value)


@pytest.mark.parametrize('static', [False, True])
def test_explicit_catalog_route_preserves_actual_manager_matching(native_metadata_io, tmp_path, static):
    config = {'model': 'ns/fixture-model-v2', 'model_provider': 'custom',
              'model_providers': {'custom': {'name': 'Custom', 'model_catalog_url': 'https://catalog.invalid'}}}
    if static:
        config['model_catalog_json'] = '/configured-catalog.json'
    value = NATIVE_METADATA(config, tmp_path)
    assert value['catalog']['route'] == ('static' if static else 'explicit_endpoint')
    assert value['catalog']['origin'] == ('active_catalog' if static else 'native_synthetic')


def test_same_provider_name_definition_and_catalog_changes_govern_without_secret_leaks(native_metadata_io, tmp_path):
    secret = 'SENTINEL_PROVIDER_CREDENTIAL'
    config = {'model': 'fixture-model', 'model_provider': 'custom',
              'model_providers': {'custom': {'name': 'Custom', 'http_headers': {'Authorization': secret}}}}
    first = NATIVE_METADATA(config, tmp_path)
    assert secret not in json.dumps(first)
    config['model_providers']['custom']['http_headers']['Authorization'] = secret + '-changed'
    assert NATIVE_METADATA(config, tmp_path)['provider'] != first['provider']
    native_metadata_io[0]['models'][0]['context_window'] = 1500
    changed = NATIVE_METADATA(config, tmp_path)
    assert changed['catalog']['sha256'] != first['catalog']['sha256']
    assert changed['effective_settings']['model_context_window'] == 1500


@pytest.mark.parametrize('allowed,forced,expected', [
    (None, None, 'api-model'), (['api'], 'api', 'api-model'),
    (['chatgpt'], None, 'chatgpt-model'), (None, 'chatgpt', 'chatgpt-model'),
])
def test_codex_api_key_uses_exec_auth_filter_not_standalone_helper(
        native_metadata_io, tmp_path, monkeypatch, allowed, forced, expected):
    monkeypatch.setenv('CODEX_API_KEY', '  private-key  ')
    catalog, listing, _ = native_metadata_io
    catalog['models'] = [metadata(slug='chatgpt-model', priority=1, visibility='list', supported_in_api=False),
                         metadata(slug='api-model', priority=2, visibility='list', supported_in_api=True)]
    listing['data'] = [{'model': 'chatgpt-model', 'isDefault': True}, {'model': 'api-model', 'isDefault': False}]
    config = {'forced_login_method': forced, '_requirements': {'allowedLoginMethods': allowed}}
    assert NATIVE_METADATA(config, tmp_path)['effective_settings']['model'] == expected


@pytest.mark.parametrize('key', ['OPENAI_FEDERATION_RULE_ID', 'OPENAI_IDENTITY_TOKEN_FILE'])
def test_api_key_does_not_replace_workload_or_provider_command_auth(
        native_metadata_io, tmp_path, monkeypatch, key):
    monkeypatch.setenv('CODEX_API_KEY', 'private-key')
    monkeypatch.setenv(key, '')  # Native presence, not truthiness, selects this route.
    assert NATIVE_METADATA({}, tmp_path)['catalog']['selection'] == 'native_catalog_default'
    monkeypatch.delenv(key)
    assert NATIVE_METADATA({'model_providers': {'openai': {'auth': {'command': 'ignored'}}}}, tmp_path)[
        'catalog']['selection'] == 'native_api_key_catalog_default'  # builtin OpenAI ignores this override
    assert NATIVE_METADATA({'model_provider': 'custom', 'model_providers': {
        'custom': {'name': 'Custom', 'auth': {'command': 'credential-helper'}}}}, tmp_path)[
        'catalog']['selection'] == 'native_catalog_default'


def test_unavailable_internal_catalog_partition_fails_instead_of_using_default_cache(
        native_metadata_io, tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'codex_work_cca')
    with pytest.raises(ValueError, match='verification is unavailable.*internal originator'):
        NATIVE_METADATA({}, tmp_path)
    assert native_metadata_io[2] == []
    # An explicit static catalogue has no named-originator partition.
    assert NATIVE_METADATA({'model': 'fixture-model', 'model_catalog_json': '/configured.json'}, tmp_path)[
        'catalog']['route'] == 'static'


def test_no_profile_exec_keeps_exact_arguments_and_avoids_all_metadata(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('unselected profile performed native metadata discovery')
    for name in ('read_config', 'read_catalog', 'validate_profile', 'native_metadata'):
        monkeypatch.setattr(profiles, name, unexpected)
    def run(argv, **kwargs):
        assert argv[:7] == ['codex', 'exec', '--ephemeral', '--sandbox', 'read-only', '--skip-git-repo-check', '-C']
        assert '--profile' not in argv
        Path(argv[argv.index('-o') + 1]).write_text('{}')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(executors, 'run_provider', run)
    assert executors.execute_process('Analyze', tmp_path, 'codex', {'DK_HOST': 'codex'}, None, 0, 10) == '{}'


def test_native_validation_and_catalog_errors_discard_secret_bearing_diagnostics(tmp_path, monkeypatch):
    secret = 'SENTINEL_NATIVE_PROVIDER_SECRET'
    def fail(argv, cwd, stdout):
        return subprocess.CompletedProcess(argv, 1, '', secret)
    monkeypatch.setattr(profiles, 'run_native', fail)
    with pytest.raises(ValueError) as validation:
        profiles.validate_profile('review', tmp_path)
    with pytest.raises(ValueError) as catalog:
        profiles.read_catalog(tmp_path, {'model_providers': {'custom': {'http_headers': {'key': secret}}}})
    assert secret not in str(validation.value) + str(catalog.value)


def test_native_toml_discovery_projection_preserves_quoted_paths_and_recursive_tables():
    value = {'projects': {'/project.with-dots and spaces': {'trust_level': 'trusted'}},
             'project_root_markers': ['.git', 'marker'],
             'features': {'network_proxy': {'credential_broker': True}}}
    decoded = {}
    argv = profiles.config_flags(value)
    for index in range(0, len(argv), 2):
        assert argv[index] == '-c'
        decoded.update(tomllib.loads(argv[index + 1]))
    assert decoded == value


def test_profile_discovery_includes_broker_inputs_without_high_precedence_model_values(profile_install):
    profile_install['profile'].write_text('model="profile-model"\nproject_root_markers=["marker"]\n'
        '[projects."/project.with.dots"]\ntrust_level="trusted"\n'
        '[features]\ncredential_masking=true\n[features.network_proxy]\ncredential_broker=true\n')
    bindings.resolve(profile_install['binding'])
    projected = profile_install['overrides_seen'][0]
    assert projected['projects']['/project.with.dots']['trust_level'] == 'trusted'
    assert projected['features']['network_proxy']['credential_broker']
    assert projected['features']['credential_masking']
    assert 'model' not in projected


def test_governing_source_change_during_metadata_discovery_blocks_preparation(profile_install, monkeypatch):
    def mutate(config, cwd):
        profile_install['profile'].write_text('model="changed-during-discovery"\n')
        return {}
    monkeypatch.setattr(profiles, 'native_metadata', mutate)
    with pytest.raises(ValueError, match='governing inputs changed during'):
        bindings.resolve(profile_install['binding'])


def test_native_catalog_change_between_captures_fails_not_silently_retries(native_metadata_io, tmp_path, monkeypatch):
    catalog, _, _ = native_metadata_io
    calls = []
    def read(cwd, projection):
        calls.append(projection)
        result = json.loads(json.dumps(catalog))
        if len(calls) > 1:
            result['models'][0]['context_window'] += 1
        return result
    monkeypatch.setattr(profiles, 'read_catalog', read)
    with pytest.raises(ValueError, match='catalog changed during'):
        NATIVE_METADATA({'model': 'fixture-model'}, tmp_path)
    assert len(calls) == 2


def test_hyphen_prefixed_native_profile_name_is_an_argument_value():
    assert profiles.profile_flags('-review') == ['--profile=-review']
    assert profiles.profile_flags('review') == ['--profile', 'review']


def test_native_catalog_matching_prefers_longest_prefix_and_keeps_instructions_hashed():
    secret = 'SENTINEL_PRIVATE_CATALOG_INSTRUCTIONS'
    catalog = {'models': [metadata(slug='fixture'), metadata(slug='fixture-model',
        base_instructions=secret, supported_reasoning_levels=[{'effort': 'high', 'description': secret}])]}
    selected, digest, _ = profiles.model_metadata('fixture-model-version', catalog, 'default')
    assert selected['slug'] == 'fixture-model'
    assert selected['supported_reasoning_levels'] == [{'effort': 'high'}]
    assert secret not in json.dumps(selected) and len(digest) == 64


@pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open'), reason='Linux owned-process notification')
def test_native_helper_cleanup_stops_credential_command_descendants(tmp_path):
    pid_file = tmp_path / 'credential-child.pid'
    code = ('import subprocess,sys; from pathlib import Path; '
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"]); '
            'Path(sys.argv[1]).write_text(str(child.pid))')
    result = profiles.run_native([sys.executable, '-c', code, str(pid_file)], tmp_path, subprocess.DEVNULL)
    assert result.returncode == 0
    try:
        descriptor = os.pidfd_open(int(pid_file.read_text()))
    except ProcessLookupError:
        return
    try:
        assert select.select([descriptor], [], [], 2)[0], 'owned credential child survived helper cleanup'
    finally:
        os.close(descriptor)
