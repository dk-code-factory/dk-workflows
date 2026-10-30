import importlib.util
from pathlib import Path
import subprocess
import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
SHA = "a" * 40
OTHER = "b" * 40


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


state = module("run_state")
ci = module("ci_wait")


def test_classification_and_preexisting_boundary():
    record = {}
    assert state.classify(record, "one", SHA, "pre_existing", "https://example.test/1", "base has bug", 20)["disposition"] == "fix"
    assert state.classify(record, "two", SHA, "pre_existing", "https://example.test/2", "base has bug", 21)["disposition"] == "deferred"
    assert state.classify(record, "three", SHA, "valid", "https://example.test/3", "introduced here")["disposition"] == "fix"
    assert state.classify(record, "four", SHA, "already_fixed", "https://example.test/4", "head changed")["disposition"] == "no_change"
    assert state.classify(record, "five", SHA, "unsupported", "https://example.test/5", "claim contradicted")["disposition"] == "no_change"
    with pytest.raises(ValueError):
        state.classify(record, "six", SHA, "pre_existing", "url", "reason")
    with pytest.raises(ValueError):
        state.classify(record, "one", SHA, "valid", "url", "changed")
    assert state.classify(record, "one", OTHER, "already_fixed", "https://example.test/1", "fixed on new head")["disposition"] == "no_change"


def test_caps_and_duplicate_actions():
    record = {}
    assert state.attempt(record, "repair", 0, SHA)["number"] == 1
    assert state.attempt(record, "rerun", 0, SHA, "initial-run")["number"] == 1
    for _ in range(5):
        state.next_cycle(record, SHA)
    with pytest.raises(ValueError): state.next_cycle(record, SHA)
    for _ in range(5): state.attempt(record, "repair", 1, SHA)
    with pytest.raises(ValueError): state.attempt(record, "repair", 1, SHA)
    for _ in range(2): state.attempt(record, "rerun", 1, SHA, "42")
    with pytest.raises(ValueError): state.attempt(record, "rerun", 1, SHA, "42")
    with pytest.raises(ValueError): state.attempt(record, "rerun", 1, SHA, "43")
    assert state.attempt(record, "rerun", 1, OTHER, "43")["number"] == 1
    with pytest.raises(ValueError): state.attempt(record, "rerun", 2, SHA, "44")
    state.append_unique(record, "events", {"key": "reply:1", "kind": "reply"}, "key")
    with pytest.raises(ValueError): state.append_unique(record, "events", {"key": "reply:1"}, "key")


def test_usage_limited_last_cycle_can_be_retried_once(tmp_path):
    record = {"events": []}
    for _ in range(5):
        state.next_cycle(record, SHA)
    record["events"].append({"kind": "review", "key": "review-cycle-5:old:incomplete",
                             "detail": "Claude Correctness run hit session limit", 'outcome': 'usage_exhausted'})
    path = tmp_path / "run.json"
    state.write(path, record)
    retry = state.mutate(path, {}, lambda data: state.retry_exhausted_cycle(data, OTHER))
    assert retry == {"number": 5, "prior_head": SHA, "head": OTHER}
    assert len(state.read(path, {})["cycles"]) == 5
    with pytest.raises(ValueError, match="already recorded"):
        state.retry_exhausted_cycle(state.read(path, {}), OTHER)


def test_cycle_retry_rejects_other_incomplete_or_completed_reviews():
    record = {"events": []}
    state.next_cycle(record, SHA)
    with pytest.raises(ValueError, match="not an incomplete"):
        state.retry_exhausted_cycle(record, SHA)
    record["events"].append({"kind": "review", "key": "review-cycle-1:head:incomplete",
                             "detail": "finding parse failed"})
    with pytest.raises(ValueError, match="not an incomplete"):
        state.retry_exhausted_cycle(record, SHA)
    record["events"][0]["detail"] = "usage limit reached"
    with pytest.raises(ValueError, match="not an incomplete"):
        state.retry_exhausted_cycle(record, SHA)
    record['events'][0]['outcome'] = 'usage_exhausted'
    record["events"].append({"kind": "review", "key": "review-cycle-1:head:clean",
                             "detail": "complete"})
    with pytest.raises(ValueError, match="not an incomplete"):
        state.retry_exhausted_cycle(record, SHA)


@pytest.mark.parametrize('status,allowed', [('exhausted', True), ('failed', False), ('running', False)])
def test_existing_result_uses_typed_session_outcomes_without_migration(tmp_path, monkeypatch, status, allowed):
    monkeypatch.setattr(state, 'work_gate', lambda *args: {})
    artifact = {'result': {'complete': False}, 'session_identity': {'reviewers': ['reviewer']}}
    monkeypatch.setattr(state.review_coverage, 'load', lambda ref: artifact)
    monkeypatch.setattr(state.review_sessions, 'result_artifact', lambda *args: artifact)
    path = tmp_path / 'session.json'
    state.write(path, {'reviews': {'reviewer': {'status': status, 'reason': 'usage limit prose is irrelevant'}}})
    original = path.read_bytes()
    record = {'iteration_run': {'id': 'run'}, 'cycles': [{'number': 1, 'head': SHA}],
        'review_results': [{'cycle': 1, 'iteration_run_id': 'run', 'path': 'result.json',
                            'sha256': 'hash', 'session_file': str(path)}]}
    if allowed:
        assert state.retry_exhausted_cycle(record, SHA)['number'] == 1
    else:
        with pytest.raises(ValueError, match='not an incomplete'):
            state.retry_exhausted_cycle(record, SHA)
    assert path.read_bytes() == original and 'outcome' not in artifact['result']


