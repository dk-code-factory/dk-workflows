import json
import subprocess
from unittest.mock import patch

import pytest

from shared import repository_scope as scope


def test_pr_identities_use_head_then_base_even_from_upstream_checkout(tmp_path):
    response = subprocess.CompletedProcess([], 0, json.dumps({
        'url': 'https://github.com/base/repo/pull/7',
        'headRepository': {'nameWithOwner': 'contributor/repo'}}), '')
    with patch.object(scope.subprocess, 'run', return_value=response) as run:
        assert scope.identities(tmp_path, 'https://github.com/base/repo/pull/7') == [
            'remote:github.com/contributor/repo', 'remote:github.com/base/repo']
    assert run.call_args.args[0][1:4] == ['pr', 'view', 'https://github.com/base/repo/pull/7']


def test_pr_with_missing_head_repository_fails_closed(tmp_path):
    response = subprocess.CompletedProcess([], 0, json.dumps({
        'url': 'https://github.com/base/repo/pull/7', 'headRepository': None}), '')
    with patch.object(scope.subprocess, 'run', return_value=response):
        with pytest.raises(RuntimeError, match='unavailable'):
            scope.identities(tmp_path, 'https://github.com/base/repo/pull/7')


def test_upstream_remote_or_github_parent_resolves_fork(tmp_path):
    origin = 'remote:github.com/contributor/repo'
    with patch.object(scope, 'git_remote', side_effect=lambda _, name:
                      'git@github.com:base/repo.git' if name == 'upstream' else None):
        assert scope.upstream(tmp_path, origin) == 'remote:github.com/base/repo'
    response = subprocess.CompletedProcess([], 0, json.dumps({
        'isFork': True, 'parent': {'nameWithOwner': 'base/repo'}}), '')
    with patch.object(scope, 'git_remote', return_value=None), \
         patch.object(scope.subprocess, 'run', return_value=response):
        assert scope.upstream(tmp_path, origin) == 'remote:github.com/base/repo'


def test_private_storage_uses_checkout_root_not_cwd(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    assert not scope.inside_checkout(home / 'data', home)
    checkout = home / 'checkout'
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    nested = checkout / 'src'
    nested.mkdir()
    assert scope.inside_checkout(checkout / 'private', nested)
    assert scope.inside_checkout(checkout / '.git' / 'private', nested)
    assert not scope.inside_checkout(home / 'data', nested)
    alias = home / 'checkout-link'
    alias.symlink_to(checkout, target_is_directory=True)
    assert scope.inside_checkout(alias / 'private', nested)


def test_private_storage_protects_bare_git_directory(tmp_path):
    bare = tmp_path / 'bare.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True)
    assert scope.inside_checkout(bare / 'private', bare)


def test_private_storage_protects_linked_worktree_common_directory(tmp_path):
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.test', 'commit', '-q',
                    '--allow-empty', '-m', 'Initial'], check=True)
    worktree = tmp_path / 'linked'
    subprocess.run(['git', '-C', str(checkout), 'worktree', 'add', '-q',
                    '--detach', str(worktree)], check=True)
    assert scope.inside_checkout(worktree / 'private', worktree)
    assert scope.inside_checkout(checkout / '.git' / 'private', worktree)
    assert not scope.inside_checkout(tmp_path / 'private', worktree)


def test_repository_lookup_failure_does_not_disable_storage_guard(tmp_path):
    failed = subprocess.CompletedProcess([], 128, '', 'fatal: detected dubious ownership')
    with patch.object(scope.subprocess, 'run', return_value=failed):
        with pytest.raises(RuntimeError, match='dubious ownership'):
            scope.inside_checkout(tmp_path / 'private', tmp_path)
