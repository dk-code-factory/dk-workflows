import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from update_fixtures import Fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import bindings, runtime_bundles


def test_entry_cyclic_resource_is_a_critical_stop(tmp_path):
    fixture = Fixture(tmp_path, 'cycle', 'off')
    (fixture.base / 'installed/dk-workflows/loop').symlink_to('loop')
    receipt = fixture.invoke()
    assert receipt['status'] == 'runtime_unavailable'
    assert receipt['action'] == 'stop' and receipt['non_blocking'] is False
    assert 'token' not in receipt and 'runtime_root' not in receipt
    assert not (fixture.base / 'state/dk-code-factory/updates/state.json').exists()


@pytest.mark.parametrize('remove_installation', [False, True])
def test_entry_registry_timeout_keeps_retained_package_pinned(tmp_path, remove_installation):
    fixture = Fixture(tmp_path, 'metadata-timeout', 'auto')
    original = fixture.subrun
    def unavailable(argv, **kwargs):
        if argv[:4] == ['codex', 'plugin', 'list', '--json']:
            if remove_installation:
                shutil.rmtree(fixture.base / 'installed/dk-workflows')
            raise subprocess.TimeoutExpired(argv, 30)
        return original(argv, **kwargs)
    fixture.subrun = unavailable
    receipt = fixture.invoke()
    assert receipt['action'] in ('continue', 'notify')
    assert receipt['identity']['pinned'] is True and receipt['update_identity_unavailable'] is True
    assert Path(receipt['runtime_root']).is_dir() and Path(receipt['completion_script']).is_file()
    assert all(not session['pending'] for session in fixture.state()['sessions'].values())
    process = subprocess.run([sys.executable, receipt['completion_script'], 'complete',
        '--host', 'codex', '--token', receipt['token']],
        env=dict(os.environ, **fixture.env), capture_output=True, text=True, check=True)
    completed = json.loads(process.stdout)
    assert completed['status'] == 'completed'
    assert not completed.get('updates')


def test_entry_storage_failure_cannot_continue_without_a_runtime(tmp_path):
    fixture = Fixture(tmp_path, 'bad-storage', 'off')
    fixture.env['XDG_STATE_HOME'] = str(fixture.base / 'installed/dk-workflows')
    receipt = fixture.invoke()
    assert receipt['action'] == 'stop' and receipt['non_blocking'] is False
    assert 'token' not in receipt and 'runtime_root' not in receipt


