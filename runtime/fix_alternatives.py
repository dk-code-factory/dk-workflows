"""Discover and reuse the initial fix plan in the existing PR task store."""
from copy import deepcopy
import re

import alternatives
import skill_config
from state import capture, git, task_key

OWNER = alternatives.FIX_OWNER
STAGES = ('assessment', 'comparison', 'selection', 'alignment')


def key(tasks, stage):
    return task_key(tasks.store.subject, 'alternatives-' + stage + '-fix-review', OWNER)


def entry(tasks, stage):
    with tasks.store.locked() as manifest:
        return deepcopy(manifest['entries'].get(key(tasks, stage)))


def pinned_definition(tasks, descriptor, definition):
    stage = descriptor['stage']
    if stage not in STAGES or set(descriptor.get('dependencies', {})) - set(STAGES):
        raise ValueError('The initial fix alternatives plan uses assessment, comparison, selection and alignment only')
    if definition.get('governing_inputs_complete') is not True or definition.get('cacheable') is False:
        raise ValueError('Once-per-PR alternatives review requires complete governing inputs and cacheable original evidence')
    original = entry(tasks, stage) or entry(tasks, 'assessment')
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
        raise ValueError('Initial alternatives review governing inputs changed; explicit reassessment is required, not another automatic review')


def gate(tasks, *, options=None, upstream=None):
    with alternatives.evaluation(tasks):
        return _gate(tasks, options=options, upstream=upstream)


def _gate(tasks, *, options=None, upstream=None):
    policy = skill_config.effective(OWNER, tasks.store.subject['repository'], upstream, options=options)['options']['alternatives']
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
        value = alternatives.accepted(tasks, key(tasks, stage))
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
    result = gate(tasks, options=options, upstream=upstream)
    if not result['release_reviewers']:
        raise ValueError('Initial alternatives gate blocks fix work: ' + result['outcome'])
    for index, reference in enumerate(result.get('governing_evidence', [])):
        spec['inputs']['alternatives_fix_governing_' + str(index)] = reference
