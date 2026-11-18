"""Discover and reuse the initial fix plan in the existing PR task store."""
from copy import deepcopy
from pathlib import Path
import re

import alternatives
import skill_config
from state import capture, digest, git, read, task_key

OWNER = alternatives.FIX_OWNER
STAGES = ('assessment', 'comparison', 'selection', 'alignment')


def reassessment(tasks, request, *, request_id, authorization, source):
    """Construct a single-use, input-bound authorization in the existing definition."""
    if any(not alternatives.text(v) for v in (request_id, authorization, source)):
        raise ValueError('Reassessment requires the original request id, exact authorization and source')
    if (not isinstance(request, dict) or not isinstance(request.get('tasks'), list)
            or len(request['tasks']) != 1):
        raise ValueError('Reassess one retained initial fix stage at a time')
    if request.get('ci_repair') or request.get('ci_rerun'):
        raise ValueError('Reassessment requires review-cycle allowance, not a CI repair/rerun reservation')
    result = deepcopy(request)
    descriptor = result['tasks'][0]
    if (not isinstance(descriptor, dict) or descriptor.get('kind') != 'alternatives'
            or descriptor.get('skill') != OWNER or descriptor.get('stage') not in STAGES
            or not isinstance(descriptor.get('definition'), dict)):
        raise ValueError('Reassessment requires a rebuilt initial fix alternatives definition')
    stage = descriptor['stage']
    original = entry(tasks, stage)
    if not original or not original.get('result'):
        raise ValueError('Reassessment requires retained original review evidence')
    saved, problem = tasks.store._load_saved(original)
    prior = original.get('managed', {}).get('reassessment')
    if not problem and saved['status'] != 'complete' and prior:
        attempt = next((item for item in reversed(original['attempts'])
                        if item.get('status') == 'complete' and item.get('result')), None)
        if attempt is None:
            raise ValueError('Original alternatives evidence is missing or damaged; restore it first')
        filename = attempt['result']
        saved, problem = tasks.store._load_saved({
            'key': original['key'], 'result': {'file': filename, 'hash': Path(filename).stem,
                                             'fingerprint': attempt['fingerprint']}})
    if problem or saved['status'] != 'complete':
        raise ValueError('Original alternatives evidence is missing, damaged or incomplete; restore it first')
    retained_run = next((attempt['run'] for attempt in reversed(original['attempts']) if attempt.get('run')), None)
    if retained_run and request.get('run') != retained_run:
        raise ValueError('Reassessment must retain the original run and its review/CI budget history')
    if retained_run and not request.get('cycle'):
        raise ValueError('Reassessment in a retained run requires an explicit budgeted review cycle')
    source = str(Path(source).absolute())
    original_request = read(source)
    if (not isinstance(original_request, dict)
            or any(not alternatives.text(original_request.get(k)) for k in ('id', 'source', 'instruction'))
            or original_request['id'] != request_id or original_request['instruction'] != authorization):
        raise ValueError('Reassessment must match the exact original user request artifact')
    if not prior or prior['request']['id'] != request_id:
        from alternatives_history import request as direct_request
        direct_request(source)
    request_reference = {'id': request_id, 'source': source}
    descriptor['definition'].setdefault('inputs', {})['alternatives_reassessment'] = source
    # Replays reuse completed work as well as uncertain claims; changing the
    # original request requires a new identity rather than rewriting its source.
    if prior and prior['request']['id'] == request_id:
        if prior['request'] != request_reference:
            raise ValueError('Reassessment request source changed; use a new explicit request identity')
        descriptor['reassessment'] = deepcopy(prior)
    else:
        if any(attempt.get('reassessment_request') == request_id for attempt in original['attempts']):
            raise ValueError('Reassessment authorization was superseded; obtain a new explicit request identity')
        descriptor.pop('reassessment', None)
        spec = tasks.spec(descriptor, authorize_reassessment=True)
        with alternatives.evaluation(tasks) as context:
            fingerprint = capture(spec, _context=context['capture'])['fingerprint']
        record = {'request': request_reference, 'key': key(tasks, stage),
                  'original_result': original['result']['hash'], 'fingerprint': fingerprint}
        descriptor['reassessment'] = {**record, 'binding': digest(record)}
    return result


def _binding(record, expected_key):
    if (not isinstance(record, dict)
            or set(record) != {'request', 'key', 'original_result', 'fingerprint', 'binding'}
            or not isinstance(record['request'], dict)
            or set(record['request']) != {'id', 'source'}
            or any(not alternatives.text(v) for v in record['request'].values())
            or record['key'] != expected_key
            or record['binding'] != digest({k: v for k, v in record.items() if k != 'binding'})):
        raise ValueError('Reassessment authorization binding changed; obtain a new explicit reassessment')


