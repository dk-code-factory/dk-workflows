"""Owned role transport uses real retained lifecycle and invocation bookkeeping."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
from contextlib import contextmanager

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import architecture, invocations, runtime_bundles


@contextmanager
def parent_lifecycle(root, host):
    updater = runtime_bundles.updater()
    parent = updater.lifecycle(root, host, 'entry')
    try:
        yield parent
    finally:
        updater.lifecycle(root, host, 'complete', token=parent['token'])


@pytest.fixture
def owned_role(tmp_path, monkeypatch):
    for name in ('DK_UPDATE_PARENT', 'DK_UPDATE_MANAGED_RECEIPT', 'DK_MANAGED_INVOCATION',
                 'DK_ARCHITECTURE_PACKET', 'DK_ARCHITECTURE_PACKETS', 'DK_ARCHITECTURE_AGENT',
                 'DK_BINDING_STACK', 'DK_REVIEW_STATS_TRACE', 'DK_CLI_REFERENCE'):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / 'config'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
    settings = config / 'dk-code-factory/updates.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{"mode":"off"}')
    repository = tmp_path / 'repository'
    repository.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.test')
    (repository / 'core.py').write_text('core = 1\n')
    git('add', '.')
    git('commit', '-qm', 'Baseline')
    source = {'text': 'Core entry point.', 'origin': 'inferred', 'rationale': 'Inspected source.',
              'sources': [{'path': 'core.py', 'start': 1, 'end': 1}]}
    architecture.ensure(repository, analyzer=lambda *_: {
        'knowledge': {key: [source] for key in architecture.KNOWLEDGE},
        'rules': [], 'proposals': [], 'conflicts': []})
    root = runtime_bundles.retain(Path(__file__).resolve().parents[1])
    spec = importlib.util.spec_from_file_location('retained_owned_executors', root / 'runtime/shared/executors.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def packet(role):
        return architecture.packet(repository, role, ['core.py'], task='role-fixture',
                                   baseline=git('rev-parse', 'HEAD'))
    def state():
        return json.loads((Path(os.environ['XDG_STATE_HOME']) /
                           'dk-code-factory/updates/state.json').read_text())
    return module, root, repository, packet, state


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('role', ['dk-architecture-review', 'dk-architecture-enforcer'])
def test_owned_role_pins_caller_and_manages_real_lifecycle(owned_role, monkeypatch, tmp_path, host, role):
    module, root, repository, packet, state = owned_role
    monkeypatch.setenv('DK_HOST', host)
    monkeypatch.setenv('DK_ANALYSIS_HOST', 'invalid-for-explicit-roles')
    # A replacement installation cannot supply the role or its version.
    replacement = tmp_path / 'replacement'
    replacement.mkdir()
    registry = tmp_path / 'host'
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(registry))
    monkeypatch.setenv('CODEX_HOME', str(registry))
    (registry / 'plugins').mkdir(parents=True)
    (registry / 'plugins/installed_plugins.json').write_text(json.dumps({'plugins': {
        'dk-workflows@new': [{'installPath': str(replacement), 'version': '99.0.0'}]}}))
    with parent_lifecycle(root, host) as parent:
        monkeypatch.setenv('DK_UPDATE_PARENT', parent['token'])
        calls = []
        def provider(argv, **kwargs):
            env, prompt = kwargs['env'], kwargs['input']
            receipt = invocations.load(env['DK_MANAGED_INVOCATION'])
            assert receipt['runtime_root'] == str(root) and receipt['skill'] == role and receipt['managed']
            lifecycle = json.loads(env['DK_UPDATE_MANAGED_RECEIPT'])
            assert lifecycle['identity']['version'] != '99.0.0'
            assert lifecycle['identity']['version'] == json.loads((root / '.codex-plugin/plugin.json').read_text())['version']
            assert lifecycle['token'] == receipt['token'] == env['DK_UPDATE_PARENT']
            assert state()['invocations'][receipt['token']]['parent'] == parent['token']
            assert not state()['invocations'][receipt['token']]['complete']
            assert 'DK_ANALYSIS_HOST' not in env
            assert json.loads(env['DK_BINDING_STACK']) == ['dk-workflows:' + role]
            assert f'procedure at {root}/runtime/{role}/evaluator.md' in prompt
            assert 'SKILL.md' not in prompt and 'executor owns lifecycle entry and completion' in prompt
            assert architecture.load(json.loads(env['DK_ARCHITECTURE_PACKET']))['agent'] == role
            if host == 'codex':
                assert argv[argv.index('--sandbox') + 1] == 'read-only'
                Path(argv[argv.index('-o') + 1]).write_text('{"accepted":true}')
            else:
                assert argv[argv.index('--permission-mode') + 1] == 'plan'
                assert argv[argv.index('--plugin-dir') + 1] == str(root)
            calls.append(receipt)
            return subprocess.CompletedProcess(argv, 0,
                json.dumps({'subtype': 'success', 'result': '{"accepted":true}'}), '')
        monkeypatch.setattr(module, 'run_provider', provider)
        assert module.execute_architecture_role('Evaluate evidence.', repository, role=role,
                                               architecture_packet=packet(role)) == '{"accepted":true}'
        assert len(calls) == 1 and state()['invocations'][calls[0]['token']]['complete']
        assert not state()['invocations'][parent['token']]['complete']
        assert invocations.end(calls[0]['invocation'])['status'] == 'executor_managed'
    assert state()['invocations'][parent['token']]['complete']


@pytest.mark.parametrize('failure', ['provider', 'assertion'])
def test_manually_owned_parent_finalizes_when_child_or_test_fails(owned_role, monkeypatch, failure):
    module, root, repository, packet, state = owned_role
    monkeypatch.setenv('DK_HOST', 'codex')
    role = 'dk-architecture-review'
    def provider(argv, **kwargs):
        if failure == 'provider':
            raise RuntimeError('synthetic provider failure')
        Path(argv[argv.index('-o') + 1]).write_text('{"accepted":true}')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(module, 'run_provider', provider)
    with pytest.raises((AssertionError, RuntimeError), match='synthetic'):
        with parent_lifecycle(root, 'codex') as parent:
            monkeypatch.setenv('DK_UPDATE_PARENT', parent['token'])
            module.execute_architecture_role('Evaluate.', repository, role=role, architecture_packet=packet(role))
            raise AssertionError('synthetic assertion after child completion')
    records = state()['invocations']
    assert len(records) == 2 and all(value['complete'] for value in records.values())
    child, = [value for token, value in records.items() if token != parent['token']]
    assert child['parent'] == parent['token']
    assert all(not session['active'] for session in state()['sessions'].values())


@pytest.mark.parametrize('failure', ['provider', 'cancel', 'setup'])
def test_owned_role_completes_lifecycle_after_failures(owned_role, monkeypatch, failure):
    module, root, repository, packet, state = owned_role
    monkeypatch.setenv('DK_HOST', 'codex')
    role = 'dk-architecture-review'
    def provider(*args, **kwargs):
        if failure == 'cancel':
            raise module.ProviderCancelled('interrupted transport')
        return subprocess.CompletedProcess(args[0], 1, '', 'transport failed')
    transport = Mock(side_effect=provider)
    monkeypatch.setattr(module, 'run_provider', transport)
    if failure == 'setup':
        monkeypatch.setattr(invocations, 'begin', Mock(side_effect=OSError('setup failed')))
    with pytest.raises((RuntimeError, OSError), match='failed|interrupted'):
        module.execute_architecture_role('Evaluate.', repository, role=role, architecture_packet=packet(role))
    records = list(state()['invocations'].values())
    assert len(records) == 1 and records[0]['complete']
    assert all(not session['active'] for session in state()['sessions'].values())
    assert transport.call_count == (0 if failure == 'setup' else 1)


@pytest.mark.parametrize('failure', ['unknown', 'missing', 'recipient', 'fingerprint', 'recursive', 'tampered'])
def test_owned_role_invalid_delivery_never_enters_lifecycle(owned_role, monkeypatch, failure):
    module, root, repository, packet, state = owned_role
    monkeypatch.setenv('DK_HOST', 'codex')
    role = 'dk-architecture-review'
    reference = packet(role)
    if failure == 'unknown':
        role = '../dk-architecture-review'
    elif failure == 'missing':
        reference = None
    elif failure == 'recipient':
        reference = packet('worker')
    elif failure == 'fingerprint':
        reference = dict(reference, fingerprint='0' * 64)
    elif failure == 'recursive':
        monkeypatch.setenv('DK_BINDING_STACK', json.dumps(['dk-workflows:' + role]))
    elif failure == 'tampered':
        procedure = root / 'runtime/dk-architecture-review/evaluator.md'
        procedure.chmod(0o600)
        procedure.write_text('changed retained evaluator')
    transport = Mock()
    monkeypatch.setattr(module, 'run_provider', transport)
    with pytest.raises((ValueError, RuntimeError)):
        module.execute_architecture_role('Evaluate.', repository, role=role, architecture_packet=reference)
    transport.assert_not_called()
    lifecycle_file = Path(os.environ['XDG_STATE_HOME']) / 'dk-code-factory/updates/state.json'
    assert not lifecycle_file.exists()


@pytest.mark.parametrize('failure', ['identity', 'repository', 'skill', 'evaluator', 'escape'])
def test_owned_role_rejects_invalid_caller_package(owned_role, monkeypatch, tmp_path, failure):
    module, _, repository, packet, _ = owned_role
    monkeypatch.setenv('DK_HOST', 'codex')
    role = 'dk-architecture-review'
    package = tmp_path / 'caller-package'
    manifest = package / '.codex-plugin/plugin.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        'name': 'other-plugin' if failure == 'identity' else 'dk-workflows', 'version': '1.0.0',
        'repository': 'https://example.test/unowned' if failure == 'repository'
                      else 'https://github.com/dk-code-factory/dk-workflows',
        'skills': './codex/skills/'}))
    skill = package / 'codex/skills' / role / 'SKILL.md'
    skill.parent.mkdir(parents=True)
    if failure != 'skill':
        skill.write_text('Packaged entry point.')
    procedure = package / 'runtime' / role / 'evaluator.md'
    procedure.parent.mkdir(parents=True)
    if failure == 'escape':
        outside = tmp_path / 'outside.md'
        outside.write_text('Unowned procedure.')
        procedure.symlink_to(outside)
    elif failure != 'evaluator':
        procedure.write_text('Read-only evaluator.')
    monkeypatch.setattr(module, '__file__', str(package / 'runtime/shared/executors.py'))
    transport = Mock()
    monkeypatch.setattr(module, 'run_provider', transport)
    with pytest.raises((ValueError, RuntimeError)):
        module.execute_architecture_role('Evaluate.', repository, role=role, architecture_packet=packet(role))
    transport.assert_not_called()
    lifecycle_file = Path(os.environ['XDG_STATE_HOME']) / 'dk-code-factory/updates/state.json'
    assert not lifecycle_file.exists()
