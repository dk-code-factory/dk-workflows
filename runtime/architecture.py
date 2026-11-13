"""Architecture gate contracts and arithmetic over existing managed evidence.

No execution engine or architecture store: the native session owns analysis.
"""
import base64
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from decimal import Decimal
import re
import subprocess
from pathlib import Path

from state import make_spec, read
import skill_config

OWNER = 'dk-workflows:dk-pr-review'
REVIEW_SKILL = 'dk-workflows:dk-architecture-review'
CRITERIA = ('edge_coverage', 'quality', 'simplicity', 'low_intrusiveness', 'low_loc', 'low_operational_cost')
PROFILES = {
    'minimal_change_edge_coverage': dict(zip(CRITERIA, (40, 15, 15, 15, 10, 5))),
    'quality': dict(zip(CRITERIA, (25, 30, 20, 10, 5, 10))),
    'minimal_change': dict(zip(CRITERIA, (15, 15, 20, 25, 20, 5))),
}
CHECKS = {
    'requirements': 'Account for functional and nonfunctional requirements and mandatory feasibility gates.',
    'alternatives': 'Cover materially distinct reasonable repository-supported approaches, including the PR and smallest viable alternative; explain omissions.',
    'compatibility': 'Assess boundaries, interfaces, dependencies and backward compatibility.',
    'security': 'Assess security and trust boundaries.',
    'concurrency': 'Assess concurrency, races and state ownership.',
    'partial-failure': 'Assess partial failure, durability and recovery.',
    'retries': 'Assess retries, idempotence and duplicate effects.',
    'migration': 'Assess migration and deployment order.',
    'rollback': 'Assess rollback and recovery limits.',
    'operational-cost': 'Assess runtime and whole-workflow operational/API dollar cost.',
    'estimates': 'Estimate added plus deleted production LOC relative to the pinned diff base, excluding tests/docs/generated output and including executable infrastructure configuration; explain complexity/intrusiveness.',
    'scoring': 'Use shared anchored evidence-backed ratings and rubric; helpers calculate weighted totals.',
    'uncertainties': 'List material doubts, unhandled edge cases and consequences.',
    'recommendation': 'Support recommendations and rejected alternatives with source evidence.',
}
STAGES = ('assessment', 'comparison', 'impact', 'selection', 'alignment', 'verification')
ALLOWS = {'assessment': (), 'comparison': ('assessment',), 'impact': ('assessment', 'comparison', 'selection'),
          'selection': ('comparison', 'impact'), 'alignment': ('comparison', 'selection', 'impact'),
          'verification': ('alignment',)}
REQUIRES = {'assessment': (), 'comparison': ('assessment',), 'impact': (),
            'selection': ('comparison',), 'alignment': ('comparison', 'selection'),
            'verification': ('alignment',)}
GUIDANCE = {
    'assessment': 'Inspect current requirements, complete discussion, Decisions and pinned diff. Require architecture review for material boundaries, interfaces, dependencies, state, concurrency, recovery or operational choices; otherwise explain a simple-change skip with evidence.',
    'comparison': 'Compare reasonable materially distinct approaches using the assigned prompt and mandatory checklist. Return all required solution fields; select a scoring profile when auto. Do not choose, publish or run ordinary reviewers.',
    'impact': 'Compare retained applicability, alternatives and settled selection with current requirements, solution choices, constraints and relevant evidence. Nonmaterial requires unchanged applicability, feasibility, choice, confidence and estimate sensitivity, including any sourced user choice. Return material, nonmaterial or uncertain impact with evidence. Only nonmaterial permits reuse; always refresh current alignment. Own Decisions edits require exact reconciled before/after evidence.',
    'selection': 'Apply assigned selection policy to accepted comparison. Evaluate confidence and estimate sensitivity. Manual choices require an explicit user source and rationale. Pending choice blocks other reviewers. Do not publish.',
    'alignment': 'Compare current pinned implementation to the selected approach. Return aligned, mismatch or uncertain with evidence. A material mismatch needs substantive required design changes. Do not run ordinary reviewers.',
    'verification': 'Independently challenge the material architecture mismatch against the selected decision and current pinned implementation. Return confirmed, rejected or uncertain with evidence and required design changes. Verify only; no publication or ordinary review.',
}


def text(value):
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def evidence(report):
    if not isinstance(report.get('evidence'), list) or not report['evidence'] or not all(map(text, report['evidence'])):
        raise ValueError('Architecture requires source evidence')


def scoring_policy(scoring):
    keys = ['profile']
    if scoring['profile'] == 'auto':
        keys.append('profile_selection_prompt')
    if scoring['profile'] == 'custom':
        keys.append('custom_prompt')
    return {key: scoring[key] for key in keys}


def rubric(report, scoring):
    profile = report.get('profile')
    configured = scoring['profile']
    if configured != 'auto' and profile != configured:
        raise ValueError('Architecture scoring profile differs from configuration')
    if not text(report.get('profile_rationale')):
        raise ValueError('Scoring profile needs a circumstance rationale')
    if configured == 'auto' and profile not in PROFILES:
        raise ValueError('Auto selects a built-in scoring profile')
    if profile == 'custom':
        if not text(scoring['custom_prompt']):
            raise ValueError('Custom scoring requires custom_prompt')
        weights = report.get('weights')
        if (not isinstance(weights, dict) or not weights
                or any(not text(k) or type(v) is not int or v <= 0 for k, v in weights.items())
                or sum(weights.values()) != 100):
            raise ValueError('Custom weights must be positive integers summing to 100')
    elif profile in PROFILES:
        weights = PROFILES[profile]
        if 'weights' in report and report['weights'] != weights:
            raise ValueError('Built-in weights cannot be overridden')
    else:
        raise ValueError('Auto selects a built-in scoring profile')
    anchors = report.get('anchors')
    if (not isinstance(anchors, dict) or set(anchors) != set(weights)
            or any(not text(v) for v in anchors.values())):
        raise ValueError('Every criterion requires shared rating anchors (0, 50, 100)')
    return weights


