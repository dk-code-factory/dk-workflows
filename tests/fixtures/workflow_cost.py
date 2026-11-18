"""Opt-in complete-workflow evidence reader; never executes native sessions.

The host database supplies the descendant inventory independently of token usage.
Each fresh experiment root includes every own turn and descendant. Initial and
fresh-session roots share the workflow bucket; implementation, review and
orchestration roots remain separate. Call only after native work has settled.
Raw host traces and database references remain private.
"""
from collections import deque
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import native_cost_observations as observations
import native_usage


BUCKETS = {'workflow-initial', 'workflow-recovery', 'implementation',
           'review', 'orchestration'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def host_graph(database, roots):
    """Read the closed host graph, including children with no usage records."""
    observations.require(isinstance(roots, dict) and roots and
                         all(v in BUCKETS for v in roots.values()), 'Invalid cost roots')
    nodes = {}
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        queue = deque((root, None, bucket) for root, bucket in roots.items())
        while queue:
            thread, parent, bucket = queue.popleft()
            observations.require(thread not in nodes, 'Overlapping or cyclic cost roots')
            row = db.execute('SELECT rollout_path FROM threads WHERE id=?', (thread,)).fetchone()
            observations.require(row is not None, 'Host graph references a missing thread')
            nodes[thread] = {'parent': parent, 'bucket': bucket, 'rollout': row[0]}
            children = db.execute('SELECT child_thread_id FROM thread_spawn_edges '
                                  'WHERE parent_thread_id=? ORDER BY child_thread_id',
                                  (thread,)).fetchall()
            queue.extend((child[0], thread, bucket) for child in children)
    return nodes


def starting_context(path, *, replacements):
    """Hash actual initial instructions AND tool declarations, preserving meaning.

    Replacements must be frozen before trials and cover only arm paths and native
    identities. Tool-only host message records have no content field.
    """
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    meta = next(row['payload'] for row in rows if row['type'] == 'session_meta')
    context = next(row['payload'] for row in rows if row['type'] == 'turn_context')
    messages, tools = [], []
    for row in rows:
        payload = row['payload']
        if row['type'] != 'response_item' or payload.get('type') != 'message':
            continue
        if payload.get('role') == 'assistant':
            break
        if payload.get('role') not in ('system', 'developer', 'user'):
            continue
        if 'content' in payload:
            messages.append({'role': payload['role'], 'content': payload['content']})
        if 'tools' in payload:
            observations.require(isinstance(payload['tools'], list) and payload['tools'] and
                                 all(isinstance(tool, dict) and tool for tool in payload['tools']),
                                 'Missing starting tool schemas')
            tools.append({'role': payload['role'], 'tools': payload['tools']})
    observations.require(meta.get('base_instructions') and messages and tools,
                         'Missing starting instructions or tool schemas')
    controls = {key: context.get(key) for key in
                ('disabled_plugin_ids', 'approval_policy', 'approvals_reviewer',
                 'sandbox_policy', 'permission_profile', 'model', 'comp_hash',
                 'collaboration_mode', 'multi_agent_version', 'realtime_active', 'effort')}
    controls.update(cli_version=meta['cli_version'], model_provider=meta['model_provider'])

    def normalized(value):
        text = json.dumps(value, sort_keys=True)
        for old, new in replacements.items():
            observations.require(bool(old) and bool(new), 'Invalid context replacement')
            text = text.replace(old, new)
        return hashlib.sha256(text.encode()).hexdigest()

    return {'instructions': normalized({'base': meta['base_instructions'], 'messages': messages}),
            'tools': normalized(tools), 'controls': normalized(controls)}


def collect(database, roots):
    """Account the complete forest, failing closed on incomplete native evidence.

    All root turns are included: preparation, waits, evidence loading and terminal
    reporting cannot be removed after seeing their cost. Cache writes without
    independently observed durations remain inconclusive in the existing reader.
    This returns accounting evidence, not a workflow coverage/quality verdict.
    """
    graph = host_graph(database, roots)
    summaries, response_ids, buckets = {}, set(), {}
    for thread, node in graph.items():
        path = Path(node['rollout'])
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        meta = next(row['payload'] for row in rows if row['type'] == 'session_meta')
        observations.require(meta['id'] == thread, 'Host rollout identity mismatch')
        if node['parent'] is not None:
            observations.require(meta.get('parent_thread_id') == node['parent'],
                                 'Host rollout parent mismatch')
        turns = [row['payload']['turn_id'] for row in rows if row['type'] == 'event_msg'
                 and row['payload'].get('type') == 'task_started']
        observations.require(bool(turns), 'Host thread has no started turn')
        result = observations.collect_thread(path, thread, expected_turns=turns)
        ids = set(result['response_ids'])
        observations.require(not response_ids.intersection(ids), 'Response charged twice')
        response_ids.update(ids)
        summaries[thread] = result
        bucket = buckets.setdefault(node['bucket'], {'cost': Decimal(0), 'responses': 0,
                                                    'threads': 0, 'turns': 0})
        bucket['cost'] += Decimal(result['api_equivalent_cost_usd'])
        bucket['responses'] += result['response_count']
        bucket['threads'] += 1
        bucket['turns'] += len(result['turns'])
    observations.require(graph == host_graph(database, roots),
                         'Host descendant graph changed during collection')
    return {'telemetry_complete': True, 'graph_sha256': digest(graph),
            'thread_count': len(graph), 'response_ids': sorted(response_ids),
            'response_count': len(response_ids),
            'buckets': {key: {**value, 'cost': str(value['cost'])}
                        for key, value in buckets.items()},
            'workflow_cost_usd': str(sum((value['cost'] for key, value in buckets.items()
                                         if key.startswith('workflow-')), Decimal(0))),
            'threads': summaries,
            'billing_basis': 'API-equivalent estimate; not actual account billing',
            'limitation': 'Accounting completeness alone establishes neither workflow coverage '
                          'nor independent finding quality or equivalent frozen conditions.'}
