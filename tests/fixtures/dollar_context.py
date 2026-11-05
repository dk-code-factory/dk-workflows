"""Audit native starting contexts independently of production resource freezes.

Native host metadata and effective initial messages both matter. A mismatch is
not dismissed because the changed instructions appear unrelated to the task.
This reader never launches replacement trials or rewrites private host records.
"""
import hashlib
import json
from pathlib import Path


def digest(value, root):
    normalized = json.dumps(value, sort_keys=True).replace(str(root.resolve()), 'ARM')
    return hashlib.sha256(normalized.encode()).hexdigest()


def starting_context(root):
    reference = json.loads((root/'private-rollout-reference.json').read_text())
    rows = [json.loads(line) for line in Path(reference['path']).read_text().splitlines()]
    meta = next(r['payload'] for r in rows if r['type'] == 'session_meta')
    contexts = [r['payload'] for r in rows if r['type'] == 'turn_context']
    if len(contexts) != 1:
        raise ValueError('Expected one native starting turn context')
    context = contexts[0]
    messages = []
    for row in rows:
        if row['type'] != 'response_item' or row['payload'].get('type') != 'message':
            continue
        message = row['payload']
        if message['role'] == 'assistant':
            break
        if message['role'] in ('developer','system','user'):
            messages.append({'role':message['role'], 'content':message['content']})
    if not meta.get('base_instructions') or not any(m['role']=='developer' for m in messages):
        raise ValueError('Incomplete native host instruction inventory')
    conditions = {k:context.get(k) for k in (
        'disabled_plugin_ids','approval_policy','approvals_reviewer','sandbox_policy',
        'permission_profile','model','comp_hash','collaboration_mode','multi_agent_version',
        'realtime_active','effort')}
    conditions.update(cli_version=meta['cli_version'], model_provider=meta['model_provider'])
    values = {'base_instruction_metadata':meta['base_instructions'],
              'effective_initial_messages':messages, 'host_conditions':conditions}
    return {**{k+'_sha256':digest(v, root) for k,v in values.items()},
            'combined_sha256':digest(values,root)}


def audit(root):
    arms = {}
    problems = []
    for i in range(1,4):
        for name in ('baseline','candidate'):
            arm = str(i)+'-'+name
            try:
                arms[arm] = starting_context(root/arm)
            except (OSError, KeyError, ValueError, StopIteration) as error:
                problems.append(arm+': '+str(error))
    variants = {key:len({a[key] for a in arms.values()}) for key in (
        'base_instruction_metadata_sha256','effective_initial_messages_sha256',
        'host_conditions_sha256','combined_sha256')}
    if any(value != 1 for value in variants.values()):
        problems.append('Native starting instructions/conditions differ across frozen arms')
    return {'passed':not problems and len(arms)==6, 'problems':problems,
            'arms':arms, 'distinct_context_counts':variants,
            'method':'Normalize ARM path only; compare retained host base metadata, effective initial messages and execution conditions.',
            'limitation':'Host instructions were not pinned before these trials. This retrospective audit cannot excuse a mismatch or authorize a rerun.'}
