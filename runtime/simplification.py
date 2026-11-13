"""Compose workflow simplification into live reviewer plans; never execute skills."""
from copy import deepcopy

OWNERS = {'dk-workflows:dk-pr-review', 'dk-workflows:dk-pr-fix'}
RUNNER = 'dk-workflows:dk-pr-reviewers-run'
REVIEWER = 'dk-workflows:dk-code-simplify'


def validate_policy(policy):
    import invocation
    if 'skill' not in invocation.normalize(policy['reviewer']):
        raise ValueError('Simplification reviewer requires a fully qualified skill')


def governing(policy):
    priority = ('Favor small, easily reviewed improvements, then resulting code clarity.'
                if policy['priority'] == 'change-first' else
                'Favor clearer resulting code, then minimize the necessary change.')
    scope = ('Recommendations must stay within changed hunks.' if not policy['allow_untouched_code'] else
             'Allow directly related surrounding code or call sites only when needed for a concrete simplification of this PR.')
    return ('Governing simplification contract: priority=' + policy['priority'] + '. ' + priority + ' ' + scope +
            ' Return read-only recommendations; do not edit or publish. Preserve behavior, public contracts and necessary operational guarantees.'
            ' Line count alone never establishes simplicity. Exclude broad redesigns, repository-wide cleanup and unrelated modernization.'
            ' Anchor scope to the supplied PR changes across every cycle; earlier suggestions do not expand that scope.'
            ' Explain complexity reduced, proposed scope, priority tradeoff and verification needed for each recommendation.'
            ' Keep out-of-scope opportunities as deferred observations, separate from actionable findings.'
            ' This workflow contract governs replacement providers and overrides conflicting provider options or additional instructions.'
            ' Simplification does not substitute for ordinary review coverage.')


def compose(loaded, policy):
    import invocation
    result = deepcopy(loaded)
    if policy['enabled']:
        call = invocation.normalize(policy['reviewer'])
        call['instructions'] = (invocation.additional_instructions(governing(policy), call['instructions'])
                                if 'instructions' in call else governing(policy))
        if call['skill'] == REVIEWER:
            call['options'] = {**call.get('options', {}),
                               **{key: policy[key] for key in ('priority', 'allow_untouched_code')}}
        result['options']['reviewers'].append(call)
    return result


def runner_configuration(workflow, repository, upstream=None):
    """Resolve the owning occurrence on every prepare/finish/resume, not a snapshot."""
    import skill_config
    if (not isinstance(workflow, dict) or set(workflow) - {'skill', 'index', 'options'}
            or workflow.get('skill') not in OWNERS or type(workflow.get('index')) is not int):
        raise ValueError('Workflow selection requires a PR workflow skill and task index')
    call, owner = skill_config.select(workflow['skill'], 'tasks', workflow['index'], repository, upstream,
                                     options=workflow.get('options'))
    if call.get('skill') != RUNNER:
        raise ValueError('Inputs changed: workflow no longer selects this reviewer stage')
    loaded = skill_config.effective(RUNNER, repository, upstream, options=call.get('options'))
    loaded['workflow_limits'] = owner['options']['limits']
    loaded['workflow_permissions'] = owner['options']['permissions']
    return compose(loaded, owner['options']['simplification'])


def selection(skill, kind, repository, upstream, options, workflow):
    import skill_config
    if workflow is None:
        return skill_config.effective(skill, repository, upstream, options=options)
    if skill != RUNNER or kind != 'reviewers' or options:
        raise ValueError('Workflow-bound reviewers derive options from their live owning task')
    return runner_configuration(workflow, repository, upstream)
