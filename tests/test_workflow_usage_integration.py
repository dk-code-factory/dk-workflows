"""Real lifecycle/helper and review-session attribution; no provider calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/dk.py'
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'runtime/dk-pr-review/tests'))
from shared import architecture, workflow_usage
from shared.review_sessions import Session
from shared.command_results import ResultFile
from test_stats_capture import pinned as pinned_review, runner, responses, prepare_current


@pytest.fixture(autouse=True)
def offline_architecture(monkeypatch):
    def analyze(prompt, checkout, validate, **kwargs):
        if 'Act as dk-architecture-review' in prompt:
            result = {'complete': True, 'reason': '', 'coverage': []}
        else:
            source = architecture.git(checkout, 'ls-files').splitlines()[0]
            item = {'text': 'Fixture source has no additional architecture policy.', 'origin': 'inferred',
                    'rationale': 'Inspected fixture source.', 'sources': [{'path': source, 'start': 1, 'end': 1}]}
            result = {'knowledge': {key: [item] for key in architecture.KNOWLEDGE},
                      'rules': [], 'proposals': [], 'conflicts': []}
        validate(result)
        return result
    monkeypatch.setattr(architecture, 'analyze', analyze)


def clean_environment(tmp_path):
    config = tmp_path / 'config/dk-code-factory/updates.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"mode":"off"}')
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / 'state'),
               XDG_DATA_HOME=str(tmp_path / 'data'), XDG_CONFIG_HOME=str(tmp_path / 'config'))
    for key in ('DK_UPDATE_PARENT', 'DK_MANAGED_INVOCATION', 'DK_UPDATE_MANAGED_RECEIPT',
                'DK_CLI_REFERENCE', workflow_usage.ENV):
        env.pop(key, None)
    return env


def command(env, cwd, *args, check=True):
    return subprocess.run([sys.executable, str(CLI), '--cwd', str(cwd), *args],
                          env=env, text=True, capture_output=True, check=check)


@pytest.mark.parametrize('terminal', ['completed', 'limited'])
def test_real_iteration_dispatch_resume_terminal_and_lifecycle_have_distinct_evidence(tmp_path, terminal):
    env = clean_environment(tmp_path)
    opened = json.loads(command(env, tmp_path, '--host', 'codex', 'begin', '--skill', 'dk-pr-iter').stdout)
    path = opened['workflow_usage']['path']
    common = ('--invocation', opened['invocation'], '--pr', 'https://github.com/o/r/pull/2', 'run-state')
    started = json.loads(command(env, tmp_path, *common, 'start-run').stdout)
    assert started['outcome'] == 'success'
    resumed = json.loads(command(env, tmp_path, *common, 'resume-run').stdout)
    finished = json.loads(command(env, tmp_path, *common, 'finish-run', '--status', terminal,
                                  '--reason', 'Recorded terminal engineering outcome.').stdout)
    native = [json.loads(Path(item['artifact']).read_text())['data'] for item in (started, resumed, finished)]
    assert native[0]['id'] == native[1]['id'] == native[2]['id']
    assert native[2]['status'] == terminal
    before = json.loads(Path(path).read_text())
    queried = json.loads(command(env, tmp_path, '--invocation', opened['invocation'], 'workflow-usage').stdout)
    assert json.loads(Path(path).read_text()) == before  # Inspection never creates another observation.
    runs = [event for event in queried['events'] if event['kind'] == 'run']
    assert [event['status'] for event in runs] == ['active', 'resumed', terminal]
    assert all(event['source'] == 'run_state' for event in runs)
    assert queried['workflow_outcome'] == terminal
    assert queried['completed_workflows'] == (1 if terminal == 'completed' else 0)
    assert queried['lifecycle_closed'] is False
    assert queried['review_coverage'] is None
    assert queried['unknown_host_invocations'] == [opened['token']]
    helper_events = {event['id']: event for event in queried['events'] if event['kind'] == 'helper'}
    assert len(helper_events) == 3
    assert all(event['status'] == 'returned' and event['artifact'] for event in helper_events.values())
    assert list(helper_events.values())[-1]['stage'] == 'completion'
    command(env, tmp_path, '--invocation', opened['invocation'], 'end')
    assert workflow_usage.query(path)['lifecycle_closed'] is True


def test_real_failed_helper_and_corrupt_usage_storage_preserve_native_behavior(tmp_path):
    env = clean_environment(tmp_path)
    opened = json.loads(command(env, tmp_path, '--host', 'codex', 'begin', '--skill', 'dk-pr-review').stdout)
    path = Path(opened['workflow_usage']['path'])
    failed = command(env, tmp_path, '--invocation', opened['invocation'], 'review-loop', 'check',
                     '--kind', 'split', '--state', 'invalid', check=False)
    assert failed.returncode == 1
    assert 'invalid review state' in failed.stderr
    events = {event['id']: event for event in workflow_usage.query(path)['events'] if event['kind'] == 'helper'}
    assert list(events.values())[-1]['status'] == 'failed'
    path.write_text('corrupt telemetry storage')
    native = command(env, tmp_path, '--invocation', opened['invocation'], '--pr',
                     'https://github.com/o/r/pull/2', 'run-state', 'start-run')
    terminal = json.loads(native.stdout)
    assert terminal['outcome'] == 'success'
    assert json.loads(Path(terminal['artifact']).read_text())['data']['status'] == 'active'
    assert 'Workflow usage evidence unavailable' in native.stderr
    command(env, tmp_path, '--invocation', opened['invocation'], 'end')


def test_zero_exit_incomplete_helper_receipt_is_not_recorded_as_success(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('usage_cli', CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    receipt = {'token': 'incomplete-root', 'skill': 'dk-pr-review', 'host': 'codex',
               'runtime_digest': 'pinned-runtime', 'runtime_root': str(ROOT),
               'invocation': str(tmp_path / 'invocation.json'), 'lifecycle': {'identity': {}}}
    ctx = workflow_usage.register(receipt, tmp_path)
    env = dict(os.environ, **{workflow_usage.ENV: json.dumps(ctx)})
    # The helper's actual process succeeds but its documented contract is incomplete.
    argv = [sys.executable, '-c', 'print(\'{"complete":false,"findings":[]}\')']
    monkeypatch.setattr(cli, 'invocation', lambda *args: (argv, tmp_path, env))
    artifact = ResultFile(tmp_path / 'helper-result.json', tmp_path, ROOT)
    try:
        assert cli.dispatch(['--host', 'codex', 'reviewers', '--context', 'unused'], artifact) == 0
        assert artifact.terminal['outcome'] == 'blocked'
    finally:
        artifact.close()
    helpers = {event['id']: event for event in workflow_usage.query(ctx['path'])['events']
               if event['kind'] == 'helper'}
    assert len(helpers) == 1
    assert next(iter(helpers.values()))['status'] == 'incomplete'


@pytest.mark.parametrize('mode', ['standalone', 'iteration'])
def test_review_resume_retains_coverage_and_checkpoint_reuse_without_new_execution(pinned_review, tmp_path, monkeypatch, mode):
    context, decisions, prep, names, git = pinned_review
    prep['mode'] = mode
    prepare_current(context, decisions, prep)
    receipt = {'token': 'observed-root', 'skill': 'dk-pr-review', 'host': 'codex',
               'runtime_digest': 'pinned-runtime', 'runtime_root': str(ROOT),
               'invocation': str(tmp_path / 'invocation.json'),
               'lifecycle': {'identity': {'name': 'dk-workflows', 'version': 'fixture'}}}
    usage_context = workflow_usage.register(receipt, tmp_path)
    monkeypatch.setenv(workflow_usage.ENV, json.dumps(usage_context))
    session = Session(context, prep, names)
    with session.execution(), patch.object(runner, 'run_skill_reviewer', side_effect=responses(
            names, [{'findings': []}, RuntimeError('provider unavailable')])):
        first = runner.review(context, names, '', decisions, '', prep, session=session)
        session.finish(first)
    assert first['complete'] is False
    resumed = Session(context, prep, names, path=session.path, resume=True)
    with resumed.execution(), patch.object(runner, 'run_skill_reviewer', return_value={'findings': []}) as calls:
        result = runner.review(context, names, '', decisions, '', prep, session=resumed)
        resumed.finish(result)
    assert calls.call_count == 1
    assert result['complete'] is True
    report = workflow_usage.query(usage_context['path'])
    coverage = [event for event in report['events'] if event['kind'] == 'review_coverage']
    assert [event['status'] for event in coverage] == ['incomplete', 'completed']
    latest = report['review_coverage']['evidence']
    assert latest['head_sha'] == context['head_sha']
    assert latest['base_sha'] == context['base_sha']
    assert latest['preparation_id'] == prep['preparation_id']
    assert latest['session_id'] == session.id
    assert latest['executed_reviewers'] == names[1:]
    assert latest['reused_reviewers'] == names[:1]
    reused = [event for event in report['events'] if event['kind'] == 'checkpoint_reuse']
    assert any(event['role'] == names[0] and event['status'] == 'reused' for event in reused)
    assert report['checkpoint_reuses'] >= 1
    assert report['usage']['tokens']['total_tokens']['known_subtotal'] is None
    assert not [event for event in report['events'] if event['kind'] == 'execution']
    assert report['workflow_outcome'] == 'unknown'
    assert not git('status', '--porcelain')
