"""The standalone exercise must not use its caller's Git repository or state."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXERCISE = ROOT/'tests/fixtures/review_continuation_exercise.py'


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
            for path in root.rglob('*') if path.is_file()}


def clean_environment():
    return {**{key: value for key, value in os.environ.items() if not key.startswith('GIT_')},
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}


def git(repo, env, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                                   text=True, timeout=20).strip()


@pytest.mark.parametrize('overrides', ['repository', 'execution'])
def test_standalone_fixture_isolates_inherited_git_and_state(tmp_path, overrides):
    env = clean_environment()
    donor = tmp_path/'donor'
    donor.mkdir()
    git(donor, env, 'init', '-q', '--template=')
    git(donor, env, 'config', 'user.name', 'Original author')
    git(donor, env, 'config', 'user.email', 'original@example.test')
    (donor/'code').write_text('original\n')
    git(donor, env, 'add', '.')
    git(donor, env, '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
        'commit', '-qm', 'original')
    # Keep both staged and unstaged donor changes to detect index/worktree writes.
    (donor/'code').write_text('staged\n')
    git(donor, env, 'add', '.')
    (donor/'code').write_text('unstaged\n')
    (donor/'untracked').write_text('retain me\n')
    caller_state = tmp_path/'caller-state'
    caller_state.mkdir()
    (caller_state/'retained.json').write_text('{"retained":true}\n')
    env['XDG_STATE_HOME'] = str(caller_state)
    if overrides == 'repository':
        env.update(GIT_DIR=str(donor/'.git'), GIT_WORK_TREE=str(donor),
                   GIT_COMMON_DIR=str(donor/'.git'), GIT_INDEX_FILE=str(donor/'.git/index'),
                   GIT_OBJECT_DIRECTORY=str(donor/'.git/objects'))
    else:
        hooks = tmp_path/'hostile-hooks'
        hooks.mkdir()
        hook = hooks/'pre-commit'
        hook.write_text('#!/bin/sh\nexit 91\n')
        hook.chmod(0o755)
        template = tmp_path/'hostile-template'
        template.mkdir()
        (template/'template-leak').write_text('must not be copied\n')
        config = tmp_path/'hostile.gitconfig'
        config.write_text(f'[core]\n\thooksPath = {hooks}\n'
                          '[commit]\n\tgpgsign = true\n'
                          '[gpg]\n\tprogram = /nonexistent-exercise-signer\n'
                          f'[init]\n\ttemplateDir = {template}\n')
        env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_SYSTEM=str(config),
                   GIT_CONFIG_NOSYSTEM='0', GIT_TEMPLATE_DIR=str(template),
                   GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.hooksPath',
                   GIT_CONFIG_VALUE_0=str(hooks))
    before_donor, before_state = snapshot(donor), snapshot(caller_state)
    before_inputs = snapshot(tmp_path)
    fixture = tmp_path/'external-exercise'
    result = subprocess.run([sys.executable, str(EXERCISE), str(fixture)], cwd=donor,
                            env=env, capture_output=True, text=True, timeout=60)
    # Compare real snapshots even when setup fails partway through.
    assert snapshot(donor) == before_donor
    assert snapshot(caller_state) == before_state
    assert {key: value for key, value in snapshot(tmp_path).items()
            if not key.startswith('external-exercise/')} == before_inputs
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    spec = json.loads(Path(response['spec']).read_text())
    repo = fixture/'repo'
    assert git(repo, clean_environment(), 'rev-parse', '--show-toplevel') == str(repo)
    assert git(repo, clean_environment(), 'rev-parse', 'HEAD') == spec['git']['head']
    assert spec['git']['base'] != spec['git']['target'] != spec['git']['head']
    assert git(repo, clean_environment(), 'status', '--porcelain') == ''
    assert not (repo/'.git/template-leak').exists()
    assert Path(response['manifest']).is_relative_to(fixture/'state')
    manifest = json.loads(Path(response['manifest']).read_text())
    assert manifest['runs']['retained-run']['cycles'] == [f'c{i}' for i in range(5)]
    assert len(manifest['entries']) == 1
    assert len(next(iter(manifest['entries'].values()))['attempts']) == 5
    assert Path(response['manifest']).read_bytes() == (fixture/'before.json').read_bytes()


@pytest.mark.parametrize('fail', [False, True])
def test_prepare_restores_process_environment(tmp_path, monkeypatch, fail):
    prepare = runpy.run_path(str(EXERCISE))['prepare']
    monkeypatch.setenv('GIT_DIR', str(tmp_path/'unused.git'))
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'user.name')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'Inherited')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'original-state'))
    before = dict(os.environ)
    if fail:
        def interrupted(directory):
            raise RuntimeError('fixture interrupted')
        monkeypatch.setitem(prepare.__globals__, 'prepare_isolated', interrupted)
        with pytest.raises(RuntimeError, match='fixture interrupted'):
            prepare(tmp_path/'exercise')
    else:
        prepare(tmp_path/'exercise')
    assert dict(os.environ) == before