def comparison(report, scoring):
    weights = rubric(report, scoring)
    solutions = report.get('solutions')
    if not isinstance(solutions, list) or len(solutions) < 2:
        raise ValueError('Compare the PR implementation and a smallest viable alternative')
    ids, kinds, calculated = set(), set(), []
    for option in solutions:
        if not isinstance(option, dict) or not text(option.get('id')) or option['id'] in ids:
            raise ValueError('Solutions require distinct IDs')
        ids.add(option['id'])
        kind = option.get('kind')
        if kind not in {'pr', 'smallest', 'alternative'}:
            raise ValueError('Solution kind must be pr, smallest or alternative')
        kinds.add(kind)
        for field in ('approach', 'components', 'pros', 'cons', 'handled_edges', 'unhandled_edges',
                      'complexity_reason', 'intrusiveness_reason', 'feasibility_reason', 'recommendation_reason'):
            if not text(option.get(field)):
                raise ValueError('Solution requires ' + field)
        evidence(option)
        loc = option.get('production_loc', {})
        if (not isinstance(loc, dict) or set(loc) != {'min', 'expected', 'max', 'reason'}
                or any(type(loc.get(k)) is not int or loc[k] < 0 for k in ('min', 'expected', 'max'))
                or not loc['min'] <= loc['expected'] <= loc['max'] or not text(loc['reason'])):
            raise ValueError('Production LOC requires ordered min/expected/max and reason')
        if any(type(option.get(k)) is not int or not 0 <= option[k] <= 100
               for k in ('complexity', 'intrusiveness')):
            raise ValueError('Complexity and intrusiveness require explained 0–100 scores')
        if type(option.get('feasible')) is not bool or type(option.get('recommended')) is not bool:
            raise ValueError('Solutions require feasibility and recommendation booleans')
        doubts = option.get('material_doubts')
        if not isinstance(doubts, list) or any(not text(v) for v in doubts):
            raise ValueError('List unresolved material doubts explicitly')
        ratings = option.get('ratings')
        reasons = option.get('rating_evidence')
        if (not isinstance(ratings, dict) or set(ratings) != set(weights)
                or any(type(v) is not int or not 0 <= v <= 100 for v in ratings.values())
                or not isinstance(reasons, dict) or set(reasons) != set(weights)
                or any(not text(v) for v in reasons.values())):
            raise ValueError('Every solution needs normalized ratings and evidence for the shared rubric')
        total = sum(Decimal(weights[k]) * ratings[k] for k in weights) / 100
        if 'total' in option and (type(option['total']) not in (int, float)
                                  or Decimal(str(option['total'])) != total):
            raise ValueError('Agent total differs from helper arithmetic')
        calculated.append({**deepcopy(option), 'total': float(total)})
    if not {'pr', 'smallest'} <= kinds:
        raise ValueError('Comparison must include PR and smallest viable alternative')
    if not text(report.get('alternative_coverage')):
        raise ValueError('Explain reasonable alternative coverage and rejected/infeasible approaches')
    return {**deepcopy(report), 'weights': weights, 'solutions': calculated}


def choose(compared, policy, assessment):
    """Hard score/feasibility rules cannot be overridden by a prompt."""
    if assessment.get('mode') == 'manual':
        if not all(text(assessment.get(k)) for k in ('choice', 'user_source', 'reason')):
            raise ValueError('Manual selection requires an explicit sourced user choice')
        option = next((s for s in compared['solutions'] if s['id'] == assessment['choice']), None)
        if option is None or not option['feasible']:
            raise ValueError('Manual choice must be a feasible compared option')
        return option['id']
    if assessment.get('mode') != 'auto':
        raise ValueError('Selection requires auto or manual mode')
    if not policy['enabled']:
        return None
    feasible = sorted((s for s in compared['solutions'] if s['feasible']), key=lambda s: s['total'], reverse=True)
    if not feasible:
        return None
    winner = feasible[0]
    if (any(s['material_doubts'] for s in compared['solutions']) or type(assessment.get('confident')) is not bool
            or not assessment['confident'] or not text(assessment.get('sensitivity'))):
        return None
    if len(feasible) > 1 and (winner['total'] == feasible[1]['total']
                             or Decimal(str(winner['total'])) - Decimal(str(feasible[1]['total']))
                             < Decimal(str(policy['min_score_gap']))):
        return None
    if len(feasible) == 1 and not text(assessment.get('rejected_alternatives')):
        return None
    return winner['id']


def validate_report(spec, report, *, compared=None):
    assigned = spec.get('options', {}).get('architecture')
    if assigned is None or not report.get('complete'):
        return
    stage, config = assigned['stage'], assigned['policy']
    evidence(report)
    if stage == 'assessment':
        if type(report.get('required')) is not bool or not text(report.get('reason')):
            raise ValueError('Applicability requires boolean required and an evidence-backed reason')
    elif stage == 'comparison':
        comparison(report, config['scoring'])
    elif stage == 'impact':
        if report.get('impact') not in {'nonmaterial', 'material', 'uncertain'} or not text(report.get('reason')):
            raise ValueError('Impact requires a supported material/nonmaterial/uncertain assessment')
    elif stage == 'selection':
        if compared is None:
            compared = read(spec['inputs']['architecture_comparison'])['normalized']
        compared = comparison(compared, assigned['comparison_scoring'])
        selected = choose(compared, config['auto_pick'], report)
        if report.get('choice') != selected:
            raise ValueError('Selection choice disagrees with deterministic rules; pending choice is null')
        if selected:
            reasons = report.get('rejection_reasons')
            alternatives = {s['id'] for s in compared['solutions']} - {selected}
            if (not text(report.get('reason')) or not isinstance(reasons, dict)
                    or set(reasons) != alternatives or any(not text(v) for v in reasons.values())):
                raise ValueError('Settled choice requires rationale and rejection reasons for every alternative')
    elif stage == 'alignment':
        if report.get('alignment') not in {'aligned', 'mismatch', 'uncertain'} or not text(report.get('reason')):
            raise ValueError('Alignment requires aligned/mismatch/uncertain and evidence')
        if report['alignment'] == 'mismatch' and not text(report.get('required_changes')):
            raise ValueError('Mismatch requires substantive design changes')
    elif stage == 'verification':
        if report.get('disposition') not in {'confirmed', 'rejected', 'uncertain'} or not text(report.get('reason')):
            raise ValueError('Independent mismatch verification requires a sourced disposition')
        if report['disposition'] == 'confirmed' and not text(report.get('required_changes')):
            raise ValueError('Confirmed mismatch requires substantive design changes')


