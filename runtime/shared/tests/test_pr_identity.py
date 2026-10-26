import copy
import json
import subprocess
from unittest.mock import patch

import pytest

from shared.pr_identity import resolve
from shared import pr_identity, repository_scope


def evidence(fork=True):
    head = 'contributor/repo' if fork else 'base/repo'
    meta = {'url': 'https://github.com/base/repo/pull/7', 'number': 7,
            'headRefOid': 'a' * 40, 'headRefName': 'topic',
            'baseRefOid': 'b' * 40, 'baseRefName': 'main', 'isCrossRepository': fork,
            'headRepository': {'id': 'R_head' if fork else 'R_base', 'name': 'repo'}}
    def repo(name):
        return {'full_name': name, 'name': 'repo',
                'node_id': 'R_base' if name == 'base/repo' else 'R_head'}
    rest = {'html_url': meta['url'], 'number': 7,
            'head': {'sha': meta['headRefOid'], 'ref': 'topic', 'repo': repo(head)},
            'base': {'sha': meta['baseRefOid'], 'ref': 'main', 'repo': repo('base/repo')}}
    return meta, rest, head


@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('modern', [False, True])
def test_cli_shapes_resolve_same_repository_and_forks(fork, modern):
    meta, rest, head = evidence(fork)
    calls = []
    if not modern:
        meta['headRepository']['nameWithOwner'] = head
    def api(*args):
        calls.append(args)
        return rest
    assert resolve(meta, api=api) == ('github.com', 'base/repo', head)
    assert meta['headRepository']['nameWithOwner'] == head
    assert len(calls) == int(modern)
    if modern:
        assert calls[0] == ('api', '--hostname', 'github.com', 'repos/base/repo/pulls/7')


@pytest.mark.parametrize('field', ['number', 'url', 'sha', 'ref', 'base_sha', 'base_ref',
                                  'base_repo', 'head_id', 'head_name', 'fork', 'deleted'])
def test_conflicting_or_inaccessible_rest_evidence_fails_closed(field):
    meta, rest, _ = evidence()
    if field == 'number': rest['number'] = 8
    if field == 'url': rest['html_url'] = 'https://github.com/other/repo/pull/7'
    if field == 'sha': rest['head']['sha'] = 'c' * 40
    if field == 'ref': rest['head']['ref'] = 'other'
    if field == 'base_sha': rest['base']['sha'] = 'c' * 40
    if field == 'base_ref': rest['base']['ref'] = 'other'
    if field == 'base_repo': rest['base']['repo']['full_name'] = 'other/repo'
    if field == 'head_id': rest['head']['repo']['node_id'] = 'R_other'
    if field == 'head_name': rest['head']['repo']['name'] = 'other'
    if field == 'fork': meta['isCrossRepository'] = False
    if field == 'deleted': rest['head']['repo'] = None
    with pytest.raises(RuntimeError):
        resolve(meta, api=lambda *args: rest)


def test_repository_configuration_uses_authoritative_head(tmp_path):
    meta, rest, _ = evidence()
    def run(argv, **kwargs):
        data = rest if argv[1] == 'api' else meta
        return subprocess.CompletedProcess(argv, 0, json.dumps(copy.deepcopy(data)), '')
    with patch('shared.github_reads.subprocess.run', side_effect=run):
        assert repository_scope.identities(tmp_path, meta['url']) == [
            'remote:github.com/contributor/repo', 'remote:github.com/base/repo']


def test_rest_read_failure_retains_failure_semantics():
    meta, _, _ = evidence()
    failed = subprocess.CompletedProcess([], 1, '', 'HTTP 404: Not Found')
    with patch('shared.github_reads.subprocess.run', return_value=failed) as run:
        with pytest.raises(RuntimeError, match='404'):
            resolve(meta)
    assert run.call_count == 1


def test_only_read_only_consumers_can_accept_explicitly_deleted_head():
    meta, rest, _ = evidence()
    meta['headRepository'] = None
    rest['head']['repo'] = None
    with pytest.raises(RuntimeError, match='unavailable'):
        resolve(meta, api=lambda *args: rest)
    assert resolve(meta, api=lambda *args: rest, allow_unavailable_head=True) == (
        'github.com', 'base/repo', None)
    assert meta['headRepository'] is None