def test_material_decision_gate():
    options = ["retain API", "change API"]
    assert state.decision_route(options, True) == "ask_human"
    assert state.decision_route(options, True, "retain API") == "record_and_implement"
    with pytest.raises(ValueError): state.decision_route(options, False, "retain API")
    with pytest.raises(ValueError): state.decision_route(options, True, "unrelated")


def test_local_choices_survive_record_reload(tmp_path):
    path = tmp_path / "run.json"
    state.mutate(path, {"local_decisions": []},
                 lambda record: state.record_local_decision(record, "Keep API", "user instruction"))
    restored = state.read(path, {})
    assert restored["local_decisions"] == [{"text": "Keep API", "source": "user instruction"}]
    with pytest.raises(ValueError, match="conflicting sources"):
        state.record_local_decision(restored, "Keep API", "different source")


def test_flaky_requires_same_head_and_later_pass():
    data = {}
    fail = state.record_flaky(data, "test_a", "assertion", SHA, "fail", "run1", "https://example.test/1")
    assert not fail["flaky"]
    assert not state.record_flaky(data, "test_a", "assertion", OTHER, "pass", "run2", "https://example.test/2")["flaky"]
    passed = state.record_flaky(data, "test_a", "assertion", SHA, "pass", "run3", "https://example.test/3")
    assert passed["flaky"] and passed["failures"][0]["attempt"] == "run1"
    with pytest.raises(ValueError): state.record_flaky(data, "test_a", "assertion", SHA, "pass", "run3", "https://example.test/3")


@pytest.mark.parametrize("buckets,expected", [([], "no_checks"), (["pass"], "success"), (["pass", "skipping"], "success"), (["pending"], "pending"), (["fail"], "failure"), (["cancel"], "failure")])
def test_check_bucket_outcomes(monkeypatch, buckets, expected):
    def fake(args):
        if args[1] == "view": return {"headRefOid": SHA}
        return [{"name": str(i), "bucket": bucket} for i, bucket in enumerate(buckets)]
    monkeypatch.setattr(ci, "gh_json", fake)
    assert ci.observe("1", SHA)["result"] == expected


def test_head_change_before_or_after_checks(monkeypatch):
    answers = iter([{"headRefOid": SHA}, [{"bucket": "pass"}], {"headRefOid": OTHER}])
    monkeypatch.setattr(ci, "gh_json", lambda _: next(answers))
    assert ci.observe("1", SHA)["result"] == "head_changed"
    monkeypatch.setattr(ci, "gh_json", lambda _: {"headRefOid": OTHER})
    assert ci.observe("1", SHA)["result"] == "head_changed"


def test_wait_timeout_and_no_checks():
    ticks = iter([0, 11])
    clock = lambda: next(ticks)
    observer = lambda *args: {"result": "pending", "head": SHA, "checks": []}
    assert ci.wait("1", SHA, timeout=10, clock=clock, sleep=lambda _: None, observer=observer)["result"] == "timeout"
    ticks = iter([0, 5])
    observer = lambda *args: {"result": "no_checks", "head": SHA, "checks": []}
    assert ci.wait("1", SHA, timeout=10, no_checks_grace=5, clock=lambda: next(ticks), sleep=lambda _: None, observer=observer)["result"] == "no_checks"


def test_wait_reports_progress_without_extra_observations():
    elapsed = [0]
    observations = []
    progress = []

    def observer(*args):
        observations.append(elapsed[0])
        state = "success" if elapsed[0] == 90 else "pending"
        return {"result": state, "head": SHA, "checks": [{"bucket": state}]}

    result = ci.wait("1", SHA, clock=lambda: elapsed[0],
                     sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
                     observer=observer, on_progress=progress.append)
    assert result["result"] == "success"
    assert observations == [0, 30, 60, 90]
    assert [item["elapsed_seconds"] for item in progress] == [0, 60]

completion = module("completion")