_EVALUATION = ContextVar('architecture_evaluation', default=None)


@contextmanager
def evaluation(tasks):
    """One read-only validation boundary; nothing survives native work or commands."""
    existing = _EVALUATION.get()
    if existing is not None and existing['tasks'] is tasks:
        yield existing
        return
    from state import _CaptureContext
    context = {'tasks': tasks, 'accepted': {}, 'active': set(), 'capture': _CaptureContext(), 'live': {}}
    token = _EVALUATION.set(context)
    try:
        yield context
    finally:
        _EVALUATION.reset(token)


def accepted(tasks, key, *, historical=False):
    with evaluation(tasks) as context:
        identity = (key, historical)
        if identity in context['active']:
            raise ValueError('Circular architecture evidence dependencies')
        if identity not in context['accepted']:
            context['active'].add(identity)
            try:
                context['accepted'][identity] = _accepted(tasks, key, context, historical=historical)
            finally:
                context['active'].remove(identity)
        return context['accepted'][identity]


def _accepted(tasks, key, context, *, historical=False):
    """Validate original evidence and checklist; historical comparisons need impact."""
    from state import capture
    # Historical checks regenerate guidance without resolving live dependencies.
    # Impact, rather than another invocation of each old stage, governs changed inputs.
    with tasks.store.locked() as manifest:
        entry = deepcopy(manifest['entries'][key])
    if historical:
        descriptor = entry['managed']
        tasks._retained_policy(descriptor)
        loaded = skill_config.effective(OWNER, tasks.store.subject['repository'], descriptor.get('upstream'),
                                        options=descriptor.get('options'))
        definition = {k: v for k, v in descriptor['definition'].items() if k not in {'checklist', 'required'}}
        execution = stage_execution(definition, descriptor['stage'], loaded['options'])
        current_spec = make_spec(**tasks.store.subject, **execution)
    else:
        current_spec = tasks.saved_spec(key)
    current_capture = capture(current_spec, _context=context['capture'])
    saved, problem = tasks.store._load_saved(entry)
    if problem or saved['status'] != 'complete':
        raise ValueError('Architecture evidence is incomplete or damaged')
    if historical:
        original_components = entry['components']
        for field in ('task', 'skill', 'instructions', 'native_review', 'instruction_policy'):
            if current_capture['components'].get(field) != original_components.get(field):
                raise ValueError('Architecture review guidance changed; refresh affected analysis')
        spec = deepcopy(original_components)
        captured = {'key': key, 'fingerprint': saved['fingerprint'],
                    'components': spec, 'cacheable': saved['cacheable']}
    else:
        spec, captured = current_spec, current_capture
    described = tasks.store.describe(captured, entry)
    if not described['coverage_complete'] or not described.get('checklist', {}).get('complete'):
        raise ValueError('Architecture evidence lacks fresh native/checklist coverage')
    report = deepcopy(saved['normalized'])
    reference = saved['path']
    native = json.loads(base64.b64decode(saved['native_execution']['evidence']))
    compared = None
    if historical and spec['options']['architecture']['stage'] == 'selection':
        from state import resource
        source = accepted(tasks, spec['options']['architecture']['dependencies']['comparison'], historical=True)
        if spec['inputs']['architecture_comparison'] != resource(source['reference']):
            raise ValueError('Architecture selection refers to a changed comparison')
        compared = source['report']
    validate_report(spec, report, compared=compared)
    assigned = spec['options']['architecture']
    return {'key': key, 'spec': spec, 'report': report, 'reference': reference, 'native_handle': native['invocation']['handle'], **assigned}


def stage_spec(tasks, descriptor, definition, *, replay=False):
    with evaluation(tasks):
        return _stage_spec(tasks, descriptor, definition, replay=replay)


def _stage_spec(tasks, descriptor, definition, *, replay=False):
    if set(descriptor) - {'kind', 'stage', 'dependencies', 'definition', 'options', 'upstream'}:
        raise ValueError('Architecture descriptors use stage, dependencies and owner options only')
    stage = descriptor.get('stage')
    if stage not in STAGES:
        raise ValueError('Unknown architecture stage')
    loaded = skill_config.effective(OWNER, tasks.store.subject['repository'], descriptor.get('upstream'),
                                    options=descriptor.get('options'))
    config = loaded['options']['architecture']
    if not config['enabled']:
        raise ValueError('Architecture is disabled; bypass assessment, execution and publication')
    dependencies = descriptor.get('dependencies', {})
    if not isinstance(dependencies, dict) or set(dependencies) - set(ALLOWS[stage]):
        raise ValueError('Architecture dependencies must reference stage task keys')
    if set(REQUIRES[stage]) - dependencies.keys():
        raise ValueError('Architecture stage is missing prerequisite evidence')
    if stage == 'impact' and not {'assessment', 'comparison'} & dependencies.keys():
        raise ValueError('Impact requires retained applicability or comparison')
    retained = {}
    for name, key in dependencies.items():
        if replay and stage == 'impact' and name == 'selection':
            retained[name] = impact_selection_source(tasks, key)
        else:
            retained[name] = accepted(tasks, key, historical=(name in {'assessment', 'comparison', 'selection'}
                                                             and (stage == 'impact' or 'impact' in dependencies)))
    for name, value in retained.items():
        if value['stage'] != name:
            raise ValueError('Architecture dependency stage mismatch')
    if stage == 'comparison' and retained['assessment']['report']['required'] is not True:
        raise ValueError('Skipped applicability cannot start comparison')
    if 'impact' in retained and retained['impact']['report']['impact'] != 'nonmaterial':
        raise ValueError('Material or uncertain impact requires refreshed architecture analysis')
    if stage == 'alignment' and 'impact' in retained:
        validate_selection_impact(retained['impact'], retained['selection'])
    if stage == 'alignment' and not retained['selection']['report'].get('choice'):
        raise ValueError('Architecture choice remains pending')
    if stage == 'verification' and retained['alignment']['report']['alignment'] != 'mismatch':
        raise ValueError('Only material mismatches require independent verification')
    execution = stage_execution(definition, stage, loaded['options'])
    execution.setdefault('inputs', {}).update({'architecture_' + name: value['reference']
                                               for name, value in retained.items()})
    execution['options']['architecture']['dependencies'] = dependencies
    if stage == 'selection':
        execution['options']['architecture']['comparison_scoring'] = retained['comparison']['policy']['scoring']
    return make_spec(**tasks.store.subject, **execution)