def guard_reassessment(captured, original, record):
    """Check input freshness and consumed authorization at the locked start boundary."""
    _binding(record, captured['key'])
    unsigned = deepcopy(captured['components'])
    unsigned['options'].pop('alternatives_reassessment', None)
    if digest(unsigned) != record['fingerprint']:
        raise ValueError('Authorized reassessment inputs changed; obtain a new explicit reassessment')
    active = (original or {}).get('components', {}).get('options', {}).get('alternatives_reassessment')
    consumed = any(attempt.get('reassessment_request') == record['request']['id']
                   for attempt in (original or {}).get('attempts', []))
    if (not original or not original.get('result') or
            (active != record and
             (consumed or original['result']['hash'] != record['original_result']))):
        raise ValueError('Reassessment authorization was superseded; obtain a new explicit reassessment')


def bind_reassessment(tasks, descriptor, spec):
    if 'alternatives_reassessment' in spec['options']:
        raise ValueError('Reassessment options require a validated explicit reassessment binding')
    record = descriptor.get('reassessment')
    if record is None:
        return
    _binding(record, key(tasks, descriptor['stage']))
    request = read(record['request']['source'])
    if (not isinstance(request, dict) or request.get('id') != record['request']['id']
            or any(not alternatives.text(request.get(k)) for k in ('id', 'source', 'instruction'))):
        raise ValueError('Original reassessment request changed or is incomplete')
    spec['inputs']['alternatives_reassessment'] = record['request']['source']
    with alternatives.evaluation(tasks) as context:
        current = capture(spec, _context=context['capture'])
    guard_reassessment(current, entry(tasks, descriptor['stage']), record)
    spec['options']['alternatives_reassessment'] = deepcopy(record)


def key(tasks, stage):
    return task_key(tasks.store.subject, 'alternatives-' + stage + '-fix-review', OWNER)


def entry(tasks, stage):
    with tasks.store.locked() as manifest:
        return deepcopy(manifest['entries'].get(key(tasks, stage)))


def pinned_definition(tasks, descriptor, definition):
    stage = descriptor['stage']
    if stage not in (*STAGES, 'verification') or set(descriptor.get('dependencies', {})) - set(STAGES):
        raise ValueError('The initial fix alternatives plan uses assessment, comparison, selection, alignment and mismatch verification only')
    if definition.get('governing_inputs_complete') is not True or definition.get('cacheable') is False:
        raise ValueError('Once-per-PR alternatives review requires complete governing inputs and cacheable original evidence')
    original = entry(tasks, stage) or entry(tasks, 'alignment' if stage == 'verification' else 'assessment')
    result = deepcopy(definition)
    if original:
        result['git'] = deepcopy(original['managed']['definition']['git'])
        source = original['managed']['definition'].get('inputs', {}).get('alternatives_current_pr')
        if source:
            result.setdefault('inputs', {})['alternatives_current_pr'] = source
    if 'git' not in result:
        raise ValueError('Initial fix alternatives review requires pinned Git inputs')
    pins = result['git']
    for name in ('target', 'base', 'head'):
        value = pins[name]
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
            pins[name] = git(pins['checkout'], 'rev-parse', '--verify', value + '^{commit}').decode().strip()
    descriptor['definition']['git'] = deepcopy(pins)
    if 'alternatives_current_pr' in result.get('inputs', {}):
        descriptor['definition'].setdefault('inputs', {})['alternatives_current_pr'] = result['inputs']['alternatives_current_pr']
    return result


def guard_completed(tasks, stage, spec):
    original = entry(tasks, stage)
    if not original or not original.get('result'):
        return
    if (original.get('managed', {}).get('reassessment')
            and 'alternatives_reassessment' not in spec['options']):
        raise ValueError('Retained reassessment requires its original authorization binding or a new explicit reassessment')
    saved, problem = tasks.store._load_saved(original)
    if problem:
        raise ValueError('Initial alternatives evidence is missing or damaged; retain its recovery references')
    if saved['status'] != 'complete' or original.get('checklist', {}).get('outcome') != 'complete':
        return
    # An unfinished choice or uncertain alignment still needs its original task.
    report = saved['normalized']
    if stage == 'selection' and not report.get('choice'):
        return
    if stage == 'alignment' and report.get('alignment') == 'uncertain':
        return
    with alternatives.evaluation(tasks) as context:
        current = capture(spec, _context=context['capture'])
    if current['fingerprint'] != saved['fingerprint']:
        authorization = spec['options'].get('alternatives_reassessment')
        if authorization and authorization['original_result'] == original['result']['hash']:
            unsigned = deepcopy(spec)
            unsigned['options'].pop('alternatives_reassessment')
            with alternatives.evaluation(tasks) as context:
                replacement = capture(unsigned, _context=context['capture'])
            if replacement['fingerprint'] == authorization['fingerprint']:
                return
        raise ValueError('Initial alternatives review governing inputs changed; explicit reassessment is required, not another automatic review')


