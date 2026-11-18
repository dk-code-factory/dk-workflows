"""Preparation and immutable freezes use only local Git and synthetic conditions."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import workflow_experiment as experiment


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
