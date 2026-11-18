"""Read pre-rename configuration and assignments without rewriting private state.

Only contract names change here; user instructions and original evidence remain
opaque. Historical task identities stay stable while live guidance is recaptured.
"""
from copy import deepcopy
from pathlib import Path

SKILLS = {
    'dk-workflows:dk-architecture-review': 'dk-workflows:dk-alternatives-review',
    'dk-workflows:dk-pr-review-architect': 'dk-workflows:dk-pr-review-alternatives',
    'dk-workflows:dk-issue-implementation-alternatives': 'dk-workflows:dk-issue-alternatives',
}
OWNER = 'dk-workflows:dk-pr-review'
ISSUE = 'dk-workflows:dk-issue-alternatives'
PR = 'dk-workflows:dk-pr-alternatives'
ASSESSMENT = 'dk-workflows:dk-pr-assessment'  # Historical task identity/settings address only.
STAGES = ('assessment', 'comparison', 'impact', 'selection', 'alignment', 'verification')


def skill(value):
    return SKILLS.get(value, value) if isinstance(value, str) else value


def aliases(value, pairs):
    result = deepcopy(value)
    if not isinstance(result, dict):
        return result
    for old, new in pairs:
        if old in result:
            if new in result and result[new] != result[old]:
                raise ValueError('Conflicting review names: ' + old + ' and ' + new)
            result[new] = result.pop(old)
    return result


def permissions(value):
    return aliases(value, [('edit_architecture_decisions', 'edit_alternatives_decisions')])


def alternatives_settings(defaults, identity):
    """Read former controller/workflow settings as per-skill fallbacks.

    Canonical settings take precedence; ambiguous legacy values require an
    explicit canonical override. No private record is rewritten.
    """
    import invocation
    explicit = defaults.get(identity, {}).get('options', {})
    shared = defaults.get(ASSESSMENT, {}).get('options', {})
    owner = 'dk-workflows:dk-pr-fix' if identity == ISSUE else OWNER
    old = defaults.get(owner, {}).get('options', {}).get('alternatives', {})
    workflow = {key: deepcopy(old[key]) for key in ('scoring',) if key in old}
    if 'auto_pick' in old:
        workflow['selection'] = deepcopy(old['auto_pick'])

    def merge(left, right, chosen):
        result = deepcopy(left)
        for key, value in right.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge(result[key], value, chosen.get(key, {}) if isinstance(chosen, dict) else {})
            else:
                if key in result and result[key] != value and (not isinstance(chosen, dict) or key not in chosen):
                    raise ValueError('Alternatives configuration conflict in legacy settings: ' + key)
                result[key] = deepcopy(value)
        return result
    return invocation.merge(merge(shared, workflow, explicit), explicit)


def options(identity, value):
    result = deepcopy(value)
    if identity not in {OWNER, 'dk-workflows:dk-pr-fix'} or not isinstance(result, dict):
        return result
    result = aliases(result, [('architecture', 'alternatives')])
    if isinstance(result.get('alternatives'), dict):
        policy = result['alternatives']
        if isinstance(policy.get('review_skill'), str):
            policy['review_skill'] = skill(policy['review_skill'])
    if 'permissions' in result:
        result['permissions'] = permissions(result['permissions'])
    return result


def call(value):
    if isinstance(value, str):
        return skill(value)
    result = deepcopy(value)
    if isinstance(result, dict) and isinstance(result.get('skill'), str):
        result['skill'] = skill(result['skill'])
        if 'options' in result:
            result['options'] = options(result['skill'], result['options'])
    return result


def configuration(value):
    result = deepcopy(value)
    defaults = result.get('skill_defaults')
    if isinstance(defaults, dict):
        defaults = aliases(defaults, SKILLS.items())
        for identity, settings in defaults.items():
            if isinstance(settings, dict) and 'options' in settings:
                settings['options'] = options(identity, settings['options'])
        result['skill_defaults'] = defaults
    for process in result.get('processes', {}).values() if isinstance(result.get('processes'), dict) else ():
        if isinstance(process, dict) and 'permissions' in process:
            process['permissions'] = permissions(process['permissions'])
    return result