@pytest.fixture(autouse=True)
def prepared_boundaries(monkeypatch):
    # These existing tests isolate CI/series/counter policy. Preparation's real
    # gates and resume interactions are exercised in test_preparation.py.
    monkeypatch.setattr(state, 'work_gate', lambda record, head: {'readiness': 'ready', 'reviewers': {}})
    monkeypatch.setattr(completion, 'current_preparation', lambda *a: {'mode': 'iteration'})
    monkeypatch.setattr(completion.run_state_api, 'verify_review', lambda *a, **kw: {})
    monkeypatch.setattr(completion, 'live_preparation_scope', lambda *a: {'body': '', 'headRefOid': SHA})
    monkeypatch.setattr(completion.preparation_api, 'verify_completion', lambda *a: {'checks': 'passed'})
    original = completion.completion_gate
    def gate(*args, **kwargs):
        kwargs.setdefault('preparation', '/private/preparation.json')
        kwargs.setdefault('pr_url', PR)
        kwargs.setdefault('verification', '/private/verification.json')
        kwargs.setdefault('run_record', '/private/run-record.json')
        return original(*args, **kwargs)
    monkeypatch.setattr(completion, 'completion_gate', gate)


PR = 'https://github.com/acme/project/pull/1'
TREE = 'c' * 40
TIP = 'd' * 40


def series_evidence():
    return dict(schema_version=1, pr_url=PR, head_repo='acme/project',
                reviewed_head=OTHER, reviewed_tree=TREE, target_tip=TIP,
                target_branch='main', final_head=SHA, outcome='published',
                commit_count=1, reasons=['Feature and fixups form one coherent commit.'])


def series_live():
    return dict(pr_url=PR, head_repo='acme/project', final_head=SHA, final_tree=TREE,
                target_tip=TIP, target_branch='main', commit_count=1)


def test_complete_gate_requires_assessment_even_on_unchanged_head():
    assert completion.completion_gate(SHA, SHA, SHA, True, False, 'success') == 'series_missing'
    evidence = series_evidence() | dict(reviewed_head=SHA, outcome='no_op')
    assert completion.completion_gate(SHA, SHA, SHA, True, False, 'success',
                                      assessment=evidence, series_snapshot=series_live()) == 'clean'
    with pytest.raises(ValueError, match='stage'):
        completion.completion_gate(SHA, SHA, SHA, True, False, 'success', stage='skip')


@pytest.mark.parametrize('change,expected', [
    ({'reasons': []}, 'series_invalid'),
    ({'reasons': [' ']}, 'series_invalid'),
    ({'commit_count': True}, 'series_invalid'),
    ({'outcome': 'pending'}, 'series_invalid'),
    ({'outcome': 'no_op'}, 'series_invalid'),
    ({'reviewed_head': 'short'}, 'series_invalid'),
    ({'pr_url': 'https://github.com/acme/project/pull/2'}, 'series_pr_mismatch'),
    ({'head_repo': 'fork/project'}, 'series_pr_mismatch'),
    ({'final_head': TIP}, 'series_head_changed'),
    ({'reviewed_tree': OTHER}, 'series_tree_changed'),
    ({'target_tip': OTHER}, 'series_target_changed'),
    ({'target_branch': 'next'}, 'series_target_changed'),
    ({'commit_count': 2}, 'series_count_changed'),
])
def test_complete_gate_rejects_stale_or_malformed_series(change, expected):
    assert completion.completion_gate(
        OTHER, SHA, SHA, True, False, 'success', assessment=series_evidence() | change,
        series_snapshot=series_live()) == expected


@pytest.mark.parametrize('result', ['failure', 'pending', 'timeout', 'no_checks', 'head_changed'])
def test_assessed_final_head_still_requires_green_ci(result):
    assert completion.completion_gate(
        OTHER, SHA, SHA, True, False, {'result': result, 'head': SHA},
        assessment=series_evidence(), series_snapshot=series_live()) == 'ci_not_green'


def test_assessed_final_head_preserves_live_known_flake_policy():
    accepted = dict(result='known_flakes', raw_result='failure', head=SHA,
                    blockers=[], verified_at='now', ignored_failures=[{'issues': ['issue']}])
    kwargs = dict(assessment=series_evidence(), series_snapshot=series_live())
    assert completion.completion_gate(OTHER, SHA, SHA, True, False, accepted, **kwargs) == 'ci_not_green'
    assert completion.completion_gate(OTHER, SHA, SHA, True, False, accepted,
                                      lambda: accepted, **kwargs) == 'clean'
    assert completion.completion_gate(OTHER, SHA, SHA, True, False, accepted,
                                      lambda: accepted | {'head': OTHER}, **kwargs) == 'ci_not_green'


