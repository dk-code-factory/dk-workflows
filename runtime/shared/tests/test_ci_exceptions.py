"""Explicit CI settings through the real CLI and exact-head policy, without network."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import ci_exceptions as config, ci_policy as policy
from shared.flaky_tests import Registry
from test_flaky_tests import FakeAPI, REPO, WORKFLOW, JOB, TEST, SIGNATURE, OTHER, FAIL, snapshot

IDENTITY = 'remote:' + REPO


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    (tmp_path / 'checkout').mkdir()
    return Registry(REPO, tmp_path / 'checkout')


def add(kind='test', scope='repo', **changes):
    values = {'kind': kind, 'workflow': WORKFLOW, 'job': JOB,
              'test': TEST if kind == 'test' else None, 'signature': None, 'reason': 'Explicitly accepted intermittent failure'}
    values.update(changes)
    item = config.entry(**values)
    config.configure(IDENTITY, 'add', scope=scope, value=item)
    return item


def decide(registry, api=None, snap=None):
    snap = snap or snapshot()
    return policy.decide(api or FakeAPI(body='No matching flaky issue'), registry, snap,
                         lambda: copy.deepcopy(snap))


def test_test_exception_accepts_without_issue_and_records_source(setup):
    item = add(signature=SIGNATURE)
    result = decide(setup)
    assert result['result'] == 'accepted_exceptions'
    assert result['raw_result'] == 'failure' and not result['blockers']
    failure = result['ignored_failures'][0]
    assert failure['test'] == TEST and failure['issues'] == []
    assert failure['exceptions'][0]['id'] == item['id']
    assert failure['exceptions'][0]['scope'] == 'repo'
    assert failure['exceptions'][0]['reason'] == item['reason']
    assert Path(result['decision_file']).is_file()


def test_optional_signature_accepts_new_error_only_for_the_exact_test(setup):
    add()
    api = FakeAPI(body='No matching issue')
    api.texts[1] = FAIL.replace(SIGNATURE, 'ValueError: different error')
    assert decide(setup, api)['result'] == 'accepted_exceptions'
    api.texts[1] = api.texts[1].replace(TEST, TEST + '_other')
    assert decide(setup, api)['result'] == 'failure'


@pytest.mark.parametrize('change', [dict(signature='Wrong signature'), dict(test=TEST + '*'),
                                 dict(job=JOB + ' other'), dict(workflow='.github/workflows/other.yml')])
def test_matchers_are_literal_and_do_not_broaden(setup, change):
    add(**change)
    assert decide(setup)['result'] == 'failure'


def test_pipeline_exception_covers_non_test_errors_without_downloading_logs(setup):
    add('pipeline', job=None)
    api = FakeAPI(text='compiler failed', body='No matching issue')
    original = api.request
    def request(endpoint, **kwargs):
        assert not endpoint.endswith('/logs'), 'pipeline exceptions must not need test logs'
        return original(endpoint, **kwargs)
    api.request = request
    result = decide(setup, api)
    assert result['result'] == 'accepted_exceptions' and not result['blockers']
    assert result['ignored_failures'][0]['exceptions'][0]['kind'] == 'pipeline'
    assert not any(call[0] == 'issues' for call in api.calls)


def test_job_restricted_pipeline_does_not_cover_other_jobs(setup):
    add('pipeline', job='another job')
    assert decide(setup, FakeAPI(text='compiler failed', body='No matching issue'))['result'] == 'failure'


def test_test_exception_does_not_hide_mixed_failures_or_build_errors(setup):
    add()
    api = FakeAPI(body='No matching issue')
    api.texts[1] = FAIL.replace('================ 1 failed',
        'FAILED tests/test_other.py::test_unknown - ValueError: broken\n================ 2 failed')
    result = decide(setup, api)
    assert result['result'] == 'failure' and len(result['ignored_failures']) == 1
    api.texts[1] = 'build failed'
    assert decide(setup, api)['result'] == 'failure'


def test_explicit_and_automatic_coverage_can_cover_different_failures(setup):
    other = 'tests/test_api.py::test_other'
    add(test=other, signature='ValueError: intermittent')
    api = FakeAPI()  # Existing issue explicitly proves TEST/SIGNATURE.
    api.texts[1] = FAIL.replace('================ 1 failed',
        f'FAILED {other} - ValueError: intermittent\n================ 2 failed')
    result = decide(setup, api)
    assert result['result'] == 'accepted_exceptions' and len(result['ignored_failures']) == 2
    automatic = next(item for item in result['ignored_failures'] if item['test'] == TEST)
    explicit = next(item for item in result['ignored_failures'] if item['test'] == other)
    assert automatic['issues'] and not automatic['exceptions']
    assert explicit['exceptions'] and not explicit['issues']


@pytest.mark.parametrize('bucket', ['pending', 'cancel'])
def test_pipeline_exception_does_not_cover_unsettled_checks(setup, bucket):
    add('pipeline')
    snap = snapshot()
    snap['checks'][0]['bucket'] = bucket
    assert decide(setup, snap=snap)['result'] == 'failure'


def test_pipeline_exception_still_requires_current_head_and_attempt(setup):
    add('pipeline')
    api = FakeAPI(head=OTHER)
    assert decide(setup, api)['result'] == 'failure'
    api = FakeAPI(attempts=2)
    assert decide(setup, api)['result'] == 'failure'  # check links to old attempt
    assert decide(setup, api, snapshot(job_id=102))['result'] == 'accepted_exceptions'


def test_configuration_removal_and_change_during_decision_revoke_acceptance(setup):
    item = add()
    assert decide(setup)['result'] == 'accepted_exceptions'
    def refresh():
        config.configure(IDENTITY, 'remove', entry_id=item['id'])
        return snapshot()
    result = policy.decide(FakeAPI(body='No matching issue'), setup, snapshot(), refresh)
    assert result['result'] == 'failure'
    assert any('configuration changed' in blocker['reason'] for blocker in result['blockers'])
    assert decide(setup)['result'] == 'failure'


def test_repository_settings_override_user_defaults_and_empty_list_masks_them(setup):
    user = add(scope='user')
    assert config.load(IDENTITY)['source'] == 'user'
    assert decide(setup)['result'] == 'accepted_exceptions'
    repo = add(test='another test')
    assert decide(setup)['result'] == 'failure'
    config.configure(IDENTITY, 'remove', entry_id=repo['id'])
    assert config.load(IDENTITY)['entries'] == [] and config.load(IDENTITY)['source'] == 'repo'
    assert decide(setup)['result'] == 'failure'
    config.configure(IDENTITY, 'reset')
    assert config.load(IDENTITY)['entries'][0]['id'] == user['id']
    assert decide(setup)['result'] == 'accepted_exceptions'


def test_repository_isolation_and_user_defaults_apply_to_other_repos(setup):
    add()
    other = Registry('github.com/acme/other')
    assert not config.load('remote:' + other.repo)['configured']
    add(scope='user')
    assert config.load('remote:' + other.repo)['source'] == 'user'
    assert config.load('remote:github.com/ACME/Project'.lower())['source'] == 'repo'


def test_readding_same_matcher_updates_reason_without_duplicating_it(setup):
    first = add()
    second = add(reason='Updated explicit rationale')
    loaded = config.load(IDENTITY)
    assert second['id'] == first['id']
    assert len(loaded['entries']) == 1
    assert loaded['entries'][0]['reason'] == 'Updated explicit rationale'


@pytest.mark.parametrize('value', [{}, [], {'entries': []}, None, 'bad'])
def test_malformed_local_config_never_falls_back_and_can_be_reset(setup, value):
    add(scope='user')
    path = config.config_path(IDENTITY, 'repo')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        config.load(IDENTITY)
    assert decide(setup)['result'] == 'failure'
    config.configure(IDENTITY, 'reset')
    assert config.load(IDENTITY)['source'] == 'user'


def test_malformed_entry_and_corrupt_json_fail_closed_and_reset(setup):
    add()
    path = config.config_path(IDENTITY, 'repo')
    saved = json.loads(path.read_text())
    saved['entries'][0]['kind'] = []
    path.write_text(json.dumps(saved))
    assert decide(setup)['result'] == 'failure'
    path.write_text('{truncated')
    assert decide(setup)['result'] == 'failure'
    config.configure(IDENTITY, 'reset')
    assert not config.load(IDENTITY)['configured']


def test_cli_base_repository_identity_crud_and_private_permissions(setup, tmp_path):
    repo = tmp_path / 'fork'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', 'git@github.com:fork/project.git'], check=True)
    def cli(*args):
        result = subprocess.run([sys.executable, str(ROOT / 'dk-config/scripts/repo_rules.py'),
            '--cwd', str(repo), '--pr', 'https://github.com/acme/project/pull/1', 'ci-exceptions', *args],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    first = cli('add', '--kind', 'test', '--workflow', WORKFLOW, '--job', JOB, '--test', TEST,
                '--reason', 'Explicit user choice', '--scope', 'user')
    assert first['source'] == 'user'
    result = cli('add', '--kind', 'pipeline', '--workflow', WORKFLOW, '--reason', 'Unreliable pipeline')
    assert result['source'] == 'repo'
    assert config.load(IDENTITY)['entries'] == result['entries']
    assert config.load('remote:github.com/fork/project')['source'] == 'user'
    assert Path(result['file']).stat().st_mode & 0o777 == 0o600
    empty = cli('remove', result['entries'][0]['id'])
    assert empty['entries'] == [] and empty['source'] == 'repo'
    assert cli('reset')['source'] == 'user'
    assert cli('reset', '--scope', 'user')['configured'] is False


def test_cli_rejects_incomplete_matchers_without_writing_config(setup, tmp_path):
    base = [sys.executable, str(ROOT / 'dk-config/scripts/repo_rules.py'), '--cwd', str(tmp_path),
            'ci-exceptions', 'add', '--kind', 'test', '--workflow', WORKFLOW, '--reason', 'Reason']
    result = subprocess.run(base, capture_output=True, text=True)
    assert result.returncode != 0 and 'exact job and test' in result.stderr
    assert not list(config.data_root().rglob('ci-exceptions.json'))
