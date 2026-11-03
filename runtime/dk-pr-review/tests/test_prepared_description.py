"""Prepared inspection validates real retained bindings without providers or registry IO."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

from test_stats_capture import pinned, runner, prepare_current
from shared import runtime_bundles
from shared.private_state import write


DESCRIBE = runner.describe_reviewer


@pytest.fixture
def prepared_description(pinned, tmp_path, monkeypatch, capsys, request):
    context, decisions, preparation, names, git = pinned
    plugin = tmp_path / 'plugin'
    (plugin / '.codex-plugin').mkdir(parents=True)
    write(plugin / '.codex-plugin/plugin.json', {'name': 'fixture', 'version': '1.0.0'})
    (plugin / 'skills/review').mkdir(parents=True)
    (plugin / 'skills/review/SKILL.md').write_text('Review the pinned diff.\n')
    (plugin / 'resource.json').write_text('{"policy": "original"}\n')
    script = plugin / 'review.py'
    script.write_text('raise AssertionError("description must never execute a reviewer")\n')
    original_which = shutil.which
    monkeypatch.setattr(runner.bindings.shutil, 'which',
                        lambda name: sys.executable if name == 'codex' else original_which(name))
    registry = Mock(return_value=[{'name': 'fixture', 'pluginId': 'fixture@local',
        'installedPath': str(plugin), 'version': '1.0.0', 'enabled': True}])
    monkeypatch.setattr(runner.bindings, 'codex_plugins', registry)
    monkeypatch.setattr(runner, 'describe_reviewer', DESCRIBE)
    providers = [{'skill': 'fixture:review', 'host': 'current'},
                 {'command': [sys.executable, str(script), '{handoff}']}]
    if getattr(request, 'param', None) == 'profiled':
        from shared import codex_profiles
        providers[0]['codex_profile'] = 'review'
        monkeypatch.setattr(codex_profiles, 'resolve_profile', lambda profile, cwd=None: {
            'profile': profile, 'settings': {'model': 'fixture-model'},
            'identity_evidence': {'workspace': str(Path(cwd or Path.cwd()).resolve())}})
    names[:] = [runner.bindings.key(provider) for provider in providers]
    preparation['reviewers'] = {name: DESCRIBE(provider) for name, provider in zip(names, providers)}
    preparation['analysis']['reviewer_addenda'] = {name: 'Review the pinned diff.' for name in names}
    prepare_current(context, decisions, preparation)
    path = tmp_path / 'preparation.json'
    real_run = runner.run
    configured = copy.deepcopy(providers)

    def run(args, cwd=None):
        if args[:2] == ['python3', str(runner.ROOT / 'dk-config/scripts/repo_rules.py')]:
            return json.dumps({'reviewers': configured})
        return real_run(args, cwd)

    monkeypatch.setattr(runner, 'run', run)
    forbidden = []
    for field in ('Session', 'run_skill_reviewer', 'for_iteration', 'review', 'invoke'):
        guard = Mock(side_effect=AssertionError('inspection launched ' + field))
        forbidden.append(guard)
        monkeypatch.setattr(runner, field, guard)
    registry.reset_mock()
    registry.side_effect = subprocess.TimeoutExpired(['codex', 'plugin', 'list', '--json'], 30)

    def invoke(mode='--describe-prepared', *options, with_preparation=True, context_file=None,
               preparation_file=None):
        write(path, preparation)
        argv = ['run_reviewers.py', '--context',
                str(context_file or runner.preparation_api.source_path(preparation, 'context')), mode]
        if with_preparation:
            argv += ['--preparation-file', str(preparation_file or path)]
        monkeypatch.setattr(sys, 'argv', [*argv, *map(str, options)])
        try:
            runner.main()
        finally:
            for guard in forbidden:
                guard.assert_not_called()
        return json.loads(capsys.readouterr().out)

    return context, decisions, preparation, configured, registry, invoke


def test_prepared_description_uses_exact_native_and_command_bindings(prepared_description):
    context, decisions, preparation, providers, registry, invoke = prepared_description
    # Neither the original installation nor its command script is required.
    shutil.rmtree(Path(providers[1]['command'][1]).parent)
    result = invoke()
    assert result == {'reviewers': [preparation['reviewers'][name] for name in preparation['reviewer_order']]}
    assert not {'complete', 'review_artifact', 'review_session'} & result.keys()
    registry.assert_not_called()


@pytest.mark.parametrize('prepared_description', ['profiled'], indirect=True)
@pytest.mark.parametrize('caller', ['repository', 'unrelated'])
def test_prepared_profile_description_uses_owned_checkout(
        prepared_description, tmp_path, monkeypatch, caller):
    context, decisions, preparation, providers, registry, invoke = prepared_description
    caller_directory = Path(context['repository']) if caller == 'repository' else tmp_path
    assert caller_directory.resolve() != Path(context['checkout']).resolve()
    monkeypatch.chdir(caller_directory)
    result = invoke()
    assert result == {'reviewers': [preparation['reviewers'][name]
                                    for name in preparation['reviewer_order']]}
    registry.assert_not_called()


@pytest.mark.parametrize('with_preparation', [False, True])
def test_installed_description_preserves_registry_discovery(prepared_description, with_preparation):
    *_, registry, invoke = prepared_description
    with pytest.raises(subprocess.TimeoutExpired):
        invoke('--describe', with_preparation=with_preparation)
    registry.assert_called_once_with()


@pytest.mark.parametrize('change', ['order', 'membership', 'host', 'contract'])
def test_prepared_description_rejects_changed_configuration(prepared_description, change):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    if change == 'order':
        configured.reverse()
    elif change == 'membership':
        configured.pop()
    elif change == 'host':
        configured[0]['host'] = 'claude'
    else:
        configured[0]['handoff_contract'] = 'git-diff-v2'
    with pytest.raises(ValueError, match='changed; refresh preparation'):
        invoke()
    registry.assert_not_called()


@pytest.mark.parametrize('change', ['changed', 'missing'])
def test_prepared_description_rejects_retained_resource_changes(prepared_description, change):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    resource = Path(preparation['reviewers'][preparation['reviewer_order'][0]]['execution']['root']) / 'resource.json'
    resource.parent.chmod(0o700)
    resource.chmod(0o600)
    if change == 'missing':
        resource.unlink()
    else:
        resource.write_text('{"policy": "changed"}\n')
    with pytest.raises(ValueError, match='refresh preparation'):
        invoke()
    registry.assert_not_called()


@pytest.mark.parametrize('change', ['missing_marker', 'unexpected_file'])
def test_prepared_description_preserves_retained_resource_failure_guidance(prepared_description, change):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    retained = Path(preparation['reviewers'][preparation['reviewer_order'][0]]['execution']['root'])
    retained.chmod(0o700)
    if change == 'missing_marker':
        (retained / '.dk-runtime-bundle.json').unlink()
        cause = FileNotFoundError
    else:
        (retained / 'unexpected.txt').write_text('Unindexed package resource\n')
        cause = runtime_bundles.updater().RetentionError
    with pytest.raises(ValueError, match='prepared reviewer description failed: .*refresh preparation') as error:
        invoke()
    assert isinstance(error.value.__cause__, cause)
    registry.assert_not_called()


@pytest.mark.parametrize('execution', [[], 0], ids=['array', 'number'])
def test_prepared_description_rejects_malformed_retained_execution(prepared_description, execution):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    preparation['reviewers'][preparation['reviewer_order'][0]]['execution'] = execution
    preparation['engineering_id'] = runner.preparation_api.engineering_id(preparation)
    preparation.pop('preparation_id')
    preparation['preparation_id'] = runner.preparation_api.digest(preparation)
    with pytest.raises(ValueError, match='prepared reviewer description failed: .*refresh preparation') as error:
        invoke()
    assert isinstance(error.value.__cause__, TypeError)
    registry.assert_not_called()


@pytest.mark.parametrize('stage', ['context_for', 'describe_prepared'])
@pytest.mark.parametrize('interruption', [KeyboardInterrupt(), SystemExit(130)])
def test_prepared_description_preserves_interruptions(prepared_description, monkeypatch, interruption, stage):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    target = runner.preparation_api if stage == 'context_for' else runner
    monkeypatch.setattr(target, stage, Mock(side_effect=interruption))
    with pytest.raises(type(interruption)) as error:
        invoke()
    assert error.value is interruption
    registry.assert_not_called()


@pytest.mark.parametrize('change', ['prompt', 'aspects', 'missing_binding', 'invalid_binding'])
def test_prepared_description_rejects_stale_or_invalid_descriptors(prepared_description, change):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    descriptor = preparation['reviewers'][preparation['reviewer_order'][0]]
    if change == 'prompt':
        descriptor['effective_prompt'] += '\nDifferent instructions'
    elif change == 'aspects':
        descriptor['aspects'] = ['Different aspect']
    elif change == 'missing_binding':
        descriptor.pop('execution')
    else:
        descriptor['execution']['identity'] = 'other:review@current'
    # Retain a valid artifact so the test distinguishes descriptor equality and
    # retained binding checks from the preparation's outer hash validation.
    if change == 'invalid_binding':
        preparation['engineering_id'] = runner.preparation_api.engineering_id(preparation)
        preparation.pop('preparation_id')
        preparation['preparation_id'] = runner.preparation_api.digest(preparation)
    else:
        prepare_current(context, decisions, preparation)
    with pytest.raises(ValueError, match='refresh preparation'):
        invoke()
    registry.assert_not_called()


@pytest.mark.parametrize('change', ['dirty', 'moved_head', 'host'])
def test_prepared_description_preserves_current_checkout_and_host_checks(
        prepared_description, monkeypatch, change):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    if change == 'host':
        monkeypatch.setenv('DK_HOST', 'claude')
    else:
        checkout = Path(context['checkout'])
        (checkout / 'untracked.txt').write_text('changed\n')
        if change == 'moved_head':
            subprocess.run(['git', 'add', '.'], cwd=checkout, check=True)
            subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                            'commit', '-qm', 'Move head'], cwd=checkout, check=True)
    with pytest.raises(ValueError, match='refresh preparation'):
        invoke()
    registry.assert_not_called()


def test_prepared_description_rejects_changed_supplied_decisions(prepared_description, tmp_path):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    supplied = {key: value for key, value in decisions.items() if key != 'decisions'}
    path = tmp_path / 'changed-decisions.json'
    write(path, supplied)
    with pytest.raises(ValueError, match='supplied Decisions changed; refresh preparation'):
        invoke('--describe-prepared', '--decisions-file', path)
    registry.assert_not_called()


@pytest.mark.parametrize('state', ['missing', 'directory'])
def test_prepared_description_rejects_unavailable_supplied_decisions(prepared_description, tmp_path, state):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    path = tmp_path / 'unavailable-decisions.json'
    if state == 'directory':
        path.mkdir()
    with pytest.raises(ValueError, match='prepared reviewer description failed: .*refresh preparation'):
        invoke('--describe-prepared', '--decisions-file', path)
    registry.assert_not_called()


@pytest.mark.parametrize('source', ['context', 'decisions'])
@pytest.mark.parametrize('state', ['missing', 'malformed'])
def test_prepared_description_guides_recovery_for_unavailable_snapshots(prepared_description, source, state):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    path = Path(runner.preparation_api.source_path(preparation, source))
    if state == 'missing':
        path.unlink()
        cause = FileNotFoundError
    else:
        path.chmod(0o600)
        path.write_text('not JSON\n')
        cause = json.JSONDecodeError
    with pytest.raises(ValueError, match='prepared reviewer description failed: .*refresh preparation') as error:
        invoke()
    assert isinstance(error.value.__cause__, cause)
    registry.assert_not_called()


@pytest.mark.parametrize('state', ['missing', 'directory', 'malformed'])
def test_prepared_description_guides_recovery_for_unreadable_preparation(prepared_description, tmp_path, state):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    path = tmp_path / 'unreadable-preparation.json'
    cause = FileNotFoundError
    if state == 'directory':
        path.mkdir()
        cause = IsADirectoryError
    elif state == 'malformed':
        path.write_text('not JSON\n')
        cause = json.JSONDecodeError
    with pytest.raises(ValueError, match='prepared reviewer description failed: .*refresh preparation') as error:
        invoke(preparation_file=path)
    assert isinstance(error.value.__cause__, cause)
    registry.assert_not_called()


def test_installed_description_preserves_bootstrap_errors(prepared_description):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    path = Path(runner.preparation_api.source_path(preparation, 'context'))
    path.chmod(0o600)
    path.write_text('not JSON\n')
    with pytest.raises(json.JSONDecodeError) as error:
        invoke('--describe')
    assert error.value.__cause__ is None
    registry.assert_not_called()


def test_prepared_description_requires_current_preparation(prepared_description):
    *_, registry, invoke = prepared_description
    with pytest.raises(SystemExit) as error:
        invoke(with_preparation=False)
    assert error.value.code == 2
    registry.assert_not_called()


def test_installed_description_succeeds_without_preparation(prepared_description):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    registry.side_effect = None
    result = invoke('--describe', with_preparation=False)
    assert result == {'reviewers': [preparation['reviewers'][name] for name in preparation['reviewer_order']]}
    registry.assert_called_once_with()


def test_prepared_description_rejects_changed_live_evidence(prepared_description):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    source = next(source for source in preparation['sources'].values() if source['mode'] == 'live')
    Path(source['path']).write_text('{"changed": true}\n')
    with pytest.raises(ValueError, match='source changed; refresh preparation'):
        invoke()
    registry.assert_not_called()


def test_prepared_description_rejects_changed_supplied_context(prepared_description, tmp_path):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    path = Path(runner.preparation_api.source_path(preparation, 'context'))
    supplied = json.loads(path.read_text())
    supplied['head_sha'] = 'a' * 40
    path = tmp_path / 'changed-context.json'
    path.write_text(json.dumps(supplied))
    with pytest.raises(ValueError, match='refresh preparation'):
        invoke(context_file=path)
    registry.assert_not_called()


def test_prepared_description_rejects_legacy_preparation(prepared_description):
    context, decisions, preparation, configured, registry, invoke = prepared_description
    preparation['preparation_version'] = 2
    with pytest.raises(SystemExit) as error:
        invoke()
    assert error.value.code == 2
    registry.assert_not_called()
