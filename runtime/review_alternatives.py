"""Pin automatic incoming alternatives work to its original PR assessment.

Explicit manual requests and retained impact chains keep their existing contract.
No completion marker or additional store is needed.
"""
from copy import deepcopy

import alternatives


def entry(tasks, stage):
    with tasks.store.locked() as manifest:
        matches = [value for value in manifest['entries'].values()
                   if value.get('managed', {}).get('kind') in {'alternatives', 'architecture'}
                   and value['managed'].get('stage') == stage
                   and value['managed'].get('skill', alternatives.OWNER) == alternatives.OWNER]
        if len(matches) > 1:
            raise ValueError('Multiple initial alternatives stage records require explicit reassessment')
        return deepcopy(matches[0]) if matches else None


def initial(definition, retained):
    return not (definition.get('inputs', {}).get('alternatives_request')
                or 'impact' in retained
                or any(value.get('request') for value in retained.values()))


def pinned_definition(tasks, descriptor, definition):
    # Preserve incoming contract-v2 keys, including independent verification.
    if definition.get('governing_inputs_complete') is not True or definition.get('cacheable') is False:
        raise ValueError('Once-per-PR alternatives review requires complete governing inputs and cacheable original evidence')
    original = entry(tasks, descriptor['stage']) or entry(tasks, 'assessment')
    result = deepcopy(definition)
    if original:
        result['git'] = deepcopy(original['managed']['definition']['git'])
        source = original['managed']['definition'].get('inputs', {}).get('alternatives_current_pr')
        if source:
            result.setdefault('inputs', {})['alternatives_current_pr'] = source
    if 'git' not in result:
        raise ValueError('Initial alternatives review requires pinned Git inputs')
    from task_primitives import git
    for name in ('target', 'base', 'head'):
        result['git'][name] = git(result['git']['checkout'], 'rev-parse', '--verify',
                                  result['git'][name] + '^{commit}').decode().strip()
    descriptor['definition']['git'] = deepcopy(result['git'])
    if 'alternatives_current_pr' in result.get('inputs', {}):
        descriptor['definition'].setdefault('inputs', {})['alternatives_current_pr'] = result['inputs']['alternatives_current_pr']
    return result


def guard_completed(tasks, stage, spec):
    from task_policy import capture
    original = entry(tasks, stage)
    if not original or not original.get('result'):
        return
    saved, problem = tasks.store._load_saved(original)
    if problem:
        raise ValueError('Initial alternatives evidence is missing or damaged; retain its recovery references')
    if saved['status'] != 'complete' or original.get('checklist', {}).get('outcome') != 'complete':
        return
    report = saved['normalized']
    if ((stage == 'selection' and not report.get('choice'))
            or (stage == 'alignment' and report.get('alignment') == 'uncertain')
            or (stage == 'verification' and report.get('disposition') != 'confirmed')):
        return
    with alternatives.evaluation(tasks) as context:
        current = capture(spec, _context=context['capture'])
    if current['fingerprint'] != saved['fingerprint']:
        raise ValueError('Initial alternatives review governing inputs changed; explicit reassessment is required, not another automatic review')