def test_gate_cli_defaults_to_complete_and_observes_series_after_ci(monkeypatch, capsys, tmp_path):
    import sys
    import shared.ci_policy
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    args = ['completion.py', '--cwd', str(tmp_path), '--pr', PR, 'gate',
            '--start-head', OTHER, '--final-head', SHA, '--review-complete', '--preparation-file', '/private/preparation.json', '--verification-file', '/private/verification.json', '--run-record', '/private/run-record.json']
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(SystemExit) as exc:
        completion.main()
    assert exc.value.code == 2 and '--series-assessment' in capsys.readouterr().err
    order = []
    monkeypatch.setattr(ci, 'observe', lambda *args: {'head': SHA, 'result': 'success'})
    monkeypatch.setattr(shared.ci_policy, 'decide', lambda *a, **k: (
        order.append('ci') or {'head': SHA, 'result': 'success', 'blockers': []}))
    monkeypatch.setattr(completion.series_assessment, 'load', lambda *a: series_evidence())
    monkeypatch.setattr(completion.series_assessment, 'command', lambda *a: TREE)
    monkeypatch.setattr(completion.series_assessment, 'live_snapshot', lambda *a: (
        order.append('series') or series_live()))
    monkeypatch.setattr(sys, 'argv', args + ['--series-assessment', str(tmp_path / 'series.json')])
    completion.main()
    assert json_output(capsys)['gate'] == 'clean'
    assert order == ['ci', 'series']
    monkeypatch.setattr(completion.series_assessment, 'live_snapshot', lambda *a: series_live() | {'target_tip': OTHER})
    with pytest.raises(SystemExit) as exc:
        completion.main()
    assert exc.value.code == 1 and json_output(capsys)['gate'] == 'series_target_changed'
    order.clear()
    monkeypatch.setattr(sys, 'argv', args + ['--stage', 'review'])
    completion.main()
    assert json_output(capsys)['gate'] == 'clean' and order == ['ci']


def json_output(capsys):
    import json
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('phase', ['initial_scope', 'ci_observation', 'final_series'])
def test_completion_cli_operational_subprocess_errors_have_diagnostics(tmp_path, monkeypatch, capsys, phase):
    import sys
    from shared import ci_policy, github_reads, flaky_actions, flaky_tests
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    monkeypatch.setattr(completion.series_assessment, 'load', lambda *a: series_evidence())
    monkeypatch.setattr(ci_policy, 'decide', lambda *a, **k: {'head': SHA, 'result': 'success'})
    monkeypatch.setattr(flaky_actions, 'GitHub', lambda *a: None)
    monkeypatch.setattr(flaky_tests, 'Registry', lambda *a, **k: None)
    reads = []
    if phase == 'initial_scope':
        # Exercise the real initial check_output call with a refusing executable.
        gh = tmp_path / 'gh'
        gh.write_text('#!' + sys.executable + '\nraise SystemExit(1)\n')
        gh.chmod(0o700)
        monkeypatch.setenv('PATH', str(tmp_path))
        live_scope = module('completion').live_preparation_scope
        monkeypatch.setattr(completion, 'live_preparation_scope', live_scope)
    else:
        def process(argv, **kwargs):
            if argv[0] == 'git':
                return subprocess.CompletedProcess(argv, 0, TREE, '')
            reads.append((argv, kwargs.get('timeout'), github_reads.read_only(argv)))
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        monkeypatch.setattr(github_reads.subprocess, 'run', process)
        ticks = iter([0, 0, 61])
        monkeypatch.setattr(github_reads.time, 'monotonic', lambda: next(ticks))
        if phase == 'final_series':
            monkeypatch.setattr(ci, 'observe', lambda *a: {'head': SHA, 'result': 'success'})
    stage = 'review' if phase == 'ci_observation' else 'complete'
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--pr', PR, 'gate', '--stage', stage,
        '--start-head', OTHER, '--final-head', SHA, '--review-complete', '--series-assessment', 'fixture',
        '--preparation-file', 'fixture', '--verification-file', 'fixture', '--run-record', 'fixture'])
    with pytest.raises(SystemExit) as stopped:
        completion.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err.splitlines()[-1].startswith('completion: ')
    assert 'Traceback' not in captured.err
    if phase != 'initial_scope':
        assert len(reads) == 1 and reads[0][1:] == (60, True)


def test_review_precheck_live_error_invalidates_coverage(monkeypatch):
    from shared import cleanup_precheck
    def unavailable(*args):
        raise RuntimeError('Live repository identity changed')
    monkeypatch.setattr(cleanup_precheck, 'review_for', unavailable)
    assert completion.completion_gate(OTHER, SHA, SHA, True, False, 'success',
        stage='review', cleanup_precheck='/private/precheck.json') == 'review_coverage_invalid'


@pytest.mark.parametrize('failure_stage', ['gate', 'report'])
def test_review_precheck_cli_reports_live_errors(monkeypatch, capsys, failure_stage):
    import json
    import sys
    from shared import cleanup_precheck, ci_policy
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    monkeypatch.setattr(ci, 'observe', lambda *a: {'head': SHA, 'result': 'success'})
    monkeypatch.setattr(ci_policy, 'decide', lambda *a, **k: {'head': SHA, 'result': 'success', 'blockers': []})
    calls = []
    def review(*args):
        calls.append(args)
        if failure_stage == 'gate' or len(calls) == 2:
            raise RuntimeError('Live repository identity changed')
        return {}
    monkeypatch.setattr(cleanup_precheck, 'review_for', review)
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--pr', PR, 'gate', '--stage', 'review',
        '--start-head', OTHER, '--final-head', SHA, '--review-complete',
        '--preparation-file', '/private/preparation.json', '--verification-file', '/private/verification.json',
        '--run-record', '/private/run-record.json', '--cleanup-precheck', '/private/precheck.json'])
    with pytest.raises(SystemExit) as stopped:
        completion.main()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output['gate'] == 'review_coverage_invalid'
    assert output['review_limitation'] == 'Live repository identity changed'
    assert not any(output[key] for key in ('review_ready', 'cleanup_ready', 'completion_ready'))
    assert len(calls) == 2
    if failure_stage == 'gate':
        assert captured.err == ''
    else:
        assert json.loads(captured.err)['phase'] == 'terminal'


