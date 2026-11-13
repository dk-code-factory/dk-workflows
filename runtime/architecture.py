"""Architecture gate contracts and arithmetic over existing managed evidence.

No execution engine or architecture store: the native session owns analysis.
"""
import base64
import json
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
PROFILE_PROMPT = ('Assess new features, existing behavior, tests, CI and maintenance. Choose a built-in '
                  'scoring profile and explain why its trade-offs fit these circumstances.')
PICK_PROMPT = ('Assess confidence and sensitivity to LOC and complexity estimates. Accept a clear winner '
               'only with supported feasibility, adequate score lead and no unresolved material doubts. '
               'For a sole feasible option substantiate rejection of every alternative.')
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
ALLOWS = {'assessment': (), 'comparison': ('assessment',), 'impact': ('comparison',),
          'selection': ('comparison', 'impact'), 'alignment': ('comparison', 'selection', 'impact'),
          'verification': ('alignment',)}
REQUIRES = {'assessment': (), 'comparison': ('assessment',), 'impact': ('comparison',),
            'selection': ('comparison',), 'alignment': ('comparison', 'selection'),
            'verification': ('alignment',)}
GUIDANCE = {
    'assessment': 'Inspect current requirements, complete discussion, Decisions and pinned diff. Require architecture review for material boundaries, interfaces, dependencies, state, concurrency, recovery or operational choices; otherwise explain a simple-change skip with evidence.',
    'comparison': 'Compare reasonable materially distinct approaches using the assigned prompt and mandatory checklist. Return all required solution fields; select a scoring profile when auto. Do not choose, publish or run ordinary reviewers.',
    'impact': 'Compare retained alternatives analysis with current requirements, solution choices, constraints and relevant evidence. Return material, nonmaterial or uncertain impact with evidence. Only nonmaterial permits reuse; always refresh current alignment. Own Decisions edits require exact reconciled before/after evidence.',
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


def validate_report(spec, report):
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


def accepted(tasks, key, *, historical=False):
    """Validate original evidence and checklist; historical comparisons need impact."""
    from state import capture
    # Resolve current definitions outside the manifest lock (dependencies also
    # read this manifest). Historical analysis uses its original captured contract.
    spec = None if historical else tasks.saved_spec(key)
    captured = None if historical else capture(spec)
    with tasks.store.locked() as manifest:
        entry = manifest['entries'][key]
        saved, problem = tasks.store._load_saved(entry)
        if problem or saved['status'] != 'complete':
            raise ValueError('Architecture evidence is incomplete or damaged')
        if historical:
            spec = deepcopy(entry['components'])
            captured = {'key': key, 'fingerprint': saved['fingerprint'],
                        'components': spec, 'cacheable': saved['cacheable']}
        described = tasks.store.describe(captured, entry)
        if not described['coverage_complete'] or not described.get('checklist', {}).get('complete'):
            raise ValueError('Architecture evidence lacks fresh native/checklist coverage')
        report = deepcopy(saved['normalized'])
        reference = saved['path']
        native = json.loads(base64.b64decode(saved['native_execution']['evidence']))
    validate_report(spec, report)
    assigned = spec['options']['architecture']
    return {'key': key, 'spec': spec, 'report': report, 'reference': reference, 'native_handle': native['invocation']['handle'], **assigned}


def stage_spec(tasks, descriptor, definition):
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
    retained = {name: accepted(tasks, key, historical=(name == 'comparison' and (stage == 'impact' or 'impact' in dependencies)))
                for name, key in dependencies.items()}
    for name, value in retained.items():
        if value['stage'] != name:
            raise ValueError('Architecture dependency stage mismatch')
    if stage == 'comparison' and retained['assessment']['report']['required'] is not True:
        raise ValueError('Skipped applicability cannot start comparison')
    if 'impact' in retained and retained['impact']['report']['impact'] != 'nonmaterial':
        raise ValueError('Material or uncertain impact requires refreshed architecture analysis')
    if stage == 'alignment' and not retained['selection']['report'].get('choice'):
        raise ValueError('Architecture choice remains pending')
    if stage == 'verification' and retained['alignment']['report']['alignment'] != 'mismatch':
        raise ValueError('Only material mismatches require independent verification')
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
    execution.setdefault('inputs', {}).update({'architecture_' + name: value['reference']
                                               for name, value in retained.items()})
    execution.setdefault('options', {})['process_limits'] = loaded['options']['limits']
    execution['options']['architecture'] = {
        'stage': stage, 'policy': policy, 'dependencies': dependencies,
        **({'comparison_scoring': retained['comparison']['policy']['scoring']} if stage == 'selection' else {}),
    }
    return make_spec(**tasks.store.subject, **execution)


def linked(value, name, other):
    if value['dependencies'].get(name) != other['key']:
        raise ValueError('Architecture evidence refers to a different ' + name)


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
    loaded = skill_config.effective(OWNER, tasks.store.subject['repository'], upstream, options=options)['options']
    if not loaded['architecture']['enabled']:
        return {'outcome': 'disabled', 'release_reviewers': True}
    if not isinstance(refs, dict) or 'assessment' not in refs:
        raise ValueError('Incoming review requires architecture applicability evidence')
    assessment = accepted(tasks, refs['assessment'])
    if assessment['spec']['skill']['identity'] != loaded['architecture']['review_skill']:
        raise ValueError('Architecture review skill changed; refresh affected applicability and comparison')
    if assessment['stage'] != 'assessment':
        raise ValueError('Architecture assessment stage required')
    if not assessment['report']['required']:
        return {'outcome': 'skipped', 'release_reviewers': True, 'reason': assessment['report']['reason']}
    impact = accepted(tasks, refs['impact']) if refs.get('impact') else None
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
    else:
        linked(compared, 'assessment', assessment)
    selection = accepted(tasks, refs['selection'])
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
    for current in (assessment, impact):
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
    live = live_pr(tasks.store.subject['pr'])
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


def html_end(line):
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
    # Unknown complete tags are conservatively kept opaque until a blank line.
    # This also avoids guessing at attribute grammar in user-supplied HTML.
    if re.fullmatch(r' {0,3}</?[A-Za-z][A-Za-z0-9-]*(?:[ \t]+[^<>]*)?/?>[ \t]*', line):
        return 'blank'
    return None


def mask_list_containers(view, original):
    """Keep list examples opaque while retaining document-level heading offsets."""
    result, content_indent, after_blank, paragraph = [], None, False, False
    for line, source in zip(view.splitlines(keepends=True), original.splitlines(keepends=True)):
        expanded = line.expandtabs(4).rstrip('\r\n')
        indent = len(expanded) - len(expanded.lstrip(' '))
        item = re.match(r' {0,3}(?:[-+*]|[0-9]{1,9}[.)])([ \t]+)', expanded)
        heading = re.match(r' {0,3}#{1,6}(?:[ \t]|$)', expanded)
        rule = re.fullmatch(r' {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})', expanded)
        if content_indent is not None:
            original_indent = len(source.expandtabs(4)) - len(source.expandtabs(4).lstrip(' '))
            if not expanded.strip():
                if source.strip() and original_indent < content_indent:
                    content_indent = None  # A masked document-level fence/HTML block ends the list.
                after_blank = True
            elif indent >= content_indent:
                paragraph = not bool(re.match(r'(?:#{1,6}(?:[ \t]|$)|>|[-+*] |[0-9]+[.)] )', expanded[content_indent:]))
                after_blank = False
                result.append(''.join(c if c in '\r\n' else ' ' for c in line))
                continue
            elif item or heading or rule or after_blank or not paragraph:
                content_indent = None
            else:
                result.append(''.join(c if c in '\r\n' else ' ' for c in line))
                continue
        if content_indent is None and item and not rule:
            spaces = len(item[1])
            content_indent = item.start(1) + (spaces if spaces <= 4 else 1)
            paragraph, after_blank = True, False
        result.append(line)
    return ''.join(result)


def heading_view(body):
    """Mask Markdown examples without moving document character offsets."""
    masked, fence, raw = [], None, None
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip('\r\n')
        if raw is not None:
            ended = not stripped.strip() if raw == 'blank' else raw.search(stripped)
            if ended:
                raw = None
        elif fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}[ \t]*', stripped):
                fence = None
        else:
            opening = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', stripped)
            if opening and (opening[1][0] != '`' or '`' not in opening[2]):
                fence = (opening[1][0], len(opening[1]))
            else:
                raw = html_end(stripped)
                if raw is None:
                    masked.append(line)
                    continue
                if raw != 'blank' and raw.search(stripped):
                    raw = None
        masked.append(''.join(c if c in '\r\n' else ' ' for c in line))
    if fence is not None or raw not in (None, 'blank'):
        raise ValueError('Unclosed Markdown example blocks a bounded Decisions edit')
    return mask_list_containers(''.join(masked), body)


