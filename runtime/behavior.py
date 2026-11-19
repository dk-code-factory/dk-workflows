"""Resolve bundled role behavior and construct native handoffs, without execution.

Only scoped edits live in configuration. Selection references and digests use the
existing task freshness boundary; inspection and rendered prompts are transient.
"""
from config_upgrades import configuration_read
from copy import deepcopy
from pathlib import Path
import re


def roles(skill):
    import skill_config
    target = skill_config.directory(skill)
    if target is None:
        raise ValueError('Behavior is owned only for bundled dk-workflows skills')
    return {path.stem: path for path in sorted((target/'prompts').glob('*.md'))}


def validate(skill, value):
    import invocation
    if not isinstance(value, dict):
        raise ValueError('Behavior must be a role-to-settings map')
    available = roles(skill) if skill else None
    for role, fields in value.items():
        if (not isinstance(role, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', role)
                or available is not None and role not in available):
            raise ValueError('Unknown behavior role: ' + str(role))
        if not isinstance(fields, dict) or set(fields) - {'override', 'append'}:
            raise ValueError('Behavior roles accept override and append')
        if any(text is not None and not invocation.text(text) for text in fields.values()):
            raise ValueError('Behavior text must be nonblank without NUL bytes, or null')
    return deepcopy(value)


def specialized(skill, role, options):
    """Legacy behavioral options still construct defaults, never replacements."""
    import json
    if role == 'default' and skill in {'dk-workflows:dk-issue-alternatives', 'dk-workflows:dk-pr-alternatives'}:
        return '\n\n'.join(specialized(skill, stage, options) for stage in ('assessment', 'selection'))
    if role in {'default', 'completion'}:
        keys = {'dk-workflows:dk-pr-feedback-reply': ['reply_prompt'],
                'dk-workflows:dk-pr-decisions': ['reconciliation_prompt'],
                'dk-workflows:dk-pr-fix': ['completion_instructions']}.get(skill, [])
        return '\n\n'.join(options[key] for key in keys if options.get(key))
    if role.startswith('alternatives-') and 'alternatives' in options:
        policy = options['alternatives']
        stage = role.removeprefix('alternatives-')
        if stage == 'comparison':
            profile = policy['scoring']['profile']
            return policy['scoring']['profile_selection_prompt'] if profile == 'auto' else policy['scoring']['custom_prompt'] if profile == 'custom' else ''
        if stage == 'selection':
            return policy['auto_pick']['prompt']
    if skill in {'dk-workflows:dk-issue-alternatives', 'dk-workflows:dk-pr-alternatives'}:
        parts = []
        if role == 'assessment':
            parts += [options['assessment_prompt'], 'Scoring compatibility guidance: ' + json.dumps(options['scoring'], sort_keys=True)]
        if role in {'assessment', 'inventory', 'comparison'}:
            parts += ['Evaluation guidance: ' + options['evaluation_prompt']]
        if role == 'selection':
            mode = options['selection']['mode']
            parts += ['Selection mode: ' + mode + '. ' + options['selection']['prompt']]
            # Mode constraints govern selection in the execution contract too.
            parts += ['Never prompt the user; return an unresolved choice when checks fail.' if mode == 'automatic' else
                      'Require an explicit sourced user choice.' if mode == 'manual' else
                      'Select a qualifying clear winner; otherwise request an explicit user choice.']
        return '\n'.join(parts)
    return ''


def _reads_domain(values):
    import skill_config
    return skill_config.reads_domain(values)


@configuration_read(workflow_only=True, domain_repository=_reads_domain)
def resolve(skill, role='default', repository=None, upstream=None, *, behavior=None,
            options=None, section=None):
    import skill_config
    from alternatives_compat import skill as current_skill
    skill = current_skill(skill)
    available = roles(skill)
    if role not in available:
        raise ValueError('Unknown behavior role: ' + str(role))
    if section not in {None, 'default', 'effective', 'sources'}:
        raise ValueError('Unknown behavior section')
    local = validate(skill, {} if behavior is None else behavior)
    if options is not None:
        skill_config.validate(skill, options)
    fields = {'override': None, 'append': None}
    sources = {key: 'default' for key in fields}
    for scope in [None] + ([upstream] if upstream else []) + ([repository] if repository else []):
        path = skill_config.scope_path(scope)
        if path.exists():
            saved = skill_config._scope_record(path)
            layer = saved.get('skill_defaults', {}).get(skill, {}).get('behavior', {}).get(role, {})
            fields.update(layer)
            sources.update({key: str(path) for key in layer})
    fields.update(local.get(role, {}))
    sources.update({key: 'invocation' for key in local.get(role, {})})
    result = {'skill': skill, 'role': role}
    if section in {None, 'default'}:
        result['default'] = available[role].read_text()
        extra = specialized(skill, role, skill_config.defaults(skill))
        if extra:
            result['default'] += '\n\n' + extra
        result['default_path'] = str(available[role])
    if section in {None, 'effective'}:
        if fields['override'] is not None:
            prompt = fields['override']
        else:
            prompt = available[role].read_text()
            extra = specialized(skill, role, skill_config.effective(skill, repository, upstream, options=options)['options'])
            if extra:
                prompt += '\n\n' + extra
        if fields['append'] is not None:
            prompt += '\n\n' + fields['append']
        result['effective'] = prompt
        result['overridden'] = fields['override'] is not None
    if section in {None, 'sources'}:
        result['sources'] = sources
    return result


def assignment(skill, role='default', repository=None, upstream=None, *, behavior=None,
               options=None, instructions=''):
    selected = resolve(skill, role, repository, upstream, behavior=behavior, options=options)
    prompt = selected['effective']
    if instructions:
        prompt += '\n\nOriginal additional instructions:\n' + instructions
    return prompt


def bind(spec, selection):
    """Attach one consumed role, keeping identities and unrelated siblings stable."""
    from task_primitives import digest
    import skill_config
    if not isinstance(selection, dict):
        raise ValueError('Behavior selection must be an object')
    skill = selection.get('skill', spec['skill']['identity'])
    if not skill_config.directory(skill):
        if selection:
            raise ValueError('External plugin behavior is outside this contract')
        return
    role = selection.get('role', 'default')
    selection = {**deepcopy(selection), 'skill': skill, 'role': role}
    if set(selection) - {'skill', 'role', 'upstream', 'behavior', 'options'}:
        raise ValueError('Unknown behavior selection field')
    # Retain only the consumed role's invocation edits, never sibling behavior.
    local = validate(skill, selection.get('behavior', {}))
    if 'behavior' in selection:
        selection['behavior'] = {role: local[role]} if role in local else {}
    selected = resolve(skill, role, spec['repository'], selection.get('upstream'),
                       behavior=selection.get('behavior'), options=selection.get('options'))
    original = spec['task']['instructions']
    spec['task']['instructions'] = selected['effective']
    if original:
        spec['task']['instructions'] += '\n\nOriginal additional instructions:\n' + original
    spec['behavior_configuration'] = selection
    spec['options']['behavior'] = digest(selected['effective'])
    # The role path is consumed only when its shipped default is used. The entry
    # and common resolver are always governing contracts, even for replacement.
    resources = spec['skill']['resources']
    consumed = {'behavior_contract': str(Path(__file__)),
                'behavior_entry': str(skill_config.directory(skill)/'SKILL.md')}
    if not selected['overridden']:
        consumed['behavior_default'] = str(roles(skill)[role])
    for label, path in consumed.items():
        while label in resources and resources[label] != path:
            label += '_'
        resources[label] = path


def check(spec):
    from task_primitives import digest
    from shared.recovery_diagnostics import condition
    selection = spec.get('behavior_configuration')
    if selection is None:
        return  # Retained pre-behavior evidence keeps its original lifecycle.
    selected = resolve(selection['skill'], selection['role'], spec['repository'], selection.get('upstream'),
                       behavior=selection.get('behavior'), options=selection.get('options'))
    if digest(selected['effective']) != spec['options'].get('behavior'):
        raise condition('stale_inputs', 'Inputs changed: consumed role behavior changed')


def narrow_call(call, role='default'):
    """Exclude unconsumed role settings from assignment fingerprints."""
    result = deepcopy(call)
    if 'behavior' in result:
        result['behavior'] = ({role: result['behavior'][role]} if role in result['behavior'] else {})
        if not result['behavior']:
            result.pop('behavior')
    return result


def selection(skill, role='default', *, upstream=None, call=None):
    result = {'skill': skill, 'role': role, 'upstream': upstream}
    if call:
        result.update({key: deepcopy(call[key]) for key in ('behavior', 'options') if key in call})
    return result


def for_task(skill, responsibility, options):
    """Select known generated responsibilities; ordinary native skills use default."""
    import skill_config
    if not skill_config.directory(skill):
        return {}
    known = {'finding-verification': 'finding-verification',
             'workflow-completion': 'completion', 'finding-fix': 'finding-fix',
             'ci-failure-diagnosis': 'failure-diagnosis'}
    role = known.get(responsibility.split(':')[0], 'default')
    if role not in roles(skill):
        role = 'default'
    return selection(skill, role, call=options.get('invocation'))
