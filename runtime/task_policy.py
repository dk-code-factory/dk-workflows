"""Spec construction and live workflow policy at each capture boundary."""
from copy import deepcopy
from pathlib import Path
import os
import invocation
import task_capture
from task_primitives import digest, identity

def make_spec(*, repository, pr, skill_identity, resources, responsibility,
              assigned_instructions, instructions, inputs, options, git=None,
              governing_inputs_complete=False, cacheable=None, native_review=None, instruction_policy=None):
    """Build a task spec and bind any adopted repository instruction sources.

    Callers supply the complete governing resource/input maps. Apart from the
    instruction policy binding, generation does not capture those resources or
    resolve Git refs. Command boundaries still validate all live inputs.
    """
    subject = identity(repository, pr)
    invocation.skill_name(skill_identity)
    for name, value in (('skill identity', skill_identity), ('responsibility', responsibility),
                        ('assigned instructions', assigned_instructions)):
        if not isinstance(value, str) or not value:
            raise ValueError('Nonempty ' + name + ' is required')

    def paths(values):
        if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
            raise ValueError('Expected a label-to-resource-path map')
        result = {}
        for key, value in values.items():
            path = os.fspath(value)
            if not isinstance(path, str) or not path:
                raise ValueError('Resource paths must be nonempty strings')
            result[key] = path
        return result

    resources, instructions, inputs = paths(resources), paths(instructions), paths(inputs)
    if type(governing_inputs_complete) is not bool:
        raise ValueError('governing_inputs_complete must be boolean')
    if cacheable is None:
        cacheable = bool(resources) and governing_inputs_complete
    if type(cacheable) is not bool:
        raise ValueError('cacheable must be boolean')
    if cacheable and (not resources or not governing_inputs_complete):
        raise ValueError('Cache requires captured resources and complete governing inputs; use cacheable:false otherwise')
    if not isinstance(options, dict):
        raise ValueError('options must be an object')
    if 'invocation' in options:
        invocation.validate(options['invocation'])
    spec = {**subject, 'skill': {'identity': skill_identity, 'resources': resources},
            'task': {'id': responsibility, 'instructions': assigned_instructions},
            'instructions': instructions, 'inputs': inputs, 'options': deepcopy(options),
            'governing_inputs_complete': governing_inputs_complete, 'cacheable': cacheable}
    if git is not None:
        if not isinstance(git, dict):
            raise ValueError('git must be an object')
        source = deepcopy(git)
        source['checkout'] = os.fspath(source['checkout'])
        for key in ('checkout', 'target', 'base', 'head'):
            if not isinstance(source[key], str) or not source[key]:
                raise ValueError('Git ' + key + ' must be a nonempty string')
        for option in ('history', 'head_sensitive'):
            if type(source.get(option, False)) is not bool:
                raise ValueError(option + ' must be boolean')
        spec['git'] = source
    if native_review is not None:
        from native_review import contract
        spec['native_review'] = deepcopy(contract(native_review))
    import instruction_sources
    checkout = git['checkout'] if git is not None else (instruction_policy or {}).get('checkout', Path.cwd())
    live_policy = instruction_sources.binding(checkout, subject['repository'])
    if instruction_policy is not None and instruction_policy != live_policy:
        raise ValueError('Repository instruction choice or sources changed; rebuild assignment and handoff')
    if live_policy is not None:
        spec['instruction_policy'] = live_policy
    digest(spec)  # Reject values that cannot survive the JSON task-spec contract.
    return spec


def validate_current(spec):
    """Validate governing sources and selections independently of storage."""
    import instruction_sources
    policy = spec.get('instruction_policy')
    checkout = spec.get('git', {}).get('checkout', policy['checkout'] if policy else Path.cwd())
    live_policy = instruction_sources.binding(checkout, spec['repository'])
    if policy != live_policy:
        raise ValueError('Repository instruction choice or sources changed; rebuild assignment and handoff')
    if policy is not None:
        instruction_sources.check_paths(checkout, spec['repository'], [
            *spec.get('instructions', {}).values(), *spec.get('inputs', {}).values(),
            *spec.get('skill', {}).get('resources', {}).values()])
    if 'skill_options_configuration' in spec:
        from skill_config import check_options
        check_options(spec)
    if 'skill_configuration' in spec:
        from skill_config import check_selection as check_skill_selection
        check_skill_selection(spec)
    if 'configuration' in spec:
        from config import check_selection
        check_selection(spec)
    if 'invocation' in spec.get('options', {}):
        invocation.validate(spec['options']['invocation'])


def capture(spec, *, _context=None):
    validate_current(spec)
    return task_capture.capture(spec, _context=_context)