def next_section(view, start, level):
    """Recognize ATX and Setext boundaries without consuming sibling content."""
    candidates = [m.start() for m in re.finditer(
        r'(?m)^ {0,3}#{1,' + str(level) + r'}(?:[ \t]|\r?$)', view[start:])]
    for match in re.finditer(r'(?m)^ {0,3}[^ \t\r\n][^\r\n]*\r?\n {0,3}(=+|-+)[ \t]*(?=\r?$)', view[start:]):
        if (1 if match[1][0] == '=' else 2) <= level:
            first = start + match.start()
            # A Setext heading consumes its entire preceding paragraph.
            while first > start:
                previous_end = first - 1
                previous_start = max(start, view.rfind('\n', start, previous_end) + 1)
                previous = view[previous_start:first].rstrip('\r\n')
                if (not previous.strip() or re.match(r' {0,3}(?:#{1,6}(?:[ \t]|$)|>|[-+*] |[0-9]+[.)] )', previous)):
                    break
                first = previous_start
            candidates.append(first - start)
    return start + min(candidates) if candidates else len(view)


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
    execution = spec.get('options', {}).get('execution', spec.get('options', {}))
    result = gate(Tasks(repository, pr), execution.get('architecture_evidence'), options=options,
                  upstream=upstream, analysis_only=execution.get('analysis_only', False))
    if not result['release_reviewers']:
        raise ValueError('Architecture gate blocks ordinary tasks: ' + result['outcome'])
    if result['outcome'] == 'skipped':
        assessment = accepted(Tasks(repository, pr), execution['architecture_evidence']['assessment'])
        if any(spec.get('git', {}).get(k) != assessment['spec'].get('git', {}).get(k) for k in ('head', 'base', 'target')):
            raise ValueError('Ordinary review pins differ from architecture applicability')
        spec['inputs']['architecture_applicability'] = assessment['reference']
    if result.get('decision'):
        alignment = accepted(Tasks(repository, pr), execution['architecture_evidence']['alignment'])
        for pin in ('head', 'base', 'target'):
            if spec.get('git', {}).get(pin) != alignment['spec'].get('git', {}).get(pin):
                raise ValueError('Ordinary review pins differ from architecture alignment')
        # Dependency paths are regenerated, never a second saved decision summary.
        for index, path in enumerate(result['governing_evidence']):
            spec['inputs']['architecture_governing_' + str(index)] = path


def governing_body(body, operation, tasks):
    """Remove only our exact reconciled edit from governing discussion context."""
    from shared import publication
    value = publication.load(operation, 'edit-pr')
    request = value['request']
    if (value['status'] != 'verified' or not value.get('receipt')
            or request['target'].lower() != tasks.store.subject['pr'].lower()):
        raise ValueError('Own Decisions normalization requires a reconciled operation')
    fields = request['approved']['fields']
    if set(fields) != {'body'}:
        raise ValueError('Own Decisions normalization cannot cover unrelated fields')
    approved = fields['body']
    heading = skill_config.effective('dk-workflows:dk-pr-decisions', tasks.store.subject['repository'])['options']['section_heading']
    section = decision_section(approved, heading)
    bounds = architecture_bounds(approved, section) if section else None
    if not bounds or replace_decision(request['expected']['body'], approved[bounds[0]:bounds[1]].strip('\n'), heading=heading) != approved:
        raise ValueError('Own Decisions normalization requires a bounded architecture edit')
    return request['expected']['body'] if body == approved else body


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