def test_retained_lifecycle_survives_removed_installation(tmp_path):
    fixture = Fixture(tmp_path, 'retained', 'off')
    source = fixture.base / 'installed/dk-workflows'
    (source / 'scripts/fixture.py').write_text('print("retained helper works")\n')
    (source / 'empty-resource-directory').mkdir()
    receipt = fixture.invoke()
    bundle = Path(receipt['runtime_root'])
    assert bundle != source and (bundle / 'empty-resource-directory').is_dir()
    shutil.rmtree(source)
    result = subprocess.run([sys.executable, str(bundle / 'scripts/fixture.py')],
                            text=True, capture_output=True, check=True)
    assert result.stdout == 'retained helper works\n'
    env = dict(os.environ, **fixture.env)
    result = subprocess.run([sys.executable, receipt['completion_script'], 'complete',
                             '--host', 'codex', '--token', receipt['token']],
                            env=env, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['status'] == 'completed'
    assert fixture.state()['invocations'][receipt['token']]['complete']


def test_content_addressed_reuse_and_modified_resource_detection(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    source = tmp_path / 'plugin'
    source.mkdir()
    (source / 'script.sh').write_text('#!/bin/sh\ntrue\n')
    (source / 'script.sh').chmod(0o755)
    lease = source / '.in_use/111'
    lease.parent.mkdir()
    lease.write_text('host lease')
    first = runtime_bundles.retain(source)
    assert not (first / '.in_use').exists()
    assert runtime_bundles.retain(source) == first
    assert (first / 'script.sh').stat().st_mode & 0o111 == 0o111
    assert not (first / 'script.sh').stat().st_mode & 0o222
    (source / 'script.sh').write_text('#!/bin/sh\nfalse\n')
    second = runtime_bundles.retain(source)
    assert second != first and (first / 'script.sh').read_text().endswith('true\n')
    nested = source / 'resources/.in_use/111'
    nested.parent.mkdir(parents=True)
    nested.write_text('normative resource')
    third = runtime_bundles.retain(source)
    assert third != second and (third / 'resources/.in_use/111').read_text() == 'normative resource'
    nested.write_text('changed normative resource')
    assert runtime_bundles.retain(source) != third
    shutil.rmtree(lease.parent)
    lease.parent.write_text('normative root file')
    fourth = runtime_bundles.retain(source)
    assert (fourth / '.in_use').read_text() == 'normative root file'
    (first / 'script.sh').chmod(0o644)
    (first / 'script.sh').write_text('tampered')
    with pytest.raises(RuntimeError, match='changed'):
        runtime_bundles.verify(first)


@pytest.mark.parametrize('tamper', ['lease', 'inventory'])
def test_historical_sealed_lease_inventory_stays_exact(tmp_path, monkeypatch, tamper):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    api = runtime_bundles.updater()
    historical = tmp_path / 'historical'
    lease = historical / '.in_use/111'
    lease.parent.mkdir(parents=True)
    lease.write_text('historical host lease')
    procedure = historical / 'procedure.md'
    procedure.write_text('pinned instructions')
    files = {'.in_use': {'directory': True},
        '.in_use/111': {'sha256': api.resource_hash(lease), 'executable': 0},
        'procedure.md': {'sha256': api.resource_hash(procedure), 'executable': 0}}
    digest = api.bundle_digest(files)
    marker = json.dumps({'version': 1, 'digest': digest, 'files': files}, sort_keys=True) + '\n'
    (historical / api.BUNDLE_MARKER).write_text(marker)
    store = tmp_path / 'state/dk-code-factory/updates/runtimes'
    store.mkdir(parents=True)
    bundle = store / digest
    historical.rename(bundle)
    before = {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob('*') if path.is_file()}
    assert runtime_bundles.verify(bundle) == digest
    assert runtime_bundles.retain(bundle) == bundle
    assert {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob('*') if path.is_file()} == before
    if tamper == 'lease':
        (bundle / '.in_use/111').write_text('changed historical lease')
    else:
        files.pop('.in_use/111')
        (bundle / api.BUNDLE_MARKER).write_text(json.dumps({'version': 1, 'digest': digest, 'files': files}))
    with pytest.raises(RuntimeError, match='changed'):
        runtime_bundles.verify(bundle)


def test_runtime_rejects_escaping_links_and_repository_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    source = tmp_path / 'plugin'
    source.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('external dependency')
    (source / 'escape').symlink_to(outside)
    with pytest.raises(RuntimeError, match='escapes'):
        runtime_bundles.retain(source)
    (source / 'escape').unlink()
    (source / 'file.txt').write_text('resource')
    (source / 'internal.txt').symlink_to(source / 'file.txt')
    retained = runtime_bundles.retain(source)
    assert (retained / 'internal.txt').read_text() == 'resource'
    assert (retained / 'internal.txt').is_symlink()
    assert (retained / 'internal.txt').resolve() == retained / 'file.txt'
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    monkeypatch.chdir(source)
    monkeypatch.setenv('XDG_STATE_HOME', str(source / 'state'))
    with pytest.raises(ValueError, match='outside'):
        runtime_bundles.retain(source)


@pytest.mark.parametrize('native_host', ['codex', 'claude'])
def test_prepared_native_reviewer_survives_registry_replacement(tmp_path, monkeypatch, native_host):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('DK_HOST', 'codex')
    monkeypatch.setattr(bindings.shutil, 'which', lambda name: '/bin/' + name)
    source = tmp_path / 'provider'
    (source / f'.{native_host}-plugin').mkdir(parents=True)
    procedure = source / ('skills/review/SKILL.md' if native_host == 'codex' else 'commands/review.md')
    procedure.parent.mkdir(parents=True)
    manifest = {'name': 'provider'}
    if native_host == 'codex':
        manifest.update(version='1.0.0', skills='./skills/')
    manifest_path = source / f'.{native_host}-plugin/plugin.json'
    manifest_path.write_text(json.dumps(manifest))
    procedure.write_text('Read old pinned instructions.')
    version = '1.0.0' if native_host == 'codex' else '58267944f921'
    registry = {'installed': [{'name': 'provider', 'pluginId': 'provider@test',
        'version': version, 'installedPath': str(source)}]}
    claude_registry = tmp_path / 'claude/plugins/installed_plugins.json'
    claude_registry.parent.mkdir(parents=True)
    claude_registry.write_text(json.dumps({'plugins': {'provider@test': [
        {'version': version, 'installPath': str(source)}]}}))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude_registry.parents[1]))
    real_run = subprocess.run
    def run(argv, **kwargs):
        if argv[:4] == ['codex', 'plugin', 'list', '--json']:
            return subprocess.CompletedProcess(argv, 0, json.dumps(registry), '')
        if argv[0] == native_host and (native_host == 'claude' or argv[1] == 'exec'):
            # Launch only our local Python fixture, never an authenticated host.
            return real_run([sys.executable, str(executable), *argv[1:]], **kwargs)
        return real_run(argv, **kwargs)
    monkeypatch.setattr(bindings.subprocess, 'run', run)
    path = Path(__file__).resolve().parents[1] / 'runtime/dk-pr-review/scripts/run_reviewers.py'
    spec = importlib.util.spec_from_file_location('retained_review_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    selection = {'skill': 'provider:review', 'host': native_host}
    descriptor = runner.describe_reviewer(selection)
    lease_directory = source / '.in_use'
    lease_directory.mkdir()
    assert runner.describe_reviewer(selection) == descriptor
    for pid, content in [('111', 'lease'), ('111', 'changed lease'), ('222', 'different PID')]:
        for lease in lease_directory.iterdir():
            lease.unlink()
        (lease_directory / pid).write_text(content)
        assert runner.describe_reviewer(selection) == descriptor
        assert not (Path(descriptor['execution']['root']) / '.in_use').exists()
    (lease_directory / '222').unlink()
    assert runner.describe_reviewer(selection) == descriptor
    lease_directory.rmdir()
    assert runner.describe_reviewer(selection) == descriptor
    resolved = bindings.resolve(selection)
    for explicit_version in (None, '9.9.9'):
        manifest_path.write_text(json.dumps(dict(manifest, version=explicit_version)))
        with pytest.raises(ValueError, match='disagree'):
            runner.describe_reviewer(selection)
        incompatible = {'execution': runtime_bundles.retained_binding(resolved)}
        with pytest.raises(ValueError, match='procedure or version changed'):
            with bindings.pinned_bindings({bindings.key(selection): incompatible}):
                pass
    manifest_path.write_text(json.dumps(manifest))
    wrong_procedure = dict(descriptor, execution=dict(descriptor['execution'], procedure=str(procedure)))
    with pytest.raises(ValueError, match='procedure or version changed'):
        with bindings.pinned_bindings({bindings.key(selection): wrong_procedure}):
            pass
    shutil.rmtree(source)
    registry['installed'] = []
    claude_registry.write_text('{"plugins": {}}')
    with bindings.pinned_bindings({bindings.key(selection): descriptor}):
        assert runner.describe_reviewer(selection) == descriptor
        assert runner.skill_path(selection) == Path(descriptor['execution']['procedure'])
        assert runner.skill_path(selection).read_text() == 'Read old pinned instructions.'
        from shared import executors
        host = tmp_path / 'host'
        host.mkdir()
        executable = host / native_host
        resource = Path(descriptor['execution']['procedure'])
        observed = tmp_path / 'used-resource.txt'
        executable.write_text('#!' + sys.executable + '\n' +
            'import sys,pathlib\n' +
            'resource=pathlib.Path(' + repr(str(resource)) + ')\n' +
            'assert str(resource) in sys.stdin.read()\n' +
            'assert resource.read_text()=="Read old pinned instructions."\n' +
            'pathlib.Path(' + repr(str(observed)) + ').write_text(str(resource))\n' +
            ('pathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(' +
             repr('{"findings": []}') + ')\n' if native_host == 'codex' else
             'print(' + repr(json.dumps({'subtype': 'success', 'result': '{"findings": []}'})) + ')\n'))
        executable.chmod(0o755)
        monkeypatch.setenv('PATH', str(host) + os.pathsep + os.environ['PATH'])
        monkeypatch.delenv('DK_BINDING_STACK', raising=False)
        assert executors.invoke(selection, tmp_path / 'handoff.json', tmp_path, 'Review') == '{"findings": []}'
        assert observed.read_text() == str(resource)
        assert bindings.resolve(selection)['version'] == version
    with pytest.raises(ValueError, match='found 0'):
        bindings.resolve(selection)
    resource.chmod(0o644)
    resource.write_text('Changed pinned instructions.')
    with pytest.raises(RuntimeError, match='changed'):
        with bindings.pinned_bindings({bindings.key(selection): descriptor}):
            pass



def test_deferred_update_reconciles_host_replacement_without_reinstalling(tmp_path):
    from unittest.mock import patch
    fixture = Fixture(tmp_path, 'replacement', 'auto')
    receipt = fixture.invoke()
    source = fixture.base / 'installed/dk-workflows'
    replacement = fixture.base / 'replacement/dk-workflows'
    shutil.copytree(source, replacement)
    for host in ['codex', 'claude']:
        path = replacement / f'.{host}-plugin/plugin.json'
        manifest = json.loads(path.read_text())
        manifest['version'] = '1.1.0'
        path.write_text(json.dumps(manifest))
    fixture.records[0].update(version='1.1.0', installPath=str(replacement))
    shutil.rmtree(source)
    api = fixture.modules['dk-workflows']
    with patch.dict(os.environ, fixture.env), patch.object(api.subprocess, 'run', side_effect=fixture.subrun):
        completed = api.lifecycle(Path(receipt['runtime_root']), 'codex', 'complete', token=receipt['token'])
    assert completed['updates'][0]['status'] == 'current'
    assert completed['updates'][0]['reload_required']
    assert not any(call[1:3] == ['plugin', 'add'] for call in fixture.calls)


@pytest.mark.parametrize('packaged', [False, True])
@pytest.mark.parametrize('interpreter_flag', ['-u', '-s', '-S', '-E', '-I'])
def test_command_execution_keeps_data_paths_and_survives_removed_entrypoint(tmp_path, monkeypatch, packaged, interpreter_flag):
    from shared import executors
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.delenv('DK_BINDING_STACK', raising=False)
    source = tmp_path / 'provider'
    source.mkdir()
    helper = source / 'review.py'
    output = tmp_path / 'existing-output.json'
    output.write_text('old')
    if packaged:
        (source / '.codex-plugin').mkdir()
        (source / '.codex-plugin/plugin.json').write_text('{"name":"fixture","version":"1.0.0"}')
        (source / 'schema.json').write_text('pinned schema')
    helper.write_text('import pathlib,sys\n' +
        ('assert (pathlib.Path(__file__).resolve().parent/"schema.json").read_text()=="pinned schema"\n' if packaged else '') +
        'pathlib.Path(sys.argv[3]).write_text("new")\nprint(\'{"findings": []}\')\n')
    if packaged:
        (source / 'commands').mkdir()
        (source / 'commands/alias.py').symlink_to(helper)
        helper = source / 'commands/alias.py'
    binding = {'command': [sys.executable, interpreter_flag, str(helper), '{handoff}', '--output', str(output)]}
    with runtime_bundles.provider_scope(binding):
        selected = bindings.resolve(binding)
        assert selected['argv'][-1] == str(output)
        shutil.rmtree(source)
        assert executors.invoke(binding, tmp_path / 'handoff.json', tmp_path, 'Review') == '{"findings": []}\n'
    assert output.read_text() == 'new'



def test_retained_provider_entry_ignores_missing_or_unavailable_origin_metadata(tmp_path, monkeypatch):
    fixture = Fixture(tmp_path, 'origin-offline', 'off')
    original = fixture.base / 'installed/dk-workflows'
    entry = fixture.invoke()
    retained = Path(entry['runtime_root'])
    api = fixture.modules['dk-workflows']
    real_installed = api.installed
    def unavailable(path, host):
        if path == original:
            raise RuntimeError('host registry unavailable')
        return real_installed(path, host)
    monkeypatch.setattr(api, 'installed', unavailable)
    monkeypatch.setenv('XDG_CONFIG_HOME', fixture.env['XDG_CONFIG_HOME'])
    monkeypatch.setenv('XDG_STATE_HOME', fixture.env['XDG_STATE_HOME'])
    receipt = api.lifecycle(retained, 'codex', 'entry', origin_root=str(original))
    assert receipt['runtime_root'] == str(retained)
    assert receipt['identity']['pinned'] and receipt['update_identity_unavailable']
    shutil.rmtree(original)
    original.mkdir()
    second = api.lifecycle(retained, 'codex', 'entry', origin_root=str(original))
    assert second['runtime_root'] == str(retained)
    assert second['identity']['pinned']