def gate(tasks, *, options=None, upstream=None):
    with alternatives.evaluation(tasks):
        return _gate(tasks, options=options, upstream=upstream)


def _gate(tasks, *, options=None, upstream=None):
    policy = alternatives.legacy_options(skill_config.effective(OWNER, tasks.store.subject['repository'], upstream, options=options)['options'])['alternatives']
    if not policy['enabled']:
        return {'outcome': 'disabled', 'release_reviewers': True}
    refs, retained = {}, {}
    for stage in STAGES:
        original = entry(tasks, stage)
        dependencies = {name: refs[name] for name in alternatives.REQUIRES[stage]}
        next_task = ({'key': key(tasks, stage)} if original else
                     {'kind': 'alternatives', 'skill': OWNER, 'stage': stage,
                      'dependencies': dependencies, 'options': options or {}, 'upstream': upstream})
        if not original or not original.get('result'):
            return {'outcome': 'review_required', 'release_reviewers': False,
                    'stage': stage, 'next_task': next_task, 'references': refs}
        saved, problem = tasks.store._load_saved(original)
        if problem:
            raise ValueError('Initial alternatives evidence is missing or damaged; retain its recovery references')
        if saved['status'] != 'complete' or original.get('checklist', {}).get('outcome') != 'complete':
            return {'outcome': 'review_required', 'release_reviewers': False,
                    'stage': stage, 'next_task': next_task, 'references': refs}
        try:
            value = alternatives.accepted(tasks, key(tasks, stage))
        except (OSError, RuntimeError) as exc:
            return {'outcome': 'reassessment_required', 'release_reviewers': False,
                    'stage': stage, 'references': refs, 'reason': str(exc),
                    'next_task': {'kind': 'alternatives', 'skill': OWNER, 'stage': stage,
                                  'dependencies': dependencies, 'options': options or {}, 'upstream': upstream},
                    'recovery': 'Restore the original sources, or use dk.py reassess with a rebuilt definition and explicit sourced user authorization'}
        if value.get('owner') != OWNER or value['stage'] != stage:
            raise ValueError('Initial alternatives plan requires the original fix workflow evidence')
        if stage in {'assessment', 'comparison'} and value['spec']['skill']['identity'] != policy['review_skill']:
            raise ValueError('Initial alternatives reviewer changed; explicit reassessment is required')
        if stage == 'comparison' and value['policy']['scoring'] != alternatives.scoring_policy(policy['scoring']):
            raise ValueError('Initial alternatives scoring changed; explicit reassessment is required')
        if stage == 'selection' and value['policy']['auto_pick'] != policy['auto_pick']:
            raise ValueError('Initial alternatives selection policy changed; explicit reassessment is required')
        for dependency in alternatives.REQUIRES[stage]:
            alternatives.linked(value, dependency, retained[dependency])
        refs[stage], retained[stage] = value['key'], value
        if stage == 'assessment' and not value['report']['required']:
            return {'outcome': 'skipped', 'release_reviewers': True, 'references': refs,
                    'governing_evidence': [value['reference']]}
        if stage == 'selection' and not value['report'].get('choice'):
            return {'outcome': 'choice_pending', 'release_reviewers': False, 'references': refs,
                    'next_task': {'key': value['key']}}
    alignment = retained['alignment']['report']
    if alignment['alignment'] == 'uncertain':
        return {'outcome': 'alignment_blocked', 'release_reviewers': False, 'references': refs,
                'next_task': {'key': refs['alignment']}}
    return {'outcome': 'planned', 'release_reviewers': True, 'references': refs,
            'selected_approach': retained['selection']['report']['choice'],
            'initial_alignment': alignment['alignment'],
            'required_changes': alignment.get('required_changes') if alignment['alignment'] == 'mismatch' else None,
            'governing_evidence': [value['reference'] for value in retained.values()]}


def enforce(spec, repository, pr, *, options=None, upstream=None):
    from tasks import Tasks
    from state import identity
    context = alternatives._EVALUATION.get()
    tasks = (context['tasks'] if context is not None and context['tasks'].store.subject == identity(repository, pr)
             else Tasks(repository, pr))
    import assessment_alternatives as shared
    return shared.enforce(spec, tasks, OWNER, options=options, upstream=upstream)
