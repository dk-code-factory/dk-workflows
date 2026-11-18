"""Automatic incoming alternatives run once; current code review stays live."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import alternatives
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_alternatives import (LIVE, offline_live_alternatives, aligned, initial, descriptor, ordinary, finish,
                               complete, compared, selection, verified_journal)


def advance(spec):
    checkout = Path(spec['git']['checkout'])
    (checkout/'code').write_text('Author change after ' + git(checkout, 'rev-parse', 'HEAD'))
    git(checkout, 'commit', '-qam', 'Author update')
    changed = deepcopy(spec)
    changed['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    return changed


@pytest.mark.parametrize('required', [False, True])
def test_automatic_initial_review_reuses_originals_across_heads_and_fresh_processes(case, tmp_path, required):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec) if required else initial(tasks, spec, required=False)
    originals = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    source = tmp_path/'refs.json'; write(source, refs)
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO,
               '--pr', PR, 'gate', '--file', str(source)]
    current = spec
    for number in range(2):
        current = advance(current)
        # This boundary performs no GitHub or model calls in the fresh process.
        result = json.loads(subprocess.check_output(command, text=True, timeout=60))
        assert result['initial_review'] and result['release_reviewers']
        for key in refs.values():
            task = Tasks(REPO, PR).prepare({'run': f'later-{number}', 'cycle': 'review',
                'tasks': [{'key': key, 'git': {'head': current['git']['head']}}]})['tasks'][0]
            assert task['action'] == 'reuse'
            assert task['assignment']['git']['head'] == spec['git']['head']
    assert originals == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    task = tasks.prepare({'tasks': [ordinary(current, refs)]})['tasks'][0]
    assert task['action'] == 'invoke' and task['assignment']['git']['head'] == current['git']['head']
    assert any(item['id'] == 'alternatives-initial-plan' for item in task['checklist']['items'])
    assert finish(tasks, task, {'complete': True}, native=False)['checklist_complete']
    current = advance(current)
    assert tasks.prepare({'tasks': [ordinary(current, refs)]})['tasks'][0]['action'] == 'invoke'
    assert all(len(read(store.manifest)['entries'][key]['attempts']) == 1 for key in refs.values())


def test_partial_initial_review_recovers_original_claim_after_author_changes(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    a = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State choice', 'evidence': ['diff']})
    task = tasks.prepare({'run': 'initial', 'cycle': 'review',
        'tasks': [descriptor(spec, 'comparison', {'assessment': a})]})['tasks'][0]
    changed = advance(spec)
    resumed = Tasks(REPO, PR).resume()
    assert next(t for t in resumed['tasks'] if t['key'] == a)['coverage_complete']
    assert resumed['unfinished'][0]['id'] == task['claim']['id']
    request = {'key': task['key'], 'git': {'head': changed['git']['head']}}
    assert tasks.prepare({'tasks': [request]})['tasks'][0]['action'] == 'recover_required'
    recovered = tasks.prepare({'run': 'initial', 'cycle': 'review',
        'recover': {task['key']: task['claim']['id']}, 'tasks': [request]})['tasks'][0]
    assert recovered['assignment']['git']['head'] == spec['git']['head']
    assert finish(tasks, recovered, compared())['checklist_complete']
    assert len(read(store.manifest)['entries'][a]['attempts']) == 1


@pytest.mark.parametrize('change', ['requirements', 'guidance', 'scoring', 'selection', 'missing'])
def test_relevant_changes_block_automatic_reruns_and_preserve_evidence(case, change):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    options = None
    if change == 'requirements':
        Path(spec['inputs']['context']).write_text('Changed requirements')
    elif change == 'guidance':
        Path(spec['skill']['resources']['entry']).write_text('Changed examination guidance')
    elif change == 'scoring':
        options = {'alternatives': {'scoring': {'profile': 'minimal_change'}}}
    elif change == 'selection':
        options = {'alternatives': {'auto_pick': {'enabled': False}}}
    else:
        Path(alternatives.accepted(tasks, refs['comparison'])['reference']).unlink()
    before = store.manifest.read_bytes()
    with pytest.raises((ValueError, OSError), match='fresh|changed|missing|damaged'):
        alternatives.gate(Tasks(REPO, PR), refs, options=options)
    d = descriptor(spec, 'selection' if change == 'selection' else 'comparison',
                   {'comparison': refs['comparison']} if change == 'selection' else {'assessment': refs['assessment']}, options)
    with pytest.raises((ValueError, OSError), match='fresh|reassessment|missing|damaged'):
        tasks.prepare({'tasks': [d]})
    assert store.manifest.read_bytes() == before
    assert all(len(entry['attempts']) == 1 for entry in read(store.manifest)['entries'].values())


def test_pending_choice_blocks_later_heads_without_repeating_comparison(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'alternatives': {'auto_pick': {'enabled': False}}}
    a = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State choice', 'evidence': ['diff']}, options=options)
    c = complete(tasks, spec, 'comparison', compared(), {'assessment': a}, options)
    s = complete(tasks, spec, 'selection', selection(choice=None), {'comparison': c}, options)
    refs = {'assessment': a, 'comparison': c, 'selection': s}
    changed = advance(spec)
    assert alternatives.gate(tasks, refs, options=options)['outcome'] == 'choice_pending'
    source = tmp_path/'choice.txt'; source.write_text('Choose the implemented approach')
    d = descriptor(changed, 'selection', {'comparison': c}, options)
    d['definition']['inputs']['user_choice'] = str(source)
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['assignment']['git']['head'] == spec['git']['head']
    assert finish(tasks, task, selection(mode='manual', user_source=str(source)))['checklist_complete']
    assert len(read(store.manifest)['entries'][c]['attempts']) == 1


def test_explicit_manual_request_after_new_head_still_reviews_current_alternatives(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    original = aligned(tasks, spec)
    originals = {key: Path(alternatives.accepted(tasks, key)['reference']) for key in original.values()}
    saved = {key: path.read_bytes() for key, path in originals.items()}
    changed = advance(spec)
    source = tmp_path/'new-request.json'
    write(source, {'id': 'message-2', 'source': 'user:2', 'instruction': 'Reassess alternatives on this PR', 'skill': 'dk-workflows:dk-pr-review-alternatives', 'origin': 'user'})
    refs = {}
    reports = {'assessment': {'required': True, 'reason': 'Explicit request', 'evidence': [str(source)]},
               'comparison': compared(), 'selection': selection(),
               'alignment': {'alignment': 'aligned', 'reason': 'Current implementation checked', 'evidence': ['diff']}}
    metadata = tmp_path/'new-pr.json'
    LIVE[PR]['headRefOid'] = changed['git']['head']; write(metadata, LIVE[PR])
    for stage in reports:
        d = descriptor(changed, stage, {name: refs[name] for name in alternatives.REQUIRES[stage]})
        if stage == 'assessment':
            d['definition']['inputs']['alternatives_request'] = str(source)
        if stage == 'alignment':
            d['definition']['inputs']['alternatives_current_pr'] = str(metadata)
        task = tasks.prepare({'tasks': [d]})['tasks'][0]
        assert task['action'] == 'invoke' and task['assignment']['git']['head'] == changed['git']['head']
        assert task['assignment']['inputs']['alternatives_request'] == str(source)
        assert finish(tasks, task, reports[stage])['checklist_complete']
        refs[stage] = task['key']
    assert alternatives.gate(tasks, refs, manual=True)['release_reviewers']
    assert all(path.read_bytes() == saved[key] for key, path in originals.items())
    latest = advance(changed)
    LIVE[PR]['headRefOid'] = latest['git']['head']
    with pytest.raises(ValueError, match='head changed'):
        alternatives.gate(tasks, refs, manual=True)
    # Ordinary incoming review reuses the completed explicit decision as context.
    assert alternatives.gate(tasks, refs)['initial_review']
    task = tasks.prepare({'tasks': [ordinary(latest, refs)]})['tasks'][0]
    assert task['action'] == 'invoke' and task['assignment']['git']['head'] == latest['git']['head']


def test_automatic_review_cannot_schedule_a_new_impact_pass(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='once per PR'):
        tasks.prepare({'tasks': [descriptor(spec, 'impact', {'comparison': refs['comparison']})]})
    assert store.manifest.read_bytes() == before


def test_provider_changes_cannot_create_another_automatic_assessment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    options = {'alternatives': {'review_skill': 'team:new-alternatives'}}
    d = descriptor(spec, 'assessment', options=options)
    d['definition']['skill_identity'] = 'team:new-alternatives'
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='reassessment'):
        tasks.prepare({'tasks': [d]})
    assert store.manifest.read_bytes() == before
    assert len(read(store.manifest)['entries'][refs['assessment']]['attempts']) == 1


def test_delivered_initial_rejection_becomes_current_review_guidance_after_author_commits(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, alignment='mismatch')
    refs['verification'] = complete(tasks, spec, 'verification', {'disposition': 'confirmed',
        'reason': 'Wrong state owner', 'required_changes': 'Use the selected state owner', 'evidence': ['diff']},
        {'alignment': refs['alignment']})
    changed = advance(spec)
    LIVE[PR]['headRefOid'] = changed['git']['head']
    with pytest.raises(KeyError):
        alternatives.gate(tasks, refs)
    refs['review_operation'] = verified_journal(tasks, spec, kind='post-review', event='REQUEST_CHANGES',
        comments=[], body='Use the selected state owner')
    before = store.manifest.read_bytes()
    for _ in range(2):
        result = alternatives.gate(Tasks(REPO, PR), refs, analysis_only=True)
        assert result['outcome'] == 'planned' and result['release_reviewers']
        assert len(result['governing_evidence']) == 4
    assert store.manifest.read_bytes() == before
    task = tasks.prepare({'tasks': [ordinary(changed, refs)]})['tasks'][0]
    assert task['assignment']['git']['head'] == changed['git']['head']
    assert len([key for key in task['assignment']['inputs'] if key.startswith('alternatives_governing_')]) == 4
    for key in (refs[name] for name in alternatives.STAGES if name in refs):
        assert len(read(store.manifest)['entries'][key]['attempts']) == 1


@pytest.mark.parametrize('field,value', [('cacheable', False), ('governing_inputs_complete', False)])
def test_initial_review_requires_reusable_original_sources_before_claiming(case, field, value):
    spec, store = case
    d = descriptor(spec, 'assessment'); d['definition'][field] = value
    with pytest.raises(ValueError, match='cacheable original evidence'):
        Tasks(REPO, PR).prepare({'tasks': [d]})
    assert not store.manifest.exists()