def impact_selection_source(tasks, key):
    """Recover context already bound by impact, never current selection coverage.

    New impact preparation accepts the current selection normally. Replaying that
    assignment keeps its exact immutable source while selection is refreshed.
    """
    from state import digest, resource
    impact_key = digest({**tasks.store.subject, 'task': 'architecture-impact-review', 'skill': OWNER})
    with tasks.store.locked() as manifest:
        impact = manifest['entries'][impact_key]
        expected = impact['components']['inputs']['architecture_selection']
        attempts = deepcopy(manifest['entries'][key]['attempts'])
    for attempt in reversed(attempts):
        filename = attempt.get('result')
        if not filename or Path(filename).name != filename:
            continue
        path = tasks.store.root/'results'/filename
        if not path.is_file() or resource(path) != expected:
            continue
        envelope = read(path)
        entry = {'key': key, 'result': {'file': filename, 'hash': Path(filename).stem,
                                       'fingerprint': envelope['fingerprint']}}
        saved, problem = tasks.store._load_saved(entry)
        if problem or saved['status'] != 'complete':
            raise ValueError('Impact selection source is incomplete or damaged')
        return {'stage': 'selection', 'reference': saved['path']}
    raise ValueError('Impact selection source is missing; retain original analysis and recovery references')


def stage_execution(definition, stage, options):
    """Construct current guidance independently of retained analysis inputs."""
    config = options['architecture']
    execution = deepcopy(definition)
    if stage == 'impact':
        source = execution.get('inputs', {}).get('architecture_current_pr')
        if not source or not isinstance(read(source).get('body'), str):
            raise ValueError('Impact requires the existing current PR metadata input architecture_current_pr')
    if 'git' not in execution:
        raise ValueError('Architecture stages require pinned Git inputs')
    if 'native_review' not in execution:
        raise ValueError('Architecture stages require a native execution contract')
    identity = config['review_skill'] if stage in {'assessment', 'comparison'} else OWNER
    if execution.get('skill_identity') != identity:
        raise ValueError('Architecture definition must resolve the exact stage skill')
    policy = ({'scoring': scoring_policy(config['scoring'])} if stage == 'comparison' else
              {'auto_pick': config['auto_pick']} if stage == 'selection' else {})
    if stage == 'comparison':
        execution['assigned_instructions'] = GUIDANCE[stage]
        if config['scoring']['profile'] == 'auto':
            execution['assigned_instructions'] += '\n' + config['scoring']['profile_selection_prompt']
        if config['scoring']['profile'] == 'custom':
            execution['assigned_instructions'] += '\n' + config['scoring']['custom_prompt']
    else:
        execution['assigned_instructions'] = GUIDANCE[stage]
    if stage == 'selection':
        execution['assigned_instructions'] += '\n' + config['auto_pick']['prompt']
    assigned = definition.get('assigned_instructions')
    if assigned:
        execution['assigned_instructions'] += '\n\nOriginal additional instructions:\n' + assigned
    execution['responsibility'] = 'architecture-' + stage + '-review'
    resources = execution.setdefault('resources', {})
    resources['architecture_contract'] = str(Path(__file__))
    resources['architecture_guidance'] = str(skill_config.ROOT/'docs/architecture-review.md')
    target = skill_config.directory(identity)
    if target:
        resources['architecture_prompt'] = str(target/'SKILL.md')
    execution.setdefault('options', {})['process_limits'] = options['limits']
    execution['options']['architecture'] = {'stage': stage, 'policy': policy, 'dependencies': {}}
    return execution


def linked(value, name, other):
    if value['dependencies'].get(name) != other['key']:
        raise ValueError('Architecture evidence refers to a different ' + name)


def validate_selection_impact(impact, selection):
    """Stable task keys alone do not identify the impact a choice consumed."""
    from state import resource
    def bound(value, label):
        source = value['spec']['inputs'].get(label)
        return resource(source) if isinstance(source, str) else source
    if (selection['dependencies'].get('impact') == impact['key']
            and bound(selection, 'architecture_impact') == resource(impact['reference'])):
        return
    linked(impact, 'selection', selection)
    if bound(impact, 'architecture_selection') != resource(selection['reference']):
        raise ValueError('Impact assessed a different selection result; refresh affected evidence')


def journal(path, tasks, *, kind, head):
    from shared import publication
    value = publication.load(path, kind)
    request = value['request']
    if (value['status'] != 'verified' or not value.get('receipt')
            or request['target'].lower() != tasks.store.subject['pr'].lower()
            or (head is not None and request['approved'].get('head') != head)):
        raise ValueError('Architecture publication needs a reconciled receipt for this PR/head')
    return value



def live_pr(pr):
    """Current publication boundary, never a retained readiness cache."""
    from shared.github_reads import run
    try:
        result = run(['gh', 'pr', 'view', pr, '--json', 'url,headRefOid,body'],
                     capture_output=True, text=True, check=True, timeout=60)
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('Live architecture publication evidence is unavailable') from exc
    if (not isinstance(value, dict) or value.get('url', '').lower() != pr.lower()
            or not isinstance(value.get('body'), str)
            or not isinstance(value.get('headRefOid'), str)):
        raise ValueError('Live architecture publication identity is incomplete')
    return value

