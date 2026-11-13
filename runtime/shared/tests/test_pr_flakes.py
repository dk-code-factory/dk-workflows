"""PR-only defaults, named rerun learning and targeted recovery, entirely offline."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from shared import ci_policy, flaky_actions, pr_flakes
from shared.flaky_tests import Registry, identity
from test_flaky_tests import FakeAPI, REPO, WORKFLOW, JOB, TEST, SIGNATURE, SHA, OTHER, PASS, snapshot, declaration


@pytest.fixture
def registry():
    return Registry(REPO)


def classify(api, registry, snap=None):
    snap = snap or snapshot()
    return ci_policy.decide(api, registry, snap, lambda: copy.deepcopy(snap))


def green(attempt=2, head=SHA):
    snap = snapshot(head=head, job_id=100 + attempt)
    snap['result'] = 'success'
    snap['checks'][0]['bucket'] = 'pass'
    return snap


def assert_no_history(api):
    assert not any(call[0] == 'request' and '/runs?' in call[1] for call in api.calls)
    assert not any(call[0] == 'request' and call[1].startswith('actions/runs?') for call in api.calls)


def test_default_records_current_failure_and_only_searches_its_test(registry):
    api = FakeAPI(body='unrelated')
    looked_up = []
    original = api.find_issues
    def find(targets, limit):
        looked_up.extend(targets)
        return original(targets, limit)
    api.find_issues = find
    result = classify(api, registry)
    assert result['result'] == 'failure'
    assert [identity(item) for item in looked_up] == [[WORKFLOW, JOB, TEST, SIGNATURE]]
    assert registry.load()['observations'][0]['outcome'] == 'fail'
    assert not registry.load()['entries']
    assert_no_history(api)


def test_green_head_without_failure_has_no_investigation_or_state(registry):
    api = FakeAPI(text=PASS)
    assert classify(api, registry, green(attempt=1))['result'] == 'success'
    assert not api.calls
    assert not registry.path.exists()


def test_named_pass_after_failure_is_learned_and_reused_across_processes(registry):
    api = FakeAPI(body=declaration(False))
    assert classify(api, registry)['result'] == 'failure'
    original_observations = copy.deepcopy(registry.load()['observations'])
    original_scans = copy.deepcopy(registry.load()['scans'])
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = PASS
    api.calls.clear()
    assert classify(api, registry, green())['result'] == 'success'
    saved = registry.load()
    assert saved['observations'][:len(original_observations)] == original_observations
    assert any(item['outcome'] == 'pass' and item['attempt'] == 2 for item in saved['observations'])
    assert not any(call[0] in {'issues', 'issue'} for call in api.calls)
    assert_no_history(api)
    before = registry.path.read_bytes()
    # A new Python process must find and validate the same completed attempt.
    driver = f'''
import json, sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from test_flaky_tests import FakeAPI, REPO, PASS, declaration
from test_pr_flakes import classify, green
from shared.flaky_tests import Registry
api = FakeAPI(body=declaration(False), attempts=2)
api.run['conclusion'] = 'success'
api.texts[2] = PASS
result = classify(api, Registry(REPO), green())
assert result['result'] == 'success'
assert not any(call[0] == 'request' and call[1].endswith('/logs') for call in api.calls)
print(json.dumps(result['learning']))
'''
    child = subprocess.run([sys.executable, '-c', driver], capture_output=True, text=True, timeout=5)
    assert child.returncode == 0, child.stderr
    assert registry.path.read_bytes() == before
    assert registry.load()['scans'] == original_scans
    # A subsequent failed attempt can now use the issue-backed historical proof.
    api.run.update(run_attempt=3, conclusion='failure')
    api.texts[3] = api.texts[1]
    assert classify(api, Registry(REPO), snapshot(job_id=103))['result'] == 'known_flakes'
    entry = next(iter(registry.load()['entries'].values()))
    assert entry['proof']['kind'] == 'same_commit_fail_pass'


@pytest.mark.parametrize('text', ['================ 1 passed in 0.10s ================', 'no named report'])
def test_green_job_alone_never_proves_the_failed_test_passed(registry, text):
    api = FakeAPI(body=declaration(False))
    classify(api, registry)
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = text
    assert classify(api, registry, green())['result'] == 'success'
    assert not any(item['outcome'] == 'pass' for item in registry.load()['observations'])
    assert not registry.synchronize([], api.issue)['entries'][0]['active']


def test_new_head_does_not_collect_passes_for_old_failure(registry):
    api = FakeAPI(body=declaration(False))
    classify(api, registry)
    api.calls.clear()
    assert classify(api, registry, green(head=OTHER))['result'] == 'success'
    assert not api.calls


def test_optional_followup_deadline_leaves_green_ci_verification_time(registry):
    api = FakeAPI(body=declaration(False))
    classify(api, registry)
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = PASS
    original, tick = api.request, [0]
    def request(endpoint, binary=False):
        tick[0] = 6
        return original(endpoint, binary)
    api.request = request
    snap = green()
    result = ci_policy.decide(api, registry, snap, lambda: copy.deepcopy(snap),
                              classification_timeout=10, clock=lambda: tick[0])
    assert result['result'] == 'success' and not result['classification']['timed_out']
    assert 'learn_followups' in result['learning']['missing'][0]
    assert not any(item['outcome'] == 'pass' for item in registry.load()['observations'])


def test_failure_and_pass_without_issue_remain_unlinked(registry):
    api = FakeAPI(body='unrelated')
    classify(api, registry)
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = PASS
    classify(api, registry, green())
    assert {item['outcome'] for item in registry.load()['observations']} == {'fail', 'pass'}
    assert not registry.synchronize([], api.issue)['entries']


def test_missing_followup_archive_is_not_proof_or_a_green_ci_failure(registry):
    api = FakeAPI(body=declaration(False))
    classify(api, registry)
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = PASS
    original = api.request
    def request(endpoint, binary=False):
        if endpoint.endswith('/2/logs'):
            raise RuntimeError('logs unavailable')
        return original(endpoint, binary)
    api.request = request
    result = classify(api, registry, green())
    assert result['result'] == 'success'
    assert 'logs unavailable' in result['learning']['missing']
    assert not any(item['outcome'] == 'pass' for item in registry.load()['observations'])
    api.request = original
    assert classify(api, Registry(REPO), green())['result'] == 'success'
    assert any(item['outcome'] == 'pass' for item in registry.load()['observations'])


def test_corrupt_followup_artifact_refreshes_only_that_attempt(registry):
    api = FakeAPI(body=declaration(False))
    classify(api, registry)
    api.run.update(run_attempt=2, conclusion='success')
    api.texts[2] = PASS
    first = classify(api, registry, green())
    old_keys = set(registry.load()['attempt_evidence'])
    Path(first['learning']['attempts'][0]['archive_file']).write_bytes(b'corrupt')
    api.calls.clear()
    result = classify(api, Registry(REPO), green())
    assert result['result'] == 'success'
    assert ('request', 'actions/runs/10/attempts/2/logs') in api.calls
    assert ('request', 'actions/runs/10/attempts/1/logs') not in api.calls
    assert set(registry.load()['attempt_evidence']) == old_keys


def test_scoped_refresh_preserves_unrelated_issue_record(registry):
    api = FakeAPI()
    classify(api, registry)
    unrelated = dict(next(iter(registry.load()['entries'].values())), test='other_test', issue_url='https://github.com/acme/project/issues/99')
    registry.update(lambda data: data['entries'].update(unrelated=unrelated))
    api.calls.clear()
    assert classify(api, registry)['result'] == 'known_flakes'
    assert ('issue', 99) not in api.calls
    assert registry.load()['entries']['unrelated'] == unrelated


def test_explicit_history_scan_still_discovers_unhandled_attempts(registry):
    api = FakeAPI(body=declaration(False), attempts=2)
    api.texts[2] = PASS
    report = flaky_actions.discover(api, registry)
    assert report['runs'] == 1 and report['attempts'] == 2
    assert report['verification']['entries'][0]['active']
    assert any(call[0] == 'request' and call[1].startswith('actions/runs?') for call in api.calls)


def test_targeted_issue_transport_quotes_tests_and_shares_limit(monkeypatch):
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([{
            'number': 7, 'url': 'https://github.com/acme/project/issues/7', 'state': 'OPEN',
            'title': 'flake', 'body': declaration(), 'updatedAt': 'now'}]), '')
    monkeypatch.setattr(pr_flakes, 'github_run', run)
    target = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)
    issues = pr_flakes.GitHub(REPO).find_issues([target, dict(target, test='zzz')], 1)
    assert len(commands) == len(issues) == 1
    assert commands[0][commands[0].index('--search') + 1] == json.dumps(TEST) + ' in:title,body sort:updated-desc'
    assert '--state' in commands[0] and 'open' in commands[0]


def load_cli():
    path = Path(__file__).resolve().parents[2]/'dk-ci/scripts/flaky_tests.py'
    spec = importlib.util.spec_from_file_location('pr_flake_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compact_status_pages_and_targeted_show_avoid_unrelated_reads(registry, monkeypatch, capsys):
    api = FakeAPI()
    classify(api, registry)
    entry = next(iter(registry.load()['entries'].values()))
    registry.update(lambda data: data['entries'].update({str(i): dict(entry, test=f'test_{i}') for i in range(25)}))
    cli = load_cli()
    monkeypatch.setattr(cli, 'GitHub', lambda *args: api)
    api.calls.clear()
    monkeypatch.setattr(sys, 'argv', ['flaky_tests.py', '--repo', REPO, '--compact', '--limit', '5', 'status'])
    cli.main()
    page = json.loads(capsys.readouterr().out)
    assert page['total'] == 26 and len(page['entries']) == page['next_offset'] == 5
    assert page['stored_only'] and not api.calls
    assert 'proof' not in page['entries'][0]
    monkeypatch.setattr(sys, 'argv', ['flaky_tests.py', '--repo', REPO, 'show', '--workflow', WORKFLOW,
                                     '--job', JOB, '--test', TEST, '--signature', SIGNATURE, '--history'])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert len(result['entries']) == 1
    assert {item['test'] for item in result['observations']} == {TEST}
    assert api.calls == [('issue', 7)]
