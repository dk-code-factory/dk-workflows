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