def gate(tasks, refs, *, options=None, upstream=None, analysis_only=False):
    with evaluation(tasks):
        return _gate(tasks, refs, options=options, upstream=upstream, analysis_only=analysis_only)


def _gate(tasks, refs, *, options=None, upstream=None, analysis_only=False):
    loaded = skill_config.effective(OWNER, tasks.store.subject['repository'], upstream, options=options)['options']
    if not loaded['architecture']['enabled']:
        return {'outcome': 'disabled', 'release_reviewers': True}
    if not isinstance(refs, dict) or 'assessment' not in refs:
        raise ValueError('Incoming review requires architecture applicability evidence')
    impact = accepted(tasks, refs['impact']) if refs.get('impact') else None
    assessment = accepted(tasks, refs['assessment'], historical=impact is not None)
    if assessment['spec']['skill']['identity'] != loaded['architecture']['review_skill']:
        raise ValueError('Architecture review skill changed; refresh affected applicability and comparison')
    if assessment['stage'] != 'assessment':
        raise ValueError('Architecture assessment stage required')
    if impact and impact['report']['impact'] != 'nonmaterial':
        return {'outcome': 'analysis_required', 'release_reviewers': False}
    if not assessment['report']['required']:
        if impact:
            linked(impact, 'assessment', assessment)
        current = impact or assessment
        return {'outcome': 'skipped', 'release_reviewers': True, 'reason': assessment['report']['reason'],
                'current_evidence': current['key'], 'applicability_reference': assessment['reference']}
    compared = accepted(tasks, refs['comparison'], historical=impact is not None)
    if compared['spec']['skill']['identity'] != loaded['architecture']['review_skill']:
        raise ValueError('Architecture review skill changed; refresh affected comparison')
    if compared['stage'] != 'comparison':
        raise ValueError('Architecture comparison required')
    if compared['policy']['scoring'] != scoring_policy(loaded['architecture']['scoring']):
        raise ValueError('Architecture scoring inputs changed; refresh affected comparison')
    if impact:
        linked(impact, 'comparison', compared)
        if impact['report']['impact'] != 'nonmaterial':
            return {'outcome': 'analysis_required', 'release_reviewers': False}
    linked(compared, 'assessment', assessment)
    selection = accepted(tasks, refs['selection'], historical=impact is not None)
    if impact:
        validate_selection_impact(impact, selection)
    linked(selection, 'comparison', compared)
    if selection['policy']['auto_pick'] != loaded['architecture']['auto_pick']:
        raise ValueError('Architecture selection policy changed; refresh selection only')
    choice = selection['report'].get('choice')
    if not choice:
        return {'outcome': 'choice_pending', 'release_reviewers': False}
    alignment = accepted(tasks, refs['alignment'])
    if selection['stage'] != 'selection' or alignment['stage'] != 'alignment':
        raise ValueError('Architecture selection/alignment stages required')
    linked(alignment, 'comparison', compared)
    linked(alignment, 'selection', selection)
    if impact:
        linked(alignment, 'impact', impact)
    pins = alignment['spec'].get('git', {})
    for current in (impact or assessment,):
        if current is not None and any(current['spec'].get('git', {}).get(k) != pins.get(k) for k in ('head', 'base', 'target')):
            raise ValueError('Architecture live stages must assess the same current Git pins')
    head = pins.get('head')
    if not head:
        raise ValueError('Alignment requires current pinned Git inputs')
    if analysis_only or not loaded['permissions']['edit_architecture_decisions']:
        return {'outcome': 'publication_blocked', 'release_reviewers': False}
    decision = journal(refs['decision_operation'], tasks, kind='edit-pr', head=None)
    fields = decision['request']['approved']['fields']
    if set(fields) != {'body'}:
        raise ValueError('Architecture publication may edit only the bounded body subsection')
    body = fields['body']
    before = decision['request']['expected']['body']
    rendered = render_decision(compared['report'], compared['policy']['scoring'], choice, selection=selection['report'])
    heading = skill_config.effective('dk-workflows:dk-pr-decisions', tasks.store.subject['repository'], upstream)['options']['section_heading']
    if body != replace_decision(before, rendered, heading=heading):
        raise ValueError('Architecture publication changed unrelated PR text or lacks the settled decision')
    with evaluation(tasks) as context:
        pr = tasks.store.subject['pr']
        if pr not in context['live']:
            context['live'][pr] = live_pr(pr)
        live = context['live'][pr]
    if live['headRefOid'] != head:
        raise ValueError('PR head changed after architecture alignment')
    current_section = decision_section(live['body'], heading)
    bounds = architecture_bounds(live['body'], current_section) if current_section else None
    if not bounds or live['body'][bounds[0]:bounds[1]].strip('\r\n') != rendered:
        return {'outcome': 'publication_required', 'release_reviewers': False}
    if live['body'] != body:
        if impact is None:
            return {'outcome': 'impact_required', 'release_reviewers': False}
        current_pr = read(impact['spec']['inputs']['architecture_current_pr'])
        if current_pr['body'] != live['body'] or current_pr.get('headRefOid') != head:
            return {'outcome': 'impact_required', 'release_reviewers': False}
    result = alignment['report']['alignment']
    if result == 'aligned':
        return {'outcome': 'aligned', 'release_reviewers': True, 'decision': rendered,
                'governing_evidence': [compared['reference'], selection['reference'], alignment['reference']]}
    if result == 'mismatch':
        verification = accepted(tasks, refs['verification'])
        if verification['stage'] != 'verification':
            raise ValueError('Independent architecture verification stage required')
        if any(verification['spec'].get('git', {}).get(k) != pins.get(k) for k in ('head', 'base', 'target')):
            raise ValueError('Architecture verification pins changed')
        linked(verification, 'alignment', alignment)
        if verification['native_handle'] == alignment['native_handle']:
            raise ValueError('Architecture mismatch verification must use an independent native agent')
        if verification['report']['disposition'] == 'confirmed':
            if not loaded['permissions']['post_review']:
                return {'outcome': 'publication_blocked', 'release_reviewers': False}
            review = journal(refs['review_operation'], tasks, kind='post-review', head=head)
            approved = review['request']['approved']
            if (approved['event'] != 'REQUEST_CHANGES' or approved.get('comments')
                    or approved.get('body') != verification['report']['required_changes']):
                raise ValueError('Architecture-only rejection requires the independently verified design changes')
            return {'outcome': 'architecture_rejected', 'release_reviewers': False, 'complete': True,
                    'ordinary_review_complete': False}
    return {'outcome': 'alignment_blocked', 'release_reviewers': False}


