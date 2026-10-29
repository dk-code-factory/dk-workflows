"""Same-snapshot agreement changes must stop every live preparation consumer."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from preparation_fixtures import Package, ROOT, prep, response, retain
from shared import agreement_binding

spec = importlib.util.spec_from_file_location('freshness_api', ROOT / 'dk-pr-decisions/scripts/decisions.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def package(tmp_path):
    return Package(tmp_path)


def stored(package):
    path = api.storage.cache_path(package.context['url'])
    return api.storage.read(path, package.context['url']), path


def add(package, text='Keep the transform local', replaces=None):
    state, path = stored(package)
    item = api.add_decision(state, {'text': text, 'references': []},
                            'Exact answer: ' + text, replaces)
    api.save_state(state, path)
    return item


def export(package):
    state, _ = stored(package)
    with patch.object(api, 'gh_json', return_value=state['pr']):
        package.decisions = api.decision_context(state)
    package.decisions['decisions'] = api.decision_records(state, package.decisions['local_decisions'])
    package.write('decisions.json', package.decisions)


@pytest.mark.parametrize('replacement', [False, True])
def test_same_head_and_body_change_blocks_collection_work_and_history_stays_readable(package, replacement):
    if replacement:
        original = add(package)
        export(package)
    saved = package.prepare()
    artifact = prep.read(saved['artifact'])
    if replacement:
        add(package, 'Use the agreed shared transform', original['id'])
    else:
        add(package)
    with patch.object(prep, 'analyze') as model:
        with pytest.raises(ValueError, match='recorded agreements changed.*refresh context and preparation'):
            prep.prepare(package.manifest_path, package.versions, previous_preparation=saved['artifact'])
    model.assert_not_called()
    with pytest.raises(ValueError, match='recorded agreements changed'):
        prep.gate(saved['artifact'], pr_url=package.context['url'], head_sha=package.head, body='')
    assert prep.historical(saved['artifact']) == artifact
    export(package)
    refreshed = prep.prepare(package.manifest_path, package.versions, previous_preparation=saved['artifact'],
                             analyzer=lambda *_: retain(artifact))
    assert refreshed['engineering_id'] != saved['engineering_id']
    prep.gate(refreshed['artifact'], pr_url=package.context['url'], head_sha=package.head)


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'corrupt_record', 'different_store', 'pending', 'unbound'])
def test_unusable_store_blocks_without_model_network_or_writes(package, monkeypatch, failure):
    saved = package.prepare()
    state, path = stored(package)
    if failure == 'missing':
        path.unlink()
    elif failure == 'corrupt':
        path.write_text('{broken')
    elif failure == 'corrupt_record':
        state['saved_decisions'] = {'invalid': {'text': 'Broken record'}}
        path.write_text(json.dumps(state))
    elif failure == 'different_store':
        monkeypatch.setenv('DK_PR_DECISIONS_HOME', str(package.root / 'another-store'))
    elif failure == 'pending':
        state['publication_intent'] = {'id': 'pending', 'status': 'pending', 'body': '',
                                       'snapshot': {'headRefOid': package.head, 'body': ''}}
        api.save_state(state, path)
    else:
        package.decisions.pop('agreement_binding')
        package.write('decisions.json', package.decisions)
    before = {str(p): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    with patch.object(api, 'gh_json', side_effect=AssertionError('network')), patch.object(prep, 'analyze') as model:
        with pytest.raises(ValueError, match='freshness check failed'):
            prep.collect(package.manifest_path)
        if failure != 'unbound':
            with pytest.raises(ValueError, match='freshness check failed'):
                prep.gate(saved['artifact'], pr_url=package.context['url'], head_sha=package.head)
    model.assert_not_called()
    assert before == {str(p): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}


@pytest.mark.parametrize('with_agreement', [False, True])
def test_operational_refresh_preserves_even_empty_verified_agreements(package, with_agreement):
    if with_agreement:
        add(package)
        export(package)
    saved = package.prepare()
    state, path = stored(package)
    state.update(run_number=999, viewer='another-viewer', summaries={}, publication_history=[])
    for record in state['saved_decisions'].values():
        record['publication_status'] = 'published'
    api.save_state(state, path)
    prep.gate(saved['artifact'], pr_url=package.context['url'], head_sha=package.head)
    export(package)
    refreshed = prep.prepare(package.manifest_path, package.versions, previous_preparation=saved['artifact'])
    assert refreshed['engineering_id'] == saved['engineering_id']
    assert refreshed['refresh']['model_call_count'] == 0


def test_store_move_is_operational_but_selected_store_must_match(package, monkeypatch):
    package.manifest['evidence']['choices'] = ['decisions.json']
    package.write('manifest.json', package.manifest)
    saved = package.prepare()
    original = copy.deepcopy(package.decisions)
    state, path = stored(package)
    monkeypatch.setenv('DK_PR_DECISIONS_HOME', str(package.root / 'selected-store'))
    with pytest.raises(ValueError, match='selected Decisions store changed'):
        prep.gate(saved['artifact'], pr_url=package.context['url'], head_sha=package.head)
    api.storage.atomic_write(api.storage.cache_path(package.context['url']), json.dumps(state))
    export(package)
    assert prep.graph_api.semantic('decisions', json.dumps(original).encode()) == prep.graph_api.semantic(
        'decisions', json.dumps(package.decisions).encode())
    refreshed = prep.prepare(package.manifest_path, package.versions, previous_preparation=saved['artifact'])
    assert refreshed['engineering_id'] == saved['engineering_id']
    assert refreshed['refresh']['model_call_count'] == 0


def test_agreement_changed_during_preparation_never_publishes_result(package):
    def analyze(*_):
        add(package)
        return response()
    with pytest.raises(ValueError, match='recorded agreements changed'):
        prep.prepare(package.manifest_path, package.versions, analyzer=analyze)
    assert not list(package.versions.glob('preparation-*/preparation.json'))


def test_agreement_changed_after_evidence_retention_blocks_finalization(package):
    original = prep.engineering_id
    def changed(prepared):
        add(package)
        return original(prepared)
    with patch.object(prep, 'engineering_id', side_effect=changed), pytest.raises(ValueError, match='recorded agreements changed'):
        package.prepare()
    assert not list(package.versions.glob('preparation-*'))


def test_unbound_artifacts_are_history_and_parents_but_require_refreshed_context_for_execution(package):
    package.decisions.pop('agreement_binding')
    package.write('decisions.json', package.decisions)
    # Simulate a retained artifact produced before the additive binding requirement.
    with patch.object(agreement_binding, 'check'):
        saved = package.prepare()
    parent = prep.historical(saved['artifact'])
    with pytest.raises(ValueError, match='bound Decisions context required'):
        prep.gate(saved['artifact'], pr_url=package.context['url'], head_sha=package.head)
    export(package)
    refreshed = prep.prepare(package.manifest_path, package.versions, previous_preparation=saved['artifact'],
                             analyzer=lambda *_: retain(parent))
    prep.gate(refreshed['artifact'], pr_url=package.context['url'], head_sha=package.head)


def test_exact_evidence_and_supersession_govern_binding(package):
    first = add(package)
    export(package)
    original = copy.deepcopy(package.decisions)
    state, path = stored(package)
    state['saved_decisions'][first['id']]['source'] += ' (corrected exact answer)'
    api.save_state(state, path)
    with pytest.raises(ValueError, match='recorded agreements changed'):
        agreement_binding.check(original)
    export(package)
    assert original['agreement_binding']['fingerprint'] != package.decisions['agreement_binding']['fingerprint']
    second = add(package, 'Use a shared transform', first['id'])
    state, _ = stored(package)
    records = api.decision_records(state)
    assert next(r for r in records if r['id'] == first['id'])['superseded_by'] == second['id']


def test_imported_choices_stay_temporary_and_recover_persisted_choice_and_history_in_another_process(package):
    draft = package.write('temporary-choice.json', [{'text': 'Keep the transform local', 'source': 'Exact human answer'}])
    state, _ = stored(package)
    with patch.object(api, 'gh_json', return_value=state['pr']):
        imported = api.decision_context(state, json.loads(draft.read_text()))
    assert imported['agreement_binding']['decision_ids'] == []
    assert api.decision_records(stored(package)[0]) == []
    first = add(package)
    draft.unlink()
    def recover():
        result = subprocess.run([sys.executable, str(ROOT / 'dk-pr-decisions/scripts/decisions.py'),
                                 '--pr', package.context['url'], 'list'], text=True, capture_output=True, check=True)
        return json.loads(result.stdout)['data']['decisions']
    assert recover()[0]['source'] == 'Exact answer: Keep the transform local'
    replacement = add(package, 'Use the shared transform', first['id'])
    records = recover()
    old = next(r for r in records if r['id'] == first['id'])
    assert old['superseded_by'] == replacement['id']
    assert old['source'] == 'Exact answer: Keep the transform local'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('replacement', [False, True])
def test_work_result_acceptance_and_both_completion_stages_recheck_store(package, replacement):
    if replacement:
        original = add(package)
        export(package)
    package.manifest['mode'] = 'iteration'
    package.write('manifest.json', package.manifest)
    saved = package.prepare()
    prepared = prep.read(saved['artifact'])
    state = load_module('agreement_run_state', 'dk-pr-iter/scripts/run_state.py')
    completion = load_module('agreement_completion', 'dk-pr-iter/scripts/completion.py')
    record = {'local_decisions': []}
    state.start_run(record)
    state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
    state.next_cycle(record, package.head)
    record_path = package.write('run-record.json', record)
    from shared.review_sessions import Session
    binding = state.review_session(record, package.head, record_path)
    package.write('run-record.json', record)
    session = Session(package.context, prepared, ['example'], path=binding['session_file'])
    result = {'complete': True, 'findings': [], 'executed_reviewers': ['example'],
              'reused_reviewers': [], 'skipped_reviewers': []}
    item = session.finish(result)
    state.register_review(record, {'review_artifact': item}, package.head)
    package.write('run-record.json', record)
    source = package.repo / 'transform.py'
    check = {'satisfied': True, 'rationale': 'Inspected supported transform and its tests',
             'evidence': [{'path': str(source), 'sha256': prep.file_hash(source)}]}
    verification = package.write('verification.json', {'verification_version': 2,
        'engineering_id': saved['engineering_id'], 'preparation_id': saved['preparation_id'],
        'pr_url': package.context['url'], 'head_sha': package.head,
        'checks': {name: check for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})
    args = (package.head, package.head, package.head, True, False, 'success')
    kwargs = dict(preparation=saved['artifact'], pr_url=package.context['url'], body='',
                  verification=verification, run_record=record_path)
    assert completion.completion_gate(*args, stage='review', **kwargs) == 'clean'
    if replacement:
        add(package, 'Use the agreed shared transform', original['id'])
    else:
        add(package)
    with pytest.raises(ValueError, match='recorded agreements changed'):
        state.work_gate(record, package.head)
    with pytest.raises(ValueError, match='recorded agreements changed'):
        state.register_review(record, {'review_artifact': item}, package.head)
    with pytest.raises(ValueError, match='recorded agreements changed'):
        session.finish(result)
    for stage in ('review', 'complete'):
        assert completion.completion_gate(*args, stage=stage, **kwargs) == 'preparation_invalid'


def test_changed_agreements_block_review_launch_and_checkpoint_reuse(package):
    runner = load_module('agreement_runner', 'dk-pr-review/scripts/run_reviewers.py')
    saved = package.prepare()
    prepared = prep.read(saved['artifact'])
    add(package)
    from unittest.mock import Mock
    session = Mock()
    session.cached.return_value = {'findings': []}
    with patch.object(runner, 'run_skill_reviewer') as model, pytest.raises(ValueError, match='recorded agreements changed'):
        runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=session)
    model.assert_not_called()
    session.cached.assert_not_called()


def test_agreement_change_during_reviewer_execution_blocks_checkpoint_and_result(package):
    runner = load_module('agreement_during_runner', 'dk-pr-review/scripts/run_reviewers.py')
    saved = package.prepare()
    prepared = prep.read(saved['artifact'])
    from shared.review_sessions import Session
    session = Session(package.context, prepared, ['example'])
    def review(*_args):
        add(package)
        return {'findings': []}
    with patch.object(runner, 'describe_reviewer', return_value=prepared['reviewers']['example']), \
            patch.object(runner, 'skill_path', return_value=package.root), \
            patch.object(runner, 'run_skill_reviewer', side_effect=review) as model:
        with pytest.raises(ValueError, match='recorded agreements changed'):
            runner.review(package.context, ['example'], '', package.decisions, '', prepared, session=session)
    model.assert_called_once()
    retained = json.loads(session.path.read_text())
    assert retained['reviews']['example']['status'] == 'running'
    assert retained['results'] == []