@pytest.mark.parametrize('head', [None, {}, {'id': ''}])
@pytest.mark.parametrize('fork', [False, 'missing', True])
def test_deleted_head_requires_positive_fork_evidence(head, fork):
    meta, rest, _ = evidence()
    meta['headRepository'] = head
    rest['head']['repo'] = None
    if fork == 'missing':
        meta.pop('isCrossRepository')
    else:
        meta['isCrossRepository'] = fork
    if fork is True:
        assert resolve(meta, api=lambda *a: rest, allow_unavailable_head=True)[2] is None
    else:
        with pytest.raises(RuntimeError, match='inconsistent'):
            resolve(meta, api=lambda *a: rest, allow_unavailable_head=True)


@pytest.mark.parametrize('ids', ['node', 'database', 'same', 'contradictory'])
def test_deleted_head_without_fork_flag_uses_consistent_independent_ids(ids):
    meta, rest, _ = evidence()
    meta.pop('isCrossRepository')
    rest['head']['repo'] = None
    if ids == 'database':
        meta['headRepository'] = {'databaseId': '101'}
        rest['base']['repo']['id'] = 202
    if ids == 'same':
        meta['headRepository']['id'] = 'R_base'
    if ids == 'contradictory':
        meta['headRepository']['databaseId'] = 202
        rest['base']['repo']['id'] = 202
    if ids in {'same', 'contradictory'}:
        with pytest.raises(RuntimeError, match='inconsistent'):
            resolve(meta, api=lambda *a: rest, allow_unavailable_head=True)
    else:
        assert resolve(meta, api=lambda *a: rest, allow_unavailable_head=True)[2] is None


@pytest.mark.parametrize('container', ['head', 'base', 'head_id', 'base_id'])
def test_deleted_head_rejects_malformed_identity_containers(container):
    meta, rest, _ = evidence()
    rest['head']['repo'] = None
    if container == 'head': meta['headRepository'] = []
    if container == 'base': meta['baseRepository'] = []
    if container == 'head_id': meta['headRepository']['id'] = []
    if container == 'base_id': rest['base']['repo']['node_id'] = []
    with pytest.raises(RuntimeError):
        resolve(meta, api=lambda *a: rest, allow_unavailable_head=True)


@pytest.mark.parametrize('field', ['url', 'number', 'base_repo', 'base_id', 'head_id',
                                  'sha', 'ref', 'base_sha', 'base_ref', 'schema', 'fork_schema'])
def test_deleted_head_does_not_hide_other_identity_failures(field):
    meta, rest, _ = evidence()
    rest['head']['repo'] = None
    if field == 'url': rest['html_url'] = 'https://github.com/other/repo/pull/7'
    if field == 'number': rest['number'] = 8
    if field == 'base_repo': rest['base']['repo']['full_name'] = 'other/repo'
    if field == 'base_id': meta['baseRepository'] = {'id': 'R_other'}
    if field == 'head_id': meta['headRepository']['id'] = 'R_base'
    if field == 'sha': rest['head']['sha'] = 'c' * 40
    if field == 'ref': rest['head']['ref'] = 'other'
    if field == 'base_sha': rest['base']['sha'] = 'c' * 40
    if field == 'base_ref': rest['base']['ref'] = 'other'
    if field == 'schema': del rest['head']['repo']
    if field == 'fork_schema': meta['isCrossRepository'] = None
    with pytest.raises(RuntimeError):
        resolve(meta, api=lambda *args: rest, allow_unavailable_head=True)


def test_optional_head_still_blocks_failed_authoritative_read():
    meta, _, _ = evidence()
    with patch.object(pr_identity, 'github_run', return_value=subprocess.CompletedProcess(
            [], 1, '', 'HTTP 401: authentication failed')):
        with pytest.raises(RuntimeError, match='401'):
            resolve(meta, allow_unavailable_head=True)


@pytest.mark.parametrize('failure', [OSError('fixture transport failure'),
                                   subprocess.TimeoutExpired(['gh', 'api'], 60)])
def test_configuration_rest_transport_errors_are_actionable(tmp_path, failure):
    meta, _, _ = evidence()
    view = subprocess.CompletedProcess([], 0, json.dumps(meta), '')
    with patch.object(repository_scope, 'github_run', return_value=view), \
         patch.object(pr_identity, 'github_run', side_effect=failure):
        with pytest.raises(RuntimeError, match='could not resolve PR repository identities') as error:
            repository_scope.identities(tmp_path, meta['url'])
    assert error.value.__cause__ is failure
