"""Offline host-observation regressions; no native agent/model is launched."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_state import case, REPO, PR, ROOT
from state import Store, capture, digest, read, write
from native_review import required
from native_review_observations import CONTRACT, observations


@pytest.fixture
def review(case):
    spec, store = case
    spec['task']['id'] = 'content-review'
    spec['skill']['identity'] = 'selected:review'
    spec['native_review'] = {'schema': 1, 'skill_version': '1.0.7', 'passes': [
        {'role': role, 'drivers': [['python3', '/installed/aspect-driver.py', '--pass', role,
                                  '--base', spec['git']['base'], '--head', spec['git']['head']]]}
        for role in ('toolkit', 'standards', 'spec')]}
    return spec, store


def finish(spec, store, evidence=None, *, claim=None, normalized=None):
    claim = claim or store.start(spec, run='run', cycle='c1')['claim']
    original = b'{"findings": []}'
    normalized = normalized or {'complete': True, 'findings': []}
    supplied = observations(spec, claim, original)
    if evidence is not None:
        value = json.loads(supplied)
        evidence(value)
        supplied = json.dumps(value).encode()
    result = store.record(spec, claim['id'], original, normalized, 'complete', native_evidence=supplied)
    return result, claim


def test_complete_independent_native_evidence_reuses_and_binds_every_identity(review):
    spec, store = review
    claim = store.start(spec, run='run', cycle='c1')['claim']
    binding = store.native_binding(spec, claim['id'])
    assert binding['binding']['claim'] == claim['id']
    assert binding['binding']['skill_version'] == '1.0.7'
    result, _ = finish(spec, store, claim=claim)
    assert result['status'] == 'complete'
    assert store.start(spec)['action'] == 'reuse'
    saved = read(result['result'])
    assert base64.b64decode(saved['native_execution']['evidence'])
    assert saved['native_execution']['diagnostic'] is None


@pytest.mark.parametrize('change,code', [
    (lambda v: v['passes'].pop(), 'missing_pass'),
    (lambda v: v['passes'][0]['drivers'].clear(), 'missing_driver'),
    (lambda v: v['passes'][0].update(state='running'), 'incomplete_pass'),
    (lambda v: v['passes'][0].update(output=''), 'incomplete_pass'),
    (lambda v: v['passes'][0].update(handle=v['passes'][1]['handle']), 'nonindependent_pass'),
    (lambda v: v['passes'][0].update(parent='unrelated'), 'nonindependent_pass'),
    (lambda v: v['passes'][0]['drivers'][0].update(exit_code=1), 'incomplete_driver'),
    (lambda v: v['passes'][0]['drivers'][0].update(handle='another-agent'), 'incomplete_driver'),
    (lambda v: v['passes'][0]['drivers'][0].update(output=''), 'incomplete_driver'),
    (lambda v: v['passes'][0]['drivers'][0].update(state='running'), 'incomplete_driver'),
    (lambda v: v['passes'][0].pop('spawn_receipt'), 'malformed_provenance'),
    (lambda v: v.update(inventory_complete=False), 'unavailable_provenance'),
    (lambda v: v.update(availability='unsupported'), 'unavailable_provenance'),
    (lambda v: v.update(claim='other-claim'), 'binding_mismatch'),
    (lambda v: v.update(fingerprint='other-inputs'), 'binding_mismatch'),
    (lambda v: v.update(skill_version='old-version'), 'binding_mismatch'),
    (lambda v: v.update(resources='other-resources'), 'binding_mismatch'),
    (lambda v: v.update(pins={}), 'binding_mismatch'),
    (lambda v: v['invocation'].update(state='failed'), 'incomplete_invocation'),
    (lambda v: v['invocation'].update(output_sha256='another-report'), 'incomplete_invocation'),
])
def test_empty_findings_never_replace_required_host_execution_evidence(review, change, code):
    spec, store = review
    result, _ = finish(spec, store, evidence=change)
    assert result['status'] == 'incomplete'
    assert result['execution_diagnostic']['code'] == code
    assert not store.status([spec])['tasks'][0]['reusable']


@pytest.mark.parametrize('payload', [None, b'not JSON', b'[]', b'{"availability":"unsupported"}'])
def test_missing_or_unavailable_evidence_preserves_raw_output_findings_and_budget(review, payload):
    spec, store = review
    claim = store.start(spec, run='run', cycle='c1')['claim']
    original = b'Original F1 including raw\x00bytes'
    normalized = {'complete': True, 'findings': [{'id': 'F1', 'body': 'Verified bug'}],
                  'coverage': {'toolkit': 'complete'}, 'skill_owned': ['retain exactly']}
    result = store.record(spec, claim['id'], original, normalized, 'complete', native_evidence=payload)
    saved = read(result['result'])
    assert result['status'] == 'incomplete' and not result['reusable']
    assert base64.b64decode(saved['original']) == original
    assert saved['native_execution']['requested_normalized'] == normalized
    assert saved['normalized']['findings'] == normalized['findings']
    assert saved['normalized']['skill_owned'] == normalized['skill_owned']
    assert normalized['complete'] is True
    assert saved['normalized']['complete'] is False
    before = Path(result['result']).read_bytes()
    retry = store.start(spec, run='run', cycle='c1', retry=True)['claim']
    finish(spec, store, claim=retry)
    assert Path(result['result']).read_bytes() == before
    retained = store.status([spec], run='run')['runs']['run']
    assert retained['cycles'] == ['c1']
    assert list(retained['retries'].values()) == [1]


def test_missing_contract_cannot_bypass_recording_or_legacy_reuse(review):
    spec, store = review
    spec.pop('native_review')
    claim = store.start(spec)['claim']
    result = store.record(spec, claim['id'], b'No findings', {'complete': True, 'findings': []}, 'complete')
    assert result['execution_diagnostic']['code'] == 'missing_contract'
    # Emulate a genuine pre-upgrade artifact, keeping its integrity metadata valid.
    saved = read(result['result'])
    saved.pop('native_execution')
    saved['status'] = 'complete'
    saved['normalized'] = {'complete': True, 'findings': []}
    filename = digest(saved) + '.json'
    write(store.root/'results'/filename, saved)
    manifest = read(store.manifest)
    entry = manifest['entries'][capture(spec)['key']]
    entry['result'] = {'file': filename, 'hash': digest(saved), 'fingerprint': saved['fingerprint']}
    write(store.manifest, manifest)
    legacy = (store.root/'results'/filename).read_bytes()
    current = store.status([spec], explain=True)['tasks'][0]
    assert not current['reusable'] and current['reason'] == 'missing_contract'
    assert 'missing_contract' in current['explanation']['blockers']
    assert store.start(spec)['action'] == 'invoke'
    assert (store.root/'results'/filename).read_bytes() == legacy


def test_no_internal_passes_still_requires_successful_native_root(review):
    spec, store = review
    spec['native_review'] = deepcopy(CONTRACT)
    result, _ = finish(spec, store)
    assert result['status'] == 'complete'
    assert read(result['result'])['native_execution']


def test_requirements_and_skill_changes_invalidate_coverage(review):
    spec, store = review
    finish(spec, store)
    spec['native_review']['passes'].append({'role': 'performance', 'drivers': []})
    assert not store.status([spec])['tasks'][0]['reusable']
    spec['native_review']['passes'].pop()
    spec['native_review']['skill_version'] = '1.0.8'
    assert not store.status([spec])['tasks'][0]['reusable']


def test_cli_binding_and_record_transport_original_evidence(review, tmp_path):
    spec, store = review
    claim = store.start(spec)['claim']
    task = tmp_path/'spec.json'; write(task, spec)
    prefix = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR]
    binding = json.loads(subprocess.check_output(prefix + ['native-binding', '--spec', str(task), '--claim', claim['id']]))
    assert binding == store.native_binding(spec, claim['id'])
    original = tmp_path/'original'; original.write_bytes(b'No findings')
    normalized = tmp_path/'normalized.json'; write(normalized, {'complete': True, 'findings': []})
    evidence = tmp_path/'observations.json'; evidence.write_bytes(observations(spec, claim, original.read_bytes()))
    result = json.loads(subprocess.check_output(prefix + ['record', '--spec', str(task), '--claim', claim['id'],
        '--original', str(original), '--normalized', str(normalized), '--native-evidence', str(evidence), '--status', 'complete']))
    assert result['status'] == 'complete'
    assert base64.b64decode(read(result['result'])['native_execution']['evidence']) == evidence.read_bytes()


@pytest.mark.parametrize('task,skill', [('content-review', 'custom:a'), ('candidate-review', 'custom:b'),
    ('finding-verification', 'dk-workflows:dk-pr-review'), ('anything', 'code-review-ad:code-review-ad@codex')])
def test_reviewer_gate_does_not_depend_on_optional_contract(task, skill):
    assert required({'task': {'id': task}, 'skill': {'identity': skill}})
