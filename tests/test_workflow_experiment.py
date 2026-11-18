"""Preparation and immutable freezes use only local Git and synthetic conditions."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import workflow_experiment as experiment
import workflow_remote
import workflow_quality


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'
    for route in ('fix', 'review'):
        folder = root/'codex/skills'/('dk-pr-'+route)
        folder.mkdir(parents=True)
        (folder/'config.json').write_bytes((experiment.ROOT/'codex/skills'/('dk-pr-'+route)/'config.json').read_bytes())
    (root/'DESIGN.md').write_text('Frozen source.\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.test', '-c', 'commit.gpgsign=false',
                    'commit', '-qm', 'Freeze fixture package'], check=True)
    return root, subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()


@pytest.mark.parametrize('route,task_count,finalizer_count', [('fix', 9, 7), ('review', 4, 3)])
def test_preparation_preserves_real_default_selections_and_never_resets(tmp_path, source, route,
                                                                     task_count, finalizer_count):
    checkout, revision = source
    root = tmp_path/('arm '+route)
    scenario = experiment.initialize(root, checkout, revision, route)
    plan = json.loads(Path(scenario['default_plan_source']).read_text())
    assert len(plan['tasks']) == task_count and len(plan['finalizers']) == finalizer_count
    assert plan['alternatives']['enabled'] and plan['simplification']['enabled']
    settings = json.loads(Path(scenario['ordinary_reviewer_settings_source']).read_text())
    assert settings['skill_defaults']['dk-workflows:dk-pr-reviewers-run']['options']['reviewers'] == [
        experiment.ORDINARY_REVIEWER]
    assert any(item.get('skill') == 'dk-workflows:dk-pr-reviewers-run' for item in plan['tasks'])
    assert 'preparation-only' in scenario['status']
    original = (root/'remote.json').read_bytes()
    with pytest.raises(ValueError, match='Never replace'):
        experiment.initialize(root, checkout, revision, route)
    assert (root/'remote.json').read_bytes() == original
    # The shell environment supports paths with spaces and routes gh locally.
    result = subprocess.run(['sh', '-c', '. "$1/environment.sh"; gh pr view '
        +experiment.PR+' --json headRefOid', 'fixture', str(root)],
        check=True, text=True, capture_output=True)
    assert json.loads(result.stdout)['headRefOid'] == scenario['head']


def conditions(root):
    path = root/'conditions.json'
    experiment.save(path, {name+'_sha256': 'a'*64 for name in
        ('instructions', 'tools', 'controls', 'assignment', 'quality_oracle', 'transport_verification')})
    return path


def test_fresh_process_freeze_reuse_and_relevant_drift(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    experiment.initialize(root, checkout, revision, 'fix')
    external = tmp_path/'native-skill.md'
    external.write_text('Actual native provider resource.\n')
    experiment.freeze(root, conditions=conditions(root), external_resources=[external])
    original = (root/'freeze.json').read_bytes()
    code = 'import json,sys;from workflow_experiment import verify_freeze;print(json.dumps(verify_freeze(sys.argv[1])))'
    result = subprocess.run([sys.executable, '-c', code, str(root)], check=True,
                            text=True, capture_output=True,
                            env={**os.environ,
                                 'PYTHONPATH': str(experiment.ROOT/'tests/fixtures')})
    assert json.loads(result.stdout)['passed']
    (root/'artifacts/result.json').write_text('{"complete":false}')
    assert experiment.verify_freeze(root)['passed']
    assert (root/'freeze.json').read_bytes() == original
    external.write_text('Changed provider instructions.\n')
    assert not experiment.verify_freeze(root)['passed']
    with pytest.raises(ValueError, match='Never replace'):
        experiment.freeze(root, conditions=root/'conditions.json', external_resources=[external])
    assert (root/'freeze.json').read_bytes() == original


def test_incomplete_preflight_cannot_seal_an_arm(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    experiment.initialize(root, checkout, revision, 'review')
    path = root/'conditions.json'
    experiment.save(path, {'controls_sha256': 'a'*64})
    with pytest.raises(ValueError, match='Missing observed'):
        experiment.freeze(root, conditions=path, external_resources=[])
    assert not (root/'freeze.json').exists()


def test_explicit_ordinary_prerequisite_is_frozen_and_cannot_drift(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    scenario = experiment.initialize(root, checkout, revision, 'review')
    settings = Path(scenario['ordinary_reviewer_settings_source'])
    original = settings.read_bytes()
    settings.write_text('{"skill_defaults":{}}')
    with pytest.raises(ValueError, match='reviewer prerequisite changed'):
        experiment.freeze(root, conditions=conditions(root), external_resources=[])
    settings.write_bytes(original)
    experiment.freeze(root, conditions=conditions(root), external_resources=[])
    settings.write_text('{"skill_defaults":{}}')
    assert not experiment.verify_freeze(root)['passed']


def test_controlled_partial_restore_keeps_original_freeze_and_inputs(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    experiment.initialize(root, checkout, revision, 'fix')
    experiment.freeze(root, conditions=conditions(root), external_resources=[])
    original = (root/'freeze.json').read_bytes()
    experiment.partial_context(root, available=False)
    assert not experiment.verify_freeze(root)['passed']
    experiment.partial_context(root, available=True)
    experiment.phase(root, 'recover')
    assert experiment.verify_freeze(root)['passed']
    assert (root/'freeze.json').read_bytes() == original
    assert json.loads((root/'control/phase.json').read_text())['phase'] == 'recover'


def test_quality_probes_require_published_clean_source_and_preserve_original_requirements(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    experiment.initialize(root, checkout, revision, 'review')
    original = workflow_quality.probe(root)
    assert original['probe_complete']
    assert set(original['violated_requirements']) == {'html-text', 'per-call-history'}

    def publish():
        experiment.git(root/'checkout', 'add', '.')
        experiment.git(root/'checkout', 'commit', '-qm', 'Controlled author correction')
        head = experiment.git(root/'checkout', 'rev-parse', 'HEAD').decode().strip()
        experiment.git(root/'checkout', 'push', str(root/'remote.git'), 'HEAD:pr-branch')
        state = json.loads((root/'remote.json').read_text())
        workflow_remote.Remote(state).pushed('pr-branch', head)
        experiment.save(root/'remote.json', state)

    implementation = root/'checkout/greeting.py'
    implementation.write_text('from html import escape\n\ndef greeting(name):\n'
                              '    return "<p>Hello, " + escape(name) + "</p>", [name]\n')
    with pytest.raises(ValueError, match='unpublished'):
        workflow_quality.probe(root)
    publish()
    corrected = workflow_quality.probe(root)
    assert corrected['probe_complete'] and corrected['violated_requirements'] == []
    requirements = root/'checkout/README.md'
    requirements.write_text(requirements.read_text() +
        '\nAn empty name displays guest but keeps the original name in per-call history.\n')
    publish()
    changed = workflow_quality.probe(root, guest_required=True)
    assert changed['violated_requirements'] == ['empty-name']
    assert changed['source']['requirements_sha256'] != original['source']['requirements_sha256']
    implementation.write_text('from html import escape\n\ndef greeting(name):\n'
                              '    display = name if name else "guest"\n'
                              '    return "<p>Hello, " + escape(display) + "</p>", [name]\n')
    experiment.git(root/'checkout', 'add', '.')
    experiment.git(root/'checkout', 'commit', '-qm', 'Unpublished guest correction')
    with pytest.raises(ValueError, match='differs from published'):
        workflow_quality.probe(root, guest_required=True)
    # The controller publishes this already committed correction.
    head = experiment.git(root/'checkout', 'rev-parse', 'HEAD').decode().strip()
    experiment.git(root/'checkout', 'push', str(root/'remote.git'), 'HEAD:pr-branch')
    state = json.loads((root/'remote.json').read_text())
    workflow_remote.Remote(state).pushed('pr-branch', head)
    experiment.save(root/'remote.json', state)
    final = workflow_quality.probe(root, guest_required=True)
    assert final['probe_complete'] and final['violated_requirements'] == []


def test_ci_release_requires_current_observation_and_actual_clean_source_tests(tmp_path, source):
    checkout, revision = source
    root = tmp_path/'arm'
    scenario = experiment.initialize(root, checkout, revision, 'review')
    with pytest.raises(ValueError, match='No observed'):
        experiment.release_ci(root)

    def observe():
        result = subprocess.run(['sh', '-c', '. "$1/environment.sh"; gh pr checks '
            +experiment.PR+' --json name,bucket,state', 'fixture', str(root)],
            text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    assert observe()[0]['bucket'] == 'pending'
    released = experiment.release_ci(root)
    assert released['head'] == scenario['head'] and released['returncode'] == 0
    original_evidence = {path: path.read_bytes() for path in (root/'artifacts').glob('ci-controller-*.json')}
    assert len(original_evidence) == 1
    assert experiment.release_ci(root) == released
    assert all(path.read_bytes() == content for path, content in original_evidence.items())
    assert observe()[0]['bucket'] == 'pass'
    (root/'checkout/test_failure.py').write_text('import unittest\nclass Failing(unittest.TestCase):\n'
                                              '    def test_failure(self):\n        self.fail("CI fails")\n')
    with pytest.raises(ValueError, match='unpublished'):
        experiment.release_ci(root)
    experiment.git(root/'checkout', 'add', '.')
    experiment.git(root/'checkout', 'commit', '-qm', 'Introduce controlled test failure')
    head = experiment.git(root/'checkout', 'rev-parse', 'HEAD').decode().strip()
    experiment.git(root/'checkout', 'push', str(root/'remote.git'), 'HEAD:pr-branch')
    state = json.loads((root/'remote.json').read_text())
    workflow_remote.Remote(state).pushed('pr-branch', head)
    assert state['historical_runs'][0]['head_sha'] == scenario['head']
    assert state['historical_runs'][0]['conclusion'] == 'success'
    assert state['run']['id'] != state['historical_runs'][0]['id']
    experiment.save(root/'remote.json', state)
    with pytest.raises(ValueError, match='No observed'):
        experiment.release_ci(root)
    observe()
    failed = experiment.release_ci(root)
    assert failed['head'] == head and failed['returncode'] != 0
    assert observe()[0]['bucket'] == 'fail'
    assert len(list((root/'artifacts').glob('ci-controller-*.json'))) == 2
    assert all(path.read_bytes() == content for path, content in original_evidence.items())


@pytest.mark.parametrize('override', ['repository', 'execution'])
def test_preparation_isolates_inherited_git_state_and_execution(tmp_path, source, monkeypatch, override):
    checkout, revision = source
    original = {str(path.relative_to(checkout)): path.read_bytes()
                for path in checkout.rglob('*') if path.is_file()}
    marker = tmp_path/'hook-executed'
    if override == 'repository':
        monkeypatch.setenv('GIT_DIR', str(checkout/'.git'))
        monkeypatch.setenv('GIT_WORK_TREE', str(checkout))
        monkeypatch.setenv('GIT_INDEX_FILE', str(checkout/'.git/index'))
    else:
        hooks = tmp_path/'hooks'
        hooks.mkdir()
        script = hooks/'pre-commit'
        script.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 91\n')
        script.chmod(0o755)
        monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
        monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.hooksPath')
        monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(hooks))
    before_environment = dict(os.environ)
    error = None
    try:
        experiment.initialize(tmp_path/'isolated-arm', checkout, revision, 'fix')
    except subprocess.CalledProcessError as caught:
        error = caught
    assert {str(path.relative_to(checkout)): path.read_bytes()
            for path in checkout.rglob('*') if path.is_file()} == original
    assert not marker.exists()
    assert dict(os.environ) == before_environment
    assert error is None
    root = tmp_path/'isolated-arm'
    result = subprocess.run(['sh', '-c', '. "$1/environment.sh"; git -C "$1/checkout" '
                             'rev-parse --show-toplevel', 'fixture', str(root)],
                            text=True, capture_output=True, check=True)
    assert result.stdout.strip() == str(root/'checkout')
    assert {str(path.relative_to(checkout)): path.read_bytes()
            for path in checkout.rglob('*') if path.is_file()} == original


@pytest.mark.parametrize('route,ownership', [('fix', 'mine'), ('review', 'others')])
def test_real_helpers_collect_complete_stable_feedback_through_local_transport(tmp_path, route, ownership):
    revision = subprocess.check_output(['git', '-C', str(experiment.ROOT), 'rev-parse', 'HEAD']).decode().strip()
    root = tmp_path/'native-preparation'
    experiment.initialize(root, experiment.ROOT, revision, route)

    def execute(script, *args):
        result = subprocess.run(['sh', '-c', '. "$1/environment.sh"; shift; exec "$@"',
            'fixture', str(root), sys.executable, str(root/'package'/script), *map(str, args)],
            cwd=root/'checkout', check=True, text=True, capture_output=True)
        return json.loads(result.stdout)

    routed = execute('scripts/pr_route.py', '--pr', experiment.PR)
    assert routed['ownership'] == ownership
    ordinary = execute('scripts/config.py', '--repository', 'github.com/owner/repo',
                       '--checkout', root/'checkout', 'skill-plan',
                       '--skill', 'dk-workflows:dk-pr-reviewers-run', '--kind', 'reviewers')
    assert ordinary['groups'] == [[experiment.ORDINARY_REVIEWER]]
    threads = execute('runtime/dk-pr-feedback-address/scripts/fetch_unresolved_review_threads.py',
                      '--pr', experiment.PR, '--cwd', root/'checkout')
    assert threads['complete'] and threads['itemCount'] == 0
    snapshot = root/'artifacts/threads.json'
    snapshot.write_text(json.dumps(threads))
    conversation = execute('runtime/dk-pr-feedback-address/scripts/fetch_pr_conversation.py',
                           '--snapshot-file', snapshot, '--cwd', root/'checkout')
    assert conversation['complete'] and conversation['reviewCount'] == 0
    # Exercise journal recovery through the real publisher, not a native-result stand-in.
    state_path = root/'remote.json'
    state = json.loads(state_path.read_text())
    state['lose_response'] = ['edit-pr' if route == 'fix' else 'post-review']
    state_path.write_text(json.dumps(state))
    if route == 'fix':
        request = {'kind': 'edit-pr', 'pr': experiment.PR, 'head': routed['head'],
                   'fields': {'body': 'Safe HTML greeting with independent history.'}}
    else:
        request = {'kind': 'post-review', 'pr': experiment.PR, 'head': routed['head'],
                   'event': 'COMMENT', 'body': 'Verified HTML escaping issue.',
                   'comments': [{'path': 'greeting.py', 'line': 3, 'side': 'RIGHT',
                                 'body': 'Render the supplied name as HTML text.'}]}
    path = root/'artifacts/publication-request.json'
    path.write_text(json.dumps(request))
    prepared = execute('scripts/publish.py', 'prepare', '--file', path)
    # Dispatch failure is expected after the fixture applied exactly one write.
    result = subprocess.run(['sh', '-c', '. "$1/environment.sh"; shift; exec "$@"',
        'fixture', str(root), sys.executable, str(root/'package/scripts/publish.py'),
        'publish', '--operation', prepared['operation']], cwd=root/'checkout',
        text=True, capture_output=True)
    assert result.returncode != 0
    recovered = execute('scripts/publish.py', 'reconcile', '--operation', prepared['operation'])
    assert recovered['status'] == 'verified'
    settled = json.loads(state_path.read_text())
    assert settled['writes'][request['kind']] == 1
    if route == 'review':
        current = execute('runtime/dk-pr-feedback-address/scripts/fetch_unresolved_review_threads.py',
                          '--pr', experiment.PR, '--cwd', root/'checkout')
        assert current['complete'] and current['itemCount'] == 1
