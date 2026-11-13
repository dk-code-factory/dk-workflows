"""Offline architecture gate, scoring and fresh-session recovery acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import architecture as arch
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
REAL_LIVE_PR = arch.live_pr


@pytest.fixture(autouse=True)
def offline_live_architecture(monkeypatch):
    LIVE.clear()
    monkeypatch.setattr(arch, 'live_pr', lambda pr: deepcopy(LIVE[pr]))


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
            'ratings': {k: rating for k in arch.CRITERIA},
            'rating_evidence': {k: 'code:1 and measured workload constraints' for k in arch.CRITERIA},
            'feasible': True, 'feasibility_reason': 'Satisfies every mandatory requirement',
            'material_doubts': [], 'recommended': name == 'implemented',
            'recommendation_reason': 'Supported compatibility and edge coverage trade-off', 'evidence': ['code:1']})
    return {'profile': 'quality', 'profile_rationale': 'Existing service maintenance and failure recovery',
            'anchors': {k: '0 fails; 50 covers ordinary cases; 100 covers all supported cases' for k in arch.CRITERIA},
            'alternative_coverage': 'Repository patterns cover both feasible approaches; remote service rejected for latency',
            'solutions': options, 'evidence': ['requirements:1']}


def policy():
    return skill_config.defaults(arch.OWNER)['architecture']


def selection(**changes):
    return {'mode': 'auto', 'choice': 'implemented', 'confident': True,
            'sensitivity': 'Lead survives the estimated LOC range', 'evidence': ['code:1'],
            'reason': 'State ownership preserves recovery',
            'rejection_reasons': {'smaller': 'Does not cover the same retry edge cases'}, **changes}


@pytest.mark.parametrize('profile', arch.PROFILES)
def test_helper_arithmetic_uses_builtin_weights(profile):
    value = compared(); value['profile'] = profile
    value['solutions'][0]['ratings']['edge_coverage'] = 80
    result = arch.comparison(value, {**policy()['scoring'], 'profile': profile})
    assert result['solutions'][0]['total'] == 90 - arch.PROFILES[profile]['edge_coverage']/10
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
    lambda r: r.update(weights={k: 1 for k in arch.CRITERIA}),
    lambda r: r.pop('alternative_coverage'),
])
def test_invalid_comparisons_rejected(mutate):
    value = compared(); mutate(value)
    with pytest.raises(ValueError):
        arch.comparison(value, policy()['scoring'])


@pytest.mark.parametrize('gap,second,expected', [(10, 80, 'implemented'), (10, 81, None), (0, 90, None), (0, 89, 'implemented')])
def test_unique_winner_and_score_gap_boundaries(gap, second, expected):
    value = compared(); value['solutions'][1]['ratings'] = {k: second for k in arch.CRITERIA}
    result = arch.comparison(value, policy()['scoring'])
    assert arch.choose(result, {**policy()['auto_pick'], 'min_score_gap': gap}, selection()) == expected


def test_doubts_disabled_auto_confidence_and_feasibility_are_hard_gates():
    value = compared(); value['solutions'][0]['material_doubts'] = ['Migration unknown']
    assert arch.choose(arch.comparison(value, policy()['scoring']), policy()['auto_pick'], selection()) is None
    value = arch.comparison(compared(), policy()['scoring'])
    assert arch.choose(value, {**policy()['auto_pick'], 'enabled': False}, selection()) is None
    assert arch.choose(value, policy()['auto_pick'], selection(confident=False)) is None
    value['solutions'][1]['feasible'] = False
    assert arch.choose(value, policy()['auto_pick'], selection()) is None
    assert arch.choose(value, policy()['auto_pick'], selection(rejected_alternatives='Smaller option fails mandatory recovery semantics')) == 'implemented'
    value['solutions'][0]['feasible'] = False
    assert arch.choose(value, policy()['auto_pick'], selection()) is None


def test_custom_rubric_and_sourced_manual_choice():
    value = compared(); value.update(profile='custom', weights={'recovery': 100}, anchors={'recovery': '0 loses data; 50 partial; 100 full recovery'})
    for s in value['solutions']:
        s.update(ratings={'recovery': 80}, rating_evidence={'recovery': 'tests:42'})
    result = arch.comparison(value, {'profile': 'custom', 'custom_prompt': 'Prefer recoverability'})
    assert result['solutions'][0]['total'] == 80
    assert arch.choose(result, policy()['auto_pick'], {'mode': 'manual', 'choice': 'smaller', 'user_source': 'user message:7', 'reason': 'Explicit user choice'}) == 'smaller'
    with pytest.raises(ValueError):
        arch.choose(result, policy()['auto_pick'], {'mode': 'manual', 'choice': 'smaller'})
    with pytest.raises(ValueError, match='custom_prompt'):
        arch.comparison(value, {'profile': 'custom', 'custom_prompt': None})
    with pytest.raises(ValueError, match='custom_prompt'):
        skill_config.effective(arch.OWNER, options={'architecture': {'scoring': {'profile': 'custom'}}})


def descriptor(spec, stage, deps=None, options=None):
    value = definition(spec)
    value['skill_identity'] = arch.REVIEW_SKILL if stage in {'assessment', 'comparison'} else arch.OWNER
    value['native_review'] = CONTRACT
    return {'kind': 'architecture', 'stage': stage, 'dependencies': deps or {},
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
    if stage == 'impact':
        metadata = Path(spec['git']['checkout']).parent/'current-pr.json'
        write(metadata, {**LIVE.get(PR, {'url': PR, 'body': 'Initial PR body'}), 'headRefOid': spec['git']['head']})
        d['definition']['inputs']['architecture_current_pr'] = str(metadata)
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


def aligned(tasks, spec, refs=None, *, alignment='aligned', dependencies=None):
    refs = refs or initial(tasks, spec)
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': alignment, 'reason': 'Compare state owner', 'required_changes': 'Use the selected state owner to preserve recovery.', 'evidence': ['code:1']},
                                 dependencies or {k: refs[k] for k in ('comparison', 'selection')})
    c = arch.accepted(tasks, refs['comparison'], historical='impact' in refs)
    body = arch.replace_decision('# Summary\n\nText\n', arch.render_decision(c['report'], c['policy']['scoring'], 'implemented', selection=selection()), heading='Decisions')
    refs['decision_operation'] = verified_journal(tasks, spec, kind='edit-pr', fields={'body': body})
    LIVE[PR] = {'url': PR, 'headRefOid': spec['git']['head'], 'body': body}
    return refs


def ordinary(spec, refs, options=None):
    value = definition(spec); value['skill_identity'] = 'dk-workflows:dk-pr-feedback-read'
    value['options']['architecture_evidence'] = refs
    return {'kind': 'skill', 'skill': arch.OWNER, 'selection': 'tasks', 'index': 0,
            'definition': value, 'options': options or {}}


def test_applicability_skip_blocks_missing_evidence_then_releases_tasks(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    with pytest.raises(ValueError, match='applicability'):
        tasks.prepare({'tasks': [ordinary(spec, {})]})
    refs = initial(tasks, spec, required=False)
    assert arch.gate(tasks, refs)['outcome'] == 'skipped'
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'invoke'
    assert 'architecture_applicability' in task['assignment']['inputs']


def test_complete_disablement_bypasses_every_architecture_stage_without_deleting(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    before = store.manifest.read_bytes()
    options = {'architecture': {'enabled': False}}
    assert arch.gate(tasks, {}, options=options) == {'outcome': 'disabled', 'release_reviewers': True}
    assert store.manifest.read_bytes() == before
    with pytest.raises(ValueError, match='disabled'):
        tasks.prepare({'tasks': [descriptor(spec, 'assessment', options=options)]})
    assert tasks.prepare({'tasks': [ordinary(spec, {}, options)]})['tasks'][0]['action'] == 'invoke'
    assert arch.accepted(tasks, refs['assessment'])['report']['required'] is False


def test_skill_override_stage_preflight_does_not_require_normal_reviewers():
    options = {'architecture': {'review_skill': 'team:architecture'}, 'tasks': [{'skill': 'unavailable:review'}]}
    calls = list(skill_config.calls(arch.OWNER, options=options, stage='architecture'))
    assert [call.get('skill') for _, call in calls] == [arch.OWNER, 'team:architecture']
    native = {calls[1][0]: CONTRACT}
    result = invocation.preflight(calls, [arch.OWNER, 'team:architecture'], PREFLIGHT_CAPABILITIES, native_reviews=native)
    assert result['ready']
    assert not invocation.preflight(calls, [arch.OWNER, 'team:architecture'], {}, native_reviews=native)['ready']
    normal = list(skill_config.calls(arch.OWNER, options=options, stage='review'))
    assert any(call.get('skill') == 'unavailable:review' for _, call in normal)


def test_pending_manual_selection_and_aligned_continuation(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    # No decision/alignment evidence cannot release an ordinary configured task.
    with pytest.raises((ValueError, KeyError)):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})
    refs = aligned(tasks, spec, refs)
    result = arch.gate(tasks, refs)
    assert result['outcome'] == 'aligned' and result['release_reviewers']
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert len([k for k in task['assignment']['inputs'] if k.startswith('architecture_governing')]) == 3
    assert not arch.gate(tasks, refs, analysis_only=True)['release_reviewers']
    assert not arch.gate(tasks, refs, options={'permissions': {'edit_architecture_decisions': False}})['release_reviewers']


def test_independently_verified_architecture_only_rejection_stops_normal_work(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, alignment='mismatch')
    refs['verification'] = complete(tasks, spec, 'verification', {'disposition': 'confirmed', 'reason': 'Alternative state owner loses retry state', 'required_changes': 'Use the selected state owner to preserve recovery.', 'evidence': ['code:1']}, {'alignment': refs['alignment']})
    with pytest.raises(KeyError):
        arch.gate(tasks, refs)
    refs['review_operation'] = verified_journal(tasks, spec, kind='post-review', event='REQUEST_CHANGES', comments=[], body='Use the selected state owner to preserve recovery.')
    outcome = arch.gate(tasks, refs)
    assert outcome == {'outcome': 'architecture_rejected', 'release_reviewers': False, 'complete': True, 'ordinary_review_complete': False}
    with pytest.raises(ValueError, match='architecture_rejected'):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})
    assert not any('skill_configuration' in e['components'] for e in read(store.manifest)['entries'].values())
    assert arch.gate(tasks, refs, options={'permissions': {'post_review': False}})['outcome'] == 'publication_blocked'


def test_primary_omissions_and_missing_native_evidence_remain_incomplete(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    d = descriptor(spec, 'assessment')
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert finish(tasks, task, {'required': False, 'reason': 'Typo only', 'evidence': ['diff']}, native=False)['status'] == 'incomplete'
    with pytest.raises(ValueError, match='incomplete'):
        arch.accepted(tasks, task['key'])
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    bad = {'complete': True, 'required': False, 'reason': 'Typo only', 'evidence': ['diff'], 'checklist': []}
    before = store.manifest.read_bytes()
    with pytest.raises(ValueError, match='every item'):
        finish(tasks, task, bad)
    assert store.manifest.read_bytes() == before


def test_decisions_are_typed_bounded_and_preserve_unrelated_text():
    decision = arch.render_decision(compared(), policy()['scoring'], 'implemented')
    original = '# Summary\n\nKeep exact.\n\n## Decisions\n\n- Scope unchanged.\n\n### Other decisions\n\nVerbatim.\n\n## Tests\n\nKeep.\n'
    updated = arch.replace_decision(original, decision, heading='Decisions')
    assert '- Scope unchanged.\n\n### Other decisions\n\nVerbatim.' in updated
    assert updated.startswith('# Summary\n\nKeep exact.') and updated.endswith('## Tests\n\nKeep.\n')
    assert 'Rejected.' in updated and '90.0/100' in updated and '70.0/100' in updated
    assert arch.replace_decision(updated, decision, heading='Decisions') == updated
    assert not any(word in updated for word in ('claim', 'manifest', 'receipt'))


def test_own_publication_normalization_requires_exact_reconciled_body(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    operation = read(refs['decision_operation'])
    body = operation['request']['approved']['fields']['body']
    assert arch.governing_body(body, refs['decision_operation'], tasks) == operation['request']['expected']['body']
    assert arch.governing_body(body+'External edit', refs['decision_operation'], tasks) == body+'External edit'
    operation['status'] = 'pending'; operation['intent'] = {'operation_id': operation['operation_id'], 'request_hash': operation['request_hash']}; write(refs['decision_operation'], operation)
    with pytest.raises(ValueError, match='reconciled'):
        arch.gate(tasks, refs)


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


def test_autopick_policy_refresh_preserves_alternatives(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    reference = arch.accepted(tasks, refs['comparison'])['reference']
    saved = Path(reference).read_bytes()
    options = {'architecture': {'auto_pick': {'enabled': False}}}
    assert tasks.prepare({'tasks': [descriptor(spec, 'comparison', {'assessment': refs['assessment']}, options)]})['tasks'][0]['action'] == 'reuse'
    task = tasks.prepare({'tasks': [descriptor(spec, 'selection', {'comparison': refs['comparison']}, options)]})['tasks'][0]
    assert task['action'] == 'invoke'
    finish(tasks, task, selection(choice=None))
    assert arch.gate(tasks, refs, options=options)['outcome'] == 'choice_pending'
    assert Path(reference).read_bytes() == saved


def test_changed_author_code_uses_targeted_impact_and_current_alignment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    reference = arch.accepted(tasks, refs['comparison'])['reference']; original = Path(reference).read_bytes()
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('same architecture; author fix\n')
    git(checkout, 'commit', '-qam', 'Author fix')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    retained = {name: arch.accepted(tasks, refs[name], historical=True)['reference'] for name in ('assessment', 'comparison', 'selection')}
    originals = {name: Path(path).read_bytes() for name, path in retained.items()}
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability, feasible choices and selection confidence unchanged; only implementation detail changed', 'evidence': ['diff:1']}, {k: refs[k] for k in ('comparison', 'selection')})
    decision_operation = refs['decision_operation']
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Current code still uses the selected state owner', 'evidence': ['code:1']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    refs['decision_operation'] = decision_operation
    LIVE[PR]['headRefOid'] = spec['git']['head']
    assert arch.gate(tasks, refs)['outcome'] == 'aligned'
    assert Path(reference).read_bytes() == original
    for name, path in retained.items():
        assert Path(path).read_bytes() == originals[name]
        assert len(read(store.manifest)['entries'][refs[name]]['attempts']) == 1
    # A new coordinator process can construct the normal reviewer handoff from
    # just the original stage refs plus current impact/alignment evidence.
    request = checkout.parent/'ordinary.json'
    write(request, {'tasks': [ordinary(spec, refs)]})
    child = ('import architecture, json, sys; from state import read; from tasks import Tasks; '
             'architecture.live_pr = lambda pr: read(sys.argv[1]); '
             'print(json.dumps(Tasks(sys.argv[2], sys.argv[3]).prepare(read(sys.argv[4]))))')
    result = json.loads(subprocess.check_output([sys.executable, '-c', child, str(checkout.parent/'current-pr.json'), REPO, PR, str(request)], text=True, timeout=60))
    assert result['tasks'][0]['action'] == 'invoke'


@pytest.mark.parametrize('impact', ['material', 'uncertain'])
def test_material_or_uncertain_impact_requires_analysis_refresh(case, impact):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': impact, 'reason': 'Requirements changed', 'evidence': ['discussion:1']}, {'comparison': refs['comparison']})
    assert arch.gate(tasks, refs)['outcome'] == 'analysis_required'


def test_fixed_profile_does_not_ask_for_automatic_selection_and_override_is_bound(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    assessment = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Interfaces change', 'evidence': ['diff']})
    options = {'architecture': {'review_skill': 'team:architect', 'scoring': {'profile': 'minimal_change', 'profile_selection_prompt': 'PROFILE SELECTION SENTINEL'}}}
    d = descriptor(spec, 'comparison', {'assessment': assessment}, options)
    d['definition']['skill_identity'] = 'team:architect'
    task = tasks.prepare({'tasks': [d]})['tasks'][0]
    assert task['assignment']['skill']['identity'] == 'team:architect'
    assert options['architecture']['scoring']['profile_selection_prompt'] not in task['assignment']['task']['instructions']
    value = compared(); value['profile'] = 'quality'
    with pytest.raises(ValueError, match='profile differs'):
        finish(tasks, task, value)
    value['profile'] = 'minimal_change'
    assert finish(tasks, task, value)['checklist_complete']
    assert len([i for i in task['checklist']['items'] if i['id'].startswith('architecture-')]) == len(arch.CHECKS)


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
    value = arch.accepted(tasks, refs['comparison'])
    body = arch.render_decision(value['report'], value['policy']['scoring'], 'smaller', selection=chosen)
    assert 'User prefers the smaller maintenance footprint' in body
    assert 'Rejected. Additional state machinery exceeds current requirements' in body
    assert 'Chosen approach: smaller components' in body


def test_comparison_checklist_omission_cannot_accept_architecture(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = {'assessment': complete(tasks, spec, 'assessment', {'required': True, 'reason': 'State changes', 'evidence': ['diff']})}
    task = tasks.prepare({'tasks': [descriptor(spec, 'comparison', refs)]})['tasks'][0]
    value = {**compared(), **checklist_report(task['checklist'])}
    value['checklist'] = [i for i in value['checklist'] if i['id'] != 'architecture-partial-failure']
    with pytest.raises(ValueError, match='every item'):
        finish(tasks, task, value)
    assert tasks.resume()['unfinished'][0]['id'] == task['claim']['id']


def test_repeated_gate_reads_do_not_duplicate_publications_or_state(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    before = {p: p.read_bytes() for p in store.root.parent.parent.rglob('*') if p.is_file()}
    assert arch.gate(tasks, refs)['release_reviewers']
    assert arch.gate(Tasks(REPO, PR), refs)['release_reviewers']
    assert before == {p: p.read_bytes() for p in store.root.parent.parent.rglob('*') if p.is_file()}


def test_changed_relevant_sources_invalidate_applicability_and_block_dependents(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    Path(spec['inputs']['context']).write_text('New public interface requirements')
    with pytest.raises(ValueError, match='fresh'):
        arch.gate(tasks, refs)
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
    options = {'architecture': {'review_skill': 'team:architect'}}
    with pytest.raises(ValueError, match='skill changed'):
        arch.gate(tasks, refs, options=options)
    with pytest.raises(ValueError, match='skill changed'):
        tasks.prepare({'tasks': [ordinary(spec, refs, options)]})


@pytest.mark.parametrize('body,heading', [('', 'Decisions'), ('Summary', 'Decisions'),
                                         ('Summary\n', '## Decisions'), ('Summary\n\n', '# Decisions'),
                                         ('## Decisions', 'Decisions'), ('## Decisions\n\nOther choice.\n', 'Decisions'),
                                         ('Summary\n', '### Custom choices')])
def test_first_decision_insertion_is_idempotent(body, heading):
    first = arch.replace_decision(body, 'Chosen approach: example', heading=heading)
    assert arch.replace_decision(first, 'Chosen approach: example', heading=heading) == first


@pytest.mark.parametrize('opener,closer', [('```markdown', '```'), ('~~~~md', '~~~~'),
                                         ('   ```', '   ````'), ('<pre>', '</pre>'), ('<!--', '-->')])
def test_decision_edit_preserves_fenced_and_html_examples(opener, closer):
    example = 'Example:\n' + opener + '\n## Decisions\n### Architecture decisions\nExample text.\n' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: example')
    assert updated.startswith(body)
    assert arch.replace_decision(updated, 'Chosen approach: example') == updated
    # A real section following an example is edited without consuming the example.
    body = example + '## Decisions\n\nExisting scope.\n\n## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: example')
    assert updated.startswith(example + '## Decisions\n\nExisting scope.')
    assert updated.endswith('## Tests\nKeep.\n')


def test_fenced_subsection_and_boundaries_do_not_replace_real_decision_content():
    body = '## Decisions\n\n```md\n### Architecture decisions\nExample only.\n```\n\n### Other\nKeep.\n\n## Tests\nKeep tests.\n'
    updated = arch.replace_decision(body, 'Chosen approach: example')
    assert '```md\n### Architecture decisions\nExample only.\n```' in updated
    assert '### Other\nKeep.' in updated
    assert updated.endswith('## Tests\nKeep tests.\n')
    assert arch.replace_decision(updated, 'Chosen approach: example') == updated


def test_unclosed_example_blocks_decisions_edit_without_deleting_text():
    with pytest.raises(ValueError, match='Unclosed'):
        arch.replace_decision('Summary\n```md\n## Decisions\n', 'Chosen approach: example')


def test_restricted_decision_receipt_cannot_include_title_edit(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    body = read(refs['decision_operation'])['request']['approved']['fields']['body']
    refs['decision_operation'] = verified_journal(tasks, spec, kind='edit-pr', fields={'body': body, 'title': 'Unrelated title edit'})
    with pytest.raises(ValueError, match='only the bounded body'):
        arch.gate(tasks, refs)
    with pytest.raises(ValueError, match='unrelated fields'):
        arch.governing_body(body, refs['decision_operation'], tasks)


def test_changed_author_recovery_can_draft_new_decision_without_reanalysis(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    original = Path(arch.accepted(tasks, refs['comparison'])['reference']).read_bytes()
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('author implementation fix\n')
    git(checkout, 'commit', '-qam', 'Author update')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability and choice remain valid; only implementation detail changed', 'evidence': ['diff']}, {k: refs[k] for k in ('comparison', 'selection')})
    body = tmp_path/'body.md'; body.write_text('Summary\n')
    command = [sys.executable, str(ROOT/'scripts/architecture.py'), '--repository', REPO, '--pr', PR,
               'decision', '--comparison', refs['comparison'], '--selection', refs['selection'], '--impact', refs['impact'], '--body', str(body)]
    result = json.loads(subprocess.check_output(command, text=True, timeout=60))
    assert result['decision_type'] == 'architecture'
    assert selection()['reason'] in result['body']
    accepted = arch.accepted(tasks, refs['comparison'], historical=True)
    assert Path(accepted['reference']).read_bytes() == original
    assert all(len(read(store.manifest)['entries'][refs[name]]['attempts']) == 1 for name in ('assessment', 'comparison', 'selection'))


def test_published_invocation_schema_uses_architecture_options_contract():
    from test_configuration_schema import validator
    value = {'skill': arch.REVIEW_SKILL, 'options': {'timeout_seconds': 60}}
    assert not validator('invocation').is_valid(value)
    with pytest.raises(ValueError):
        invocation.validate(value)
    value['options'] = {}
    validator('invocation').validate(value)
    assert invocation.validate(value) == value


def test_mismatch_verification_must_use_a_different_native_agent(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec, alignment='mismatch')
    handle = arch.accepted(tasks, refs['alignment'])['native_handle']
    task = tasks.prepare({'tasks': [descriptor(spec, 'verification', {'alignment': refs['alignment']})]})['tasks'][0]
    finish(tasks, task, {'disposition': 'confirmed', 'reason': 'State owner differs',
                         'required_changes': 'Use the selected state owner', 'evidence': ['code:1']}, handle=handle)
    refs['verification'] = task['key']
    with pytest.raises(ValueError, match='independent native agent'):
        arch.gate(tasks, refs)


@pytest.mark.parametrize('indent', [' ', '  ', '   '])
@pytest.mark.parametrize('spacing,closing', [(' ', ''), ('\t', ' ###')])
def test_indented_sibling_heading_preserves_unrelated_decision(indent, spacing, closing):
    other = indent + '###' + spacing + 'Other decisions' + closing + '\nKeep unrelated.\n\n'
    tests = indent + '##' + spacing + 'Tests' + closing + '\nKeep tests.\n'
    body = '## Decisions\n\n### Architecture decisions\n\nOld architecture.\n\n' + other + tests
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(other + tests)
    assert 'Old architecture.' not in updated
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opener,closer', [('<div>', '</div>'), ('<table><tr><td>', '</td></tr></table>'),
                                         ('<div\tclass="example">', '</div>'), ('<pre\tclass="example">', '</pre>'),
                                         ('<custom-tag>', '</custom-tag>'), ('<?processing', '?>'),
                                         ('<![CDATA[', ']]>'), ('<!DECLARATION', '>')])
def test_raw_html_blocks_preserve_apparent_decisions(opener, closer):
    example = opener + '\n## Decisions\n### Architecture decisions\nExample text.\n' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated
    body = '## Decisions\n\n' + example + '### Other\nKeep this choice.\n\n## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert example in updated and '### Other\nKeep this choice.' in updated


def test_historical_receipt_cannot_release_after_decision_is_removed(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    LIVE[PR]['body'] = 'Author removed Architecture decisions.\n'
    Path(spec['inputs']['context']).write_text('Current discussion: same design; author removed public decision')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Same state boundary', 'evidence': ['discussion']})
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'No solution changes; public decision was removed', 'evidence': ['discussion']}, {'comparison': refs['comparison']})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Same state owner', 'evidence': ['code']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    assert arch.gate(tasks, refs) == {'outcome': 'publication_required', 'release_reviewers': False}
    with pytest.raises(ValueError, match='publication_required'):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})


def test_unrelated_external_body_change_needs_current_impact_but_reuses_receipt(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    operation = refs['decision_operation']
    LIVE[PR]['body'] = 'Updated description.\n\n' + LIVE[PR]['body']
    assert arch.gate(tasks, refs)['outcome'] == 'impact_required'
    Path(spec['inputs']['context']).write_text('Current discussion: typo in unrelated description fixed')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Same architecture', 'evidence': ['discussion']})
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Only unrelated description typo', 'evidence': ['discussion']}, {'comparison': refs['comparison']})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    refs['alignment'] = complete(tasks, spec, 'alignment', {'alignment': 'aligned', 'reason': 'Same implementation', 'evidence': ['code']}, {k: refs[k] for k in ('comparison', 'selection', 'impact')})
    assert arch.gate(tasks, refs)['outcome'] == 'aligned'
    assert refs['decision_operation'] == operation
    LIVE[PR]['body'] = 'Another external edit.\n\n' + LIVE[PR]['body']
    assert arch.gate(tasks, refs)['outcome'] == 'impact_required'


def test_live_head_change_blocks_completed_alignment(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    LIVE[PR]['headRefOid'] = 'a'*40
    with pytest.raises(ValueError, match='head changed'):
        arch.gate(tasks, refs)


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
    body = '## Decisions\n\n### Architecture decisions\n\nOld design.\n\n' + other
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(other)
    assert 'Old design.' not in updated
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


def test_crlf_headings_preserve_existing_unrelated_sections():
    body = '# Summary\r\n\r\nKeep.\r\n\r\n## Decisions\r\n\r\n### Architecture decisions\r\n\r\nOld.\r\n\r\n### Other\r\nKeep this.\r\n\r\n## Tests\r\nKeep tests.\r\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith('# Summary\r\n\r\nKeep.\r\n\r\n## Decisions\r\n')
    assert updated.endswith('### Other\r\nKeep this.\r\n\r\n## Tests\r\nKeep tests.\r\n')
    assert 'Old.' not in updated
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('underline', ['-------', '======='])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_multiline_setext_heading_is_preserved_in_full(underline, newline):
    sibling = newline.join(['Release', 'notes', underline, '', 'Keep unrelated.', ''])
    body = newline.join(['## Decisions', '', '### Architecture decisions', '', 'Old design.', '', '']) + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert 'Old design.' not in updated
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('marker', ['-', '*', '+', '1.', '12)'])
def test_list_contained_architecture_heading_is_an_example(marker):
    indent = ' ' * (len(marker)+1)
    example = marker + ' Documentation example:\n\n' + indent + '### Architecture decisions\n\n' + indent + 'Keep this example.\n\n'
    body = '## Decisions\n\n' + example + '### Other decisions\n\nKeep this decision.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert example in updated and '### Other decisions\n\nKeep this decision.' in updated
    assert updated.count('### Architecture decisions') == 2
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_empty_heading_stops_decision_edit(newline):
    sibling = newline.join(['##', 'Unrelated section content.', ''])
    body = newline.join(['## Decisions', '', '### Architecture decisions', '', 'Old design.', '', '']) + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert 'Old design.' not in updated


def test_cli_preserves_crlf_and_recognizes_exact_own_publication(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    body = '# Summary\r\n\r\nKeep exact.\r\n\r\n## Decisions\r\n\r\nScope unchanged.\r\n\r\n## Tests\r\nKeep tests.\r\n'
    body_file = tmp_path/'crlf-body.md'; body_file.write_bytes(body.encode())
    command = [sys.executable, str(ROOT/'scripts/architecture.py'), '--repository', REPO, '--pr', PR]
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


def test_architecture_run_limits_apply_before_any_ordinary_task(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    options = {'limits': {'review_cycles': 1, 'retries_per_task': 0}}
    d = descriptor(spec, 'assessment', options=options)
    request = {'run': 'limited', 'cycle': 'cycle-1', 'tasks': [d]}
    task = tasks.prepare(request)['tasks'][0]
    assert task['assignment']['options']['process_limits']['review_cycles'] == 1
    assert task['assignment']['options']['process_limits']['retries_per_task'] == 0
    output = b'Architecture evidence unavailable'
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
    rendered = arch.render_decision(compared(), policy()['scoring'], 'implemented', selection=selection())
    sibling = '  ### Other decisions\nKeep unrelated.\n'
    body = '## Decisions\n\n### Architecture decisions\n\nOld design.\n\n' + sibling
    updated = arch.replace_decision(body, rendered)
    assert updated.endswith(sibling)
    assert arch.replace_decision(updated, rendered) == updated


@pytest.mark.parametrize('marker', ['-', '*', '+', '1.'])
def test_marker_only_list_does_not_expose_architecture_example(marker):
    indent = ' ' * (len(marker)+1)
    example = marker + '\n' + indent + '### Architecture decisions\n\n' + indent + 'Keep this example.\n\n'
    body = '## Decisions\n\n' + example + '### Other decisions\nKeep unrelated.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert example in updated
    assert '### Other decisions\nKeep unrelated.' in updated
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opener,closer', [('```text', '```'), ('~~~text', '~~~')])
def test_closed_list_fences_do_not_become_unclosed_document_examples(opener, closer):
    example = '- ' + opener + '\n  example\n  ' + closer + '\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


def test_historical_comparison_rejects_same_identity_changed_guidance(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    reference = arch.accepted(tasks, refs['comparison'])['reference']
    original = Path(reference).read_bytes()
    # The fixture's installed governing resource keeps its identity and path.
    Path(spec['skill']['resources']['entry']).write_text('Updated architecture examination requirements')
    refs['assessment'] = complete(tasks, spec, 'assessment', {'required': True, 'reason': 'Current guidance still requires architecture', 'evidence': ['guidance']})
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
    command = [sys.executable, str(ROOT/'scripts/architecture.py'), '--repository', REPO, '--pr', PR, '--upstream', upstream]
    result = json.loads(subprocess.check_output([*command, 'decision', '--comparison', refs['comparison'],
                        '--selection', refs['selection'], '--body', str(source)], text=True, timeout=60))
    assert '## Design choices' in result['body']
    operation = publication.prepare('edit-pr', PR, {'body': before},
                                    {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': result['body']}}, ['fixture-reconcile'])
    saved = read(operation['operation']); saved.update(status='verified', receipt={'url': PR}); write(operation['operation'], saved)
    source.write_text(result['body'])
    restored = json.loads(subprocess.check_output([*command, 'governing-body', '--body', str(source), '--operation', operation['operation']], text=True, timeout=60))
    assert restored['body'] == before
    assert arch.governing_body(result['body'], operation['operation'], tasks, upstream=upstream) == before


def test_dependency_validation_is_shared_within_each_fresh_boundary(case, monkeypatch):
    from collections import Counter
    import state
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    captures = Counter()
    original_capture = state.capture
    def capture(assignment, **kwargs):
        if 'architecture' in assignment.get('options', {}):
            captures[assignment['task']['id']] += 1
        return original_capture(assignment, **kwargs)
    monkeypatch.setattr(state, 'capture', capture)
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'invoke'
    # Construction and claim are separate live boundaries, with each stage once.
    expected = {'architecture-' + stage + '-review': 2 for stage in ('assessment', 'comparison', 'selection', 'alignment')}
    assert dict(captures) == expected
    captures.clear()
    task = tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]
    assert task['action'] == 'recover_required' and dict(captures) == expected
    Path(spec['inputs']['context']).write_text('Requirements changed after the preceding helper command')
    with pytest.raises(ValueError, match='fresh'):
        tasks.prepare({'tasks': [ordinary(spec, refs)]})


@pytest.mark.parametrize('paragraph', ['Old architecture\n<span>\n', '-     code example\n'])
def test_inline_html_and_code_list_do_not_hide_root_siblings(paragraph):
    sibling = ('### Other decisions\nKeep unrelated.\n\n' if paragraph.startswith('Old') else
               'Unrelated section\n-----------------\nKeep unrelated.\n\n')
    body = '## Decisions\n\n### Architecture decisions\n\n' + paragraph + sibling + '## Tests\nKeep tests.\n'
    updated = arch.replace_decision(body, 'New architecture')
    assert updated.endswith(sibling + '## Tests\nKeep tests.\n')
    assert arch.replace_decision(updated, 'New architecture') == updated


def test_nonmaterial_impact_reuses_skipped_applicability(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec, required=False)
    checkout = Path(spec['git']['checkout']); (checkout/'code').write_text('another typo\n')
    git(checkout, 'commit', '-qam', 'Second typo')
    spec = deepcopy(spec); spec['git']['head'] = git(checkout, 'rev-parse', 'HEAD')
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Still only typos; no material design choices', 'evidence': ['diff']}, {'assessment': refs['assessment']})
    assert arch.gate(tasks, refs)['outcome'] == 'skipped'
    assert tasks.prepare({'tasks': [ordinary(spec, refs)]})['tasks'][0]['action'] == 'invoke'
    assert len(read(store.manifest)['entries'][refs['assessment']]['attempts']) == 1


def test_successive_own_publications_normalize_original_body_in_fresh_process(case, tmp_path):
    spec, store = case; tasks = Tasks(REPO, PR)
    before = '# Summary\nOriginal governing context.\n'
    body = before
    for index in range(3):
        updated = arch.replace_decision(body, 'Chosen approach: revision ' + str(index))
        operation = publication.prepare('edit-pr', PR, {'body': body},
                    {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': updated}}, ['fixture-reconcile'])
        value = read(operation['operation']); value.update(status='verified', receipt={'url': PR}); write(operation['operation'], value)
        body = updated
    source = tmp_path/'body.md'; source.write_text(body)
    command = [sys.executable, str(ROOT/'scripts/architecture.py'), '--repository', REPO, '--pr', PR,
               'governing-body', '--body', str(source), '--operation', operation['operation']]
    assert json.loads(subprocess.check_output(command, text=True, timeout=60))['body'] == before
    assert arch.governing_body(body + 'External edit', operation['operation'], tasks) == body + 'External edit'
    external = body + 'External edit'
    updated = arch.replace_decision(external, 'Chosen approach: after external edit')
    operation = publication.prepare('edit-pr', PR, {'body': external},
                {'kind': 'edit-pr', 'pr': PR, 'head': spec['git']['head'], 'fields': {'body': updated}}, ['fixture-reconcile'])
    value = read(operation['operation']); value.update(status='verified', receipt={'url': PR}); write(operation['operation'], value)
    assert arch.governing_body(updated, operation['operation'], tasks) == external


@pytest.mark.parametrize('tag', ['<span title="a>b">', "<custom-tag title='<'>", '<widget a="<>" b=plain c d=\'value\'>'])
def test_complete_html_attributes_keep_example_headings_opaque(tag):
    example = tag + '\n## Decisions\n### Architecture decisions\nExample only.\n</span>\n\n'
    body = example + '## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith(body)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('opening,closing', [('<pre>', '</pre>'), ('<!--', '-->'), ('```md', '```'), ('<custom-tag>', '')])
def test_list_blocks_do_not_hide_following_setext_siblings(opening, closing):
    block = '- ' + opening + '\n  example\n' + ('  ' + closing + '\n' if closing else '\n')
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Architecture decisions\n\n' + block + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


def test_parallel_incoming_siblings_share_two_live_validation_boundaries(case, monkeypatch):
    from collections import Counter
    import state
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = aligned(tasks, spec)
    captures, reads = Counter(), []
    original_capture = state.capture
    def capture(assignment, **kwargs):
        if 'architecture' in assignment.get('options', {}):
            captures[assignment['task']['id']] += 1
        return original_capture(assignment, **kwargs)
    monkeypatch.setattr(state, 'capture', capture)
    def live(pr):
        reads.append(pr)
        return deepcopy(LIVE[pr])
    monkeypatch.setattr(arch, 'live_pr', live)
    options = {'tasks': ['dk-workflows:dk-pr-feedback-read'] * 7}
    group = []
    for index in range(7):
        item = ordinary(spec, refs, options)
        item['index'] = index
        group.append(item)
    prepared = tasks.prepare({'tasks': group})['tasks']
    assert all(task['action'] == 'invoke' for task in prepared)
    expected = {'architecture-' + stage + '-review': 2 for stage in ('assessment', 'comparison', 'selection', 'alignment')}
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
    body = '## Decisions\n\n### Architecture decisions\n\n' + tag + '\n' + sibling
    assert arch.replace_decision(body, 'Chosen approach: new').endswith(sibling)


@pytest.mark.parametrize('marker', ['2.', '3)', '123.', '-', '1.'])
def test_noninterrupting_list_markers_preserve_real_siblings(marker):
    # Empty items never interrupt paragraphs; ordered items need start number 1.
    content = ' This is paragraph text' if marker not in {'-', '1.'} else ''
    sibling = '   ### Other decisions\nKeep unrelated.\n\n## Tests\nKeep tests.\n'
    body = '## Decisions\n\n### Architecture decisions\n\nOld design.\n' + marker + content + '\n' + sibling
    assert arch.replace_decision(body, 'Chosen approach: new').endswith(sibling)


def test_dense_setext_suffix_is_not_rescanned_after_known_boundary():
    class Counted(str):
        scans = 0
        def rfind(self, *args):
            type(self).scans += 1
            return super().rfind(*args)
    for prefix in ['## Changelog\n', 'Changelog\n---------\n']:
        view = Counted('old\n\n' + prefix + 'x\n--\n' * 10000)
        Counted.scans = 0
        assert arch.next_section(view, 0, 3) == len('old\n\n')
        assert Counted.scans <= 1


@pytest.mark.parametrize('manual', [False, True])
def test_selection_refresh_finishes_after_impact_bound_old_selection(case, tmp_path, manual):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    old_selection = arch.accepted(tasks, refs['selection'])['reference']
    old_bytes = Path(old_selection).read_bytes()
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Applicability and existing choice remain valid', 'evidence': ['discussion']}, {k: refs[k] for k in ('comparison', 'selection')})
    old_impact = arch.accepted(tasks, refs['impact'])['reference']
    impact_bytes = Path(old_impact).read_bytes()
    options = {'architecture': {'auto_pick': {'enabled': False}}}
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
    assert arch.accepted(tasks, refs['impact'])['reference'] == old_impact
    assert arch.accepted(tasks, refs['selection'])['report']['choice'] == ('smaller' if manual else None)
    child = ('import architecture, json, sys; from tasks import Tasks; '
             't=Tasks(sys.argv[1], sys.argv[2]); '
             'print(json.dumps([architecture.accepted(t, k)["report"] for k in sys.argv[3:]]))')
    reports = json.loads(subprocess.check_output([sys.executable, '-c', child, REPO, PR, refs['impact'], refs['selection']], text=True, timeout=60))
    assert reports[0]['impact'] == 'nonmaterial' and reports[1]['choice'] == value['choice']
    assert Path(old_selection).read_bytes() == old_bytes and Path(old_impact).read_bytes() == impact_bytes
    assert len(read(store.manifest)['entries'][refs['selection']]['attempts']) == 2
    if not manual:
        assert arch.gate(tasks, refs, options=options)['outcome'] == 'choice_pending'


@pytest.mark.parametrize('content', ['***', '* * *', '___', '- - -', 'Title\n  ===', 'Title\n  ---', '*'])
def test_list_nonparagraph_blocks_preserve_root_setext_siblings(content):
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep tests.\n'
    body = '## Decisions\n\n### Architecture decisions\n\nOld design.\n\n- ' + content + '\n' + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


@pytest.mark.parametrize('ending', ['\r', '\r\n', '\n'])
def test_mixed_line_endings_preserve_unrelated_sections(ending):
    sibling = '## Tests' + ending + 'Keep.' + ending
    body = '## Decisions\n\n### Architecture decisions\n\nOld.' + ending * 2 + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


def test_unicode_paragraph_separators_do_not_create_markdown_line_boundaries():
    body = 'Paragraph\u2028```md\n## Decisions\n\n### Architecture decisions\n\nOld.\n\n## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.startswith('Paragraph\u2028```md\n')
    assert updated.endswith('## Tests\nKeep.\n')



def test_new_impact_key_alias_does_not_imply_selection_consumed_new_result(case):
    spec, store = case; tasks = Tasks(REPO, PR)
    refs = initial(tasks, spec)
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Original choice remains valid', 'evidence': ['discussion']}, {k: refs[k] for k in ('comparison', 'selection')})
    refs['selection'] = complete(tasks, spec, 'selection', selection(), {k: refs[k] for k in ('comparison', 'impact')})
    prior_impact = arch.accepted(tasks, refs['impact'])['reference']
    # A later impact uses the same stable key but did not assess the selection.
    refs['impact'] = complete(tasks, spec, 'impact', {'impact': 'nonmaterial', 'reason': 'Comparison still fits changed discussion', 'evidence': ['discussion:2']}, {'comparison': refs['comparison']})
    assert arch.accepted(tasks, refs['impact'])['reference'] != prior_impact
    with pytest.raises(ValueError, match='different selection'):
        tasks.prepare({'tasks': [descriptor(spec, 'alignment', {k: refs[k] for k in ('comparison', 'selection', 'impact')})]})


@pytest.mark.parametrize('prefix', ['- ', '- - ', '1. - - '])
@pytest.mark.parametrize('opening,closing', [('<pre>', '</pre>'), ('<custom-tag>', '</custom-tag>'), ('```md', '```'), ('***', None)])
def test_nested_list_blocks_preserve_following_root_sections(prefix, opening, closing):
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    block = prefix + opening + '\n'
    if closing is not None:
        block += ' ' * len(prefix) + 'example\n' + ' ' * len(prefix) + closing + '\n'
    body = '## Decisions\n\n### Architecture decisions\n\n' + block + sibling
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert updated.endswith(sibling)
    assert arch.replace_decision(updated, 'Chosen approach: new') == updated


def test_thematic_break_ends_root_paragraph_before_html_example():
    example = '<custom-tag>\n### Architecture decisions\nExample only.\n</custom-tag>\n\n'
    body = '## Decisions\n\nOld paragraph.\n* * *\n' + example + '## Tests\nKeep.\n'
    updated = arch.replace_decision(body, 'Chosen approach: new')
    assert example in updated and updated.endswith('## Tests\nKeep.\n')



def test_indented_paragraph_continuation_cannot_start_html_block():
    sibling = '### Other decisions\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Architecture decisions\n\nParagraph.\n    Continuation.\n<span>\n' + sibling
    assert arch.replace_decision(body, 'Chosen approach: new').endswith(sibling)


def test_new_list_item_starts_its_own_html_block():
    sibling = 'Other section\n-------------\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Architecture decisions\n\n- Paragraph.\n- <custom-tag>\n  example\n  </custom-tag>\n' + sibling
    assert arch.replace_decision(body, 'Chosen approach: new').endswith(sibling)


@pytest.mark.parametrize('text', ['===', '--'])
def test_underline_without_preceding_paragraph_is_plain_text(text):
    sibling = '### Other decisions\nKeep unrelated.\n\n## Tests\nKeep.\n'
    body = '## Decisions\n\n### Architecture decisions\n\n' + text + '\n<span>\n' + sibling
    assert arch.replace_decision(body, 'Chosen approach: new').endswith(sibling)
