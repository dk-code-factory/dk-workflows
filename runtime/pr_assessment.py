"""Controller-injected assessment prompts and native evidence contracts.

Only original managed results are durable. No model execution or policy cache.
"""
from copy import deepcopy

import invocation
import skill_config
from alternatives import PROFILES, evidence, text

# Keep the original managed identity addressable; this is not an installed skill
# or a native invocation. The controller injects assessment instructions directly.
SKILL = 'dk-workflows:dk-pr-assessment'
FIX = 'dk-workflows:dk-pr-fix'
REVIEW = 'dk-workflows:dk-pr-review'
ISSUE = 'dk-workflows:dk-issue-alternatives'
PR = 'dk-workflows:dk-pr-alternatives'
SMALL_FIX = PROFILES['minimal_change_edge_coverage']
FEATURE = {'edge_coverage': 30, 'quality': 35, 'simplicity': 20, 'low_operational_cost': 15}


def resolve(repository=None, upstream=None, *, owner=None, options=None, policy_options=None,
            alternatives_options=None, skill=None):
    """Resolve the invoked alternatives skill, including legacy read fallbacks."""
    from alternatives_compat import skill as current_skill
    target = current_skill(skill) if skill else PR if owner == REVIEW else ISSUE
    if target not in {ISSUE, PR}:
        raise ValueError('Selection configuration targets dk-issue-alternatives or dk-pr-alternatives')
    local = {}
    if owner:
        skill_config.validate(owner, options or {})
        local = translate((options or {}).get('alternatives', {}))
    # Former explicit controller options remain readable. New skill invocation
    # options win and are retained only as the original invocation overrides.
    local = invocation.merge(local, skill_config.validate(target, policy_options or {}))
    local = invocation.merge(local, skill_config.validate(target, alternatives_options or {}))
    result = skill_config.effective(target, repository, upstream, options=local)['options']
    if result['scoring']['profile'] == 'custom' and not text(result['scoring']['custom_prompt']):
        raise ValueError('Custom assessment scoring requires custom_prompt')
    return result


def translate(old):
    result = {key: deepcopy(old[key]) for key in ('scoring',) if key in old}
    if 'auto_pick' in old:
        result['selection'] = deepcopy(old['auto_pick'])
    return result


def assessment_policy(policy):
    """Selection changes do not invalidate assessment or comparisons."""
    return {key: deepcopy(policy[key]) for key in ('assessment_prompt', 'evaluation_prompt', 'scoring')}


def validate(report):
    evidence(report)
    for key in ('purpose', 'scope', 'rationale'):
        if not text(report.get(key)):
            raise ValueError('PR assessment requires ' + key)
    if report.get('work_type') not in {'small_fix', 'feature', 'mixed'}:
        raise ValueError('Assessment requires small_fix, feature or mixed work_type')
    constraints = report.get('mandatory_constraints')
    if not isinstance(constraints, list) or any(not text(item) for item in constraints):
        raise ValueError('Assessment requires explicit mandatory constraints')
    rubrics = report.get('rubrics')
    if not isinstance(rubrics, list) or not rubrics:
        raise ValueError('Assessment requires scoped rubrics')
    ids = set()
    for rubric in rubrics:
        if not isinstance(rubric, dict) or not text(rubric.get('id')) or rubric['id'] in ids:
            raise ValueError('Rubrics require unique IDs')
        ids.add(rubric['id'])
        for field in ('scope', 'rationale'):
            if not text(rubric.get(field)):
                raise ValueError('Rubric requires ' + field)
        evidence(rubric)
        weights = rubric.get('weights')
        if (not isinstance(weights, dict) or not weights
                or any(not text(k) or type(v) is not int or v <= 0 for k, v in weights.items())
                or sum(weights.values()) != 100):
            raise ValueError('Rubric weights must be positive integers summing to 100')
        if rubric.get('work_type') not in {'small_fix', 'feature'}:
            raise ValueError('Each rubric identifies small_fix or feature scope')
        if report['work_type'] != 'mixed' and rubric['work_type'] != report['work_type']:
            raise ValueError('Rubric type differs from assessment scope')
        if rubric['work_type'] == 'feature' and {'low_loc', 'low_intrusiveness', 'change_volume'} & weights.keys():
            raise ValueError('Feature ranking excludes change volume; assess actual consequences')
        anchors = rubric.get('anchors')
        if (not isinstance(anchors, dict) or set(anchors) != set(weights)
                or any(not isinstance(a, dict) or set(a) != {'0', '50', '100'}
                       or any(not text(v) for v in a.values()) for a in anchors.values())):
            raise ValueError('Each criterion needs explicit 0, 50 and 100 scoring anchors')
    if report['work_type'] == 'mixed' and {r['work_type'] for r in rubrics} != {'small_fix', 'feature'}:
        raise ValueError('Mixed work needs rubrics for its distinct scopes')
    return report


def rubric(report, identity):
    validate(report)
    found = next((r for r in report['rubrics'] if r['id'] == identity), None)
    if found is None:
        raise ValueError('Decision must use an applicable persisted assessment rubric')
    return found