def test_completion_prompt_precedence_and_reset(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    clone = tmp_path / "clone"
    for path in (repo, clone):
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "git@github.com:Owner/Repo.git"], check=True)
    assert completion.override_path("repo", repo) == completion.override_path("repo", clone)
    assert completion.selected(repo)[0] == "default"
    completion.set_override(completion.override_path("user", repo), "user prompt")
    assert completion.selected(repo) == ("user", "user prompt")
    completion.set_override(completion.override_path("repo", repo), "repo prompt")
    assert completion.selected(clone) == ("repo", "repo prompt")
    completion.override_path("repo", repo).unlink()
    assert completion.selected(repo) == ("user", "user prompt")
    completion.override_path("user", repo).unlink()
    assert completion.selected(repo)[0] == "default"
    for bad in ("", " \n", "bad\x00text"):
        with pytest.raises(ValueError): completion.set_override(completion.override_path("user", repo), bad)
    completion.override_path("user", repo).write_bytes(b"\xff")
    with pytest.raises(ValueError): completion.selected(repo)


def test_completion_prompt_fork_upstream_user_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    fork = tmp_path / 'fork'
    upstream = tmp_path / 'upstream'
    for path, remote in ((fork, 'git@github.com:fork/repo.git'),
                         (upstream, 'git@github.com:base/repo.git')):
        path.mkdir()
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', remote], check=True)
    subprocess.run(['git', '-C', str(fork), 'remote', 'add', 'upstream',
                    'git@github.com:base/repo.git'], check=True)
    completion.set_override(completion.override_path('user', fork), 'user prompt')
    assert completion.selected(fork) == ('user', 'user prompt')
    completion.set_override(completion.override_path('repo', upstream), 'upstream prompt')
    assert completion.selected(fork) == ('upstream', 'upstream prompt')
    completion.set_override(completion.override_path('repo', fork), 'fork prompt')
    assert completion.selected(fork) == ('repo', 'fork prompt')
    monkeypatch.setattr(completion, 'identities', lambda root, pr=None: [
        'remote:github.com/fork/repo', 'remote:github.com/base/repo'])
    assert completion.selected(upstream, 'https://github.com/base/repo/pull/7') == ('repo', 'fork prompt')


@pytest.mark.parametrize("review_complete,actionable,ci_result,live_head,expected", [
    (True, False, "success", SHA, "clean"),
    (False, False, "success", SHA, "review_incomplete"),
    (True, True, "success", SHA, "actionable_work"),
    (True, False, "timeout", SHA, "ci_not_green"),
    (True, False, "success", OTHER, "head_changed"),
])
def test_completion_gate(review_complete, actionable, ci_result, live_head, expected):
    assert completion.completion_gate(OTHER, SHA, live_head, review_complete, actionable, ci_result, stage='review') == expected


def test_thread_audit_resolution_and_duplicate_reply():
    open_thread = {"isResolved": False, "comments": [{"author": {"login": "reviewer"}, "body": "please fix"}]}
    assert completion.thread_action(open_thread, False, True, "fixed.", "author") == "leave_open"
    assert completion.thread_action(open_thread, True, True, "fixed.", "author") == "reply_then_resolve"
    open_thread["comments"].append({"author": "Author", "text": "The existing behavior already covers this case.", "url": "https://github.com/owner/repo/pull/7#discussion_r2"})
    reply = {"url": open_thread["comments"][-1]["url"], "text": open_thread["comments"][-1]["text"]}
    assert completion.thread_action(open_thread, True, True, reply, "author") == "resolve"
    assert completion.thread_action(open_thread, True, True, reply, "automation-user") == "resolve"
    assert completion.thread_action(open_thread, True, False, "", "author") == "reply_then_resolve"
    assert completion.thread_action(open_thread, True, True, "fixed.", "author") == "reply_then_resolve"
    open_thread["comments"].append({"reference": "C3", "author": "reviewer", "text": "Still broken"})
    assert completion.thread_action(open_thread, True, True, reply, "author") == "reply_then_resolve"
    open_thread['comments'][-1]['text'] = 'Thanks, this covers it.'
    reply['laterResponses'] = [{'source': 'C3', 'disposition': 'no_objection',
                               'reason': 'Reviewer confirms this is addressed.'}]
    assert completion.thread_action(open_thread, True, True, reply, "author") == "resolve"
    assert completion.thread_action(open_thread, True, True, reply, "automation-user") == "resolve"
    assert completion.thread_action({"isResolved": True}, False, True, "", "author") == "complete"
    with pytest.raises(ValueError): completion.thread_action({"isResolved": False, "comments": []}, True, False, "", "author")


