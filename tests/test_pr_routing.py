"""Personal routing uses immutable, host-local identities and preserves policies."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

import config
from shared.pr_ownership import classify
from state import write

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pr_route', ROOT/'scripts/pr_route.py')
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def test_personal_identity_survives_login_change_and_does_not_use_login_equality():
    result = classify('github.com', {'id': 1, 'login': 'renamed'}, {'id': 1, 'login': 'old-login'})
    assert result['ownership'] == 'mine' and result['skill'] == 'dk-workflows:dk-pr-fix'
    result = classify('github.com', {'id': 2, 'login': 'same'}, {'id': 1, 'login': 'same'})
    assert result['ownership'] == 'others' and result['skill'] == 'dk-workflows:dk-pr-review'


def test_configured_personal_accounts_are_host_scoped():
    author, actor = {'id': 2, 'login': 'alternate'}, {'id': 1, 'login': 'current'}
    aliases = [{'host': 'GITHUB.COM', 'id': 2}]
    assert classify('github.com', author, actor, aliases)['ownership'] == 'mine'
    assert classify('ghe.example', author, actor, aliases)['ownership'] == 'others'


@pytest.mark.parametrize('identity', [None, {}, {'id': True, 'login': 'x'},
                                      {'id': '1', 'login': 'x'}, {'id': 1}])
def test_unknown_or_malformed_identity_blocks_routing(identity):
    with pytest.raises(ValueError, match='identities'):
        classify('github.com', identity, {'id': 1, 'login': 'x'})


def test_route_reads_canonical_pr_and_effective_personal_identity():
    write(config.path(), {'identities': [{'host': 'github.com', 'id': 2}]})
    pr = 'https://github.com/owner/repo/pull/7'
    sources = {'user': {'id': 1, 'login': 'current'},
               'repos/owner/repo/pulls/7': {'number': 7, 'html_url': pr,
                    'head': {'sha': 'a'*40}, 'user': {'id': 2, 'login': 'alternate'}}}
    reads = []
    def api(path):
        reads.append(path)
        return sources[path]
    result = router.route(pr, api=api)
    assert result['ownership'] == 'mine' and result['head'] == 'a'*40
    assert reads == ['repos/owner/repo/pulls/7', 'user']
    sources['repos/owner/repo/pulls/7']['html_url'] = pr.replace('/7', '/8')
    with pytest.raises(ValueError, match='inconsistent'):
        router.route(pr, api=api)


@pytest.mark.parametrize('repository_identities,expected', [
    (None, 'mine'),
    ([], 'others'),
    ([{'host': 'ghe.example', 'id': 2}], 'others'),
])
def test_route_inherits_upstream_identities_with_repository_precedence(repository_identities, expected):
    repository = 'github.com/fork/repo'
    upstream = 'github.com/upstream/repo'
    write(config.path(upstream), {'identities': [{'host': 'github.com', 'id': 2}]})
    if repository_identities is not None:
        write(config.path(repository), {'identities': repository_identities})
    pr = 'https://' + repository + '/pull/7'
    sources = {'user': {'id': 1, 'login': 'current'},
               'repos/fork/repo/pulls/7': {'number': 7, 'html_url': pr,
                    'head': {'sha': 'a'*40}, 'user': {'id': 2, 'login': 'alternate'}}}
    result = router.route(pr, upstream=upstream, api=sources.__getitem__)
    assert result['ownership'] == expected
    assert result['skill'] == ('dk-workflows:dk-pr-fix' if expected == 'mine'
                               else 'dk-workflows:dk-pr-review')


def test_route_cli_applies_explicit_upstream(monkeypatch, capsys):
    upstream = 'github.com/upstream/repo'
    write(config.path(upstream), {'identities': [{'host': 'github.com', 'id': 2}]})
    pr = 'https://github.com/fork/repo/pull/7'
    sources = {'user': {'id': 1, 'login': 'current'},
               'repos/fork/repo/pulls/7': {'number': 7, 'html_url': pr,
                    'head': {'sha': 'a'*40}, 'user': {'id': 2, 'login': 'alternate'}}}
    monkeypatch.setattr(router, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, json.dumps(sources[argv[-1]]), ''))
    monkeypatch.setattr(sys, 'argv', ['pr_route.py', '--pr', pr, '--upstream', upstream])
    router.main()
    result = json.loads(capsys.readouterr().out)
    assert result['ownership'] == 'mine'
    assert result['repository'] == 'github.com/fork/repo'


@pytest.mark.parametrize('pr', ['https://github.com/owner/repo/issues/7',
                               'https://github.com/owner/repo/pull/7?x=1',
                               'https://someone@github.com/owner/repo/pull/7'])
def test_invalid_route_target_does_not_read_github(pr):
    def api(_):
        pytest.fail('Invalid target must not reach GitHub')
    with pytest.raises(ValueError, match='Canonical'):
        router.route(pr, api=api)


def test_review_policy_inherits_each_action_without_enabling_others():
    write(config.path(), {'incoming_review': {'approve': True}})
    write(config.path('github.com/upstream/repo'), {'incoming_review': {'merge': True}})
    write(config.path('github.com/fork/repo'), {'incoming_review': {'post': False, 'approve': False}})
    policy = config.effective('github.com/fork/repo', 'github.com/upstream/repo')['settings']['incoming_review']
    assert policy == {'post': False, 'approve_ci': True, 'approve': False, 'merge': True, 'merge_method': 'squash'}
    assert config.effective('github.com/other/repo')['settings']['incoming_review']['merge'] is False


@pytest.mark.parametrize('settings', [
    {'incoming_review': {'merge': 'yes'}}, {'incoming_review': {'unknown': True}},
    {'incoming_review': {'merge_method': 'rebase'}},
    {'identities': [{'host': 'github.com', 'id': True}]},
    {'identities': [{'host': 'github.com', 'login': 'alternate'}]},
    {'identities': [{'host': 'github.com', 'id': 2}, {'host': 'GITHUB.COM', 'id': 2}]},
])
def test_malformed_action_policy_and_personal_identity_are_rejected(settings):
    with pytest.raises(ValueError):
        config.validate(settings)


def test_ci_approval_policy_and_role_inherit_independently():
    repository, upstream = 'github.com/fork/repo', 'github.com/upstream/repo'
    assert config.DEFAULTS['incoming_review']['approve_ci'] is True
    write(config.path(), {'incoming_review': {'approve_ci': False},
                          'skills': {'ci_approve': 'custom:ci-approve'}})
    write(config.path(upstream), {'incoming_review': {'approve_ci': True}})
    write(config.path(repository), {'incoming_review': {'approve': False, 'merge': False}})
    settings = config.effective(repository, upstream)['settings']
    assert settings['incoming_review']['approve_ci'] is True
    assert settings['skills']['ci_approve'] == 'custom:ci-approve'
    assert not settings['incoming_review']['approve'] and not settings['incoming_review']['merge']
    write(config.path(repository), {'incoming_review': {'approve_ci': False}})
    assert config.effective(repository, upstream)['settings']['incoming_review']['approve_ci'] is False
