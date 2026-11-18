"""Original requests and canonical claim admission survive interruption offline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

import alternatives
import assessment_alternatives as plans
import pr_assessment as assessment
from state import Store, read, write
from tasks import Tasks
from test_state import case, REPO, PR
from test_alternatives_history import files, manual
from test_pr_assessment import complete, descriptor, purpose, inventory, comparison
from test_alternatives import finish


@pytest.mark.parametrize('owner', [assessment.FIX, assessment.REVIEW])
@pytest.mark.parametrize('required', [False, True])
def test_shared_bootstrap_keeps_legacy_applicability_until_requested_inventory(case, tmp_path, owner, required):
    from test_alternatives import complete as legacy_complete
    from test_fix_alternatives import complete as fix_complete
    spec, store = case; tasks = Tasks(REPO, PR)
    creator = fix_complete if owner == assessment.FIX else legacy_complete
    legacy = creator(tasks, spec, 'assessment', {'required': required, 'reason': 'Original applicability', 'evidence': ['issue:1']})
    original = deepcopy(read(store.manifest)['entries'][legacy])
    source = manual(tmp_path, 2)
    assert plans.gate(tasks, assessment.REVIEW, request=source)['stage'] == 'assessment'
    shared = complete(tasks, spec, 'assessment', purpose())
    before = files(store)
    for consumer in (assessment.FIX, assessment.REVIEW):
        result = plans.gate(Tasks(REPO, PR), consumer)
        assert result['references']['assessment'] == legacy
        if required:
            assert result['stage'] == 'comparison' and result['next_task']['kind'] == 'alternatives'
        else:
            assert result['outcome'] == 'skipped' and result['release_reviewers']
    assert files(store) == before
    with pytest.raises(ValueError, match='original alternatives chain'):
        tasks.prepare({'tasks': [descriptor(spec, 'inventory', {'assessment': shared})]})
    resumed = plans.gate(tasks, assessment.REVIEW, request=source)
    assert resumed['stage'] == 'inventory'
    d = descriptor(spec, 'inventory', resumed['next_task']['dependencies'], inputs={'alternatives_request': source})
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    finish(tasks, task, inventory(('storage', 'inherited')))
    # Ordinary continuation preserves the same sourced manual request.
    result = plans.gate(tasks, assessment.FIX)
    d = descriptor(spec, 'comparison', result['next_task']['dependencies'], decision='storage')
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['assignment']['inputs']['alternatives_request'] == source
    finish(tasks, task, comparison(spec))
    assert plans.gate(tasks, assessment.REVIEW, request=source)['stage'] == 'selection'
    assert len(read(store.manifest)['entries'][task['key']]['attempts']) == 1
    assert read(store.manifest)['entries'][legacy] == original


def test_old_descendant_inherits_original_request_without_repeating_comparison(case, tmp_path, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    source = manual(tmp_path, 2)
    inv = complete(tasks, spec, 'inventory', inventory(('storage', 'inherited')), {'assessment': shared},
                   inputs={'alternatives_request': source})
    d = descriptor(spec, 'comparison', {'assessment': shared, 'inventory': inv}, decision='storage')
    original_spec = tasks.spec
    # Older definitions omitted the descendant's explicit request reference.
    with monkeypatch.context() as previous:
        previous.setattr(tasks, 'spec', lambda definition, **kwargs: original_spec(definition, replay=True))
        task = tasks.prepare({'tasks': [d]})['tasks'][0]
        assert 'alternatives_request' not in task['assignment']['inputs']
        finish(tasks, task, comparison(spec))
    Path(spec['skill']['resources']['entry']).write_text('Updated installed guidance')
    before = files(store)
    result = plans.gate(Tasks(REPO, PR), assessment.REVIEW, request=source)
    assert result['stage'] == 'selection'
    assert files(store) == before
    assert len(read(store.manifest)['entries'][task['key']]['attempts']) == 1


def test_completed_renamed_alignment_uses_normalized_metadata_and_live_checks(case, monkeypatch):
    from test_alternatives import aligned
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, manual=True)
    with store.locked() as manifest:
        for key in refs.values():
            managed = manifest['entries'][key]['managed']
            managed['kind'] = 'architecture'
            inputs = managed['definition']['inputs']
            if 'alternatives_current_pr' in inputs:
                inputs['architecture_current_pr'] = inputs.pop('alternatives_current_pr')
        write(store.manifest, manifest)
    alignment = alternatives.accepted(tasks, refs['alignment'], settled=True)
    source = alignment['spec']['inputs']['alternatives_request']
    metadata = read(alignment['spec']['inputs']['alternatives_current_pr'])
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: metadata)
    before = files(store)
    assert plans.gate(tasks, assessment.REVIEW, request=source)['outcome'] == 'planned'
    assert files(store) == before
    metadata = {**metadata, 'body': 'Changed live scope'}
    assert plans.gate(tasks, assessment.REVIEW, request=source)['outcome'] == 'alignment_blocked'
    assert files(store) == before


def test_competing_routes_are_admitted_under_one_claim_lock(case, monkeypatch):
    spec, store = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    rendezvous = Barrier(2)
    original_start = Store.start_batch
    def synchronized_start(self, *args, **kwargs):
        rendezvous.wait(timeout=60)
        return original_start(self, *args, **kwargs)
    def prepare(owner):
        try:
            return Tasks(REPO, PR).prepare({'run': 'race', 'cycle': 'first', 'tasks': [
                descriptor(spec, 'inventory', {'assessment': shared}, owner=owner)]})['tasks'][0]
        except ValueError as exc:
            return {'blocked': str(exc)}
    with monkeypatch.context() as concurrent:
        concurrent.setattr(Store, 'start_batch', synchronized_start)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(prepare, owner) for owner in (assessment.FIX, assessment.REVIEW)]
            results = [future.result(timeout=60) for future in futures]
    winners = [result for result in results if result.get('action') == 'invoke']
    assert len(winners) == 1
    assert len([result for result in results if 'Canonical alternatives route changed' in result.get('blocked', '')]) == 1
    manifest = read(store.manifest)
    assert len(manifest['entries']) == 2
    assert manifest['runs']['race']['cycles'] == ['first']
    before = files(store)
    for owner in (assessment.FIX, assessment.REVIEW):
        result = plans.gate(Tasks(REPO, PR), owner)
        replay = Tasks(REPO, PR).prepare({'tasks': [result['next_task']]})['tasks'][0]
        assert replay['action'] == 'recover_required' and replay['unfinished']['id'] == winners[0]['claim']['id']
    assert files(store) == before


def test_competing_batch_rejects_before_any_claim_or_budget_change(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    shared = complete(tasks, spec, 'assessment', purpose())
    before = files(store)
    with pytest.raises(ValueError, match='Canonical alternatives route changed'):
        tasks.prepare({'run': 'race', 'cycle': 'first', 'tasks': [
            descriptor(spec, 'inventory', {'assessment': shared}, owner=owner)
            for owner in (assessment.FIX, assessment.REVIEW)]})
    assert files(store) == before