def test_reviewer_candidates_and_duplicate_request_prevention():
    reviews = [
        {"user": {"login": "Alice"}, "state": "COMMENTED", "commit_id": OTHER},
        {"user": {"login": "Alice"}, "state": "APPROVED", "commit_id": SHA},
        {"user": {"login": "bot[bot]"}, "state": "COMMENTED", "commit_id": OTHER},
        {"user": {"login": "Bob"}, "state": "CHANGES_REQUESTED", "commit_id": OTHER},
        {"user": {"login": "Author"}, "state": "COMMENTED", "commit_id": OTHER},
        {"user": {"login": "Pending"}, "state": "COMMENTED", "commit_id": OTHER},
        {"user": {"login": "Draft"}, "state": "PENDING", "commit_id": OTHER},
    ]
    assert completion.reviewer_candidates(reviews, ["Pending"], "Author", SHA) == ["bot[bot]", "Bob"]
    assert completion.reviewer_candidates(reviews, ["Pending"], "Author", SHA, ["Bob"]) == ["bot[bot]"]

threads = module("completion_threads")


def test_complete_thread_audit_keeps_resolved_and_open(monkeypatch, tmp_path):
    metadata = {"headRefOid": SHA, "number": 1}
    all_threads = [
        {"id": "resolved", "isResolved": True, "comments": [{"body": "done"}]},
        {"id": "open", "isResolved": False, "comments": [{"body": "request"}]},
    ]
    calls = []
    monkeypatch.setattr(threads.fetcher, "resolve_pull_request", lambda pr, cwd: metadata)
    def fetch(meta, cwd):
        calls.append(1)
        return all_threads
    monkeypatch.setattr(threads.fetcher, "fetch_all_threads", fetch)
    result = threads.snapshot("1", tmp_path)
    assert result["complete"] and result["threadCount"] == 2
    assert {item["id"] for item in result["threads"]} == {"open", "resolved"}
    assert len(calls) == 2


@pytest.mark.parametrize('field,reference', [('reviewReference', 'pullrequestreview-42'),
                                             ('commentReference', 'discussion_r4365784024')])
def test_complete_thread_audit_preserves_selection(monkeypatch, tmp_path, field, reference):
    metadata = {"headRefOid": SHA, "number": 1, field: reference}
    all_threads = [{"id": "selected", "isResolved": True,
                    "comments": [{"reviewReference": "pullrequestreview-42", "databaseId": 4365784024,
                                  "text": "request"}]},
                   {"id": "unrelated", "isResolved": False, "comments": []}]
    monkeypatch.setattr(threads.fetcher, "resolve_pull_request", lambda pr, cwd: metadata)
    monkeypatch.setattr(threads.fetcher, "fetch_all_threads", lambda meta, cwd: all_threads)
    result = threads.snapshot('https://github.com/owner/repo/pull/1#' + reference, tmp_path)
    assert result['selection'][field] == reference
    assert result['selection']['threadReferences'] == ['selected']
    assert result['threadCount'] == 2


@pytest.mark.parametrize('matches', [0, 2])
def test_complete_thread_audit_rejects_missing_or_ambiguous_comment(monkeypatch, tmp_path, matches):
    metadata = {"headRefOid": SHA, "number": 1, "commentReference": "discussion_r4365784024"}
    all_threads = [{"id": str(i), "comments": [{"databaseId": 4365784024}]} for i in range(matches)]
    monkeypatch.setattr(threads.fetcher, "resolve_pull_request", lambda pr, cwd: metadata)
    monkeypatch.setattr(threads.fetcher, "fetch_all_threads", lambda meta, cwd: all_threads)
    with pytest.raises(threads.fetcher.FetchError, match='exactly one thread'):
        threads.snapshot('https://github.com/owner/repo/pull/1#discussion_r4365784024', tmp_path)


def test_complete_thread_audit_rejects_unstable_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(threads.fetcher, "resolve_pull_request", lambda pr, cwd: {"headRefOid": SHA})
    count = iter(range(3))
    monkeypatch.setattr(threads.fetcher, "fetch_all_threads", lambda meta, cwd: [{"id": str(next(count))}])
    with pytest.raises(threads.fetcher.FetchError):
        threads.snapshot("1", tmp_path)