# CommonMark HTML block starts; this scanner only locates safe edit boundaries.
HTML_BLOCK_TAGS = (
    'address article aside base basefont blockquote body caption center col colgroup dd details '
    'dialog dir div dl dt fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 '
    'head header hr html iframe legend li link main menu menuitem nav noframes ol optgroup option '
    'p param search section summary table tbody td tfoot th thead title tr track ul'
).split()


# CommonMark complete tags: angle brackets are allowed in quoted attributes.
THEMATIC_BREAK = re.compile(r' {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})')
HTML_NAME = r'[A-Za-z][A-Za-z0-9-]*'
HTML_VALUE = r'''(?:[^ \t\r\n\f\v"'=<>`]+|"[^"]*"|'[^']*')'''
HTML_ATTRIBUTE = r'[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*' + HTML_VALUE + r')?'
COMPLETE_HTML_TAG = re.compile(
    r' {0,3}(?:<' + HTML_NAME + r'(?:[ \t]+' + HTML_ATTRIBUTE + r')*[ \t]*/?>|</' + HTML_NAME + r'[ \t]*>)[ \t]*')


def html_end(line, *, paragraph=False):
    """Return the raw-block terminator, or None for ordinary Markdown."""
    if re.match(r' {0,3}<(?:pre|script|style|textarea)(?:[ \t>]|$)', line, re.I):
        return re.compile(r'</(?:pre|script|style|textarea)>', re.I)
    for start, end in [('<!--', '-->'), ('<?', '?>'), ('<![CDATA[', ']]>')]:
        if re.match(r' {0,3}' + re.escape(start), line):
            return re.compile(re.escape(end))
    if re.match(r' {0,3}<![A-Za-z]', line):
        return re.compile('>')
    if re.match(r' {0,3}</?(?:' + '|'.join(HTML_BLOCK_TAGS) + r')(?:[ \t>]|/>|$)', line, re.I):
        return 'blank'
    # CommonMark type 7 cannot interrupt an open paragraph.
    if not paragraph and COMPLETE_HTML_TAG.fullmatch(line):
        return 'blank'
    return None


class ListContext:
    """Mask list-contained examples before interpreting document fences or HTML."""
    def __init__(self):
        self.indent = None
        self.after_blank = False
        self.paragraph = False
        self.raw = None
        self.fence = None
        self.block_indent = 0

    @staticmethod
    def opaque(line):
        return ''.join(c if c in '\r\n' else ' ' for c in line)

    @staticmethod
    def item(line, *, paragraph=False):
        match = re.match(r' {0,3}(?:[-+*]|[0-9]{1,9}[.)])([ \t]+|$)', line)
        if match is None or THEMATIC_BREAK.fullmatch(line):
            return None
        ordered = re.match(r' {0,3}([0-9]{1,9})[.)]', line)
        if paragraph and (not line[match.end():].strip() or ordered and int(ordered[1]) != 1):
            return None
        return match

    def content(self, line):
        """Only paragraph content can authorize a lazy list continuation."""
        if (self.raw is not None or self.fence is not None) and line.strip():
            indent = len(line) - len(line.lstrip(' '))
            if indent < self.block_indent:
                self.raw = self.fence = None
                self.paragraph = False
            else:
                line = line[self.block_indent:]
        if self.raw is not None:
            ended = not line.strip() if self.raw == 'blank' else self.raw.search(line)
            if ended:
                self.raw = None
            self.paragraph = False
            return
        if self.fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(self.fence[0]) + '{' + str(self.fence[1]) + r',}[ \t]*', line):
                self.fence = None
            self.paragraph = False
            return
        self.block_indent = 0
        # Remove nested list prefixes iteratively. Their block content cannot
        # authorize a lazy paragraph after an HTML/fence/thematic block ends.
        while (item := self.item(line, paragraph=self.paragraph)) is not None:
            spaces = len(item[1])
            self.block_indent += item.start(1) + (spaces if 1 <= spaces <= 4 else 1)
            line = line[item.end():]
            self.paragraph = False
            if spaces > 4:
                line = '    ' + line
                break
        opening = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
        if opening and (opening[1][0] != '`' or '`' not in opening[2]):
            self.fence = (opening[1][0], len(opening[1]))
            self.paragraph = False
            return
        raw = html_end(line, paragraph=self.paragraph)
        if raw is not None:
            self.raw = raw if raw == 'blank' or not raw.search(line) else None
            self.paragraph = False
            return
        rule = THEMATIC_BREAK.fullmatch(line)
        setext = self.paragraph and re.fullmatch(r' {0,3}(?:=+|-+)[ \t]*', line)
        indented_code = not self.paragraph and line.startswith('    ')
        self.paragraph = bool(line.strip()) and not (rule or setext or indented_code or re.match(
            r' {0,3}(?:#{1,6}(?:[ \t]|$)|>)', line))

    def mask(self, line, *, paragraph=False):
        expanded = line.expandtabs(4).rstrip('\r\n')
        indent = len(expanded) - len(expanded.lstrip(' '))
        item = self.item(expanded)
        block = re.match(r' {0,3}(?:#{1,6}(?:[ \t]|$)|>|`{3,}|~{3,})', expanded)
        rule = THEMATIC_BREAK.fullmatch(expanded)
        if self.indent is not None:
            if not expanded.strip():
                self.after_blank = True
                self.content('')
                return line
            if indent >= self.indent:
                self.content(expanded[self.indent:])
                self.after_blank = False
                return self.opaque(line)
            if item or block or rule or self.after_blank or not self.paragraph or html_end(expanded, paragraph=self.paragraph):
                self.indent = None
                self.raw = self.fence = None
            else:
                return self.opaque(line)
        if item and not rule:
            if self.item(expanded, paragraph=paragraph) is None:
                return line
            spaces = len(item[1])
            self.indent = item.start(1) + (spaces if 1 <= spaces <= 4 else 1)
            content = expanded[item.end():]
            self.raw = self.fence = None
            self.paragraph = False
            self.content(content if spaces <= 4 else '    ' + content)
            self.after_blank = False
            return self.opaque(line)
        return line


