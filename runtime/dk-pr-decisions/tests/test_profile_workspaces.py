"""Actual profile evidence follows preparation's owned checkout, entirely offline."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, analysis, bind_agreements, prep, response
from shared import bindings, codex_profiles as profiles


@pytest.fixture
def profiled_package(tmp_path, monkeypatch):
    # These source tests own isolated stores, rather than the enclosing workflow.
    monkeypatch.delenv('DK_UPDATE_PARENT', raising=False)
    monkeypatch.delenv('DK_UPDATE_MANAGED_RECEIPT', raising=False)
    monkeypatch.setenv('DK_HOST', 'codex')
    for key in tuple(os.environ):
        if key.startswith(('CODEX_', 'OPENAI_', 'AWS_', 'GOOGLE_')):
            monkeypatch.delenv(key)
    package = Package(tmp_path)
    project = package.repo / '.codex'
    project.mkdir()
    (project / 'config.toml').write_text(
        'model = "fixture-model"\nmodel_instructions_file = "instructions.md"\n')
    (project / 'first.md').write_text('Review the supported transform.\n')
    (project / 'second.md').write_bytes((project / 'first.md').read_bytes())
    (project / 'instructions.md').symlink_to('first.md')
    prep.git(package.repo, 'add', '.')
    prep.git(package.repo, 'commit', '-qm', 'Tracked project profile inputs')
    package.head = prep.git(package.repo, 'rev-parse', 'HEAD')
    package.context.update(head_sha=package.head, base_sha=package.head)
    package.decisions['head_sha'] = package.head
    bind_agreements(package.context, package.decisions)
    package.write('context.json', package.context)
    package.write('decisions.json', package.decisions)

    home = tmp_path / 'codex'
    home.mkdir()
    (home / 'config.toml').write_text('model_reasoning_effort = "medium"\n')
    (home / 'review.config.toml').write_text('model = "fixture-model"\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    provider = tmp_path / 'provider'
    (provider / '.codex-plugin').mkdir(parents=True)
    (provider / 'skills/review').mkdir(parents=True)
    (provider / 'skills/review/SKILL.md').write_text('Review the pinned changes read-only.')
    (provider / '.codex-plugin/plugin.json').write_text(json.dumps({
        'name': 'provider', 'version': '1.0.0', 'skills': './skills/'}))
    monkeypatch.setattr(bindings.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(bindings, 'codex_plugins', lambda: [{
        'name': 'provider', 'pluginId': 'provider@test',
        'installedPath': str(provider), 'version': '1.0.0'}])
    state = {'disabled': False, 'calls': []}

    def read_config(cwd, overrides=None, **kwargs):
        cwd = Path(cwd).resolve()
        state['calls'].append(cwd)
        config_file = cwd / '.codex/config.toml'
        config = tomllib.loads(config_file.read_text())
        # Native file loading normalizes AbsolutePathBuf before hashing a layer.
        config['model_instructions_file'] = str(profiles.native_path(
            config['model_instructions_file'], config_file.parent))
        project_layer = {'name': {'type': 'project', 'dotCodexFolder': str(config_file.parent)},
                         'config': config, 'version': 'sha256:' + profiles.fingerprint(config)}
        if state['disabled']:
            project_layer['disabledReason'] = 'project is untrusted'
        base_file = home / 'config.toml'
        base = {'name': {'type': 'user', 'file': str(base_file)},
                'config': tomllib.loads(base_file.read_text()),
                'version': 'sha256:' + hashlib.sha256(base_file.read_bytes()).hexdigest()}
        effective = profiles.merge(base['config'], {} if state['disabled'] else config)
        origins = {key: {field: layer[field] for field in ('name', 'version')}
                   for layer in (base, project_layer) if not layer.get('disabledReason')
                   for key in layer['config']}
        return {'config': effective, 'origins': origins,
                'layers': [project_layer, base], '_requirements': {}}, 'codex_cli_rs/0.164.1'

    monkeypatch.setattr(profiles, 'read_config', read_config)
    monkeypatch.setattr(profiles, 'run_native', lambda argv, cwd, *args, **kwargs:
                        subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(profiles, 'read_catalog', lambda *args, **kwargs: {'models': [{
        'slug': 'fixture-model', 'context_window': 10000, 'max_context_window': 20000,
        'default_reasoning_level': 'medium', 'effective_context_window_percent': 95}]})
    launch = Mock(side_effect=AssertionError('offline fixture launched a model'))
    monkeypatch.setattr(prep, 'analyze', launch)
    from shared import executors
    monkeypatch.setattr(executors, 'execute', launch)
    binding = {'skill': 'provider:review', 'host': 'codex', 'codex_profile': 'review'}
    with bindings.execution_directory(package.repo):
        descriptor = prep_runner().describe_reviewer(binding)
    # Unknown path-looking strings are governing prose, not relocation fields.
    descriptor['future_path'] = str(package.repo / 'opaque-location')
    descriptor['effective_prompt'] += '\nKeep literal ' + str(package.repo / 'opaque-instruction')
    package.write('reviewer.json', descriptor)
    return package, binding, descriptor, state, launch


def prep_runner():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'dk-pr-review/scripts/run_reviewers.py'
    spec = importlib.util.spec_from_file_location('workspace_profile_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_binds_profile_before_analysis_and_retains_both_sources(profiled_package, monkeypatch):
    package, binding, original, state, launch = profiled_package
    name = bindings.key(binding)
    original_bytes = (package.root / 'reviewer.json').read_bytes()
    seen = []
    architecture_prepare = prep.architecture.prepare
    graph_inputs = prep.graph_api.inputs

    def check_sources(sources):
        source = sources['reviewer-execution/0']
        path = Path(source['path'] if isinstance(source, dict) else source)
        derived = prep.read(path)
        folder = derived['execution']['codex_execution']['layers'][0]['name']['dotCodexFolder']
        assert folder == str(path.parent.parent / 'checkout/.codex')
        assert path.is_relative_to(package.versions)
        seen.append(derived)

    def architecture(context, decisions, **kwargs):
        # Capture must precede architecture; it cannot be a post-analysis patch.
        captures = list(package.versions.glob('preparation-*/reviewer-execution/0.json'))
        assert captures
        for path in captures:
            check_sources({'reviewer-execution/0': path})
        return architecture_prepare(context, decisions, **kwargs)

    def inputs(context, sources):
        check_sources(sources)
        return graph_inputs(context, sources)

    monkeypatch.setattr(prep.architecture, 'prepare', architecture)
    monkeypatch.setattr(prep.graph_api, 'inputs', inputs)

    def analyze(prompt, checkout):
        index = json.loads(prompt.split('\n\nINPUT INDEX (read original paths):\n', 1)[1])
        assert index['reviewers'][name] in seen
        assert 'reviewer-execution/0' in index['sources']
        return response(analysis((name,)))

    saved = prep.prepare(package.manifest_path, package.versions, analyzer=analyze)
    artifact = prep.read(saved['artifact'])
    clone = Path(artifact['workspace'])
    derived = artifact['reviewers'][name]
    before = original['execution']['codex_execution']
    after = derived['execution']['codex_execution']
    assert before['layers'][0]['version'] != after['layers'][0]['version']
    assert before['setting_sources']['model_instructions_file']['path'] != after['setting_sources']['model_instructions_file']['path']
    assert before['effective_settings'] == after['effective_settings']
    assert derived['future_path'] == original['future_path']
    assert derived['effective_prompt'].split('\n', 1)[1] == original['effective_prompt'].split('\n', 1)[1]
    association = artifact['sources']['reviewer-execution/0']
    assert association['reviewer'] == name
    assert association['derived_from'] == artifact['sources']['reviewers/0']['origin_identity']
    assert Path(prep.source_path(artifact, 'reviewers/0')).read_bytes() == original_bytes
    assert prep.read(prep.source_path(artifact, 'reviewer-execution/0')) == derived
    for key in ('reviewers/0', 'reviewer-execution/0'):
        assert not Path(prep.source_path(artifact, key)).stat().st_mode & 0o222
    assert (package.root / 'reviewer.json').read_bytes() == original_bytes
    assert prep.context_for(artifact)['checkout'] == str(clone)
    assert prep.read(artifact['recovery_context']['path'])['checkout'] == str(clone)
    collected = prep.collect(artifact['recovery_manifest']['path'])
    assert collected[1]['checkout'] == str(clone)
    assert collected[4] == artifact['reviewers']
    recovered, context, decisions, _ = prep.retained(saved['artifact'])
    assert context['checkout'] == str(clone)
    state['calls'].clear()
    prep.validate(recovered, context, decisions)
    assert state['calls'] and set(state['calls']) == {clone}
    with pytest.raises(ValueError, match='execution settings changed'):
        profiles.verify_profile(original['execution'], clone)

    repeated = prep.prepare(package.manifest_path, package.versions, analyzer=analyze)
    assert repeated['engineering_id'] == saved['engineering_id']
    assert prep.read(repeated['artifact'])['workspace'] != str(clone)
    changed = copy.deepcopy(original)
    changed['future_path'] = str(package.repo / 'different-opaque-location')
    package.write('reviewer.json', changed)
    unknown = prep.prepare(package.manifest_path, package.versions, analyzer=analyze)
    assert unknown['engineering_id'] != saved['engineering_id']
    launch.assert_not_called()


@pytest.mark.parametrize('change', ['content', 'trust', 'reference-target'])
def test_actual_workspace_profile_drift_rejects_before_provider(profiled_package, change):
    package, binding, original, state, launch = profiled_package
    saved = prep.prepare(package.manifest_path, package.versions,
                         analyzer=lambda *_: response(analysis((bindings.key(binding),))))
    artifact = prep.read(saved['artifact'])
    clone = Path(artifact['workspace'])
    if change == 'content':
        (clone / '.codex/first.md').write_text('Changed governing instruction.\n')
    elif change == 'trust':
        state['disabled'] = True
    else:
        reference = clone / '.codex/instructions.md'
        reference.unlink()
        reference.symlink_to('second.md')
        previous = artifact['reviewers'][bindings.key(binding)]['execution']['codex_execution']
        current = profiles.resolve_profile('review', clone)
        old_source = previous['setting_sources']['model_instructions_file']
        new_source = current['setting_sources']['model_instructions_file']
        assert old_source['sha256'] == new_source['sha256']
        assert old_source['path'] == new_source['path']
        assert old_source['resolved_path'] != new_source['resolved_path']
    with pytest.raises(ValueError, match='execution settings changed'):
        prep.validate(artifact, prep.context_for(artifact), package.decisions)
    launch.assert_not_called()


@pytest.mark.parametrize('source', ['supplied', 'derived'])
def test_analyzer_descriptor_mutation_rejects_and_cleans_candidate(profiled_package, source):
    package, binding, original, state, launch = profiled_package
    before = set(package.versions.iterdir())
    candidates = []

    def mutate(prompt, checkout):
        index = json.loads(prompt.split('\n\nINPUT INDEX (read original paths):\n', 1)[1])
        derived_path = Path(index['sources']['reviewer-execution/0']['path'])
        candidates.append(derived_path.parent.parent)
        target = package.root / 'reviewer.json' if source == 'supplied' else derived_path
        descriptor = prep.read(target)
        descriptor['effective_prompt'] += '\nChanged governing instruction during analysis.'
        target.write_text(json.dumps(descriptor))
        return response(analysis((bindings.key(binding),)))

    with pytest.raises(ValueError, match='supplied descriptor changed during preparation'):
        prep.prepare(package.manifest_path, package.versions, analyzer=mutate)
    assert len(candidates) == 1 and not candidates[0].exists()
    assert set(package.versions.iterdir()) == before
    assert not list(package.versions.rglob('preparation.json'))
    launch.assert_not_called()


def test_unchanged_profile_refresh_validates_parent_and_discards_candidate(profiled_package, monkeypatch):
    package, binding, original, state, launch = profiled_package
    first = prep.prepare(package.manifest_path, package.versions,
                         analyzer=lambda *_: response(analysis((bindings.key(binding),))))
    parent = prep.read(first['artifact'])
    retained = Path(first['artifact']).read_bytes()
    before = set(package.versions.iterdir())
    candidates, validated = [], []
    collect, validate = prep.collect, prep.validate

    def collecting(*args, **kwargs):
        if kwargs.get('workspace') is not None:
            candidates.append(Path(kwargs['workspace']).parent)
        return collect(*args, **kwargs)

    def validating(value, context, decisions, **kwargs):
        if value.get('preparation_id') == parent['preparation_id'] and kwargs.get('check_sources', True):
            validated.append(context['checkout'])
        return validate(value, context, decisions, **kwargs)

    monkeypatch.setattr(prep, 'collect', collecting)
    monkeypatch.setattr(prep, 'validate', validating)
    analyzer = Mock(side_effect=AssertionError('unchanged preparation reran analysis'))
    refreshed = prep.prepare(package.manifest_path, package.versions, analyzer=analyzer,
                             previous_preparation=first['artifact'])
    assert refreshed['artifact'] == first['artifact']
    assert refreshed['refresh']['mode'] == 'unchanged'
    assert validated and set(validated) == {parent['workspace']}
    assert candidates and all(not candidate.exists() for candidate in candidates)
    assert set(package.versions.iterdir()) == before
    assert Path(parent['workspace']).is_dir()
    assert Path(first['artifact']).read_bytes() == retained
    analyzer.assert_not_called()
    launch.assert_not_called()


def test_symlinked_state_retains_success_and_unchanged_parent(profiled_package, monkeypatch, tmp_path):
    package, binding, original, state, launch = profiled_package
    state_root = Path(os.environ['XDG_STATE_HOME']).resolve()
    state_alias = tmp_path / 'state-alias'
    state_alias.symlink_to(state_root, target_is_directory=True)
    monkeypatch.setenv('XDG_STATE_HOME', str(state_alias))
    # Exercise fresh allocation through the symlinked state directory, rather
    # than a preselected workflow directory already normalized by owned().
    package.context.pop('workflow_dir')
    package.write('context.json', package.context)
    saved = prep.prepare(package.manifest_path, package.versions,
                         analyzer=lambda *_: response(analysis((bindings.key(binding),))))
    artifact_path = Path(saved['artifact'])
    artifact = prep.read(artifact_path)
    workspace = Path(artifact['workspace'])
    assert workspace.is_dir() and workspace == workspace.resolve()
    for key in ('reviewers/0', 'reviewer-execution/0'):
        assert Path(prep.source_path(artifact, key)).is_file()
    prep.validate(artifact, prep.context_for(artifact), package.decisions)
    workflow = Path(artifact['workflow_dir'])
    before = set(workflow.iterdir())
    retained_bytes = artifact_path.read_bytes()
    analyzer = Mock(side_effect=AssertionError('unchanged preparation reran analysis'))
    refreshed = prep.prepare(package.manifest_path, package.versions, analyzer=analyzer,
                             previous_preparation=artifact_path)
    assert refreshed['artifact'] == saved['artifact']
    assert refreshed['refresh']['mode'] == 'unchanged'
    assert artifact_path.read_bytes() == retained_bytes
    assert workspace.is_dir() and set(workflow.iterdir()) == before
    analyzer.assert_not_called()
    launch.assert_not_called()