def descriptor(value):
    result = deepcopy(value)
    if result.get('kind') == 'architecture':
        result['kind'] = 'alternatives'
    if 'options' in result:
        result['options'] = options(OWNER, result['options'])
    definition = result.get('definition')
    if not isinstance(definition, dict):
        return result
    if isinstance(definition.get('skill_identity'), str):
        definition['skill_identity'] = skill(definition['skill_identity'])
    provider = ISSUE if result.get('skill') == 'dk-workflows:dk-pr-fix' else PR
    if result.get('kind') == 'assessed-alternatives' and result.get('stage') == 'assessment' and definition.get('skill_identity') == ASSESSMENT:
        definition['skill_identity'] = provider
    for field in ('options', 'inputs', 'resources'):
        mapping = definition.get(field)
        if not isinstance(mapping, dict):
            continue
        labels = {'architecture', 'architecture_evidence', 'architecture_current_pr',
                  'architecture_request', 'architecture_contract', 'architecture_guidance',
                  'architecture_prompt', 'architecture_applicability',
                  *('architecture_' + stage for stage in STAGES)}
        pairs = [(key, 'alternatives' + key[len('architecture'):]) for key in mapping
                 if key in labels or isinstance(key, str) and key.startswith('architecture_governing_')]
        definition[field] = aliases(mapping, pairs)
    # Relocate only bundled sources, never user files with a similar basename.
    root = Path(__file__).resolve().parents[1]
    paths = {str(root/old): str(root/new) for old, new in (
        ('runtime/architecture.py', 'runtime/alternatives.py'),
        ('docs/architecture-review.md', 'docs/alternatives-review.md'),
        *((f'{host}/skills/{old}/{file}', f'{host}/skills/{new}/{file}')
          for host in ('codex', 'claude') for old, new in (
              ('dk-architecture-review', 'dk-alternatives-review'),
              ('dk-pr-review-architect', 'dk-pr-review-alternatives'),
              ('dk-issue-implementation-alternatives', 'dk-issue-alternatives'))
          for file in ('', 'SKILL.md', 'config.json', 'config.schema.json')),
    )}
    for host in ('codex', 'claude'):
        previous = root/host/'skills/dk-pr-assessment'
        current = root/host/'skills'/provider.split(':')[-1]
        paths[str(previous)] = str(current)
        paths[str(previous/'SKILL.md')] = str(root/'runtime/pr_assessment.py')
        for filename in ('config.json', 'config.schema.json'):
            paths[str(previous/filename)] = str(current/filename)
    for field in ('instructions', 'resources'):
        if isinstance(definition.get(field), dict):
            definition[field] = {key: paths.get(path, path) for key, path in definition[field].items()}
    return result


def storage_identity(task, identity, options=None):
    """Keep contract-v2 keys, original attempts, claims and budgets addressable."""
    if task == 'pr-purpose-assessment' and identity in {
            ASSESSMENT, ISSUE, PR, 'dk-workflows:dk-issue-implementation-alternatives'}:
        return task, ASSESSMENT
    assigned = (options or {}).get('alternatives', {})
    stage = assigned.get('stage') if isinstance(assigned, dict) else None
    if isinstance(assigned, dict) and assigned.get('owner') == 'dk-workflows:dk-pr-fix':
        return task, 'dk-workflows:dk-pr-fix'
    if ((identity in {OWNER, *SKILLS, *SKILLS.values()} or stage in STAGES)
            and task in {'alternatives-' + stage + '-review' for stage in STAGES}):
        task = 'architecture-' + task[len('alternatives-'):]
    original = {new: old for old, new in SKILLS.items()}
    return task, original.get(identity, identity)