def heading_view(body):
    """Mask Markdown examples without moving document character offsets."""
    masked, fence, raw, paragraph = [], None, None, False
    lists = ListContext()
    for line in re.findall(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$', body):
        stripped = line.rstrip('\r\n')
        if raw is not None:
            ended = not stripped.strip() if raw == 'blank' else raw.search(stripped)
            if ended:
                raw = None
        elif fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}[ \t]*', stripped):
                fence = None
        else:
            visible = lists.mask(line, paragraph=paragraph)
            if visible != line:
                masked.append(visible)
                paragraph = False
                continue
            opening = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', stripped)
            if opening and (opening[1][0] != '`' or '`' not in opening[2]):
                fence = (opening[1][0], len(opening[1]))
            else:
                raw = html_end(stripped, paragraph=paragraph)
                if raw is None:
                    indented_code = not paragraph and stripped.startswith('    ')
                    setext = paragraph and re.fullmatch(r' {0,3}(?:=+|-+)[ \t]*', stripped)
                    paragraph = bool(stripped.strip()) and not (indented_code or setext or THEMATIC_BREAK.fullmatch(stripped) or re.match(
                        r' {0,3}(?:#{1,6}(?:[ \t]|$)|>)', stripped))
                    masked.append(line)
                    continue
                if raw != 'blank' and raw.search(stripped):
                    raw = None
        paragraph = False
        masked.append(''.join(c if c in '\r\n' else ' ' for c in line))
    if fence is not None or raw not in (None, 'blank'):
        raise ValueError('Unclosed Markdown example blocks a bounded Decisions edit')
    # Normalize lone CR only in the search view, preserving every offset and
    # leaving the original body bytes available for the bounded replacement.
    return re.sub(r'\r(?!\n)', '\n', ''.join(masked))


def next_section(view, start, level):
    """Recognize ATX and Setext boundaries without consuming sibling content."""
    atx = re.search(r'(?m)^ {0,3}#{1,' + str(level) + r'}(?:[ \t]|\r?$)', view[start:])
    end = start + atx.start() if atx else len(view)
    for match in re.finditer(r'(?m)^ {0,3}[^ \t\r\n][^\r\n]*\r?\n {0,3}(=+|-+)[ \t]*(?=\r?$)', view[start:end]):
        if (1 if match[1][0] == '=' else 2) <= level:
            first = start + match.start()
            # A Setext heading consumes only its own preceding paragraph.
            while first > start:
                previous_end = first - 1
                previous_start = max(start, view.rfind('\n', start, previous_end) + 1)
                previous = view[previous_start:first].rstrip('\r\n')
                if (not previous.strip()
                        or re.match(r' {0,3}(?:#{1,6}(?:[ \t]|$)|>|[-+*] |[0-9]+[.)] )', previous)
                        or re.fullmatch(r' {0,3}(?:=+|-+)[ \t]*', previous)):
                    break
                first = previous_start
            return first
    return end


def decision_section(body, heading):
    """Locate the configured real heading, excluding fenced/HTML examples."""
    title = heading.strip().lstrip('#').strip()
    view = heading_view(body)
    matches = list(re.finditer(r'(?m)^ {0,3}(#{1,6})[ \t]+' + re.escape(title) + r'(?:[ \t]+#+)?[ \t]*(?=\r?$)', view))
    if len(matches) > 1:
        raise ValueError('Ambiguous Decisions heading')
    if not matches:
        return None
    start = matches[0]
    level = len(start[1])
    end = next_section(view, start.end(), level)
    return start, end, level


def architecture_bounds(body, section):
    start, end, level = section
    view = heading_view(body)[start.end():end]
    matches = list(re.finditer(r'(?m)^ {0,3}#{' + str(level + 1) + r'}[ \t]+Architecture[ \t]+decisions(?:[ \t]+#+)?[ \t]*(?=\r?$)', view))
    if len(matches) > 1:
        raise ValueError('Ambiguous Architecture decisions subsection')
    if not matches:
        return None
    existing = matches[0]
    stop = next_section(view, existing.end(), level + 1)
    return start.end() + existing.end(), start.end() + stop


def replace_decision(body, rendered, *, heading='## Decisions'):
    """Preserve all bytes outside the distinct Architecture decisions subsection."""
    section = decision_section(body, heading)
    if section is None:
        title = heading.strip()
        if not title.startswith('#'):
            title = '## ' + title
        level = len(title) - len(title.lstrip('#'))
        if not 1 <= level < 6 or not re.fullmatch(r'#{1,5} [^\r\n]+', title):
            raise ValueError('Decisions heading needs an available subsection depth')
        marker = '#' * (level + 1) + ' Architecture decisions'
        return body + ('\n\n' if body and not body.endswith('\n\n') else '') + title + '\n\n' + marker + '\n\n' + rendered + '\n\n'
    start, end, level = section
    if level >= 6:
        raise ValueError('Decisions heading has no available subsection depth')
    bounds = architecture_bounds(body, section)
    if bounds is None:
        marker = '#' * (level + 1) + ' Architecture decisions'
        return body[:end] + ('\n' if body[:end].endswith('\n') else '\n\n') + marker + '\n\n' + rendered + '\n\n' + body[end:]
    first, stop = bounds
    return body[:first] + '\n\n' + rendered + '\n\n' + body[stop:]