def test_failed_checks_do_not_short_circuit_pending_jobs(monkeypatch):
    elapsed = [0]
    observations = []
    def fake(args):
        if args[1] == 'view':
            return {'headRefOid': SHA}
        observations.append(elapsed[0])
        return [{'bucket': 'fail'}, {'bucket': 'pending' if elapsed[0] < 60 else 'pass'}]
    monkeypatch.setattr(ci, 'gh_json', fake)
    result = ci.wait('1', SHA, clock=lambda: elapsed[0],
                     sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    assert result['result'] == 'failure' and observations == [0, 30, 60]


@pytest.mark.parametrize('effective', ['known_flakes', 'accepted_exceptions'])
def test_completion_red_ci_requires_fresh_live_verification(effective):
    accepted = {'result': effective, 'raw_result': 'failure', 'head': SHA,
                'blockers': [], 'verified_at': 'now',
                'ignored_failures': [{'issues': ['https://github.com/acme/project/issues/7']} ]}
    gate = lambda evidence, verify=None: completion.completion_gate(
        OTHER, SHA, SHA, True, False, evidence, verify, stage='review')
    assert gate(effective) == 'ci_not_green'
    assert gate(accepted) == 'ci_not_green'
    assert gate(accepted, lambda: accepted) == 'clean'
    assert gate(accepted, lambda: accepted | {'result': 'failure', 'blockers': ['issue closed']}) == 'ci_not_green'
    assert gate(accepted, lambda: accepted | {'head': OTHER}) == 'ci_not_green'
    assert gate(accepted, lambda: accepted | {'ignored_failures': []}) == 'ci_not_green'
    assert gate(accepted, lambda: accepted | {'verified_at': None}) == 'ci_not_green'


@pytest.mark.parametrize('effective', ['known_flakes', 'accepted_exceptions'])
def test_waiter_main_accepts_effective_result_and_preserves_raw(monkeypatch, capsys, effective):
    import sys
    raw = {'result': 'failure', 'head': SHA, 'checks': [{'bucket': 'fail'}]}
    accepted = raw | {'result': effective, 'raw_result': 'failure',
                      'ignored_failures': [{'issues': ['https://github.com/acme/project/issues/7']}],
                      'blockers': []}
    monkeypatch.setattr(sys, 'argv', ['ci_wait.py', '--pr', 'https://github.com/acme/project/pull/1', '--head', SHA])
    monkeypatch.setattr(ci, 'wait', lambda *a, **k: raw)
    monkeypatch.setattr(ci, 'Registry', lambda *a, **k: object())
    monkeypatch.setattr(ci, 'decide', lambda *a, **k: accepted)
    ci.main()
    import json
    assert json.loads(capsys.readouterr().out) == accepted
    monkeypatch.setattr(ci, 'decide', lambda *a, **k: raw)
    with pytest.raises(SystemExit) as failed:
        ci.main()
    assert failed.value.code == 1


@pytest.mark.parametrize('effective', ['known_flakes', 'accepted_exceptions'])
@pytest.mark.parametrize('classification_timeout', [None, 120])
def test_completion_gate_cli_refreshes_ci_policy(monkeypatch, capsys, effective, classification_timeout):
    import json
    import sys
    from shared import ci_policy, flaky_actions, flaky_tests
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--pr', 'https://github.com/acme/project/pull/1',
                        'gate', '--stage', 'review', '--start-head', OTHER, '--final-head', SHA, '--review-complete', '--preparation-file', '/private/preparation.json', '--verification-file', '/private/verification.json', '--run-record', '/private/run-record.json'])
    monkeypatch.setattr(ci, 'observe', lambda *a: {'result': 'failure', 'head': SHA, 'checks': []})
    monkeypatch.setattr(flaky_tests, 'Registry', lambda *a, **k: object())
    accepted = {'result': effective, 'raw_result': 'failure', 'head': SHA, 'blockers': [],
                'ignored_failures': [{'issues': ['https://github.com/acme/project/issues/7']}], 'verified_at': 'now'}
    calls = []
    if classification_timeout is not None:
        sys.argv += ['--classification-timeout', str(classification_timeout)]
    def decide(*a, **kwargs):
        assert kwargs['classification_timeout'] == (classification_timeout or 60)
        calls.append('live')
        return accepted
    monkeypatch.setattr(ci_policy, 'decide', decide)
    completion.main()
    assert json.loads(capsys.readouterr().out)['gate'] == 'clean'
    assert calls == ['live']
    accepted.update(result='failure', blockers=[{'reason': 'issue closed'}])
    with pytest.raises(SystemExit) as closed:
        completion.main()
    assert closed.value.code == 1
    assert json.loads(capsys.readouterr().out)['gate'] == 'ci_not_green'


def test_waiter_reports_terminal_status_before_classification(monkeypatch, capsys):
    import json
    import sys
    raw = {"result": "failure", "head": SHA, "checks": [{"bucket": "fail"}]}
    monkeypatch.setattr(sys, 'argv', ['ci_wait.py', '--pr', 'https://github.com/acme/project/pull/1',
                                    '--head', SHA, '--classification-timeout', '7'])
    monkeypatch.setattr(ci, 'wait', lambda *a, **k: raw)
    monkeypatch.setattr(ci, 'Registry', lambda *a, **k: object())
    def classify(*args, **kwargs):
        update = json.loads(capsys.readouterr().err)
        assert update['phase'] == 'terminal' and update['raw_result'] == 'failure'
        assert update['check_count'] == 1 and 'checks' not in update
        assert kwargs['classification_timeout'] == 7
        kwargs['on_progress']({'phase': 'discover_unknown'})
        return raw
    monkeypatch.setattr(ci, 'decide', classify)
    with pytest.raises(SystemExit) as failed:
        ci.main()
    output = capsys.readouterr()
    assert failed.value.code == 1
    assert json.loads(output.out) == raw
    assert json.loads(output.err)['phase'] == 'discover_unknown'


