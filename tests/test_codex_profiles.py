"""Offline profile selection, native config transport and retained-evidence guards."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import bindings, codex_profiles as profiles, executors, preparation_views
from shared.runtime_bundles import retained_binding
from shared.reviewer_telemetry import ENV


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
    higher = []
    def config(cwd):
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
        return {'config': effective, 'origins': origins, 'layers': layers}, 'codex_cli_rs/0.164.1'
    monkeypatch.setattr(profiles, 'read_config', config)
    return {'binding': {'skill': 'provider:review', 'host': 'codex', 'codex_profile': 'review'},
            'home': home, 'profile': selected, 'root': root, 'reads': reads, 'higher': higher,
            'read_config': config}


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
    assert 'model_provider' in snapshot['unresolved_defaults']
    assert bindings.key(binding) != bindings.key({**binding, 'codex_profile': 'other'})
    assert bindings.key(binding) != 'provider:review@codex'
    assert profile_install['reads'] == [tmp_path.resolve()]


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
        profile_install['higher'].append({'name': {'type': 'sessionFlags'},
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
def test_unsupported_layer_selection_fails_explicitly(profile_install, change):
    profile_install['profile'].write_text(change + ' = {}\n')
    with pytest.raises(ValueError, match='configuration-layer selection'):
        bindings.resolve(profile_install['binding'])


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
    monkeypatch.setattr(profiles, 'read_config', lambda cwd: (response, agent))
    with pytest.raises(ValueError, match='support|precedence|ambiguous'):
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


def test_native_config_transport_waits_for_initialize_and_cleans_up(tmp_path, monkeypatch):
    # A real child process exercises the request ordering and transport cleanup.
    script = tmp_path / 'codex'
    script.write_text('#!' + sys.executable + '\nimport sys,json\n'
        'for line in sys.stdin:\n'
        ' request=json.loads(line)\n'
        ' if request.get("method")=="initialize":\n'
        '  print(json.dumps({"id":request["id"],"result":{"userAgent":"codex_cli_rs/0.164.1"}}),flush=True)\n'
        ' elif request.get("method")=="config/read":\n'
        '  print(json.dumps({"id":request["id"],"result":{"config":{},"origins":{},"layers":[]}}),flush=True)\n')
    script.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    result, agent = profiles.read_config(tmp_path)
    assert result == {'config': {}, 'origins': {}, 'layers': []}
    assert agent == 'codex_cli_rs/0.164.1'


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
    assert profile_install['reads'] == [tmp_path.resolve()]


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
    with pytest.raises(ValueError, match='absolute path|Cannot read governing'):
        bindings.resolve(profile_install['binding'])


def test_native_config_startup_failure_is_not_masked_by_cleanup(tmp_path, monkeypatch):
    script = tmp_path / 'codex'
    script.write_text('#!' + sys.executable + '\nimport sys\n'
                      'sys.stdin.readline()\nprint("native startup error",file=sys.stderr)\nsys.exit(1)\n')
    script.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    with pytest.raises(ValueError, match='ended without a response: native startup error'):
        profiles.read_config(tmp_path)



def test_base_user_config_symlink_remains_a_valid_native_layer(profile_install, tmp_path):
    base = profile_install['home'] / 'config.toml'
    managed = tmp_path / 'dotfiles-config.toml'
    base.replace(managed)
    base.symlink_to(managed)
    snapshot = bindings.resolve(profile_install['binding'])['codex_execution']
    assert snapshot['settings']['model'] == 'profile-model'
