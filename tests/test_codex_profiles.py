"""Offline profile selection, native config transport and retained-evidence guards."""
import copy
import hashlib
import importlib.util
import json
import os
import select
import signal
from pathlib import Path
from types import SimpleNamespace
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
    with bindings.execution_directory(tmp_path):
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
    with bindings.execution_directory(tmp_path):
        resolved = bindings.resolve(profile_install['binding'])
    def run(argv, **kwargs):
        Path(argv[argv.index('-o') + 1]).write_text('{"findings":[]}')
        return subprocess.CompletedProcess(argv, 0, '', detail + '\nuser\n')
    monkeypatch.setattr(executors, 'run_provider', run)
    with pytest.raises(ValueError, match='setting mismatch'):
        executors.execute_process('Review', tmp_path, 'codex', {'DK_HOST': 'codex'}, resolved, 0, 10)


def test_profile_drift_during_execution_blocks_response(profile_install, tmp_path, monkeypatch):
    with bindings.execution_directory(tmp_path):
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


@pytest.fixture
def native_child(tmp_path, monkeypatch):
    script = tmp_path / 'codex'
    script.write_text('#!' + sys.executable + '\n' + r"""
import json, os, sys
import hashlib, pathlib, re, tomllib
full = bool(os.environ.get('DK_TEST_NATIVE_FULL'))

def merge(base, value):
    result = dict(base)
    for key, item in value.items():
        result[key] = merge(result[key], item) if isinstance(result.get(key), dict) and isinstance(item, dict) else item
    return result

def version(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def normalized(value, parent):
    value = json.loads(json.dumps(value))
    for field in ('model_catalog_json', 'model_instructions_file'):
        if isinstance(value.get(field), str):
            value[field] = os.path.normpath(os.path.join(str(parent), value[field]))
    for provider in value.get('model_providers', {}).values():
        auth = provider.get('auth') if isinstance(provider, dict) else None
        if isinstance(auth, dict) and isinstance(auth.get('cwd'), str):
            auth['cwd'] = os.path.normpath(os.path.join(str(parent), auth['cwd']))
    return value

def headers(provider, reverse=False):
    result = {}
    valid_name = lambda name: bool(re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name)) and len(name) <= 65535
    def valid_value(value):
        try:
            return all(byte == 9 or byte >= 32 and byte != 127 for byte in value.encode('utf-8'))
        except UnicodeError:
            return False
    for field in ('http_headers', 'env_http_headers'):
        items = list(provider.get(field, {}).items())
        for name, item in reversed(items) if reverse else items:
            value = os.environ.get(item) if field == 'env_http_headers' else item
            if value is not None and valid_name(name) and valid_value(value) and (field != 'env_http_headers' or value.strip()):
                result[name.lower()] = value
    return result

cfg, layers, requirements, overrides = {}, [], {}, {}
if full:
    home = pathlib.Path(os.environ['CODEX_HOME'])
    file = home / 'config.toml'
    base = normalized(tomllib.loads(file.read_text()) if file.exists() else {}, home)
    cfg = json.loads(json.dumps(base))
    layers.append({'name': {'type': 'user', 'file': str(file), 'profile': None}, 'version': version(base), 'config': base})
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == '-c':
            overrides = merge(overrides, tomllib.loads(args[index + 1]))
    selected = None
    if '--profile' in args:
        selected = args[args.index('--profile') + 1]
    selected = next((arg.split('=', 1)[1] for arg in args if arg.startswith('--profile=')), selected)
    if selected:
        cfg = merge(cfg, normalized(tomllib.loads((home / (selected + '.config.toml')).read_text()), home))
    discovery = merge(cfg, overrides)
    project_file = pathlib.Path.cwd() / '.codex/config.toml'
    if project_file.exists() and discovery.get('projects', {}).get(os.getcwd(), {}).get('trust_level') == 'trusted':
        project = normalized(tomllib.loads(project_file.read_text()), project_file.parent)
        cfg = merge(cfg, project)
        layers.insert(0, {'name': {'type': 'project', 'dotCodexFolder': str(project_file.parent)}, 'version': version(project), 'config': project})
    cfg = merge(cfg, overrides)
    if overrides:
        layers.insert(0, {'name': {'type': 'sessionFlags'}, 'version': version(overrides), 'config': overrides})
    requirements_file = home / 'requirements.json'
    requirements = json.loads(requirements_file.read_text()) if requirements_file.exists() else {}
    for native, key in [('modelProvider', 'model_provider'), ('modelCatalogJson', 'model_catalog_json')]:
        if requirements.get(native) is not None:
            cfg[key] = requirements[native]
    cfg['model_providers'] = {**cfg.get('model_providers', {}), **requirements.get('modelProviders', {})}
    provider = cfg['model_providers'].get(cfg.get('model_provider', 'openai'), {})
    trace = {'argv': args, 'overrides': overrides, 'headers': headers(provider), 'reverse_headers': headers(provider, True),
             'env': {key: value for key, value in os.environ.items() if key.startswith(('DK_CODEX_METADATA_', 'TEST_NATIVE_')) and key not in json.loads(os.environ.get('DK_TEST_NATIVE_RESERVED', '[]'))},
             'auth': provider.get('auth'), 'query_params': provider.get('query_params'), 'projects': cfg.get('projects')}
    with open(os.environ['DK_TEST_NATIVE_TRACE'], 'a') as output:
        output.write(json.dumps(trace) + '\n')
    if args[-1] == 'models':
        if os.environ.get('DK_TEST_NATIVE_FAILURE') == 'models':
            print('SENTINEL_NATIVE_FAILURE_DETAIL', file=sys.stderr)
            sys.exit(3)
        catalog = pathlib.Path(cfg.get('model_catalog_json', home / 'catalog.json'))
        print(catalog.read_text())
        sys.exit(0)
    if args[-1] == 'prompt-input':
        print('[]')
        sys.exit(0)

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
        result = {'config': cfg, 'origins': {key: {'name': layers[-1]['name'], 'version': layers[-1]['version']} for key in cfg}, 'layers': layers} if full else {'config': {}, 'origins': {}, 'layers': []}
    elif method == 'configRequirements/read':
        assert ready and request['params'] == {}
        result = {'requirements': requirements or None}
    elif method == 'model/list':
        assert ready and request['params'].get('includeHidden') is True
        if full:
            data = json.loads(pathlib.Path(cfg.get('model_catalog_json', home / 'catalog.json')).read_text())
            result = {'data': [{'model': row['slug'], 'isDefault': index == 0} for index, row in enumerate(data['models'])]}
        elif os.environ.get('DK_TEST_PAGED') == 'nonobject':
            print('[]', flush=True)
            continue
        elif os.environ.get('DK_TEST_PAGED') and 'cursor' not in request['params']:
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
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))
    home = tmp_path / 'native-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    for key in ('CODEX_API_KEY', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
        monkeypatch.delenv(key, raising=False)
    reserved = [key for key in os.environ if key.startswith('DK_CODEX_METADATA_')]
    monkeypatch.setenv('DK_TEST_NATIVE_RESERVED', json.dumps(reserved))
    for key in list(os.environ):
        if key.startswith('TEST_NATIVE_'):
            monkeypatch.delenv(key)
    return home


@pytest.mark.parametrize('paged', [False, True, 'repeat', 'nonobject', 'timeout', 'brokenpipe'])
def test_native_config_transport_waits_for_initialize_and_cleans_up(tmp_path, monkeypatch, native_child, paged):
    if paged:
        monkeypatch.setenv('DK_TEST_PAGED', paged if isinstance(paged, str) else '1')
    if paged == 'repeat':
        with pytest.raises(ValueError, match='repeated cursor'):
            profiles.read_config(tmp_path, requirements=True, models=True)
        return
    if paged in ('timeout', 'brokenpipe'):
        original = profiles.subprocess.Popen
        clock = SimpleNamespace(now=0.0)
        launched = []
        class Writer:
            def __init__(self, stream):
                self.stream = stream
            def write(self, payload):
                if paged == 'brokenpipe':
                    raise BrokenPipeError('SENTINEL_PRIVATE_PIPE_DETAIL')
                if json.loads(payload).get('method') == 'model/list':
                    clock.now = 31.0
                return self.stream.write(payload)
            def flush(self):
                return self.stream.flush()
            def close(self):
                return self.stream.close()
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            launched.append(process)
            process.stdin = Writer(process.stdin)
            return process
        monkeypatch.setattr(profiles.subprocess, 'Popen', launch)
        if paged == 'timeout':
            monkeypatch.setattr(profiles, 'time', SimpleNamespace(monotonic=lambda: clock.now))
        pattern = 'model/list.*shared 30-second' if paged == 'timeout' else 'Native Codex.*BrokenPipeError'
        with pytest.raises(ValueError, match=pattern) as failure:
            profiles.read_config(tmp_path, requirements=True, models=True)
        assert 'SENTINEL_PRIVATE_PIPE_DETAIL' not in str(failure.value)
        assert launched[0].returncode is not None
        return
    if paged == 'nonobject':
        with pytest.raises(ValueError, match='model/list.*non-object'):
            profiles.read_config(tmp_path, requirements=True, models=True)
        return
    result, agent, listing = profiles.read_config(tmp_path, requirements=True, models=True)
    assert result == {'config': {}, 'origins': {}, 'layers': [], '_requirements': {}}
    if paged:
        assert listing == {'data': [{'model': 'hidden-model', 'isDefault': False},
                                    {'model': 'visible-model', 'isDefault': True}]}
        catalog = {'models': [metadata(slug='hidden-model', visibility='hidden'),
                              metadata(slug='visible-model', visibility='list')]}
        monkeypatch.setattr(profiles, 'read_catalog', lambda *args, **kwargs: catalog)
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
    def read_catalog(cwd, projection, **kwargs):
        calls.append(('catalog', projection))
        if 'model_catalog_json' in projection and Path(projection['model_catalog_json']).exists():
            return json.loads(Path(projection['model_catalog_json']).read_text())
        return json.loads(json.dumps(catalog))
    def read_config(cwd, projection, **kwargs):
        assert kwargs.get('models') is True
        assert isinstance(kwargs.get('env'), dict)
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


def test_provider_auth_cwd_comparison_projects_only_typed_owned_paths(native_metadata_io, tmp_path):
    def capture(root, *, command='./credential', unknown='unchanged', external=None):
        root.mkdir(exist_ok=True)
        (root / 'credentials').mkdir(exist_ok=True)
        config = {'model': 'fixture-model', 'model_provider': 'custom',
                  'model_catalog_json': '/file-backed/catalog.json',
                  'model_providers': {'custom': {'name': 'Custom', 'auth': {
                      'command': command, 'cwd': str(external or root / 'credentials'),
                      'unknown': unknown}}}}
        # Unchanged native-file-backed leaves need no helper CLI override.
        config['_helper_baseline'] = copy.deepcopy(config)
        original = copy.deepcopy(config)
        result = NATIVE_METADATA(config, root)
        assert config == original
        return result

    first, relocated = tmp_path / 'first', tmp_path / 'relocated'
    before, after = capture(first), capture(relocated)
    assert before['provider']['definition_sha256'] != after['provider']['definition_sha256']
    assert before['_provider_comparison']['comparison'] == after['_provider_comparison']['comparison']
    assert before['effective_settings'] == after['effective_settings']
    comparison = before['_provider_comparison']['comparison']
    for changed in (capture(first, command='./different-credential'),
                    capture(first, unknown=str(first / 'opaque-value')),
                    capture(first, unknown=str(relocated / 'opaque-value')),
                    capture(first, external=tmp_path / 'external-credentials')):
        assert changed['_provider_comparison']['comparison'] != comparison
    assert capture(first, unknown=str(first / 'opaque-value'))['_provider_comparison']['comparison'] != (
        capture(relocated, unknown=str(relocated / 'opaque-value'))['_provider_comparison']['comparison'])


def test_native_toml_non_bmp_dictionary_keys_round_trip():
    value = {'projects': {'/workspace/\U0001f680': {'trust_level': 'trusted'}},
             'model_providers': {'\U00010400': {'name': 'Unicode provider'}}}
    flags = profiles.config_flags(value)
    decoded = {}
    for index in range(0, len(flags), 2):
        assert flags[index] == '-c'
        decoded.update(tomllib.loads(flags[index + 1]))
    assert decoded == value


@pytest.mark.parametrize('static', [False, True])
def test_explicit_catalog_route_preserves_actual_manager_matching(native_metadata_io, tmp_path, static):
    config = {'model': 'ns/fixture-model-v2', 'model_provider': 'custom',
              'model_providers': {'custom': {'name': 'Custom', 'model_catalog_url': 'https://catalog.invalid'}}}
    config['_helper_baseline'] = copy.deepcopy(config)
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
    def fail(argv, cwd, stdout, **kwargs):
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
    def read(cwd, projection, **kwargs):
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


@pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'), reason='Linux owned-process notification')
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
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            os.close(descriptor)


def test_app_server_startup_failure_has_named_secret_safe_contract(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('SENTINEL_PRIVATE_STARTUP_DETAIL')
    monkeypatch.setattr(profiles.subprocess, 'Popen', missing)
    with pytest.raises(ValueError, match='Native Codex.*FileNotFoundError') as failure:
        profiles.read_config(tmp_path)
    assert 'SENTINEL_PRIVATE_STARTUP_DETAIL' not in str(failure.value)


@pytest.mark.parametrize('name', [None, 'invalid-name-shape', [], {'type': []}, {'type': 'user', 'file': None}, {'type': 'project', 'dotCodexFolder': None}])
def test_bad_layer_name_is_diagnosed_before_field_dereference(profile_install, monkeypatch, name):
    response, agent = profile_install['read_config'](Path.cwd())
    response['layers'][0]['name'] = name
    monkeypatch.setattr(profiles, 'read_config', lambda *args, **kwargs: (response, agent))
    result = bindings.diagnose(profile_install['binding'])
    assert len(result['missing']) == 1 and 'unsupported layer shape' in result['missing'][0]


def test_managed_catalogue_retains_requirements_origin_and_actual_reference(profile_install, tmp_path, monkeypatch):
    catalogue = tmp_path / 'managed-catalogue.json'
    catalogue.write_text('{"models": []}')
    response, agent = profile_install['read_config'](tmp_path)
    response['_requirements'] = {'modelCatalogJson': str(catalogue)}
    monkeypatch.setattr(profiles, 'read_config', lambda *args, **kwargs: (response, agent))
    profile_install['profile'].write_text('model_catalog_json="missing-overridden-catalogue.json"\n')
    value, _ = profiles.selected_layers('review', tmp_path)
    assert value['settings']['model_catalog_json'] == str(catalogue)
    assert value['origins']['model_catalog_json'] == {
        'name': {'type': 'requirements'}, 'version': 'sha256:' + profiles.fingerprint(response['_requirements'])}
    assert value['setting_sources']['model_catalog_json']['path'] == str(catalogue)


@pytest.mark.parametrize('higher,malformed_lower', [({}, False), ({'command': './other-token'}, False),
    ({'cwd': '/managed/credentials'}, False), ({'command': './managed-token', 'cwd': '/managed/cwd'}, True)])
def test_explicit_profile_auth_cwd_preserves_winning_leaf_origin(profile_install, tmp_path, higher, malformed_lower):
    profile_install['profile'].write_text('model_provider="custom"\n[model_providers.custom]\nname="Custom"\n'
        '[model_providers.custom.auth]\ncommand="./token"\ncwd="credentials"\n')
    if malformed_lower:
        profile_install['profile'].write_text('model_provider="custom"\n[model_providers.custom]\n'
            'name="Custom"\nauth="ignored-lower-shape"\n')
    if higher:
        auth = higher
        profile_install['higher'].append({'name': {'type': 'legacyManagedConfigTomlFromFile', 'file': '/managed/config.toml'},
            'version': 'managed-auth', 'config': {'model_providers': {'custom': {'auth': auth}}}})
    _, config = profiles.selected_layers('review', tmp_path)
    actual = config['model_providers']['custom']['auth']
    expected = higher.get('cwd') or str(profile_install['home'] / 'credentials')
    assert actual['cwd'] == expected
    assert actual['command'] == higher.get('command', './token')


@pytest.mark.parametrize('cwd', [None, []])
def test_auth_cwd_omission_and_overridden_malformed_lower_value_are_preserved(profile_install, tmp_path, cwd):
    contents = '[model_providers.custom.auth]\ncommand="./token"\n'
    if cwd is not None:
        contents += 'cwd=[]\n'
        profile_install['higher'].append({'name': {'type': 'legacyManagedConfigTomlFromFile', 'file': '/managed/config.toml'},
            'version': 'managed-cwd', 'config': {'model_providers': {'custom': {'auth': {'cwd': '/managed/cwd'}}}}})
    profile_install['profile'].write_text(contents)
    _, config = profiles.selected_layers('review', tmp_path)
    auth = config['model_providers']['custom']['auth']
    assert auth.get('cwd') == (None if cwd is None else '/managed/cwd')
    if cwd is None:
        assert 'cwd' not in auth


@pytest.mark.parametrize('selected', [False, True])
def test_only_selected_profile_canonicalizes_execution_cwd(profile_install, tmp_path, monkeypatch, selected):
    physical = tmp_path / 'physical'
    (physical / 'nested').mkdir(parents=True)
    canonical = physical / 'checkout'
    canonical.mkdir()
    (tmp_path / 'alias').symlink_to(physical / 'nested', target_is_directory=True)
    raw = tmp_path / 'alias/../checkout'
    resolved = bindings.resolve(profile_install['binding']) if selected else None
    verified = []
    monkeypatch.setattr(profiles, 'verify_profile', lambda value, cwd: verified.append(cwd))
    def run(argv, **kwargs):
        expected = canonical if selected else raw
        assert argv[argv.index('-C') + 1] == str(expected)
        assert kwargs['cwd'] == expected
        Path(argv[argv.index('-o') + 1]).write_text('{}')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(executors, 'run_provider', run)
    assert executors.execute_process('Read evidence', raw, 'codex', {'DK_HOST': 'codex'}, resolved, 0, 10) == '{}'
    assert verified == ([canonical, canonical] if selected else [])


@pytest.fixture
def native_private_config(native_child, tmp_path, monkeypatch):
    home = native_child
    trace = tmp_path / 'native-trace.jsonl'
    monkeypatch.setenv('DK_TEST_NATIVE_FULL', '1')
    monkeypatch.setenv('DK_TEST_NATIVE_TRACE', str(trace))
    (home / 'catalog.json').write_text(json.dumps({'models': [metadata(priority=0, visibility='list', supported_in_api=True)]}))
    def save(base, profile, policy=None):
        (home / 'config.toml').write_text('\n'.join(key + '=' + profiles.toml_value(value) for key, value in base.items()))
        (home / 'review.config.toml').write_text('\n'.join(key + '=' + profiles.toml_value(value) for key, value in profile.items()))
        if policy is not None:
            (home / 'requirements.json').write_text(json.dumps(policy))
    def observations():
        return [json.loads(line) for line in trace.read_text().splitlines()]
    return home, save, observations


@pytest.mark.parametrize('dynamic', [None, '', ' \t ', 'invalid\r\nvalue', 'SENTINEL_ENV_WIN', '\udcff', '☃'])
def test_all_native_helper_argv_use_private_environment_and_native_header_precedence(
        native_private_config, tmp_path, monkeypatch, dynamic):
    home, save, observations = native_private_config
    base_secret, selected_secret = 'SENTINEL_BASE_HEADER', 'SENTINEL_SELECTED_HEADER'
    base_provider = {'name': 'Custom', 'base_url': 'https://catalog.invalid',
        'http_headers': {'AUTHORIZATION': base_secret, 'X-Base': 'unchanged'},
        'env_http_headers': {'Authorization': 'TEST_NATIVE_AUTH', 'authorization': 'TEST_NATIVE_OTHER'}}
    base = {'model_provider': 'custom', 'model_providers': {'custom': base_provider},
            'projects': {str(tmp_path): {'trust_level': 'untrusted'}}}
    profile = {'model_providers': {'custom': {'http_headers': {'AUTHORIZATION': selected_secret}}},
               'projects': {str(tmp_path): {'trust_level': 'trusted'}}, 'project_root_markers': ['.git']}
    save(base, profile)
    project = tmp_path / '.codex'
    project.mkdir()
    (project / 'config.toml').write_text('model_context_window=1600\n')
    if dynamic is not None:
        monkeypatch.setenv('TEST_NATIVE_AUTH', dynamic)
    monkeypatch.setenv('TEST_NATIVE_OTHER', '')
    before = dict(os.environ)
    first = profiles.resolve_profile('review', tmp_path)
    second = profiles.resolve_profile('review', tmp_path)
    assert first == second  # Fresh random bridge names never enter retained provenance.
    assert os.environ.keys() == before.keys()
    assert not [key for key, value in before.items() if os.environ[key] != value]
    assert first['effective_settings']['model_context_window'] == 1600
    assert not any(secret in json.dumps(first) for secret in (base_secret, selected_secret, 'SENTINEL_ENV_WIN'))
    expected = dynamic if dynamic in ('SENTINEL_ENV_WIN', '☃') else selected_secret
    records = observations()
    for record in records:
        assert not any(secret in json.dumps(record['argv']) for secret in (base_secret, selected_secret, 'SENTINEL_ENV_WIN'))
    metadata_calls = [record for record in records if record['argv'][-1] == 'models' or
                      'model_catalog_json' in record['overrides']]
    assert len(metadata_calls) == 8  # Both complete raw-catalog/list sequences.
    for record in metadata_calls:
        assert record['headers'] == record['reverse_headers'] == {'authorization': expected, 'x-base': 'unchanged'}
        assert record['projects'][str(tmp_path)]['trust_level'] == 'trusted'
    if expected == selected_secret:
        assert any(selected_secret in record['env'].values() for record in metadata_calls)


def test_unchanged_native_file_secrets_and_unselected_fields_are_not_reprojected(native_private_config, tmp_path):
    _, save, observations = native_private_config
    sentinel = 'SENTINEL_FILE_BACKED_SECRET'
    definition = {'name': 'Custom', 'base_url': 'https://catalog.invalid',
                  'query_params': {'key': sentinel}, 'auth': {'command': './token', 'args': [sentinel]},
                  'http_headers': {'Authorization': sentinel}}
    save({'model': 'fixture-model', 'model_provider': 'custom', 'model_providers': {
        'custom': definition, 'unselected': {'name': 'Unused', 'query_params': {'key': sentinel}}}},
         {'model_reasoning_effort': 'high'})
    value = profiles.resolve_profile('review', tmp_path)
    assert value['effective_settings']['model'] == 'fixture-model'
    for record in observations():
        assert sentinel not in json.dumps(record['argv'])
        if record['argv'][-1] == 'models':
            assert 'model_providers' not in record['overrides']
            assert record['auth']['args'] == [sentinel] and record['query_params'] == {'key': sentinel}


def test_required_bearer_override_preserves_bytes_and_existing_env_key_precedence(native_private_config, tmp_path, monkeypatch):
    _, save, observations = native_private_config
    token = '  SENTINEL_BEARER_BYTES  '
    save({'model': 'fixture-model', 'model_provider': 'custom', 'model_providers': {
        'custom': {'name': 'Custom', 'base_url': 'https://catalog.invalid'}}},
         {'model_providers': {'custom': {'experimental_bearer_token': token}}})
    result = profiles.resolve_profile('review', tmp_path)
    assert token not in json.dumps(result)
    records = observations()
    raw = [item for item in records if item['argv'][-1] == 'models']
    assert raw and all(token not in json.dumps(item['argv']) for item in records)
    for item in raw:
        projected = item['overrides']['model_providers']['custom']
        assert 'experimental_bearer_token' not in projected
        assert item['env'][projected['env_key']] == token
    baseline = {'name': 'Custom', 'env_key': 'TEST_NATIVE_KEY'}
    desired = {**baseline, 'experimental_bearer_token': token}
    env = {}
    delta = profiles._safe_provider_delta('custom', baseline, desired,
        static_catalog=False, configured_model=True, env=env)
    assert delta == {} and 'TEST_NATIVE_KEY' not in env  # Preserve the original missing-env error.


@pytest.mark.parametrize('field,override', [
    ('query_params', {'query_params': {'key': 'SENTINEL_QUERY_OVERRIDE'}}),
    ('auth.args', {'auth': {'command': './token', 'args': ['SENTINEL_AUTH_OVERRIDE']}}),
])
def test_required_unsupported_sensitive_delta_fails_before_affected_native_spawn(native_private_config, tmp_path, field, override):
    _, save, observations = native_private_config
    save({'model': 'fixture-model', 'model_provider': 'custom',
          'model_providers': {'custom': {'name': 'Custom', 'base_url': 'https://catalog.invalid'}}},
         {'model_providers': {'custom': override}})
    with pytest.raises(ValueError, match=field) as failure:
        profiles.resolve_profile('review', tmp_path)
    assert 'SENTINEL_' not in str(failure.value)
    records = observations()
    assert records and not any(item['argv'][-1] == 'models' for item in records)
    assert not any('SENTINEL_' in json.dumps(item['argv']) for item in records)


@pytest.mark.parametrize('configured', [False, True])
def test_static_catalogue_does_not_transport_unused_endpoint_or_inference_credentials(native_private_config, tmp_path, configured):
    home, save, observations = native_private_config
    secret = 'SENTINEL_UNUSED_INFERENCE'
    save({'model_provider': 'custom', 'model_providers': {'custom': {'name': 'Custom'}}},
         {**({'model': 'fixture-model'} if configured else {}), 'model_catalog_json': str(home / 'catalog.json'),
          'model_providers': {'custom': {'http_headers': {'Authorization': secret},
              'model_catalog_url': 'https://catalog.invalid/' + secret,
              'auth': {'command': './token', 'args': [secret]}, 'query_params': {'key': secret}}}})
    result = profiles.resolve_profile('review', tmp_path)
    assert result['catalog']['route'] == 'static'
    for record in observations():
        assert secret not in json.dumps(record['argv'])
        if record['argv'][-1] == 'models':
            delta = record['overrides'].get('model_providers', {}).get('custom', {})
            assert not {'http_headers', 'query_params', 'model_catalog_url'} & delta.keys()
            assert 'auth' not in delta if configured else 'args' not in delta.get('auth', {})


@pytest.mark.parametrize('provider', ['custom', 'openai'])
@pytest.mark.parametrize('url', ['https://catalog.invalid/models?opaque=SENTINEL_CATALOG_QUERY',
                                'https://catalog.invalid/SENTINEL_CATALOG_PATH/models'])
def test_required_sensitive_catalogue_url_fails_before_affected_helper(native_private_config, tmp_path, provider, url):
    _, save, observations = native_private_config
    save({'model': 'fixture-model', 'model_provider': provider,
          'model_providers': {provider: {'name': 'Custom'}}},
         {'model_providers': {provider: {'model_catalog_url': url}}})
    with pytest.raises(ValueError, match='transport unavailable: model_catalog_url') as failure:
        profiles.resolve_profile('review', tmp_path)
    assert url not in str(failure.value) and 'SENTINEL_' not in str(failure.value)
    records = observations()
    assert records and not any(record['argv'][-1] == 'models' for record in records)
    assert all('SENTINEL_' not in json.dumps(record['argv']) for record in records)


@pytest.mark.parametrize('managed', [False, True])
def test_unchanged_sensitive_catalogue_url_stays_file_backed_with_other_changes(native_private_config, tmp_path, managed):
    _, save, observations = native_private_config
    url = 'https://catalog.invalid/SENTINEL_PRIVATE_PATH?opaque=SENTINEL_PRIVATE_QUERY'
    definition = {'name': 'Custom', 'model_catalog_url': url}
    base = {'model': 'fixture-model', 'model_provider': 'custom', 'model_providers': {'custom': definition}}
    selected = {'model_providers': {'custom': {'name': 'Harmless selected name'}}}
    policy = None
    if managed:
        selected['model_providers']['custom']['model_catalog_url'] = 'https://ignored.invalid/PROFILE_SENTINEL'
        policy = {'modelProviders': {'custom': definition}, 'modelProvider': 'custom'}
    save(base, selected, policy)
    result = profiles.resolve_profile('review', tmp_path)
    assert result['catalog']['route'] == 'explicit_endpoint'
    assert 'SENTINEL_' not in json.dumps(result)
    for record in observations():
        assert 'SENTINEL_' not in json.dumps(record['argv'])
        assert 'model_catalog_url' not in record['overrides'].get('model_providers', {}).get('custom', {})


@pytest.mark.parametrize('url', ['https://provider.invalid/v1?region=west',
    'https://provider.invalid/path@literal?email=name@example.invalid#@fragment',
    'https://@provider.invalid/v1'])
def test_plain_string_routing_urls_keep_existing_transport(url):
    config = {'model_provider': 'custom', 'openai_base_url': url, 'chatgpt_base_url': url}
    definition = {'name': 'Custom', 'base_url': url}
    projection, _ = profiles.metadata_projection(config, 'custom', definition, False)
    assert projection['openai_base_url'] == config['openai_base_url']
    assert projection['chatgpt_base_url'] == config['chatgpt_base_url']
    assert projection['model_providers']['custom']['base_url'] == definition['base_url']


@pytest.mark.parametrize('field,static', [('base_url', False), ('base_url', True),
                                       ('openai_base_url', False), ('chatgpt_base_url', False)])
@pytest.mark.parametrize('userinfo', ['SENTINEL_URL_USERNAME', ':SENTINEL_URL_PASSWORD',
    'SENTINEL_USER:SENTINEL_PASSWORD', '%53ENTINEL_USER:%53ENTINEL_PASSWORD'])
def test_required_url_userinfo_fails_before_affected_helper(native_private_config, tmp_path, field, static, userinfo):
    home, save, observations = native_private_config
    base = {'model': 'fixture-model', 'model_provider': 'custom',
            'model_providers': {'custom': {'name': 'Custom'}}}
    selected = {'model_catalog_json': str(home / 'catalog.json')} if static else {}
    url = 'https://' + userinfo + '@catalog.invalid/v1'
    if field == 'base_url':
        selected['model_providers'] = {'custom': {field: url}}
    else:
        selected[field] = url
    save(base, selected)
    with pytest.raises(ValueError, match='transport unavailable: ' + field) as failure:
        profiles.resolve_profile('review', tmp_path)
    assert url not in str(failure.value) and 'SENTINEL_' not in str(failure.value)
    records = observations()
    assert records and not any(record['argv'][-1] == 'models' for record in records)
    assert all('SENTINEL_' not in json.dumps(record['argv']) for record in records)


@pytest.mark.parametrize('field', ['base_url', 'openai_base_url', 'chatgpt_base_url'])
def test_unchanged_url_userinfo_stays_native_file_backed(native_private_config, tmp_path, field):
    _, save, observations = native_private_config
    base = {'model': 'fixture-model', 'model_provider': 'custom',
            'model_providers': {'custom': {'name': 'Custom'}}}
    url = 'https://user:SENTINEL_NATIVE_PASSWORD@catalog.invalid/v1'
    if field == 'base_url':
        base['model_providers']['custom'][field] = url
    else:
        base[field] = url
    save(base, {'model_providers': {'custom': {'name': 'Harmless selected change'}}})
    value = profiles.resolve_profile('review', tmp_path)
    assert 'SENTINEL_' not in json.dumps(value)
    for record in observations():
        assert 'SENTINEL_' not in json.dumps(record['argv'])


@pytest.mark.parametrize('field', ['base_url', 'openai_base_url', 'chatgpt_base_url'])
def test_url_parser_errors_are_field_only(field):
    url = 'https://[SENTINEL_MALFORMED_URL'
    config = {field: url} if field != 'base_url' else {}
    definition = {'name': 'Custom', **({field: url} if field == 'base_url' else {})}
    with pytest.raises(ValueError, match='unsupported ' + field + ' URL') as failure:
        profiles.metadata_projection(config, 'custom', definition, False)
    assert 'SENTINEL_' not in str(failure.value)


def test_managed_sensitive_provider_replacement_stays_native_file_backed(native_private_config, tmp_path):
    _, save, observations = native_private_config
    secret = 'SENTINEL_MANAGED_PROVIDER'
    save({'model': 'fixture-model', 'model_provider': 'custom'},
         {'model_providers': {'custom': {'name': 'Overridden', 'auth': {'command': './bad', 'args': ['unused-profile']}}}},
         {'modelProvider': 'custom', 'modelProviders': {'custom': {
            'name': 'Required', 'base_url': 'https://catalog.invalid', 'query_params': {'key': secret}}}})
    value = profiles.resolve_profile('review', tmp_path)
    assert value['effective_settings']['model_provider'] == 'custom'
    for item in observations():
        assert secret not in json.dumps(item['argv'])
        if item['argv'][-1] == 'models':
            assert 'model_providers' not in item['overrides']
            assert item['query_params'] == {'key': secret}


def test_protected_helper_failure_has_no_argv_or_diagnostic_secret(native_private_config, tmp_path, monkeypatch):
    _, save, observations = native_private_config
    secret = 'SENTINEL_HEADER_ON_FAILURE'
    save({'model': 'fixture-model', 'model_provider': 'custom', 'model_providers': {'custom': {'name': 'Custom'}}},
         {'model_providers': {'custom': {'http_headers': {'Authorization': secret}}}})
    monkeypatch.setenv('DK_TEST_NATIVE_FAILURE', 'models')
    with pytest.raises(ValueError) as failure:
        profiles.resolve_profile('review', tmp_path)
    assert secret not in str(failure.value) and 'SENTINEL_NATIVE_FAILURE_DETAIL' not in str(failure.value)
    records = observations()
    assert any(secret in item['env'].values() for item in records)
    assert all(secret not in json.dumps(item['argv']) for item in records)


@pytest.mark.parametrize('value,expected', [('', ''), (' \t ', ' \t '), ('☃\t', '☃\t'), ('bad\n', None)])
def test_header_present_blank_unicode_and_invalid_values_preserve_native_semantics(value, expected):
    baseline = {'name': 'Custom', 'http_headers': {'X-Test': 'old'}, 'env_http_headers': {'x-test': 'OLD_ENV'}}
    desired = profiles.merge(baseline, {'http_headers': {'X-Test': value}, 'env_http_headers': {'x-test': 'MISSING_ENV'}})
    env = {'OLD_ENV': 'old-dynamic'}
    delta = profiles._safe_provider_delta('custom', baseline, desired,
        static_catalog=False, configured_model=True, env=env)
    merged = profiles.merge(baseline, delta)
    assert profiles._headers(merged, env) == ({} if expected is None else {'x-test': expected})
    if value.strip():
        assert json.dumps(value, ensure_ascii=False)[1:-1] not in ''.join(
            profiles.config_flags({'model_providers': {'custom': delta}}))


def test_header_duplicate_ambiguity_and_explicit_bearer_override_are_not_fabricated():
    with pytest.raises(ValueError, match='ambiguous'):
        profiles._safe_provider_delta('custom', {}, {'name': 'Custom', 'http_headers': {'X-A': 'one', 'x-a': 'two'}},
            static_catalog=False, configured_model=True, env={})
    desired = {'name': 'Custom', 'http_headers': {'Authorization': 'secret-one', 'authorization': 'secret-two'},
               'env_key': 'REAL_KEY'}
    env = {'REAL_KEY': 'actual-bearer'}
    result = profiles._safe_provider_delta('custom', {}, desired, static_catalog=False, configured_model=True, env=env)
    assert 'http_headers' not in result and result['env_key'] == 'REAL_KEY'


def test_native_helper_rebinds_every_inherited_dynamic_case_variant(native_private_config, tmp_path, monkeypatch):
    _, save, observations = native_private_config
    secret = 'SENTINEL_FINAL_HEADER'
    base = {'model': 'fixture-model', 'model_provider': 'custom', 'model_providers': {'custom': {
        'name': 'Custom', 'http_headers': {'Authorization': 'SENTINEL_LOWER_LITERAL'},
        'env_http_headers': {'authorization': 'TEST_NATIVE_AUTH', 'AUTHORIZATION': 'TEST_NATIVE_OTHER'}}}}
    save(base, {'model_providers': {'custom': {'http_headers': {'Authorization': secret},
        'env_http_headers': {'authorization': 'MISSING_ONE', 'AUTHORIZATION': 'MISSING_TWO'}}}})
    monkeypatch.setenv('TEST_NATIVE_AUTH', 'SENTINEL_OLD_DYNAMIC_ONE')
    monkeypatch.setenv('TEST_NATIVE_OTHER', 'SENTINEL_OLD_DYNAMIC_TWO')
    profiles.resolve_profile('review', tmp_path)
    for item in observations():
        assert secret not in json.dumps(item['argv'])
        if item['argv'][-1] == 'models':
            assert item['headers'] == item['reverse_headers'] == {'authorization': secret}


@pytest.mark.parametrize('provider', ['openai', 'ollama', 'lmstudio'])
def test_ignored_builtin_provider_credentials_never_need_transport(native_private_config, tmp_path, provider):
    _, save, observations = native_private_config
    secret = 'SENTINEL_IGNORED_BUILTIN'
    save({'model': 'fixture-model', 'model_provider': provider}, {'model_providers': {provider: {
        'name': 'Ignored', 'http_headers': {'Authorization': secret},
        'base_url': 'https://user:' + secret + '@ignored.invalid',
        'experimental_bearer_token': secret, 'auth': {'command': './ignored', 'args': [secret]}}}})
    profiles.resolve_profile('review', tmp_path)
    for item in observations():
        assert secret not in json.dumps(item['argv'])
        if item['argv'][-1] == 'models':
            assert 'model_providers' not in item['overrides']


@pytest.mark.parametrize('name', ['bad name', '☃', ''])
def test_invalid_native_header_names_are_ignored_without_secret_argv(name):
    secret = 'SENTINEL_IGNORED_HEADER'
    result = profiles._safe_provider_delta('custom', {}, {'name': 'Custom', 'http_headers': {name: secret}},
        static_catalog=False, configured_model=False, env={})
    assert secret not in json.dumps(result) and 'http_headers' not in result


def test_generated_transport_variable_cannot_activate_an_original_missing_reference(monkeypatch):
    first, second = 'A' * 32, 'B' * 32
    missing = 'DK_CODEX_METADATA_' + first
    sequence = iter([first, second])
    monkeypatch.setattr(profiles.secrets, 'token_hex', lambda size: next(sequence))
    baseline = {'name': 'Custom', 'env_http_headers': {'X-B': missing}}
    desired = profiles.merge(baseline, {'http_headers': {'X-A': 'SENTINEL_HEADER'}})
    env = {}
    result = profiles._safe_provider_delta('custom', baseline, desired, static_catalog=False, configured_model=False, env=env)
    assert missing not in env
    assert result['env_http_headers']['x-a'] == 'DK_CODEX_METADATA_' + second


def test_guard_reuses_verified_profile_only_for_exact_descriptor_and_canonical_cwd(profile_install, tmp_path, monkeypatch):
    binding = profile_install['binding']
    with bindings.execution_directory(tmp_path):
        retained = retained_binding(bindings.resolve(binding))
    descriptors = {bindings.key(binding): {'execution': retained}}
    original = profiles.resolve_profile
    observed = []
    def resolve(name, cwd=None):
        observed.append(str(cwd))
        return original(name, cwd)
    monkeypatch.setattr(profiles, 'resolve_profile', resolve)
    with bindings.pinned_bindings(descriptors, cwd=tmp_path):
        assert bindings.resolve(binding) == retained
        assert bindings.resolve(binding) == retained
        assert len(observed) == 1
        with bindings.pinned_bindings(descriptors, cwd=tmp_path):
            bindings.resolve(binding)
        assert len(observed) == 2  # A new logical guard always observes afresh.
        with bindings.execution_directory(tmp_path / 'different'):
            with pytest.raises(ValueError, match='execution settings changed'):
                bindings.resolve(binding)
        assert len(observed) == 3
        procedure = retained['procedure']
        retained['procedure'] = str(Path(procedure).with_name('different.md'))
        try:
            with pytest.raises(ValueError, match='procedure or version changed'):
                bindings.resolve(binding)
        finally:
            retained['procedure'] = procedure
        assert len(observed) == 3  # Tamper check precedes native discovery.
    with bindings.pinned_bindings(descriptors, cwd=tmp_path, cache_profiles=False):
        bindings.resolve(binding)
        bindings.resolve(binding)
    assert len(observed) == 6  # Long binding lifetimes cannot become review-wide caches.


def test_explicit_prelaunch_postexecution_and_new_guard_bypass_short_guard_reuse(profile_install, tmp_path, monkeypatch):
    binding = profile_install['binding']
    with bindings.execution_directory(tmp_path):
        retained = retained_binding(bindings.resolve(binding))
    descriptors = {bindings.key(binding): {'execution': retained}}
    original, observations = profiles.resolve_profile, []
    def resolve(*args):
        observations.append(args)
        return original(*args)
    monkeypatch.setattr(profiles, 'resolve_profile', resolve)
    def run(argv, **kwargs):
        Path(argv[argv.index('-o') + 1]).write_text('{}')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(executors, 'run_provider', run)
    with bindings.pinned_bindings(descriptors, cwd=tmp_path):
        bindings.resolve(binding)
        assert executors.execute_process('Review', tmp_path, 'codex', {'DK_HOST': 'codex'}, retained, 0, 10) == '{}'
        assert len(observations) == 3  # Entry plus explicit pre-launch/post-result.
        profile_install['profile'].write_text('model="changed-during-guard"\n')
        assert bindings.resolve(binding) == retained  # Same short observation only.
        with pytest.raises(ValueError, match='execution settings changed'):
            profiles.verify_profile(retained, tmp_path)
        with pytest.raises(ValueError, match='execution settings changed'):
            with bindings.pinned_bindings(descriptors, cwd=tmp_path):
                pass


def test_complete_native_api_baseline_values_and_origins_survive_omitted_packaged_layers(profile_install, tmp_path, monkeypatch):
    profile_install['profile'].write_text('model_reasoning_effort="high"\n')
    response, agent = profile_install['read_config'](tmp_path)
    response['config']['model_context_window'] = 14000
    origin = {'name': {'type': 'packagedDefaults', 'file': '/native/defaults.toml'}, 'version': 'native-defaults-v1'}
    response['origins']['model_context_window'] = origin
    monkeypatch.setattr(profiles, 'read_config', lambda *args, **kwargs: (response, agent))
    value, _ = profiles.selected_layers('review', tmp_path)
    assert value['settings']['model_context_window'] == 14000
    assert value['origins']['model_context_window'] == origin


@pytest.mark.skipif(os.name != 'posix', reason='owned POSIX process group')
@pytest.mark.parametrize('exit_code', [0, 23, -signal.SIGTERM])
def test_native_holder_reports_native_status_and_signals_before_reaping(tmp_path, monkeypatch, exit_code):
    original_spawn, original_group = profiles.subprocess.Popen, profiles.os.killpg
    events, launched = [], []
    class Owned:
        def __init__(self, process):
            self.process = process
        def __getattr__(self, name):
            return getattr(self.process, name)
        def poll(self):
            pytest.fail('holder was polled before group cleanup')
        def terminate(self):
            pytest.fail('terminate may reap before group cleanup')
        def wait(self, **kwargs):
            assert events and events[-1] == ('group', self.process.pid)
            events.append(('wait', self.process.pid))
            return self.process.wait(**kwargs)
    def spawn(argv, **kwargs):
        assert argv[:4] == [sys.executable, '-I', '-S', '-c']
        process = original_spawn(argv, **kwargs)
        launched.append(process)
        return Owned(process)
    def group(pid, sig):
        events.append(('group', pid))
        return original_group(pid, sig)
    monkeypatch.setattr(profiles.subprocess, 'Popen', spawn)
    monkeypatch.setattr(profiles.os, 'killpg', group)
    code = 'import sys;sys.exit(' + str(exit_code) + ')' if exit_code >= 0 else (
        'import os,signal;os.kill(os.getpid(),signal.SIGTERM)')
    result = profiles.run_native([sys.executable, '-I', '-S', '-c', code], tmp_path, subprocess.DEVNULL)
    assert result.returncode == exit_code and result.args == []
    assert launched[0].returncode == -signal.SIGKILL
    assert events == [('group', launched[0].pid), ('wait', launched[0].pid)]


@pytest.mark.skipif(os.name != 'posix', reason='owned POSIX helper status protocol')
@pytest.mark.parametrize('packet,expected', [(b'invalid\n', 'malformed'), (b'9' * 40 + b'\n', 'bound'), (b'', 'unavailable')])
def test_native_holder_rejects_bounded_status_failures_and_drains(tmp_path, monkeypatch, packet, expected):
    code = ('import os,sys;status,control=map(int,sys.argv[1:3]);'
            'os.write(status,' + repr(packet) + ');os.close(status);os.read(control,1)')
    monkeypatch.setattr(profiles, '_NATIVE_HOLDER', code)
    original, processes = profiles.subprocess.Popen, []
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(profiles.subprocess, 'Popen', spawn)
    with pytest.raises(ValueError, match='Native Codex.*' + expected):
        profiles.run_native([sys.executable, '-c', 'raise SystemExit(0)'], tmp_path, subprocess.DEVNULL)
    assert processes[0].returncode == -signal.SIGKILL


@pytest.mark.skipif(os.name != 'posix', reason='owned POSIX helper startup')
def test_native_holder_missing_executable_is_unavailable_not_status_127(tmp_path):
    with pytest.raises(ValueError, match='exit status unavailable'):
        profiles.run_native([str(tmp_path / 'missing-native')], tmp_path, subprocess.DEVNULL)


@pytest.mark.skipif(os.name != 'posix' or not Path('/proc').exists(), reason='controlled Linux FD inspection')
def test_native_holder_preserves_context_and_does_not_inherit_protocol_descriptors(tmp_path):
    output = tmp_path / 'native-output.json'
    code = ('import json,os,pathlib,sys; '
            'parent=pathlib.Path("/proc")/str(os.getppid())/"cmdline"; '
            'args=parent.read_bytes().split(b"\\0"); '
            'private=list(map(int,args[5:7])); '
            'opened=[]; '
            '\nfor fd in private:\n'
            ' try:os.fstat(fd);opened.append(True)\n'
            ' except OSError:opened.append(False)\n'
            'print(json.dumps({"cwd":os.getcwd(),"value":os.environ["TEST_NATIVE_VALUE"],'
            '"argv":sys.argv[1:],"private_open":opened}))')
    env = dict(os.environ, TEST_NATIVE_VALUE='SENTINEL_ENV_ONLY')
    # Startup hooks and a cwd-local subprocess module must not enter the holder.
    (tmp_path / 'subprocess.py').write_text('raise RuntimeError("untrusted holder module")')
    (tmp_path / 'sitecustomize.py').write_text('raise RuntimeError("untrusted startup hook")')
    with output.open('wb') as stream:
        result = profiles.run_native([sys.executable, '-I', '-S', '-c', code, 'literal arg'], tmp_path, stream, env=env)
    assert result.returncode == 0
    assert json.loads(output.read_text()) == {'cwd': str(tmp_path), 'value': 'SENTINEL_ENV_ONLY',
                                           'argv': ['literal arg'], 'private_open': [False, False]}


@pytest.mark.skipif(os.name != 'posix', reason='owned POSIX timeout cleanup')
def test_native_holder_timeout_kills_active_native_child_without_reaping_first(tmp_path, monkeypatch):
    original_spawn, original_selector = profiles.subprocess.Popen, profiles.selectors.DefaultSelector
    launched = []
    def spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        launched.append(process)
        return process
    class Timeout:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def register(self, *args): pass
        def select(self, timeout):
            with original_selector() as readiness:
                readiness.register(launched[0].stdout, profiles.selectors.EVENT_READ)
                assert readiness.select(3), 'controlled native never became ready'
                assert launched[0].stdout.readline() == b'ready\n'
            return []
    monkeypatch.setattr(profiles.subprocess, 'Popen', spawn)
    monkeypatch.setattr(profiles.selectors, 'DefaultSelector', Timeout)
    try:
        with pytest.raises(subprocess.TimeoutExpired) as failure:
            profiles.run_native([sys.executable, '-I', '-S', '-c',
                'import time;print("ready",flush=True);time.sleep(120)'], tmp_path, subprocess.PIPE)
        assert failure.value.cmd == []
        assert launched[0].returncode == -signal.SIGKILL
    finally:
        for process in launched:
            if process.stdout is not None:
                process.stdout.close()


@pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open') or
                    not hasattr(signal, 'pidfd_send_signal'), reason='controlled Linux process exit notification')
def test_holder_control_eof_stops_an_active_native_child(tmp_path):
    status_read, status_write = os.pipe()
    control_read, control_write = os.pipe()
    process, native_fd = None, None
    try:
        code = 'import os,time;print(os.getpid(),flush=True);time.sleep(120)'
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', profiles._NATIVE_HOLDER,
            str(status_write), str(control_read), sys.executable, '-I', '-S', '-c', code],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            pass_fds=(status_write, control_read), close_fds=True)
        os.close(status_write)
        status_write = None
        os.close(control_read)
        control_read = None
        assert select.select([process.stdout], [], [], 3)[0], 'controlled child did not start'
        native_fd = os.pidfd_open(int(process.stdout.readline()))
        os.close(control_write)  # Simulate loss of the owning parent while native is active.
        control_write = None
        assert process.wait(timeout=3) == -signal.SIGKILL
        assert select.select([native_fd], [], [], 3)[0], 'native child survived owner loss'
        assert os.read(status_read, 32) == b''  # No fabricated native success status.
    finally:
        if process is not None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)  # Still unreaped/owned here.
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            if process.stdout is not None:
                process.stdout.close()
        if native_fd is not None:
            try:
                signal.pidfd_send_signal(native_fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(native_fd)
        for descriptor in (status_read, status_write, control_read, control_write):
            if descriptor is not None:
                os.close(descriptor)


def test_short_profile_observation_preserves_shared_cohort_binding_map(profile_install, tmp_path):
    binding = profile_install['binding']
    with bindings.execution_directory(tmp_path):
        retained = retained_binding(bindings.resolve(binding))
    descriptors = {bindings.key(binding): {'execution': retained}}
    with bindings.pinned_bindings(descriptors, cwd=tmp_path, cache_profiles=False):
        cohort = bindings._PINNED_BINDINGS.get()
        with bindings.profile_observation(descriptors, cwd=tmp_path):
            assert bindings._PINNED_BINDINGS.get() is cohort
            assert bindings.resolve(binding) == retained
        assert bindings._PINNED_BINDINGS.get() is cohort
