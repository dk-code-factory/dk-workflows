"""Offline alternatives gate, scoring and fresh-session recovery acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import alternatives
import config
import invocation
import skill_config
from state import read, write
from tasks import Tasks
from test_state import case, REPO, PR, ROOT, git
from test_batch_state import definition
from checklist_reports import report as checklist_report
from native_review_observations import CONTRACT, observations, PREFLIGHT_CAPABILITIES
from shared import publication


LIVE = {}
REAL_LIVE_PR = alternatives.live_pr


@pytest.fixture(autouse=True)
def offline_live_alternatives(monkeypatch):
    LIVE.clear()
    monkeypatch.setattr(alternatives, 'live_pr', lambda pr: deepcopy(LIVE[pr]))


def compared():
    options = []
    for name, kind, rating in [('implemented', 'pr', 90), ('smaller', 'smallest', 70)]:
        options.append({
            'id': name, 'kind': kind, 'approach': name + ' components', 'components': 'service and store',
            'pros': 'Preserves compatibility', 'cons': 'Migration cost',
            'handled_edges': 'Retries deduplicate writes', 'unhandled_edges': 'Unavailable store delays work',
            'production_loc': {'min': 10, 'expected': 20, 'max': 30, 'reason': 'Added + deleted production code from diff base'},
            'complexity': 20, 'complexity_reason': 'One state owner',
            'intrusiveness': 10, 'intrusiveness_reason': 'One interface changes',
            'ratings': {k: rating for k in alternatives.CRITERIA},
            'rating_evidence': {k: 'code:1 and measured workload constraints' for k in alternatives.CRITERIA},
            'feasible': True, 'feasibility_reason': 'Satisfies every mandatory requirement',
            'material_doubts': [], 'recommended': name == 'implemented',
            'recommendation_reason': 'Supported compatibility and edge coverage trade-off', 'evidence': ['code:1']})
    return {'profile': 'quality', 'profile_rationale': 'Existing service maintenance and failure recovery',
            'anchors': {k: '0 fails; 50 covers ordinary cases; 100 covers all supported cases' for k in alternatives.CRITERIA},
            'alternative_coverage': 'Repository patterns cover both feasible approaches; remote service rejected for latency',
            'solutions': options, 'evidence': ['requirements:1']}


def policy():
    return skill_config.defaults(alternatives.OWNER)['alternatives']


def selection(**changes):
    return {'mode': 'auto', 'choice': 'implemented', 'confident': True,
            'sensitivity': 'Lead survives the estimated LOC range', 'evidence': ['code:1'],
            'reason': 'State ownership preserves recovery',
            'rejection_reasons': {'smaller': 'Does not cover the same retry edge cases'}, **changes}


@pytest.mark.parametrize('profile', alternatives.PROFILES)
def test_helper_arithmetic_uses_builtin_weights(profile):
    value = compared(); value['profile'] = profile
    value['solutions'][0]['ratings']['edge_coverage'] = 80
    result = alternatives.comparison(value, {**policy()['scoring'], 'profile': profile})
    assert result['solutions'][0]['total'] == 90 - alternatives.PROFILES[profile]['edge_coverage']/10
    assert result['solutions'][1]['total'] == 70


@pytest.mark.parametrize('mutate', [
    lambda r: r['solutions'].pop(),
    lambda r: r['solutions'][1].update(id='implemented'),
    lambda r: r['solutions'][1].update(kind='alternative'),
    lambda r: r['solutions'][0].pop('unhandled_edges'),
    lambda r: r['solutions'][0].update(production_loc={'min': 30, 'expected': 20, 'max': 10, 'reason': 'bad'}),
    lambda r: r['solutions'][0]['ratings'].update(edge_coverage=101),
    lambda r: r['solutions'][0]['ratings'].update(edge_coverage=True),
    lambda r: r['solutions'][0].update(total=89),
    lambda r: r['solutions'][0].update(material_doubts=None),
    lambda r: r['solutions'][0].update(feasible='yes'),
    lambda r: r['solutions'][0]['rating_evidence'].pop('quality'),
    lambda r: r['anchors'].pop('quality'),
    lambda r: r.update(weights={k: 1 for k in alternatives.CRITERIA}),
    lambda r: r.pop('alternative_coverage'),
])
def test_invalid_comparisons_rejected(mutate):
    value = compared(); mutate(value)
    with pytest.raises(ValueError):
        alternatives.comparison(value, policy()['scoring'])


@pytest.mark.parametrize('gap,second,expected', [(10, 80, 'implemented'), (10, 81, None), (0, 90, None), (0, 89, 'implemented')])
def test_unique_winner_and_score_gap_boundaries(gap, second, expected):
    value = compared(); value['solutions'][1]['ratings'] = {k: second for k in alternatives.CRITERIA}
    result = alternatives.comparison(value, policy()['scoring'])
    assert alternatives.choose(result, {**policy()['auto_pick'], 'min_score_gap': gap}, selection()) == expected


def test_doubts_disabled_auto_confidence_and_feasibility_are_hard_gates():
    value = compared(); value['solutions'][0]['material_doubts'] = ['Migration unknown']
    assert alternatives.choose(alternatives.comparison(value, policy()['scoring']), policy()['auto_pick'], selection()) is None
    value = alternatives.comparison(compared(), policy()['scoring'])
    assert alternatives.choose(value, {**policy()['auto_pick'], 'enabled': False}, selection()) is None
    assert alternatives.choose(value, policy()['auto_pick'], selection(confident=False)) is None
    value['solutions'][1]['feasible'] = False
    assert alternatives.choose(value, policy()['auto_pick'], selection()) is None
    assert alternatives.choose(value, policy()['auto_pick'], selection(rejected_alternatives='Smaller option fails mandatory recovery semantics')) == 'implemented'
    value['solutions'][0]['feasible'] = False
    assert alternatives.choose(value, policy()['auto_pick'], selection()) is None


def test_custom_rubric_and_sourced_manual_choice():
    value = compared(); value.update(profile='custom', weights={'recovery': 100}, anchors={'recovery': '0 loses data; 50 partial; 100 full recovery'})
    for s in value['solutions']:
        s.update(ratings={'recovery': 80}, rating_evidence={'recovery': 'tests:42'})
    result = alternatives.comparison(value, {'profile': 'custom', 'custom_prompt': 'Prefer recoverability'})
    assert result['solutions'][0]['total'] == 80
    assert alternatives.choose(result, policy()['auto_pick'], {'mode': 'manual', 'choice': 'smaller', 'user_source': 'user message:7', 'reason': 'Explicit user choice'}) == 'smaller'
    with pytest.raises(ValueError):
        alternatives.choose(result, policy()['auto_pick'], {'mode': 'manual', 'choice': 'smaller'})
    with pytest.raises(ValueError, match='custom_prompt'):
        alternatives.comparison(value, {'profile': 'custom', 'custom_prompt': None})
    with pytest.raises(ValueError, match='custom_prompt'):
        skill_config.effective(alternatives.OWNER, options={'alternatives': {'scoring': {'profile': 'custom'}}})


def descriptor(spec, stage, deps=None, options=None):
    value = deepcopy(definition(spec))
    value['skill_identity'] = alternatives.REVIEW_SKILL if stage in {'assessment', 'comparison'} else alternatives.OWNER
    value['native_review'] = CONTRACT
    if stage == 'alignment':
        metadata = Path(spec['git']['checkout']).parent/'current-pr.json'
        if not metadata.exists():
            LIVE[PR] = {'url': PR, 'body': '# Summary\n\nText\n', 'headRefOid': spec['git']['head']}
            write(metadata, LIVE[PR])
        value['inputs']['alternatives_current_pr'] = str(metadata)
    return {'kind': 'alternatives', 'stage': stage, 'dependencies': deps or {},
            'definition': value, 'options': options or {}}


def finish(tasks, task, value, *, native=True, handle=None):
    value = {**checklist_report(task['checklist']), 'findings': [], **value}
    original = json.dumps(value).encode()
    observed = observations(task['assignment'], task['claim'], original) if native else None
    if observed and handle:
        data = json.loads(observed); data['invocation']['handle'] = handle; observed = json.dumps(data).encode()
    return tasks.finish(task['key'], task['claim']['id'], original, value, native_evidence=observed)


def complete(tasks, spec, stage, value, deps=None, options=None):
    d = descriptor(spec, stage, deps, options)
    if stage in {'impact', 'alignment'}:
        metadata = Path(spec['git']['checkout']).parent/'current-pr.json'
        LIVE.setdefault(PR, {'url': PR, 'body': '# Summary\n\nText\n'})
        LIVE[PR]['headRefOid'] = spec['git']['head']
        write(metadata, LIVE[PR])
        d['definition']['inputs']['alternatives_current_pr'] = str(metadata)
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert finish(tasks, task, value)['checklist_complete']
    return task['key']


def initial(tasks, spec, *, required=True):
    assessment = complete(tasks, spec, 'assessment', {'required': required, 'reason': 'State boundary changes' if required else 'Typo only', 'evidence': ['diff:1']})
    if not required:
        return {'assessment': assessment}
    comparison = complete(tasks, spec, 'comparison', compared(), {'assessment': assessment})
    selected = complete(tasks, spec, 'selection', selection(), {'comparison': comparison})
    return {'assessment': assessment, 'comparison': comparison, 'selection': selected}


def verified_journal(tasks, spec, *, kind, **fields):
    request = {'kind': kind, 'pr': PR, 'head': spec['git']['head'], **fields}
    operation = publication.prepare(kind, PR, {'body': '# Summary\n\nText\n'}, request, ['fixture-reconcile'])
    value = read(operation['operation']); value.update(status='verified', receipt={'url': PR, 'id': 1})
    write(operation['operation'], value)
    return operation['operation']


def aligned(tasks, spec, refs=None, *, alignment='aligned', dependencies=None, legacy=False):
    refs = refs or initial(tasks, spec)
    if legacy:
        c = alternatives.accepted(tasks, refs['comparison'], historical='impact' in refs)
        body = alternatives.replace_decision('# Summary\n\nText\n', alternatives.render_decision(c['report'], c['policy']['scoring'], 'implemented', selection=selection()), heading='Decisions')
        refs['decision_operation'] = verified_journal(tasks, spec, kind='edit-pr', fields={'body': body})
        LIVE[PR] = {'url': PR, 'headRefOid': spec['git']['head'], 'body': body}
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': alignment, 'reason': 'Compare state owner', 'required_changes': 'Use the selected state owner to preserve recovery.', 'evidence': ['code:1']},
                                 dependencies or {k: refs[k] for k in ('comparison', 'selection')})
    return refs


def ordinary(spec, refs, options=None):
    value = definition(spec); value['skill_identity'] = 'dk-workflows:dk-pr-feedback-read'
    value['options']['alternatives_evidence'] = refs
    return {'kind': 'skill', 'skill': alternatives.OWNER, 'selection': 'tasks', 'index': 0,
            'definition': value, 'options': options or {}}


def test_applicability_skip_blocks_missing_evidence_then_releases_tasks(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    with pytest.raises(ValueError, match='applicability'):
        tasks.prepare({'tasks': [ordinary(spec, {})]})
    refs = initial(tasks, spec, required=False)
    assert alternatives.gate(tasks, refs)['outcome'] == 'skipped'
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert 'alternatives_applicability' in task['assignment']['inputs']


def test_complete_disablement_bypasses_every_alternatives_stage_without_deleting(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    before = store.manifest.read_bytes()
    options = {'alternatives': {'enabled': False}}
    assert alternatives.gate(tasks, {}, options=options) == {'outcome': 'disabled', 'release_reviewers': True}
    assert store.manifest.read_bytes() == before
    with pytest.raises(ValueError, match='disabled'):
        tasks.prepare({'tasks': [descriptor(spec, 'assessment', options=options)]})
    assert tasks.prepare({'tasks': [ordinary(spec, {}, options)]})['tasks'][0]['action'] == 'invoke'
    assert alternatives.accepted(tasks, refs['assessment'])['report']['required'] is False


def test_skill_override_stage_preflight_does_not_require_normal_reviewers():
    options = {'alternatives': {'review_skill': 'team:alternatives'}, 'tasks': [{'skill': 'unavailable:review'}]}
    calls = list(skill_config.calls(alternatives.OWNER, options=options, stage='alternatives'))
    assert [call.get('skill') for _, call in calls] == [alternatives.OWNER, 'team:alternatives']
    native = {calls[1][0]: CONTRACT}
    result = invocation.preflight(calls, [alternatives.OWNER, 'team:alternatives'], PREFLIGHT_CAPABILITIES, native_reviews=native)
    assert result['ready']
    assert not invocation.preflight(calls, [alternatives.OWNER, 'team:alternatives'], {}, native_reviews=native)['ready']
    normal = list(skill_config.calls(alternatives.OWNER, options=options, stage='review'))
    assert any(call.get('skill') == 'unavailable:review' for _, call in normal)


def test_pending_manual_selection_and_aligned_continuation(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    # No decision/alignment evidence cannot release an ordinary configured task.
    with pytest.raises((ValueError, KeyError)):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})
    refs = aligned(tasks, spec, refs)
    result = alternatives.gate(tasks, refs)
    assert result['outcome'] == 'aligned' and result['release_reviewers']
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert len([k for k in task['assignment']['inputs'] if k.startswith('alternatives_governing')]) == 3
    assert alternatives.gate(tasks, refs, analysis_only=True)['release_reviewers']
    for legacy_permission in (False, True):
        assert alternatives.gate(tasks, refs, options={'permissions': {'edit_alternatives_decisions': legacy_permission}})['release_reviewers']
    assert 'decision_operation' not in refs
    assert 'decision' not in result
    assert LIVE[PR]['body'] == '# Summary\n\nText\n'


def test_independently_verified_alternatives_only_rejection_stops_normal_work(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, alignment='mismatch')
    refs['verification'] = complete(tasks, spec, 'verification', {'disposition': 'confirmed', 'reason': 'Alternative state owner loses retry state', 'required_changes': 'Use the selected state owner to preserve recovery.', 'evidence': ['code:1']}, {'alignment': refs['alignment']})
    with pytest.raises(KeyError):
        alternatives.gate(tasks, refs)
    refs['review_operation'] = verified_journal(tasks, spec, kind='post-review', event='REQUEST_CHANGES', comments=[], body='Use the selected state owner to preserve recovery.')
    outcome = alternatives.gate(tasks, refs)
    assert outcome == {'outcome': 'alternatives_rejected', 'release_reviewers': False, 'complete': True, 'ordinary_review_complete': False}
    with pytest.raises(ValueError, match='alternatives_rejected'):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})
    assert not any('skill_configuration' in e['components'] for e in read(store.manifest)['entries'].values())
    assert alternatives.gate(tasks, refs, options={'permissions': {'post_review': False}})['outcome'] == 'publication_blocked'
    assert alternatives.gate(tasks, refs, analysis_only=True)['outcome'] == 'publication_blocked'


def test_primary_omissions_and_missing_native_evidence_remain_incomplete(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    d = descriptor(spec, 'assessment')
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert finish(tasks, task, {'required': False, 'reason': 'Typo only', 'evidence': ['diff']}, native=False)['status'] == 'incomplete'
    with pytest.raises(ValueError, match='incomplete'):
        alternatives.accepted(tasks, task['key'])
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    bad = {'complete': True, 'required': False, 'reason': 'Typo only', 'evidence': ['diff'], 'checklist': []}
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='every item'):
        finish(tasks, task, bad)
    assert store.manifest.read_bytes() == before


def test_decisions_are_typed_bounded_and_preserve_unrelated_text():
    decision = alternatives.render_decision(compared(), policy()['scoring'], 'implemented')
    original = '# Summary\n\nKeep exact.\n\n## Decisions\n\n- Scope unchanged.\n\n### Other decisions\n\nVerbatim.\n\n## Tests\n\nKeep.\n'
    updated = alternatives.replace_decision(original, decision, heading='Decisions')
    assert '- Scope unchanged.\n\n### Other decisions\n\nVerbatim.' in updated
    assert updated.startswith('# Summary\n\nKeep exact.') and updated.endswith('## Tests\n\nKeep.\n')
    assert 'Rejected.' in updated and '90.0/100' in updated and '70.0/100' in updated
    assert alternatives.replace_decision(updated, decision, heading='Decisions') == updated
    assert not any(word in updated for word in ('claim', 'manifest', 'receipt'))


def test_own_publication_normalization_requires_exact_reconciled_body(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, legacy=True)
    operation = read(refs['decision_operation'])
    body = operation['request']['approved']['fields']['body']
    assert alternatives.governing_body(body, refs['decision_operation'], tasks) == operation['request']['expected']['body']
    assert alternatives.governing_body(body+'External edit', refs['decision_operation'], tasks) == body+'External edit'
    operation['status'] = 'pending'; operation['intent'] = {'operation_id': operation['operation_id'], 'request_hash': operation['request_hash']}; write(refs['decision_operation'], operation)
    with pytest.raises(ValueError, match='reconciled'):
        alternatives.governing_body(body, refs['decision_operation'], tasks)
    # Old intents are retained for publisher recovery, not alternatives coverage.
    assert alternatives.gate(tasks, refs)['release_reviewers']
    assert read(refs['decision_operation']) == operation


def test_unchanged_fresh_process_reuse_and_partial_claim_recovery(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    # Leave a sibling in flight; reuse never clears its claim.
    pending = tasks.prepare({'tasks': [descriptor(spec, 'alignment', {k: refs[k] for k in ('comparison', 'selection')})]})['tasks'][0]
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR, 'resume']
    resumed = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert len([t for t in resumed['tasks'] if t['coverage_complete']]) == 3
    assert resumed['unfinished'][0]['id'] == pending['claim']['id']
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    assert tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': refs['assessment']})]})['tasks'][0]['action'] == 'reuse'


def test_explicit_alternatives_request_reruns_completed_analysis_and_preserves_history(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    originals = {name: Path(alternatives.accepted(tasks, key)['reference']) for name, key in refs.items()}
    original_bytes = {name: path.read_bytes() for name, path in originals.items()}
    # Ordinary coverage is an independent sibling, not part of this manual run.
    sibling = deepcopy(spec); sibling['task']['id'] = 'ordinary-sibling'
    claim = store.start(sibling)['claim']
    store.record(sibling, claim['id'], b'Ordinary review', {'complete': True, 'findings': []}, 'complete')
    request_text = 'Use dk-workflows:dk-pr-review-alternatives on ' + PR
    for number in (1, 2):
        source = tmp_path/f'alternatives-request-{number}.json'
        write(source, {'id': f'message-{number}', 'source': f'conversation:user:{number}',
                       'instruction': request_text})
        d = descriptor(spec, 'assessment', options={'alternatives': {'enabled': True}})
        d['definition'] = deepcopy(d['definition'])
        d['definition']['inputs']['alternatives_request'] = str(source)
        assessment = tasks.prepare({'tasks': [d]})['tasks'][0]
        assert assessment['action'] == 'invoke' and assessment['key'] == refs['assessment']
        with pytest.raises(ValueError, match='cannot skip'):
            finish(tasks, assessment, {'required': False, 'reason': 'Simple PR', 'evidence': ['diff']})
        finish(tasks, assessment, {'required': True, 'reason': 'Explicit alternatives review', 'evidence': [str(source)]})
        comparison = tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': refs['assessment']})]})['tasks'][0]
        assert comparison['action'] == 'invoke' and comparison['key'] == refs['comparison']
        assert comparison['assignment']['inputs']['alternatives_request'] == str(source)
        finish(tasks, comparison, compared())
        assert alternatives.accepted(tasks, comparison['key'], historical=True)['report']['solutions']
        selected = tasks.prepare({'tasks': [descriptor(spec, 'selection', {'comparison': refs['comparison']})]})['tasks'][0]
        assert selected['action'] == 'invoke' and selected['key'] == refs['selection']
        finish(tasks, selected, selection())
        assert store.start(sibling)['action'] == 'reuse'
        for name, path in originals.items():
            assert path.read_bytes() == original_bytes[name]
            assert len(read(store.manifest)['entries'][refs[name]]['attempts']) == number + 1


def test_manual_alternatives_fresh_session_reuses_completed_stages_and_recovers_pending(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    source = tmp_path/'request.json'
    write(source, {'id': 'message-1', 'source': 'conversation:user:1', 'instruction': 'Review alternatives on ' + PR})
    d = descriptor(spec, 'assessment')
    d['definition']['inputs'] = {**d['definition']['inputs'], 'alternatives_request': str(source)}
    assessment = tasks.prepare({'run': 'manual', 'cycle': 'initial', 'tasks': [d]})['tasks'][0]
    finish(tasks, assessment, {'required': True, 'reason': 'Explicit request', 'evidence': [str(source)]})
    comparison_d = descriptor(spec, 'comparison', {'assessment': assessment['key']})
    comparison = tasks.prepare({'run': 'manual', 'cycle': 'initial', 'tasks': [comparison_d]})['tasks'][0]
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR, 'resume', '--run', 'manual']
    resumed = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert next(t for t in resumed['tasks'] if t['key'] == assessment['key'])['reusable']
    assert resumed['unfinished'][0]['id'] == comparison['claim']['id']
    before = store.manifest.read_bytes()
    request = tmp_path/'prepare.json'
    write(request, {'run': 'manual', 'cycle': 'initial', 'tasks': [{'key': assessment['key']}]})
    prepare_command = [*command[:command.index('resume')], 'prepare', '--file', str(request)]
    assert json.loads(subprocess.check_output(prepare_command, text=True, timeout=60))['tasks'][0]['action'] == 'reuse'
    assert store.manifest.read_bytes() == before
    recovered = tasks.prepare({'run': 'manual', 'cycle': 'initial',
                               'recover': {comparison['key']: comparison['claim']['id']},
                               'tasks': [{'key': comparison['key']}]})['tasks'][0]
    assert recovered['action'] == 'invoke'
    finish(tasks, recovered, compared())
    assert tasks.prepare({'run': 'manual', 'cycle': 'initial', 'tasks': [comparison_d]})['tasks'][0]['action'] == 'reuse'
    # Scoring changes refresh comparison while assessment remains reusable.
    changed = deepcopy(comparison_d)
    changed['options'] = {'alternatives': {'scoring': {'profile': 'quality'}}}
    assert tasks.prepare({'run': 'manual', 'cycle': 'changed-policy', 'tasks': [changed]})['tasks'][0]['action'] == 'invoke'
    assert tasks.prepare({'run': 'manual', 'cycle': 'changed-policy', 'tasks': [d]})['tasks'][0]['action'] == 'reuse'


@pytest.mark.parametrize('value', [None, [], {}, {'id': 'message'},
                                        {'id': 'message', 'source': 'user:1', 'instruction': ''}])
def test_manual_alternatives_requires_original_request_identity_before_claims(case, tmp_path, value):
    spec, store = case; tasks = Tasks(REPO, PR)
    source = tmp_path/'request.json'; write(source, value)
    d = descriptor(spec, 'assessment')
    d['definition']['inputs'] = {**d['definition']['inputs'], 'alternatives_request': str(source)}
    with pytest.raises(ValueError, match='stable id'):
        tasks.prepare({'tasks': [d]})
    assert not store.manifest.exists()


def test_autopick_policy_refresh_preserves_alternatives(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    reference = alternatives.accepted(tasks, refs['comparison'])['reference']
    saved = Path(reference).read_bytes()
    options = {'alternatives': {'auto_pick': {'enabled': False}}}
    assert tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': refs['assessment']}, options)]})['tasks'][0]['action'] == 'reuse'
    task = tasks.prepare({'tasks': [descriptor(spec, 'selection', {'comparison': refs['comparison']}, options)]})['tasks'][0]
    assert task['action'] == 'invoke'
    finish(tasks, task, selection(choice=None))
    assert alternatives.gate(tasks, refs, options=options)['outcome'] == 'choice_pending'
    assert Path(reference).read_bytes() == saved


def test_changed_author_code_uses_targeted_impact_and_current_alignment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    reference = alternatives.accepted(tasks, refs['comparison'])['reference']; original = Path(reference).read_bytes()
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('same alternatives; author fix\n')
    git(checkout, 'commit', '-qam', 'Author fix')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    retained = {name: alternatives.accepted(tasks, refs[name], historical=True)['reference'] for name in ('assessment', 'comparison', 'selection')}
    originals = {name: Path(path).read_bytes() for name, path in retained.items()}
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability, feasible choices and selection confidence unchanged; only implementation detail changed', 'evidence': ['diff:1']}, {k: refs[k] for k in ('comparison', 'selection')})
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Current code still uses the selected state owner', 'evidence': ['code:1']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    LIVE[PR]['headRefOid'] = spec['git']['head']
    assert alternatives.gate(tasks, refs)['outcome'] == 'aligned'
    assert Path(reference).read_bytes() == original
    for name, path in retained.items():
        assert Path(path).read_bytes() == originals[name]
        assert len(read(store.manifest)['entries'][refs[name]]['attempts']) == 1
    # A new coordinator process can construct the normal reviewer handoff from
    # just the original stage refs plus current impact/alignment evidence.
    request = checkout.parent/'ordinary.json'
    write(request, {'tasks': [ordinary(spec, refs)]})
    child = ('import alternatives, json, sys; from state import read; from tasks import Tasks; '
             'alternatives.live_pr = lambda pr: read(sys.argv[1]); '
             'print(json.dumps(Tasks(sys.argv[2], sys.argv[3]).prepare(read(sys.argv[4]))))')
    result = json.loads(subprocess.check_output([sys.executable, '-c', child, str(checkout.parent/'current-pr.json'), REPO, PR, str(request)], text=True, timeout=60))
    assert result['tasks'][0]['action'] == 'invoke'


@pytest.mark.parametrize('impact', ['material', 'uncertain'])
def test_material_or_uncertain_impact_requires_analysis_refresh(case, impact):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': impact, 'reason': 'Requirements changed', 'evidence': ['discussion:1']}, {'comparison': refs['comparison']})
    assert alternatives.gate(tasks, refs)['outcome'] == 'analysis_required'


def test_fixed_profile_does_not_ask_for_automatic_selection_and_override_is_bound(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    assessment = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Interfaces change', 'evidence': ['diff']})
    options = {'alternatives': {'review_skill': 'team:alternatives-review', 'scoring': {'profile': 'minimal_change', 'profile_selection_prompt': 'PROFILE SELECTION SENTINEL'}}}
    d = descriptor(spec, 'comparison', {'assessment': assessment}, options)
    d['definition']['skill_identity'] = 'team:alternatives-review'
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['assignment']['skill']['identity'] == 'team:alternatives-review'
    assert options['alternatives']['scoring']['profile_selection_prompt'] not in task['assignment']['task']['instructions']
    value = compared(); value['profile'] = 'quality'
    with pytest.raises(ValueError, match='profile differs'):
        finish(tasks, task, value)
    value['profile'] = 'minimal_change'
    assert finish(tasks, task, value)['checklist_complete']
    assert len([i for i in task['checklist']['items'] if i['id'].startswith('alternatives-')]) == len(alternatives.CHECKS)


def test_manual_choice_publishes_its_actual_rationale_and_rejects_other_options(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    chosen = selection(mode='manual', choice='smaller', user_source='user:message-9',
                       reason='User prefers the smaller maintenance footprint',
                       rejection_reasons={'implemented': 'Additional state machinery exceeds current requirements'})
    user_choice = Path(spec['git']['checkout']).parent/'choice.json'
    write(user_choice, {'source': 'user:message-9', 'choice': 'smaller', 'reason': chosen['reason']})
    d = descriptor(spec, 'selection', {'comparison': refs['comparison']})
    d['definition']['inputs']['user_choice'] = str(user_choice)
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert finish(tasks, task, chosen)['checklist_complete']
    refs['selection'] = task['key']
    value = alternatives.accepted(tasks, refs['comparison'])
    body = alternatives.render_decision(value['report'], value['policy']['scoring'], 'smaller', selection=chosen)
    assert 'User prefers the smaller maintenance footprint' in body
    assert 'Rejected. Additional state machinery exceeds current requirements' in body
    assert 'Chosen approach: smaller components' in body


def test_comparison_checklist_omission_cannot_accept_alternatives(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = {'assessment': complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State changes', 'evidence': ['diff']})}
    task = tasks.prepare({'tasks': [descriptor(spec, 'comparison', refs)]})['tasks'][0]
    value = {**compared(), **checklist_report(task['checklist'])}
    value['checklist'] = [i for i in value['checklist'] if i['id'] != 'alternatives-partial-failure']
    with pytest.raises(ValueError, match='every item'):
        finish(tasks, task, value)
    assert tasks.resume()['unfinished'][0]['id'] == task['claim']['id']


def test_repeated_gate_reads_do_not_duplicate_publications_or_state(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    before = {p: p.read_bytes() for p in store.root.parent.parent.rglob('*') if p.is_file()}
    assert alternatives.gate(tasks, refs)['release_reviewers']
    assert alternatives.gate(Tasks(REPO, PR), refs)['release_reviewers']
    assert before == {p: p.read_bytes() for p in store.root.parent.parent.rglob('*') if p.is_file()}


def test_changed_relevant_sources_invalidate_applicability_and_block_dependents(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    Path(spec['inputs']['context']).write_text('New public interface requirements')
    with pytest.raises(ValueError, match='fresh'):
        alternatives.gate(tasks, refs)
    with pytest.raises(ValueError):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})


def test_stage_dependencies_reject_cycles_before_claims(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    with pytest.raises(ValueError, match='dependencies'):
        tasks.prepare({'tasks': [descriptor(spec, 'assessment', {'assessment': 'self'})]})
    assert not store.manifest.exists()


@pytest.mark.parametrize('required', [False, True])
def test_changed_invocation_local_review_skill_cannot_reuse_old_gate(case, required):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=required)
    options = {'alternatives': {'review_skill': 'team:alternatives-review'}}
    with pytest.raises(ValueError, match='skill changed'):
        alternatives.gate(tasks, refs, options=options)
    with pytest.raises(ValueError, match='skill changed'):
        tasks.prepare({'tasks': [ordinary(spec, refs, options)]})


@pytest.mark.parametrize('body,heading', [('', 'Decisions'), ('Summary', 'Decisions'),
                                         ('Summary\n', '## Decisions'), ('Summary\n\n', '# Decisions'),
                                         ('## Decisions', 'Decisions'), ('## Decisions\n\nOther choice.\n', 'Decisions'),
                                         ('Summary\n', '### Custom choices')])
def test_first_decision_insertion_is_idempotent(body, heading):
    first = alternatives.replace_decision(body, 'Chosen approach: example', heading=heading)
    assert alternatives.replace_decision(first, 'Chosen approach: example', heading=heading) == first


@pytest.mark.parametrize('opener,closer', [('```markdown', '```'), ('~~~~md', '~~~~'),
                                         ('   ```', '   ````'), ('<pre>', '</pre>'), ('<!--', '-->')])
def test_decision_edit_preserves_fenced_and_html_examples(opener, closer):
    example = 'Example:\n' + opener + '\n## Decisions\n### Alternatives decisions\nExample text.\n' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: example')
    assert updated.startswith(body)
    assert alternatives.replace_decision(updated, 'Chosen approach: example') == updated
    # A real section following an example is edited without consuming the example.
    body = example + '## Decisions\n\nExisting scope.\n\n## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: example')
    assert updated.startswith(example + '## Decisions\n\nExisting scope.')
    assert updated.endswith('## Tests\nKeep.\n')


def test_fenced_subsection_and_boundaries_do_not_replace_real_decision_content():
    body = '## Decisions\n\n```md\n### Alternatives decisions\nExample only.\n```\n\n### Other\nKeep.\n\n## Tests\nKeep tests.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: example')
    assert '```md\n### Alternatives decisions\nExample only.\n```' in updated
    assert '### Other\nKeep.' in updated
    assert updated.endswith('## Tests\nKeep tests.\n')
    assert alternatives.replace_decision(updated, 'Chosen approach: example') == updated


def test_unclosed_example_blocks_decisions_edit_without_deleting_text():
    with pytest.raises(ValueError, match='Unclosed'):
        alternatives.replace_decision('Summary\n```md\n## Decisions\n', 'Chosen approach: example')


def test_restricted_decision_receipt_cannot_include_title_edit(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, legacy=True)
    body = read(refs['decision_operation'])['request']['approved']['fields']['body']
    refs['decision_operation'] = verified_journal(tasks, spec, kind='edit-pr', fields={'body': body, 'title': 'Unrelated title edit'})
    with pytest.raises(ValueError, match='unrelated fields'):
        alternatives.governing_body(body, refs['decision_operation'], tasks)


def test_changed_author_recovery_can_draft_new_decision_without_reanalysis(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    original = Path(alternatives.accepted(tasks, refs['comparison'])['reference']).read_bytes()
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('author implementation fix\n')
    git(checkout, 'commit', '-qam', 'Author update')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability and choice remain valid; only implementation detail changed', 'evidence': ['diff']}, {k: refs[k] for k in ('comparison', 'selection')})
    body = tmp_path/'body.md'; body.write_text('Summary\n')
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR,
               'decision', '--comparison', refs['comparison'], '--selection', refs['selection'], '--impact', refs['impact'], '--body', str(body)]
    result = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert result['decision_type'] == 'alternatives'
    assert selection()['reason'] in result['body']
    accepted = alternatives.accepted(tasks, refs['comparison'], historical=True)
    assert Path(accepted['reference']).read_bytes() == original
    assert all(len(read(store.manifest)['entries'][refs[name]]['attempts']) == 1 for name in ('assessment', 'comparison', 'selection'))


def test_published_invocation_schema_uses_alternatives_options_contract():
    from test_configuration_schema import validator
    value = {'skill': alternatives.REVIEW_SKILL, 'options': {'timeout_seconds': 60}}
    assert not validator('invocation').is_valid(value)
    with pytest.raises(ValueError):
        invocation.validate(value)
    value['options'] = {}
    validator('invocation').validate(value)
    assert invocation.validate(value) == value


def test_mismatch_verification_must_use_a_different_native_agent(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, alignment='mismatch')
    handle = alternatives.accepted(tasks, refs['alignment'])['native_handle']
    task = tasks.prepare({'tasks': [descriptor(spec, 'verification', {'alignment': refs['alignment']})]})['tasks'][0]
    finish(tasks, task, {'disposition': 'confirmed', 'reason': 'State owner differs',
                         'required_changes': 'Use the selected state owner', 'evidence': ['code:1']}, handle=handle)
    refs['verification'] = task['key']
    with pytest.raises(ValueError, match='independent native agent'):
        alternatives.gate(tasks, refs)


@pytest.mark.parametrize('indent', [' ', '  ', '   '])
@pytest.mark.parametrize('spacing,closing', [(' ', ''), ('\t', ' ###')])
def test_indented_sibling_heading_preserves_unrelated_decision(indent, spacing, closing):
    other = indent + '###' + spacing + 'Other decisions' + closing + '\nKeep unrelated.\n\n'
    tests = indent + '##' + spacing + 'Tests' + closing + '\nKeep tests.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nOld alternatives.\n\n' + other + tests
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(other + tests)
    assert 'Old alternatives.' not in updated
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opener,closer', [('<div>', '</div>'), ('<table><tr><td>', '</td></tr></table>'),
                                         ('<div\tclass="example">', '</div>'), ('<pre\tclass="example">', '</pre>'),
                                         ('<custom-tag>', '</custom-tag>'), ('<?processing', '?>'),
                                         ('<![CDATA[', ']]>'), ('<!DECLARATION', '>')])
def test_raw_html_blocks_preserve_apparent_decisions(opener, closer):
    example = opener + '\n## Decisions\n### Alternatives decisions\nExample text.\n' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated
    body = '## Decisions\n\n' + example + '### Other\nKeep this choice.\n\n## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert example in updated and '### Other\nKeep this choice.' in updated


def test_removed_legacy_decision_needs_no_republication_after_current_assessment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, legacy=True)
    operation = Path(refs['decision_operation']).read_bytes()
    LIVE[PR]['body'] = 'Author removed Alternatives decisions.\n'
    Path(spec['inputs']['context']).write_text('Current discussion: same design; author removed public decision')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Same state boundary', 'evidence': ['discussion']})
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'No solution changes; public decision was removed', 'evidence': ['discussion']}, {'comparison': refs['comparison']})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Same state owner', 'evidence': ['code']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    assert alternatives.gate(tasks, refs)['outcome'] == 'aligned'
    assert tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]['action'] == 'invoke'
    assert LIVE[PR]['body'] == 'Author removed Alternatives decisions.\n'
    assert Path(refs['decision_operation']).read_bytes() == operation


@pytest.mark.parametrize('legacy', [False, True])
def test_unrelated_external_body_change_needs_current_impact_but_reuses_analysis(case, legacy):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, legacy=legacy)
    operation = refs.get('decision_operation')
    LIVE[PR]['body'] = 'Updated description.\n\n' + LIVE[PR]['body']
    assert alternatives.gate(tasks, refs)['outcome'] == 'impact_required'
    Path(spec['inputs']['context']).write_text('Current discussion: typo in unrelated description fixed')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Same alternatives', 'evidence': ['discussion']})
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Only unrelated description typo', 'evidence': ['discussion']}, {'comparison': refs['comparison']})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Same implementation', 'evidence': ['code']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    assert alternatives.gate(tasks, refs)['outcome'] == 'aligned'
    assert refs.get('decision_operation') == operation
    LIVE[PR]['body'] = 'Another external edit.\n\n' + LIVE[PR]['body']
    assert alternatives.gate(tasks, refs)['outcome'] == 'impact_required'


def test_live_head_change_blocks_completed_alignment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    LIVE[PR]['headRefOid'] = 'a'*40
    with pytest.raises(ValueError, match='head changed'):
        alternatives.gate(tasks, refs)


@pytest.mark.parametrize('failure', ['timeout', 'identity', 'missing-body', 'malformed'])
def test_live_publication_read_fails_closed_without_authenticated_calls(monkeypatch, failure):
    from shared import github_reads
    value = {'url': PR, 'body': 'Published decision', 'headRefOid': 'a'*40}
    if failure == 'identity': value['url'] = PR + '0'
    if failure == 'missing-body': value.pop('body')
    def run(argv, **kwargs):
        assert argv == ['gh', 'pr', 'view', PR, '--json', 'url,headRefOid,body']
        assert kwargs['timeout'] == 60 and kwargs['check']
        if failure == 'timeout': raise subprocess.TimeoutExpired(argv, 60)
        output = '{invalid' if failure == 'malformed' else json.dumps(value)
        return subprocess.CompletedProcess(argv, 0, stdout=output)
    monkeypatch.setattr(github_reads, 'run', run)
    with pytest.raises(ValueError):
        REAL_LIVE_PR(PR)


@pytest.mark.parametrize('underline', ['=======', '-------'])
def test_setext_section_boundary_preserves_unrelated_pr_text(underline):
    other = 'Other section\n' + underline + '\n\nKeep unrelated.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nOld design.\n\n' + other
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(other)
    assert 'Old design.' not in updated
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


def test_crlf_headings_preserve_existing_unrelated_sections():
    body = '# Summary\r\n\r\nKeep.\r\n\r\n## Decisions\r\n\r\n### Alternatives decisions\r\n\r\nOld.\r\n\r\n### Other\r\nKeep this.\r\n\r\n## Tests\r\nKeep tests.\r\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith('# Summary\r\n\r\nKeep.\r\n\r\n## Decisions\r\n')
    assert updated.endswith('### Other\r\nKeep this.\r\n\r\n## Tests\r\nKeep tests.\r\n')
    assert 'Old.' not in updated
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('underline', ['-------', '======='])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_multiline_setext_heading_is_preserved_in_full(underline, newline):
    sibling = newline.join(['Release', 'notes', underline, '', 'Keep unrelated.', ''])
    body = newline.join(['## Decisions', '', '### Alternatives decisions', '', 'Old design.', '', '']) + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert 'Old design.' not in updated
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('marker', ['-', '*', '+', '1.', '12)'])
def test_list_contained_alternatives_heading_is_an_example(marker):
    indent = ' ' * (len(marker)+1)
    example = marker + ' Documentation example:\n\n' + indent + '### Alternatives decisions\n\n' + indent + 'Keep this example.\n\n'
    body = '## Decisions\n\n' + example + '### Other decisions\n\nKeep this decision.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert example in updated and '### Other decisions\n\nKeep this decision.' in updated
    assert updated.count('### Alternatives decisions') == 2
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_empty_heading_stops_decision_edit(newline):
    sibling = newline.join(['##', 'Unrelated section content.', ''])
    body = newline.join(['## Decisions', '', '### Alternatives decisions', '', 'Old design.', '', '']) + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert 'Old design.' not in updated


def test_cli_preserves_crlf_and_recognizes_exact_own_publication(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    body = '# Summary\r\n\r\nKeep exact.\r\n\r\n## Decisions\r\n\r\nScope unchanged.\r\n\r\n## Tests\r\nKeep tests.\r\n'
    body_file = tmp_path/'crlf-body.md'; body_file.write_bytes(body.encode())
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR]
    result = json.loads(subprocess.check_output([*command, 'decision', '--comparison', refs['comparison'],
                        '--selection', refs['selection'], '--body', str(body_file)], text=True, timeout=60))
    assert result['body'].startswith('# Summary\r\n\r\nKeep exact.\r\n\r\n')
    assert result['body'].endswith('## Tests\r\nKeep tests.\r\n')
    prepared = publication.prepare('edit-pr', PR, {'body': body},
                                   {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': result['body']}},
                                   ['fixture-reconcile'])
    journal = read(prepared['operation']); journal.update(status='verified', receipt={'url': PR})
    write(prepared['operation'], journal)
    body_file.write_bytes(result['body'].encode())
    normalized = json.loads(subprocess.check_output([*command, 'governing-body', '--body', str(body_file),
                                                      '--operation', prepared['operation']], text=True, timeout=60))
    assert normalized['body'] == body


def test_alternatives_run_limits_apply_before_any_ordinary_task(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'limits': {'review_cycles': 1, 'retries_per_task': 0}}
    d = descriptor(spec, 'assessment', options=options)
    request = {'run': 'limited', 'cycle': 'cycle-1', 'tasks': [d]}
    task = tasks.prepare(request)['tasks'][0]
    assert task['assignment']['options']['process_limits']['review_cycles'] == 1
    assert task['assignment']['options']['process_limits']['retries_per_task'] == 0
    output = b'Alternatives evidence unavailable'
    tasks.finish(task['key'], task['claim']['id'], output, {'complete': False, 'findings': []}, status='failed',
                 native_evidence=observations(task['assignment'], task['claim'], output))
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='retries are disabled'):
        tasks.prepare({**request, 'retry': True})
    with pytest.raises(ValueError, match='cycle limit reached'):
        tasks.prepare({**request, 'cycle': 'cycle-2'})
    assert store.manifest.read_bytes() == before
    assert read(store.manifest)['runs']['limited']['cycles'] == ['cycle-1']


def test_public_alternatives_do_not_absorb_indented_siblings():
    rendered = alternatives.render_decision(compared(), policy()['scoring'], 'implemented', selection=selection())
    sibling = '  ### Other decisions\nKeep unrelated.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nOld design.\n\n' + sibling
    updated = alternatives.replace_decision(body, rendered)
    assert updated.endswith(sibling)
    assert alternatives.replace_decision(updated, rendered) == updated


@pytest.mark.parametrize('marker', ['-', '*', '+', '1.'])
def test_marker_only_list_does_not_expose_alternatives_example(marker):
    indent = ' ' * (len(marker)+1)
    example = marker + '\n' + indent + '### Alternatives decisions\n\n' + indent + 'Keep this example.\n\n'
    body = '## Decisions\n\n' + example + '### Other decisions\nKeep unrelated.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert example in updated
    assert '### Other decisions\nKeep unrelated.' in updated
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opener,closer', [('```text', '```'), ('~~~text', '~~~')])
def test_closed_list_fences_do_not_become_unclosed_document_examples(opener, closer):
    example = '- ' + opener + '\n  example\n  ' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


def test_historical_comparison_rejects_same_identity_changed_guidance(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    reference = alternatives.accepted(tasks, refs['comparison'])['reference']
    original = Path(reference).read_bytes()
    # The fixture's installed governing resource keeps its identity and path.
    Path(spec['skill']['resources']['entry']).write_text('Updated alternatives examination requirements')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Current guidance still requires alternatives', 'evidence': ['guidance']})
    with pytest.raises(ValueError, match='review guidance changed'):
        complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Implementation is unchanged', 'evidence': ['diff']}, {'comparison': refs['comparison']})
    assert Path(reference).read_bytes() == original
    assert len(read(store.manifest)['entries'][refs['comparison']]['attempts']) == 1


def test_upstream_heading_is_preserved_in_own_publication_normalization(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    upstream = 'github.com/upstream/project'
    write(config.path(upstream), {'skill_defaults': {'dk-workflows:dk-pr-decisions': {'options': {'section_heading': 'Design choices'}}}})
    refs = initial(tasks, spec)
    before = 'Summary\n'
    source = tmp_path/'body.md'; source.write_text(before)
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR, '--upstream', upstream]
    result = json.loads(subprocess.check_output([*command, 'decision', '--comparison', refs['comparison'],
                        '--selection', refs['selection'], '--body', str(source)], text=True, timeout=60))
    assert '## Design choices' in result['body']
    operation = publication.prepare('edit-pr', PR, {'body': before},
                                    {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': result['body']}}, ['fixture-reconcile'])
    saved = read(operation['operation']); saved.update(status='verified', receipt={'url': PR}); write(operation['operation'], saved)
    source.write_text(result['body'])
    restored = json.loads(subprocess.check_output([*command, 'governing-body', '--body', str(source), '--operation', operation['operation']], text=True, timeout=60))
    assert restored['body'] == before
    assert alternatives.governing_body(result['body'], operation['operation'], tasks, upstream=upstream) == before


def test_dependency_validation_is_shared_within_each_fresh_boundary(case, monkeypatch):
    from collections import Counter
    import state
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    captures = Counter()
    original_capture = state.capture
    def capture(assignment, **kwargs):
        if 'alternatives' in assignment.get('options', {}):
            captures[assignment['task']['id']] += 1
        return original_capture(assignment, **kwargs)
    monkeypatch.setattr(state, 'capture', capture)
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'invoke'
    # Construction and claim are separate live boundaries, with each stage once.
    expected = {'alternatives-' + stage + '-review': 2 for stage in ('assessment', 'comparison', 'selection', 'alignment')}
    assert dict(captures) == expected
    captures.clear()
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'recover_required' and dict(captures) == expected
    Path(spec['inputs']['context']).write_text('Requirements changed after the preceding helper command')
    with pytest.raises(ValueError, match='fresh'):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})


@pytest.mark.parametrize('paragraph', ['Old alternatives\n<span>\n', '-     code example\n'])
def test_inline_html_and_code_list_do_not_hide_root_siblings(paragraph):
    sibling = ('### Other decisions\nKeep unrelated.\n\n' if paragraph.startswith('Old') else
               'Unrelated section\n-----------------\nKeep unrelated.\n\n')
    body = '## Decisions\n\n### Alternatives decisions\n\n' + paragraph + sibling + '## Tests\nKeep tests.\n'
    updated = alternatives.replace_decision(body, 'New alternatives')
    assert updated.endswith(sibling + '## Tests\nKeep tests.\n')
    assert alternatives.replace_decision(updated, 'New alternatives') == updated


def test_nonmaterial_impact_reuses_skipped_applicability(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('another typo\n')
    git(checkout, 'commit', '-qam', 'Second typo')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Still only typos; no material design choices', 'evidence': ['diff']}, {'assessment': refs['assessment']})
    assert alternatives.gate(tasks, refs)['outcome'] == 'skipped'
    assert tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]['action'] == 'invoke'
    assert len(read(store.manifest)['entries'][refs['assessment']]['attempts']) == 1


def test_successive_own_publications_normalize_original_body_in_fresh_process(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    before = '# Summary\nOriginal governing context.\n'
    body = before
    for index in range(3):
        updated = alternatives.replace_decision(body, 'Chosen approach: revision ' + str(index))
        operation = publication.prepare('edit-pr', PR, {'body': body},
                    {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': updated}}, ['fixture-reconcile'])
        value = read(operation['operation']); value.update(status='verified', receipt={'url': PR}); write(operation['operation'], value)
        body = updated
    source = tmp_path/'body.md'; source.write_text(body)
    command = [sys.executable, str(ROOT/'scripts/alternatives.py'), '--repository', REPO, '--pr', PR,
               'governing-body', '--body', str(source), '--operation', operation['operation']]
    assert json.loads(subprocess.check_output(command, text=True, timeout=60))['body'] == before
    assert alternatives.governing_body(body + 'External edit', operation['operation'], tasks) == body + 'External edit'
    external = body + 'External edit'
    updated = alternatives.replace_decision(external, 'Chosen approach: after external edit')
    operation = publication.prepare('edit-pr', PR, {'body': external},
                {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': updated}}, ['fixture-reconcile'])
    value = read(operation['operation']); value.update(status='verified', receipt={'url': PR}); write(operation['operation'], value)
    assert alternatives.governing_body(updated, operation['operation'], tasks) == external


@pytest.mark.parametrize('tag', ['<span title="a>b">', "<custom-tag title='<'>", '<widget a="<>" b=plain c d=\'value\'>'])
def test_complete_html_attributes_keep_example_headings_opaque(tag):
    example = tag + '\n## Decisions\n### Alternatives decisions\nExample only.\n</span>\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opening,closing', [('<pre>', '</pre>'), ('<!--', '-->'), ('```md', '```'), ('<custom-tag>', '')])
def test_list_blocks_do_not_hide_following_setext_siblings(opening, closing):
    block = '- ' + opening + '\n  example\n' + ('  ' + closing + '\n' if closing else '\n')
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\n' + block + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


def test_parallel_incoming_siblings_share_two_live_validation_boundaries(case, monkeypatch):
    from collections import Counter
    import state
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    captures, reads = Counter(), []
    original_capture = state.capture
    def capture(assignment, **kwargs):
        if 'alternatives' in assignment.get('options', {}):
            captures[assignment['task']['id']] += 1
        return original_capture(assignment, **kwargs)
    monkeypatch.setattr(state, 'capture', capture)
    def live(pr):
        reads.append(pr)
        return deepcopy(LIVE[pr])
    monkeypatch.setattr(alternatives, 'live_pr', live)
    options = {'tasks': ['dk-workflows:dk-pr-feedback-read'] * 7}
    group = []
    for index in range(7):
        item = ordinary(spec, refs, options)
        item['index'] = index
        group.append(item)
    prepared = tasks.prepare({'tasks': group})['tasks']
    assert all(task['action'] == 'invoke' for task in prepared)
    expected = {'alternatives-' + stage + '-review': 2 for stage in ('assessment', 'comparison', 'selection', 'alignment')}
    assert dict(captures) == expected and len(reads) == 2
    for task in prepared:
        finish(tasks, task, {'complete': True}, native=False)
    captures.clear(); reads.clear()
    resumed = Tasks(REPO, PR).resume(keys=[task['key'] for task in prepared])
    assert all(task['coverage_complete'] for task in resumed['tasks'])
    assert dict(captures) == expected and len(reads) == 2
    # A subsequent command must see changed publication and block every sibling.
    LIVE[PR]['body'] = '# Summary\nDecision removed\n'
    captures.clear(); reads.clear()
    resumed = Tasks(REPO, PR).resume(keys=[task['key'] for task in prepared])
    assert all(not task['coverage_complete'] for task in resumed['tasks'])
    assert len(reads) == 1


@pytest.mark.parametrize('tag', ['<widget invalid@>', '</widget invalid>', '<widget a=>'])
def test_invalid_complete_tags_cannot_hide_real_sibling_headings(tag):
    sibling = '### Other decisions\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\n' + tag + '\n' + sibling
    assert alternatives.replace_decision(body, 'Chosen approach: new').endswith(sibling)


@pytest.mark.parametrize('marker', ['2.', '3)', '123.', '-', '1.'])
def test_noninterrupting_list_markers_preserve_real_siblings(marker):
    # Empty items never interrupt paragraphs; ordered items need start number 1.
    content = ' This is paragraph text' if marker not in {'-', '1.'} else ''
    sibling = '   ### Other decisions\nKeep unrelated.\n\n## Tests\nKeep tests.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nOld design.\n' + marker + content + '\n' + sibling
    assert alternatives.replace_decision(body, 'Chosen approach: new').endswith(sibling)


def test_dense_setext_suffix_is_not_rescanned_after_known_boundary():
    class Counted(str):
        scans = 0
        def rfind(self, *args):
            type(self).scans += 1
            return super().rfind(*args)
    for prefix in ['## Changelog\n', 'Changelog\n---------\n']:
        view = Counted('old\n\n' + prefix + 'x\n--\n' * 10000)
        Counted.scans = 0
        assert alternatives.next_section(view, 0, 3) == len('old\n\n')
        assert Counted.scans <= 1


@pytest.mark.parametrize('manual', [False, True])
def test_selection_refresh_finishes_after_impact_bound_old_selection(case, tmp_path, manual):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    old_selection = alternatives.accepted(tasks, refs['selection'])['reference']
    old_bytes = Path(old_selection).read_bytes()
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability and existing choice remain valid', 'evidence': ['discussion']}, {k: refs[k] for k in ('comparison', 'selection')})
    old_impact = alternatives.accepted(tasks, refs['impact'])['reference']
    impact_bytes = Path(old_impact).read_bytes()
    options = {'alternatives': {'auto_pick': {'enabled': False}}}
    d = descriptor(spec, 'selection', {k: refs[k] for k in ('comparison', 'impact')}, options)
    value = selection(choice=None)
    if manual:
        choice_source = tmp_path/'choice.txt'; choice_source.write_text('User chooses the smaller approach for this PR.')
        d['definition']['inputs']['user_choice'] = str(choice_source)
        value = {'mode': 'manual', 'choice': 'smaller', 'user_source': str(choice_source),
                 'reason': 'User prefers smaller change', 'rejection_reasons': {'implemented': 'User rejects additional scope'}, 'evidence': [str(choice_source)]}
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['action'] == 'invoke' and task['key'] == refs['selection']
    assert finish(tasks, task, value)['checklist_complete']
    assert alternatives.accepted(tasks, refs['impact'])['reference'] == old_impact
    assert alternatives.accepted(tasks, refs['selection'])['report']['choice'] == ('smaller' if manual else None)
    child = ('import alternatives, json, sys; from tasks import Tasks; '
             't=Tasks(sys.argv[1], sys.argv[2]); '
             'print(json.dumps([alternatives.accepted(t, k)["report"] for k in sys.argv[3:]]))')
    reports = json.loads(subprocess.check_output([sys.executable, '-c', child, REPO, PR, refs['impact'], refs['selection']], text=True, timeout=60))
    assert reports[0]['impact'] == 'nonmaterial' and reports[1]['choice'] == value['choice']
    assert Path(old_selection).read_bytes() == old_bytes and Path(old_impact).read_bytes() == impact_bytes
    assert len(read(store.manifest)['entries'][refs['selection']]['attempts']) == 2
    if not manual:
        assert alternatives.gate(tasks, refs, options=options)['outcome'] == 'choice_pending'


@pytest.mark.parametrize('content', ['***', '* * *', '___', '- - -', 'Title\n  ===', 'Title\n  ---', '*'])
def test_list_nonparagraph_blocks_preserve_root_setext_siblings(content):
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep tests.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nOld design.\n\n- ' + content + '\n' + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('ending', ['\r', '\r\n', '\n'])
def test_mixed_line_endings_preserve_unrelated_sections(ending):
    sibling = '## Tests' + ending + 'Keep.' + ending
    body = '## Decisions\n\n### Alternatives decisions\n\nOld.' + ending * 2 + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


def test_unicode_paragraph_separators_do_not_create_markdown_line_boundaries():
    body = 'Paragraph\u2028```md\n## Decisions\n\n### Alternatives decisions\n\nOld.\n\n## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith('Paragraph\u2028```md\n')
    assert updated.endswith('## Tests\nKeep.\n')



def test_new_impact_key_alias_does_not_imply_selection_consumed_new_result(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Original choice remains valid', 'evidence': ['discussion']}, {k: refs[k] for k in ('comparison', 'selection')})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    prior_impact = alternatives.accepted(tasks, refs['impact'])['reference']
    # A later impact uses the same stable key but did not assess the selection.
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Comparison still fits changed discussion', 'evidence': ['discussion:2']}, {'comparison': refs['comparison']})
    assert alternatives.accepted(tasks, refs['impact'])['reference'] != prior_impact
    with pytest.raises(ValueError, match='different selection'):
        tasks.prepare({'tasks': [descriptor(spec, 'alignment', {k: refs[k] for k in ('comparison', 'selection', 'impact')})]})


@pytest.mark.parametrize('prefix', ['- ', '- - ', '1. - - '])
@pytest.mark.parametrize('opening,closing', [('<pre>', '</pre>'), ('<custom-tag>', '</custom-tag>'), ('```md', '```'), ('***', None)])
def test_nested_list_blocks_preserve_following_root_sections(prefix, opening, closing):
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    block = prefix + opening + '\n'
    if closing is not None:
        block += ' ' * len(prefix) + 'example\n' + ' ' * len(prefix) + closing + '\n'
    body = '## Decisions\n\n### Alternatives decisions\n\n' + block + sibling
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert alternatives.replace_decision(updated, 'Chosen approach: new') == updated


def test_thematic_break_ends_root_paragraph_before_html_example():
    example = '<custom-tag>\n### Alternatives decisions\nExample only.\n</custom-tag>\n\n'
    body = '## Decisions\n\nOld paragraph.\n* * *\n' + example + '## Tests\nKeep.\n'
    updated = alternatives.replace_decision(body, 'Chosen approach: new')
    assert example in updated and updated.endswith('## Tests\nKeep.\n')



def test_indented_paragraph_continuation_cannot_start_html_block():
    sibling = '### Other decisions\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\nParagraph.\n    Continuation.\n<span>\n' + sibling
    assert alternatives.replace_decision(body, 'Chosen approach: new').endswith(sibling)


def test_new_list_item_starts_its_own_html_block():
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\n- Paragraph.\n- <custom-tag>\n  example\n  </custom-tag>\n' + sibling
    assert alternatives.replace_decision(body, 'Chosen approach: new').endswith(sibling)


@pytest.mark.parametrize('text', ['===', '--'])
def test_underline_without_preceding_paragraph_is_plain_text(text):
    sibling = '### Other decisions\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Alternatives decisions\n\n' + text + '\n<span>\n' + sibling
    assert alternatives.replace_decision(body, 'Chosen approach: new').endswith(sibling)



def test_deep_list_prefix_work_and_copying_are_linear(monkeypatch):
    class Counted(str):
        copied = 0
        def __getitem__(self, index):
            value = super().__getitem__(index)
            if isinstance(index, slice):
                type(self).copied += len(value)
                assert type(self).copied <= 4 * len(line)
                return type(self)(value)
            return value
    line = Counted('- ' * 30000 + 'text')
    original = alternatives.THEMATIC_BREAK
    class BoundedPattern:
        visited = 0
        def fullmatch(self, value, start=0):
            self.visited += len(value) - start
            assert self.visited <= 4 * len(line)
            return original.fullmatch(value, start)
    pattern = BoundedPattern()
    monkeypatch.setattr(alternatives, 'THEMATIC_BREAK', pattern)
    context = alternatives.ListContext()
    context.content(line)
    assert context.paragraph and context.block_indent == 60000
    assert Counted.copied <= 4 * len(line) and pattern.visited <= 4 * len(line)


@pytest.mark.parametrize('stage', alternatives.STAGES)
def test_renamed_stages_keep_original_contract_v2_keys(stage):
    from state import digest, identity, task_key
    subject = identity(REPO, PR)
    skill = alternatives.REVIEW_SKILL if stage in {'assessment', 'comparison'} else alternatives.OWNER
    old_skill = 'dk-workflows:dk-architecture-review' if stage in {'assessment', 'comparison'} else skill
    expected = digest({**subject, 'task': 'architecture-' + stage + '-review', 'skill': old_skill})
    assert task_key(subject, 'alternatives-' + stage + '-review', skill) == expected
    # An unrelated provider's deliberately named task keeps its own identity.
    assert task_key(subject, 'alternatives-' + stage + '-review', 'team:review') == digest(
        {**subject, 'task': 'alternatives-' + stage + '-review', 'skill': 'team:review'})
    assert task_key(subject, 'alternatives-' + stage + '-review', 'team:review',
                    options={'alternatives': {'stage': stage}}) == digest(
        {**subject, 'task': 'architecture-' + stage + '-review', 'skill': 'team:review'})


def test_saved_old_options_and_skill_names_are_read_without_rewriting(case):
    legacy = {'skill_defaults': {
        alternatives.OWNER: {'options': {'architecture': {
            'enabled': False, 'review_skill': 'dk-workflows:dk-architecture-review',
            'auto_pick': {'min_score_gap': 25}}, 'permissions': {'edit_architecture_decisions': True}}},
        'dk-workflows:dk-architecture-review': {'instructions': 'Original architecture guidance'},
        'dk-workflows:dk-pr-review-architect': {'instructions': 'Original manual review guidance'},
    }, 'processes': {'pr-review': {'permissions': {'edit_architecture_decisions': True}}}}
    path = skill_config.scope_path(REPO)
    write(path, legacy)
    before = path.read_bytes()
    child = ('import json, skill_config, sys; '
             'print(json.dumps(skill_config.effective(sys.argv[1], sys.argv[2])["options"]))')
    result = json.loads(subprocess.check_output(
        [sys.executable, '-c', child, alternatives.OWNER, REPO], text=True, timeout=60))
    assert result['alternatives']['enabled'] is False
    assert result['alternatives']['review_skill'] == alternatives.REVIEW_SKILL
    assert result['alternatives']['auto_pick']['min_score_gap'] == 25
    assert result['permissions']['edit_alternatives_decisions'] is True
    assert 'architecture' not in result
    assert invocation.normalize('dk-workflows:dk-pr-review-architect')['skill'] == 'dk-workflows:dk-pr-review-alternatives'
    assert skill_config.configured_call('dk-workflows:dk-pr-review-alternatives', REPO)['instructions'] == 'Original manual review guidance'
    assert config.validate(legacy)['skill_defaults'][alternatives.REVIEW_SKILL]['instructions'] == 'Original architecture guidance'
    assert alternatives.gate(Tasks(REPO, PR), {})['outcome'] == 'disabled'
    assert path.read_bytes() == before


@pytest.mark.parametrize('options', [
    {'architecture': {'enabled': False}, 'alternatives': {'enabled': True}},
    {'permissions': {'edit_architecture_decisions': False, 'edit_alternatives_decisions': True}},
])
def test_conflicting_review_aliases_are_rejected_without_state_changes(case, options):
    spec, store = case
    with pytest.raises(ValueError, match='Conflicting review names'):
        skill_config.effective(alternatives.OWNER, REPO, options=options)
    assert not store.manifest.exists()


def test_old_managed_names_resume_completed_siblings_and_pending_claims(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    complete_key = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State choices', 'evidence': ['diff']})
    pending = tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': complete_key})]})['tasks'][0]
    with store.locked() as manifest:
        for entry in manifest['entries'].values():
            managed = entry['managed']
            managed['kind'] = 'architecture'
            managed['definition']['skill_identity'] = 'dk-workflows:dk-architecture-review'
            managed['options'] = {'architecture': {'review_skill': 'dk-workflows:dk-architecture-review'}}
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    command = [sys.executable, str(ROOT/'scripts/dk.py'), '--repository', REPO, '--pr', PR, 'resume']
    resumed = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert next(t for t in resumed['tasks'] if t['key'] == complete_key)['coverage_complete']
    assert resumed['unfinished'][0]['id'] == pending['claim']['id']
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    reused = tasks.prepare({'tasks': [{'key': complete_key}]})['tasks'][0]
    assert reused['action'] == 'reuse' and reused['assignment']['task']['id'] == 'alternatives-assessment-review'
    blocked = tasks.prepare({'tasks': [{'key': pending['key']}]})['tasks'][0]
    assert blocked['action'] == 'recover_required'
    recovered = tasks.prepare({'recover': {pending['key']: pending['claim']['id']},
                               'tasks': [{'key': pending['key']}]})['tasks'][0]
    assert recovered['action'] == 'invoke' and recovered['key'] == pending['key']
    assert finish(tasks, recovered, compared())['checklist_complete']
    assert tasks.prepare({'tasks': [{'key': complete_key}]})['tasks'][0]['action'] == 'reuse'
    assert len(read(store.manifest)['entries'][complete_key]['attempts']) == 1


def test_old_assignment_paths_and_contract_labels_resolve_only_bundled_sources(case, tmp_path):
    from alternatives_compat import descriptor as current_descriptor
    spec, store = case
    old = descriptor(spec, 'assessment')
    old['kind'] = 'architecture'
    old['definition']['skill_identity'] = 'dk-workflows:dk-architecture-review'
    old['definition']['assigned_instructions'] = 'Original architecture instruction'
    old['definition']['resources'].update({
        'architecture_contract': str(ROOT/'runtime/architecture.py'),
        'architecture_prompt': str(ROOT/'codex/skills/dk-architecture-review'),
        'custom': str(tmp_path/'architecture.py')})
    old['definition']['inputs']['architecture_rules'] = str(tmp_path/'rules.md')
    normalized = current_descriptor(old)
    assert normalized['kind'] == 'alternatives'
    assert normalized['definition']['assigned_instructions'] == 'Original architecture instruction'
    assert normalized['definition']['resources']['alternatives_contract'] == str(ROOT/'runtime/alternatives.py')
    assert normalized['definition']['resources']['alternatives_prompt'] == str(ROOT/'codex/skills/dk-alternatives-review')
    assert normalized['definition']['resources']['custom'] == str(tmp_path/'architecture.py')
    assert normalized['definition']['inputs']['architecture_rules'] == str(tmp_path/'rules.md')
    assert old['kind'] == 'architecture'


@pytest.mark.parametrize('existing', [False, True])
def test_historical_decisions_heading_remains_bounded_for_publication_recovery(case, existing):
    spec, store = case; tasks = Tasks(REPO, PR)
    original = '# Summary\nKeep.\n\n'
    if existing:
        original += '## Decisions\n\n### Architecture decisions\n\nOld.\n\n### Other decisions\nKeep sibling.\n'
    updated = alternatives.replace_decision(original, 'New.').replace('### Alternatives decisions', '### Architecture decisions')
    if existing:
        assert updated == original.replace('Old.', 'New.')
    operation = publication.prepare('edit-pr', PR, {'body': original},
                {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': updated}}, ['fixture-reconcile'])
    value = read(operation['operation']); value.update(status='verified', receipt={'url': PR})
    write(operation['operation'], value)
    assert alternatives.governing_body(updated, operation['operation'], tasks) == original
    ambiguous = updated + '\n### Alternatives decisions\nAnother decision.\n'
    with pytest.raises(ValueError, match='Ambiguous'):
        alternatives.replace_decision(ambiguous, 'New.')