def test_observation_retains_canonical_url_without_extra_reads(monkeypatch):
    calls = []
    url = 'https://github.com/acme/project/pull/70'
    def read(args):
        calls.append(args)
        if args[1] == 'checks':
            return [{'bucket': 'pass'}]
        return {'headRefOid': SHA, 'url': url}
    monkeypatch.setattr(ci, 'gh_json', read)
    observed = ci.observe('70', SHA)
    assert observed['url'] == url and observed['result'] == 'success'
    assert len(calls) == 3 and calls[0][-1] == 'headRefOid,url'


def test_numbered_waiter_reuses_observed_url_and_defers_legacy_import(monkeypatch, capsys):
    import sys
    url = 'https://github.com/acme/project/pull/70'
    raw = {'result': 'success', 'head': SHA, 'checks': [{'bucket': 'pass'}], 'url': url}
    monkeypatch.setattr(sys, 'argv', ['ci_wait.py', '--pr', '70', '--head', SHA])
    monkeypatch.setattr(ci, 'wait', lambda *a, **k: raw)
    original = ci.pr_repository
    def repository(pr, repo=None):
        assert pr == url, 'numbered PR resolution must be completed during polling'
        return original(pr, repo)
    def registry(*args, **kwargs):
        assert kwargs == {'import_legacy': False}
        return object()
    monkeypatch.setattr(ci, 'pr_repository', repository)
    monkeypatch.setattr(ci, 'Registry', registry)
    monkeypatch.setattr(ci, 'decide', lambda *a, **k: raw)
    ci.main()
    assert 'success' in capsys.readouterr().out


@pytest.mark.parametrize('timeout', ['0', '-1'])
def test_completion_rejects_invalid_budget_before_live_queries(monkeypatch, capsys, timeout):
    import sys
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--pr', 'https://github.com/acme/project/pull/1',
        'gate', '--start-head', OTHER, '--final-head', SHA, '--preparation-file', '/private/preparation.json',
        '--verification-file', '/private/verification.json', '--run-record', '/private/run-record.json',
        '--classification-timeout', timeout])
    def unexpected(*args):
        raise AssertionError('invalid budget must not query live preparation or CI')
    monkeypatch.setattr(completion, 'live_preparation_scope', unexpected)
    with pytest.raises(SystemExit) as invalid:
        completion.main()
    assert invalid.value.code == 2
    assert 'classification timeout must be positive' in capsys.readouterr().err


@pytest.mark.parametrize('raw_result', ['success', 'failure'])
def test_completion_reports_terminal_and_phase_progress_before_blocking_timeout(
        monkeypatch, capsys, raw_result):
    import json
    import sys
    from shared import ci_policy, flaky_tests
    monkeypatch.setitem(sys.modules, 'ci_wait', ci)
    monkeypatch.setattr(sys, 'argv', ['completion.py', '--pr', PR, 'gate', '--stage', 'review',
        '--start-head', OTHER, '--final-head', SHA, '--review-complete',
        '--preparation-file', 'fixture', '--verification-file', 'fixture', '--run-record', 'fixture',
        '--classification-timeout', '7'])
    raw = {'result': raw_result, 'head': SHA,
           'checks': [{'bucket': 'pass' if raw_result == 'success' else 'fail'}]}
    observations = []
    def observe(*args):
        observations.append(raw_result)
        return raw
    monkeypatch.setattr(ci, 'observe', observe)
    monkeypatch.setattr(flaky_tests, 'Registry', lambda *a, **k: object())
    def classify(*args, **kwargs):
        # A user must already know the terminal raw state when classification begins.
        diagnostic = capsys.readouterr().err
        assert diagnostic, 'completion must report terminal CI before classification'
        terminal = json.loads(diagnostic)
        assert terminal == {'phase': 'terminal', 'raw_result': raw_result,
                            'head': SHA, 'check_count': 1}
        assert kwargs['classification_timeout'] == 7
        kwargs['on_progress']({'phase': 'verify_current', 'elapsed_seconds': 7})
        return raw | {'result': 'failure', 'raw_result': raw_result,
                      'classification': {'timed_out': True},
                      'blockers': [{'reason': 'CI classification deadline exceeded during verify_current'}]}
    monkeypatch.setattr(ci_policy, 'decide', classify)
    with pytest.raises(SystemExit) as blocked:
        completion.main()
    assert blocked.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {'phase': 'verify_current', 'elapsed_seconds': 7}
    output = json.loads(captured.out)
    assert output['gate'] == 'ci_not_green' and output['ci']['classification']['timed_out']
    assert not any(output[key] for key in ('review_ready', 'cleanup_ready', 'completion_ready'))
    assert observations == [raw_result, raw_result]
