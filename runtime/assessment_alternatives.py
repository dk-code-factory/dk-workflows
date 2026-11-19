"""Managed shared assessment and per-decision alternatives, with no executor.

The existing private PR task store retains originals, claims and budgets. All
plans, totals and status below are transient projections of accepted evidence.
"""
import base64
from copy import deepcopy
import json
from pathlib import Path
import re

import alternatives
import pr_assessment
import skill_config
from task_policy import capture, make_spec
from task_primitives import git, read, task_key


KIND = 'assessed-alternatives'
STAGES = ('assessment', 'inventory', 'comparison', 'selection', 'interactions', 'alignment', 'verification')


def provider(owner):
    if owner not in {pr_assessment.FIX, pr_assessment.REVIEW}:
        raise ValueError('Alternatives workflow must be dk-pr-fix or dk-pr-review')
    return 'dk-workflows:dk-issue-alternatives' if owner == pr_assessment.FIX else 'dk-workflows:dk-pr-alternatives'


def identity(owner, stage, decision=None):
    provider(owner)
    if stage not in STAGES:
        raise ValueError('Unknown assessed alternatives stage')
    if stage == 'assessment':
        return 'pr-purpose-assessment', provider(owner)
    prefix = 'issue-implementation-' if owner == pr_assessment.FIX else 'pr-architectural-'
    if stage in {'comparison', 'selection'}:
        if not isinstance(decision, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', decision):
            raise ValueError('Per-decision work requires a stable decision ID')
        prefix += decision + '-'
    elif decision is not None:
        raise ValueError('Only comparison and selection take a decision ID')
    return prefix + stage, provider(owner)


def key(tasks, owner, stage, decision=None):
    return task_key(tasks.store.subject, *identity(owner, stage, decision))


def entry(tasks, owner, stage, decision=None):
    with tasks.store.locked() as manifest:
        return deepcopy(manifest['entries'].get(key(tasks, owner, stage, decision)))


def accepted(tasks, task, *, historical=False):
    if historical:
        with alternatives.evaluation(tasks) as context:
            cache_key = ('assessed-history', task)
            if cache_key in context['active']:
                raise ValueError('Circular historical alternatives dependencies')
            if cache_key not in context['accepted']:
                context['active'].add(cache_key)
                try:
                    context['accepted'][cache_key] = _historical_accepted(tasks, task)
                finally:
                    context['active'].remove(cache_key)
            return context['accepted'][cache_key]
    return _current_accepted(tasks, task)


def _historical_accepted(tasks, task):
    from alternatives_history import accepted as original_result
    from task_capture import resource
    value = original_result(tasks, task)
    original_inputs = value['spec']['inputs']
    if value['managed']['kind'] != KIND:
        raise ValueError('Assessed alternatives requires its original assessed definition')
    # Keep the existing spec interface for gate dependency comparisons. Paths
    # come from retained definitions/results, never a copied settings snapshot.
    value['spec']['inputs'] = deepcopy(value['managed']['definition'].get('inputs', {}))
    inherited_requests = set()
    for label, dependency in value['managed'].get('dependencies', {}).items():
        consumed = accepted(tasks, dependency, historical=True)
        if (consumed.get('request')
                and value['spec']['inputs'].get('assessed_' + label, consumed['reference']) == consumed['reference']):
            inherited_requests.add(consumed['request'])
        value['spec']['inputs'].setdefault('assessed_' + label, consumed['reference'])
        for name, path in consumed['spec']['inputs'].items():
            if name.startswith('assessed_'):
                value['spec']['inputs'].setdefault(name, path)
    original = original_inputs
    for name, path in list(value['spec']['inputs'].items()):
        if name.startswith('assessed_'):
            if name not in original:
                value['spec']['inputs'].pop(name)
            elif resource(path) != original[name]:
                raise ValueError('Original alternatives dependency changed; restore its consumed evidence')
    value['request'] = value['spec']['inputs'].get('alternatives_request')
    if not value['request'] and len(inherited_requests) == 1:
        value['request'] = next(iter(inherited_requests))
    return value


def _current_accepted(tasks, task):
    with alternatives.evaluation(tasks) as context:
        cache_key = ('assessed', task)
        if cache_key in context['active']:
            raise ValueError('Circular assessed alternatives dependencies')
        if cache_key in context['accepted']:
            return context['accepted'][cache_key]
        context['active'].add(cache_key)
        try:
            with tasks.store.locked() as manifest:
                value = deepcopy(manifest['entries'].get(task))
            if not value or value.get('managed', {}).get('kind') != KIND:
                raise ValueError('New architectural coverage requires assessed alternatives evidence; legacy results remain recoverable')
            spec = tasks.saved_spec(task)
            saved, problem = tasks.store._load_saved(value)
            if problem or saved['status'] != 'complete':
                raise ValueError('Assessed alternatives evidence is incomplete or damaged')
            status = tasks.store.describe(capture(spec, _context=context['capture']), value)
            if not status['coverage_complete'] or not status.get('checklist', {}).get('complete'):
                raise ValueError('Assessed alternatives evidence requires refresh or completion')
            validate_report(spec, saved['normalized'])
            result = {'key': task, 'spec': spec, 'report': saved['normalized'], 'reference': saved['path']}
            context['accepted'][cache_key] = result
            return result
        finally:
            context['active'].remove(cache_key)


def pinned(tasks, descriptor, definition):
    owner, stage = descriptor['skill'], descriptor['stage']
    original = entry(tasks, owner, stage, descriptor.get('decision')) or entry(tasks, owner, 'assessment')
    result = deepcopy(definition)
    explicit = result.get('inputs', {}).get('alternatives_reassessment') or (stage != 'assessment' and result.get('inputs', {}).get('alternatives_request'))
    if original and not explicit:
        result['git'] = deepcopy(original['managed']['definition']['git'])
    if result.get('governing_inputs_complete') is not True or result.get('cacheable') is False:
        raise ValueError('Shared assessment and alternatives require complete governing inputs')
    if 'git' not in result or 'native_review' not in result:
        raise ValueError('Assessed alternatives requires pinned Git and native execution evidence')
    for pin in ('target', 'base', 'head'):
        result['git'][pin] = git(result['git']['checkout'], 'rev-parse', '--verify',
                                 result['git'][pin] + '^{commit}').decode().strip()
    descriptor['definition']['git'] = deepcopy(result['git'])
    return result


def stage_spec(tasks, descriptor, definition, *, replay=False):
    owner, stage = descriptor.get('skill'), descriptor.get('stage')
    task, skill = identity(owner, stage, descriptor.get('decision'))
    if set(descriptor) - {'kind', 'skill', 'stage', 'decision', 'dependencies', 'definition', 'options', 'policy_options', 'alternatives_options', 'upstream'}:
        raise ValueError('Unknown assessed alternatives descriptor field')
    workflow = skill_config.effective(owner, tasks.store.subject['repository'], descriptor.get('upstream'),
                                      options=descriptor.get('options'))['options']
    if not workflow['alternatives']['enabled']:
        raise ValueError('Alternatives is disabled; retain original evidence and bypass native work')
    policy = pr_assessment.resolve(tasks.store.subject['repository'], descriptor.get('upstream'), owner=owner,
                                   options=descriptor.get('options'), policy_options=descriptor.get('policy_options'),
                                   alternatives_options=descriptor.get('alternatives_options'))
    deps = descriptor.get('dependencies', {})
    if not isinstance(deps, dict) or any(not alternatives.text(k) or not alternatives.text(v) for k, v in deps.items()):
        raise ValueError('Dependencies require labeled accepted task keys')
    required = {'assessment': set(), 'inventory': {'assessment'},
                'comparison': {'assessment', 'inventory'}, 'selection': {'assessment', 'comparison'},
                'interactions': {'assessment', 'inventory'}, 'alignment': {'assessment', 'interactions'},
                'verification': {'alignment'}}[stage]
    if required - deps.keys():
        raise ValueError('Assessed alternatives stage is missing prerequisites')
    retained = {name: accepted(tasks, ref, historical=True) for name, ref in deps.items()}
    inherited_requests = {value['request'] for value in retained.values() if value.get('request')}
    if not definition.get('inputs', {}).get('alternatives_request') and inherited_requests:
        if len(inherited_requests) != 1:
            raise ValueError('Ambiguous original manual request; supply a distinct direct request')
        source = next(iter(inherited_requests))
        from alternatives_history import request as original_request
        original_request(source, tasks=tasks)
        if not replay:
            definition = deepcopy(definition)
            definition.setdefault('inputs', {})['alternatives_request'] = source
            descriptor['definition'].setdefault('inputs', {})['alternatives_request'] = source
    definition = pinned(tasks, descriptor, definition)
    if not replay:
        from alternatives_history import guard_route
        guard_route(tasks, owner, KIND, definition, stage=stage)
    for name in ('alternatives_request', 'alternatives_reassessment'):
        source = definition.get('inputs', {}).get(name)
        if source:
            from alternatives_history import request as original_request
            original_request(source, tasks=tasks)
    if stage == 'assessment' and definition.get('inputs', {}).get('alternatives_request'):
        raise ValueError('Manual alternatives refresh reuses unchanged shared assessment; use explicit reassessment to change it')
    if stage == 'alignment':
        source = definition.get('inputs', {}).get('alternatives_current_pr')
        if not source or not isinstance(read(source).get('body'), str):
            raise ValueError('Alignment requires the existing alternatives_current_pr metadata source')
    for name, value in retained.items():
        assigned = value['spec']['options']['assessed_alternatives']
        expected_stage = 'selection' if name.startswith('selection_') else name
        if assigned['stage'] != expected_stage or (expected_stage != 'assessment' and assigned['owner'] != owner):
            raise ValueError('Assessed alternatives dependency stage or workflow mismatch')
        if 'assessment' in retained and expected_stage != 'assessment':
            bound = value['spec']['inputs'].get('assessed_assessment')
            if bound and bound != retained['assessment']['reference']:
                raise ValueError('Alternatives must consume the same original shared assessment')
        if expected_stage != 'assessment' and value['spec']['git']['target'] != definition['git']['target']:
            raise ValueError('Every comparison and selection must use the same pinned target revision')
    if stage == 'assessment' and deps:
        raise ValueError('Shared assessment has no workflow dependencies')
    if stage == 'comparison':
        decision = find_decision(retained['inventory']['report'], descriptor['decision'])
        if not decision['questionable']:
            raise ValueError('Compare only decisions judged questionable by the persisted rubric')
        pr_assessment.rubric(retained['assessment']['report'], decision['rubric'])
    if stage == 'selection':
        source = retained['comparison']['spec']['options']['assessed_alternatives']
        if source['decision'] != descriptor['decision']:
            raise ValueError('Selection must refer to its decision comparison')
    if stage == 'interactions':
        expected = {d['id'] for d in retained['inventory']['report']['decisions'] if d['questionable']}
        selected = [v for name, v in retained.items() if name.startswith('selection_')]
        if {v['spec']['options']['assessed_alternatives']['decision'] for v in selected} != expected or len(selected) != len(expected):
            raise ValueError('Interactions require exactly every questionable decision selection')
        if any(not v['report'].get('choice') for v in selected):
            raise ValueError('Interactions cannot consume pending choices')
        for selected_value in selected:
            with tasks.store.locked() as manifest:
                comparison_key = manifest['entries'][selected_value['key']]['managed']['dependencies']['comparison']
            compared_value = accepted(tasks, comparison_key, historical=True)
            decision_id = selected_value['spec']['options']['assessed_alternatives']['decision']
            if not same_decision(compared_value['spec']['inputs']['assessed_inventory'], retained['inventory']['report'], decision_id):
                raise ValueError('Interactions cannot mix decisions from different inventories')
    if stage == 'alignment' and not retained['interactions']['report']['compatible']:
        raise ValueError('Unresolved approach interactions block alignment')
    if stage == 'verification' and retained['alignment']['report']['alignment'] != 'mismatch':
        raise ValueError('Verify only a material design mismatch')
    execution = deepcopy(definition)
    if execution.get('skill_identity', skill) != skill:
        raise ValueError('Assessed alternatives definition must resolve ' + skill)
    execution['skill_identity'] = skill
    execution['responsibility'] = task
    relevant_policy = (pr_assessment.assessment_policy(policy) if stage in {'assessment', 'inventory', 'comparison'} else
                       {'selection': policy['selection']} if stage == 'selection' else {})
    execution['assigned_instructions'] = definition.get('assigned_instructions', '') or 'Return the required stage evidence and explicit completion.'
    execution['behavior'] = {**definition.get('behavior', {}), 'skill': skill, 'role': stage,
                             'upstream': descriptor.get('upstream')}
    # Raw invocation edits only; live defaults/scopes are resolved at assignment.
    local = pr_assessment.translate((descriptor.get('options') or {}).get('alternatives', {}))
    import invocation
    local = invocation.merge(local, descriptor.get('policy_options') or {})
    local = invocation.merge(local, descriptor.get('alternatives_options') or {})
    execution['behavior']['options'] = local
    execution.setdefault('inputs', {}).update({'assessed_' + name: value['reference'] for name, value in retained.items()})
    if stage in {'alignment', 'verification'}:
        source = retained['interactions' if stage == 'alignment' else 'alignment']['spec']['inputs']
        for label, reference in source.items():
            if label.startswith('assessed_'):
                execution['inputs'].setdefault(label, reference)
    if stage == 'comparison':
        # Keep the original immutable inventory source when this decision is
        # unchanged. Added/changed siblings must not repeat completed analysis.
        prior = definition.get('inputs', {}).get('assessed_inventory')
        if prior and same_decision(prior, retained['inventory']['report'], descriptor['decision']):
            execution['inputs']['assessed_inventory'] = prior
        descriptor['definition'].setdefault('inputs', {})['assessed_inventory'] = execution['inputs']['assessed_inventory']
    if stage == 'interactions':
        for name, selected in retained.items():
            if not name.startswith('selection_'):
                continue
            label = 'assessed_' + name
            comparison_label = label + '_comparison'
            old_inputs = definition.get('inputs', {})
            if same_choice(old_inputs, label, selected):
                execution['inputs'][label] = old_inputs[label]
            execution['inputs'][comparison_label] = selected['spec']['inputs']['assessed_comparison']
            for field in (label, comparison_label):
                descriptor['definition'].setdefault('inputs', {})[field] = execution['inputs'][field]
    resources = execution.setdefault('resources', {})
    resources.update(assessment_contract=str(Path(pr_assessment.__file__)),
                     assessed_alternatives_contract=str(Path(__file__)),
                     assessed_alternatives_guidance=str(skill_config.ROOT/'docs/alternatives-review.md'))
    if stage != 'assessment':
        resources['assessed_prompt'] = str(skill_config.directory(skill)/'SKILL.md')
    execution.setdefault('options', {})['assessed_alternatives'] = {
        'stage': stage, 'owner': owner if stage != 'assessment' else 'assessment-controller',
        'decision': descriptor.get('decision'), 'policy': relevant_policy}
    execution['options']['process_limits'] = workflow['limits']
    spec = make_spec(**tasks.store.subject, **execution)
    original = entry(tasks, owner, stage, descriptor.get('decision'))
    # Automatic initial analysis cannot silently accept changed governing scope.
    # Explicit requests refresh alternatives, never the unchanged shared assessment.
    if original and original.get('result') and not original.get('claim') and not replay:
        saved, problem = tasks.store._load_saved(original)
        if problem:
            raise ValueError('Original assessed evidence is missing or damaged; restore its sources')
        explicit = spec['inputs'].get('alternatives_reassessment') or (stage != 'assessment' and spec['inputs'].get('alternatives_request'))
        settled = saved['status'] == 'complete' and not (stage == 'selection' and not saved['normalized'].get('choice'))
        if settled and not explicit and capture(spec)['fingerprint'] != saved['fingerprint']:
            raise ValueError('Governing assessment or alternatives inputs changed; explicit reassessment required')
        if settled and explicit:
            previous = original['managed']['definition'].get('inputs', {})
            prior_request = previous.get('alternatives_reassessment') or previous.get('alternatives_request')
            if prior_request:
                from task_capture import resource
                field = 'alternatives_reassessment' if previous.get('alternatives_reassessment') else 'alternatives_request'
                if resource(prior_request) != original['components']['inputs'].get(field):
                    raise ValueError('Original manual request changed; restore its original source')
                if read(prior_request)['id'] == read(explicit)['id'] and capture(spec)['fingerprint'] != saved['fingerprint']:
                    raise ValueError('Completed work of this direct request cannot restart; use a distinct direct user invocation')
    return spec


def find_decision(inventory, decision):
    found = next((d for d in inventory['decisions'] if d['id'] == decision), None)
    if found is None:
        raise ValueError('Comparison requires an inventoried decision')
    return found


def same_decision(source, inventory, decision):
    """Read only original evidence; never retain a copied decision or digest."""
    return find_decision(read(source)['normalized'], decision) == find_decision(inventory, decision)


def same_choice(inputs, label, selected):
    """A mode/rationale refresh with the same option needs no new interaction analysis."""
    source = inputs.get(label)
    return bool(source and inputs.get(label + '_comparison') == selected['spec']['inputs']['assessed_comparison']
                and read(source)['normalized'].get('choice') == selected['report']['choice'])


def compare(report, assessment, rubric_id, target, *, has_pr=True):
    """Both skills use identical rubric enforcement and weighted arithmetic."""
    rubric = pr_assessment.rubric(assessment, rubric_id)
    if report.get('rubric') != rubric_id or report.get('target_revision') != target:
        raise ValueError('Comparison must bind the applicable rubric and pinned target revision')
    if any(field in report and report[field] != rubric[field] for field in ('weights', 'anchors')):
        raise ValueError('Comparison cannot replace the persisted rubric')
    normalized = {**deepcopy(report), 'profile': 'custom', 'profile_rationale': rubric['rationale'],
                  'weights': rubric['weights'], 'anchors': {k: json.dumps(v, sort_keys=True) for k, v in rubric['anchors'].items()}}
    return alternatives.comparison(normalized, {'profile': 'custom', 'custom_prompt': 'Use persisted assessment rubric'}, require_pr=has_pr)


def select(compared, policy, report):
    mode = policy['mode']
    if mode == 'automatic' and report.get('mode') == 'manual':
        raise ValueError('Automatic selection cannot consume a manual choice')
    if mode == 'manual' and report.get('mode') != 'manual':
        if report.get('mode') != 'auto' or report.get('choice') is not None:
            raise ValueError('Manual mode requires an explicit sourced user choice')
        return None
    return alternatives.choose(compared, policy, report)


def scores(spec):
    assigned = spec.get('options', {}).get('assessed_alternatives')
    if not assigned or assigned['stage'] != 'selection':
        return None
    source = read(spec['inputs']['assessed_comparison'])['normalized']
    assessment = read(spec['inputs']['assessed_assessment'])['normalized']
    compared = compare(source, assessment, source['rubric'], spec['git']['target'])
    return {'weights': compared['weights'], 'scores': [
        {k: option[k] for k in ('id', 'total', 'feasible', 'material_doubts')} for option in compared['solutions']],
        'can_prompt': assigned['policy']['selection']['mode'] != 'automatic'}


def validate_report(spec, report):
    assigned = spec.get('options', {}).get('assessed_alternatives')
    if not assigned or not report.get('complete'):
        return
    stage = assigned['stage']
    alternatives.evidence(report)
    if stage == 'assessment':
        pr_assessment.validate(report)
    elif stage == 'inventory':
        if not alternatives.text(report.get('coverage')) or not isinstance(report.get('decisions'), list):
            raise ValueError('Inventory requires branch-wide coverage and decisions')
        assessment = read(spec['inputs']['assessed_assessment'])['normalized']
        ids = set()
        for decision in report['decisions']:
            if not isinstance(decision, dict):
                raise ValueError('Inventory decision must be an object')
            identity(assigned['owner'], 'comparison', decision.get('id'))
            if decision['id'] in ids or decision.get('origin') not in {'inherited', 'introduced', 'materially_changed'}:
                raise ValueError('Inventory requires unique IDs and explicit decision origins')
            ids.add(decision['id'])
            for field in ('scope', 'decision', 'reason'):
                if not alternatives.text(decision.get(field)):
                    raise ValueError('Inventory decision requires ' + field)
            if type(decision.get('questionable')) is not bool:
                raise ValueError('Judge each decision questionable or retained')
            alternatives.evidence(decision)
            pr_assessment.rubric(assessment, decision.get('rubric'))
    elif stage == 'comparison':
        inventory = read(spec['inputs']['assessed_inventory'])['normalized']
        decision = find_decision(inventory, assigned['decision'])
        compare(report, read(spec['inputs']['assessed_assessment'])['normalized'], decision['rubric'], spec['git']['target'])
    elif stage == 'selection':
        source = read(spec['inputs']['assessed_comparison'])['normalized']
        compared = compare(source, read(spec['inputs']['assessed_assessment'])['normalized'], source['rubric'], spec['git']['target'])
        choice = select(compared, assigned['policy']['selection'], report)
        if report.get('choice') != choice:
            raise ValueError('Selection disagrees with shared policy; unresolved choice is null')
        if report.get('mode') == 'manual':
            source = report.get('user_source')
            if source != spec['inputs'].get('user_choice') or source not in report['evidence']:
                raise ValueError('Manual selection requires its explicit user source bound as an input and cited')
        if choice:
            rejected = {s['id'] for s in compared['solutions']} - {choice}
            reasons = report.get('rejection_reasons')
            if (not alternatives.text(report.get('reason')) or not isinstance(reasons, dict)
                    or set(reasons) != rejected or any(not alternatives.text(v) for v in reasons.values())):
                raise ValueError('Settled choices require rationale and rejection reasons for every option')
    elif stage == 'interactions':
        if type(report.get('compatible')) is not bool or not alternatives.text(report.get('reason')):
            raise ValueError('Interactions require compatible boolean and reason')
        if not report['compatible'] and not alternatives.text(report.get('required_changes')):
            raise ValueError('Incompatible choices require substantive resolution')
    else:
        alternatives.validate_report({**spec, 'options': {'alternatives': {'stage': stage, 'policy': {}}}}, report)


def gate(tasks, owner, *, options=None, policy_options=None, alternatives_options=None, upstream=None, request=None,
         review_operation=None, analysis_only=False, _historical=False, _consumer=None):
    """Discover compact next work; no durable completion flag or rendered plan."""
    provider(owner)
    consumer = _consumer or owner
    enabled = skill_config.effective(consumer, tasks.store.subject['repository'], upstream, options=options)['options']['alternatives']['enabled']
    if not enabled:
        return {'outcome': 'disabled', 'release_reviewers': True}
    if request:
        from alternatives_history import request as original_request
        original_request(request, tasks=tasks)
    if not _historical and _consumer is None:
        from alternatives_history import route
        retained_route = route(tasks, owner, request_source=request)
        if retained_route:
            if retained_route['kind'] == KIND:
                return gate(tasks, retained_route['owner'], options=options, policy_options=policy_options,
                            alternatives_options=alternatives_options, upstream=upstream,
                            review_operation=review_operation, analysis_only=analysis_only, request=request,
                            _historical=not bool(request),
                            _consumer=owner)
            from alternatives_history import legacy_gate
            return legacy_gate(tasks, retained_route['owner'], review_operation=review_operation,
                               analysis_only=analysis_only, consumer=owner, request_source=request)
    owner_options = options if owner == consumer else None
    policy = pr_assessment.resolve(tasks.store.subject['repository'], upstream, owner=owner, options=owner_options,
                                   policy_options=policy_options, alternatives_options=alternatives_options)
    retained, refs = {}, {}
    with alternatives.evaluation(tasks):
        def require(stage, dependencies, decision=None):
            task = key(tasks, owner, stage, decision)
            descriptor = {'kind': KIND, 'skill': owner, 'stage': stage, 'dependencies': dependencies,
                          'options': owner_options or {}, 'policy_options': policy_options or {},
                          'alternatives_options': alternatives_options or {}, 'upstream': upstream,
                          'definition': {'skill_identity': identity(owner, stage, decision)[1]}}
            if decision:
                descriptor['decision'] = decision
            original = entry(tasks, owner, stage, decision)
            managed = (original or {}).get('managed')
            definition = managed.get('definition') if isinstance(managed, dict) else None
            from alternatives_history import definition_valid
            if original and not definition_valid(definition):
                return None, {'outcome': 'recovery_required', 'release_reviewers': False,
                              'stage': stage, 'next_task': {'key': task}, 'references': refs,
                              'reason': 'Restore the original alternatives definition'}
            value = None
            problem = None
            if original and original.get('result'):
                try:
                    value = accepted(tasks, task, historical=_historical or bool(request))
                except ValueError as exc:
                    problem = str(exc)
            if value is not None:
                prior_request = value.get('request', value['spec']['inputs'].get('alternatives_request'))
                continuing = bool(request and prior_request and read(prior_request)['id'] == read(request)['id'])
                reuse_history = _historical or continuing or stage == 'assessment' and bool(request)
                assigned = value['spec']['options']['assessed_alternatives']
                expected_policy = (pr_assessment.assessment_policy(policy) if stage in {'assessment', 'inventory', 'comparison'} else
                                   {'selection': policy['selection']} if stage == 'selection' else {})
                same_deps = all(
                    same_decision(value['spec']['inputs']['assessed_inventory'], retained[ref]['report'], decision)
                    if stage == 'comparison' and name == 'inventory' else
                    same_choice(value['spec']['inputs'], 'assessed_' + name, retained[ref])
                    if stage == 'interactions' and name.startswith('selection_') else
                    value['spec']['inputs'].get('assessed_' + name) == retained[ref]['reference']
                    for name, ref in dependencies.items())
                same_request = not request or stage == 'assessment' or prior_request == request
                if (reuse_history or assigned['policy'] == expected_policy) and same_deps and same_request:
                    retained[task] = value
                    refs[stage + (':' + decision if decision else '')] = task
                    return value, None
            # A saved descriptor keeps original inputs and native contract. The
            # coordinator supplies changed explicit sources when refreshing.
            if original:
                descriptor['definition'] = deepcopy(original['managed']['definition'])
            if _historical and original and original.get('result'):
                saved, damaged = tasks.store._load_saved(original)
                if damaged or saved['status'] == 'complete':
                    return None, {'outcome': 'recovery_required', 'release_reviewers': False,
                                  'stage': stage, 'next_task': {'key': task}, 'references': refs,
                                  'reason': problem or 'Restore original evidence; do not repeat completed analysis'}
            if stage != 'assessment' and request:
                descriptor.setdefault('definition', {}).setdefault('inputs', {})['alternatives_request'] = request
            return None, {'outcome': 'review_required', 'release_reviewers': False, 'stage': stage,
                          'next_task': descriptor, 'references': refs, **({'reason': problem} if problem else {})}
        assessment, pending = require('assessment', {})
        if pending:
            return pending
        assessment_key = assessment['key']
        inventory, pending = require('inventory', {'assessment': assessment_key})
        if pending:
            return pending
        selections = {}
        for decision in inventory['report']['decisions']:
            if not decision['questionable']:
                continue
            compared, pending = require('comparison', {'assessment': assessment_key, 'inventory': inventory['key']}, decision['id'])
            if pending:
                return pending
            selected, pending = require('selection', {'assessment': assessment_key, 'comparison': compared['key']}, decision['id'])
            if pending:
                return pending
            if not selected['report'].get('choice'):
                return {'outcome': 'choice_pending', 'release_reviewers': False, 'references': refs,
                        'next_task': {'key': selected['key']}, 'can_prompt': policy['selection']['mode'] != 'automatic'}
            selections['selection_' + decision['id']] = selected['key']
        interactions, pending = require('interactions', {'assessment': assessment_key, 'inventory': inventory['key'], **selections})
        if pending:
            return pending
        if not interactions['report']['compatible']:
            return {'outcome': 'interactions_blocked', 'release_reviewers': False, 'references': refs}
        alignment, pending = require('alignment', {'assessment': assessment_key, 'interactions': interactions['key']})
        if pending:
            return pending
        result = alignment['report']['alignment']
        governing = [v['reference'] for v in retained.values()]
        if request:
            live = alternatives.live_pr(tasks.store.subject['pr'])
            current = read(alignment['spec']['inputs']['alternatives_current_pr'])
            if (live['headRefOid'] != alignment['spec']['git']['head']
                    or any(current.get(k) != live.get(k) for k in ('body', 'headRefOid'))):
                return {'outcome': 'alignment_blocked', 'release_reviewers': False, 'references': refs}
        if result == 'aligned' or (_consumer or owner) == pr_assessment.FIX and result == 'mismatch':
            from alternatives_history import context
            return context({'outcome': 'planned', 'release_reviewers': True, 'references': refs, 'governing_evidence': governing,
                            'required_changes': alignment['report'].get('required_changes')})
        if result == 'mismatch':
            verification, pending = require('verification', {'alignment': alignment['key']})
            if pending:
                return pending
            if verification['report']['disposition'] == 'confirmed':
                def handle(value):
                    with tasks.store.locked() as manifest:
                        saved, _ = tasks.store._load_saved(manifest['entries'][value['key']])
                    return json.loads(base64.b64decode(saved['native_execution']['evidence']))['invocation']['handle']
                if handle(verification) == handle(alignment):
                    raise ValueError('Design mismatch requires independent native verification')
                if review_operation:
                    head = alignment['spec']['git']['head']
                    operation = alternatives.journal(review_operation, tasks, kind='post-review', head=head)
                    approved = operation['request']['approved']
                    if (approved['event'] != 'REQUEST_CHANGES' or approved.get('comments')
                            or approved.get('body') != verification['report']['required_changes']):
                        raise ValueError('Alternatives rejection must contain the independently verified required design changes')
                    live = alternatives.live_pr(tasks.store.subject['pr'])
                    if request and live['headRefOid'] != head:
                        raise ValueError('Explicit manual alternatives requires current alignment')
                    if live['headRefOid'] != head:
                        from alternatives_history import context
                        return context({'outcome': 'planned', 'release_reviewers': True, 'references': refs,
                                        'governing_evidence': [*governing, verification['reference']]})
                    return {'outcome': 'alternatives_rejected', 'release_reviewers': False,
                            'complete': True, 'ordinary_review_complete': False, 'references': refs}
                permissions = skill_config.effective(consumer, tasks.store.subject['repository'], upstream, options=options)['options']['permissions']
                if analysis_only or not permissions['post_review']:
                    return {'outcome': 'publication_blocked', 'release_reviewers': False, 'references': refs}
                return {'outcome': 'mismatch_verified', 'release_reviewers': False, 'references': refs,
                        'required_changes': verification['report']['required_changes'], 'head': alignment['spec']['git']['head']}
        return {'outcome': 'alignment_blocked', 'release_reviewers': False, 'references': refs}


def enforce(spec, tasks, owner, *, options=None, upstream=None):
    execution = spec.get('options', {}).get('execution', spec.get('options', {}))
    result = gate(tasks, owner, options=options, upstream=upstream,
                  policy_options=execution.get('alternatives_policy_options'),
                  alternatives_options=execution.get('alternatives_skill_options'),
                  review_operation=execution.get('alternatives_review_operation'),
                  analysis_only=execution.get('analysis_only', False))
    if not result['release_reviewers']:
        label = ('Initial alternatives gate blocks fix work' if owner == pr_assessment.FIX else
                 'Alternatives applicability gate blocks ordinary tasks')
        raise ValueError(label + ': ' + result['outcome'])
    if owner == pr_assessment.REVIEW and result['outcome'] == 'skipped':
        spec['inputs']['alternatives_applicability'] = result['governing_evidence'][0]
        return
    for index, reference in enumerate(result.get('governing_evidence', [])):
        spec['inputs']['alternatives_fix_governing_' + str(index) if owner == pr_assessment.FIX else
                       'alternatives_governing_' + str(index)] = reference