def render_decision(report, scoring, choice, *, selection=None):
    compared = comparison(report, scoring)
    selected = next(s for s in compared['solutions'] if s['id'] == choice and s['feasible'])
    def inline(value):
        return ' '.join(value.splitlines())
    rationale = inline(selection['reason'] if selection else selected['recommendation_reason'])
    lines = ['Chosen approach: ' + inline(selected['approach']), '', 'Rationale: ' + rationale, '',
             'Considered alternatives (scores out of 100):', '']
    for option in compared['solutions']:
        reason = (rationale if option['id'] == choice else selection['rejection_reasons'][option['id']]
                  if selection else option['recommendation_reason'])
        lines.append('Approach: ' + inline(option['approach']) + ' — ' + str(option['total']) + '/100. '
                     + ('Chosen. ' if option['id'] == choice else 'Rejected. ') + inline(reason))
        lines.append('')
    return '\n'.join(lines).rstrip('\n')


def enforce(spec, repository, pr, *, options=None, upstream=None):
    """Only incoming-review occurrences use this gate; standalone review is unchanged."""
    from tasks import Tasks
    from state import identity
    context = _EVALUATION.get()
    tasks = (context['tasks'] if context is not None and context['tasks'].store.subject == identity(repository, pr)
             else Tasks(repository, pr))
    with evaluation(tasks):
        _enforce(spec, tasks, options=options, upstream=upstream)


def _enforce(spec, tasks, *, options=None, upstream=None):
    execution = spec.get('options', {}).get('execution', spec.get('options', {}))
    result = gate(tasks, execution.get('architecture_evidence'), options=options,
                  upstream=upstream, analysis_only=execution.get('analysis_only', False))
    if not result['release_reviewers']:
        raise ValueError('Architecture gate blocks ordinary tasks: ' + result['outcome'])
    if result['outcome'] == 'skipped':
        current = accepted(tasks, result['current_evidence'])
        if any(spec.get('git', {}).get(k) != current['spec'].get('git', {}).get(k) for k in ('head', 'base', 'target')):
            raise ValueError('Ordinary review pins differ from architecture applicability')
        spec['inputs']['architecture_applicability'] = result['applicability_reference']
        if current['stage'] == 'impact':
            spec['inputs']['architecture_impact'] = current['reference']
    if result.get('decision'):
        alignment = accepted(tasks, execution['architecture_evidence']['alignment'])
        for pin in ('head', 'base', 'target'):
            if spec.get('git', {}).get(pin) != alignment['spec'].get('git', {}).get(pin):
                raise ValueError('Ordinary review pins differ from architecture alignment')
        # Dependency paths are regenerated, never a second saved decision summary.
        for index, path in enumerate(result['governing_evidence']):
            spec['inputs']['architecture_governing_' + str(index)] = path


def governing_body(body, operation, tasks, *, upstream=None):
    """Normalize the exact reconciled lineage, stopping at external text changes."""
    from shared import publication
    from shared.workflow_storage import operations_root
    heading = skill_config.effective('dk-workflows:dk-pr-decisions', tasks.store.subject['repository'], upstream)['options']['section_heading']

    def transition(value):
        request = value['request']
        if (value['status'] != 'verified' or not value.get('receipt')
                or request['target'].lower() != tasks.store.subject['pr'].lower()):
            raise ValueError('Own Decisions normalization requires a reconciled operation')
        fields = request['approved']['fields']
        if set(fields) != {'body'}:
            raise ValueError('Own Decisions normalization cannot cover unrelated fields')
        approved = fields['body']
        section = decision_section(approved, heading)
        bounds = architecture_bounds(approved, section) if section else None
        if not bounds or replace_decision(request['expected']['body'], approved[bounds[0]:bounds[1]].strip('\r\n'), heading=heading) != approved:
            raise ValueError('Own Decisions normalization requires a bounded architecture edit')
        return approved, request['expected']['body']

    approved, before = transition(publication.load(operation, 'edit-pr'))
    if body != approved:
        return body
    # Read existing PR-scoped journals, including the low-level publisher layout.
    # No lineage database, saved summary or copied body is needed.
    root = operations_root()
    paths = set((root/publication.digest(tasks.store.subject['pr'].lower())).glob('*/edit-pr-*.json'))
    paths.update(root.glob('workflow-*/edit-pr-*.json'))
    paths.update(Path(operation).parent.glob('edit-pr-*.json'))
    predecessors = {}
    for path in paths:
        value = publication.load(path, 'edit-pr')
        request = value['request']
        if (value['status'] != 'verified' or not value.get('receipt')
                or request['target'].lower() != tasks.store.subject['pr'].lower()
                or set(request['approved'].get('fields', {})) != {'body'}):
            continue
        try:
            after, prior = transition(value)
        except ValueError:
            continue  # An ordinary body edit is an external governing boundary.
        if after != prior:
            predecessors.setdefault(after, set()).add(prior)
    visited = set()
    while before in predecessors:
        if before in visited or len(predecessors[before]) != 1:
            raise ValueError('Ambiguous or circular own Decisions publication lineage')
        visited.add(before)
        before = next(iter(predecessors[before]))
    return before


def scoring_handoff(spec):
    """Transient helper arithmetic for confidence assessment; never saved again."""
    assigned = spec.get('options', {}).get('architecture')
    if not assigned or assigned['stage'] != 'selection':
        return None
    compared = comparison(read(spec['inputs']['architecture_comparison'])['normalized'],
                          assigned['comparison_scoring'])
    return {'profile': compared['profile'], 'weights': compared['weights'],
            'scores': [{'id': s['id'], 'total': s['total'], 'feasible': s['feasible'],
                        'material_doubts': s['material_doubts']} for s in compared['solutions']]}
