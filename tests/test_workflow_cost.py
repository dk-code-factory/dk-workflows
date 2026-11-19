"""Complete accounting needs host descendants, terminal turns and actual controls."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from test_native_cost_observations import seed, usage
import workflow_cost


def write_trace(path, thread, parent=None, *, tier='default'):
    rows = seed()
    rows.insert(0, {'type': 'session_meta', 'payload': {
        'id': thread, 'parent_thread_id': parent, 'base_instructions': {'text': 'Host'},
        'cli_version': '1', 'model_provider': 'openai'}})
    for row in rows:
        value = row['payload']
        if row['type'] == 'token_usage_record':
            value['thread_id'] = value['session_id'] = thread
            value['response_id'] = thread+'-'+value['response_id']
        if row['type'] == 'event_msg' and value.get('type') == 'thread_settings_applied':
            value['thread_settings']['service_tier'] = tier
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    return rows


def host(tmp_path):
    database = tmp_path/'host.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)')
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)')
        for thread, parent, tier in [('initial', None, 'default'), ('reviewer', 'initial', 'fast'),
                                     ('resume', None, 'default'), ('orchestration', None, 'default')]:
            path = tmp_path/(thread+'.jsonl')
            write_trace(path, thread, parent, tier=tier)
            db.execute('INSERT INTO threads VALUES (?,?)', (thread, str(path)))
            if parent:
                db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', (parent, thread))
    return database


def test_entire_host_graph_is_priced_and_nonrecurring_cost_stays_separate(tmp_path):
    database = host(tmp_path)
    result = workflow_cost.collect(database, {'initial': 'workflow-initial',
        'resume': 'workflow-recovery', 'orchestration': 'orchestration'})
    assert result['telemetry_complete'] and result['thread_count'] == 4
    assert result['response_count'] == 12
    assert result['workflow_cost_usd'] == '0.0036'
    assert result['buckets']['orchestration']['cost'] == '0.0009'
    assert result['buckets']['workflow-initial']['threads'] == 2
    assert result['threads']['reviewer']['settings'][0]['service_tier'] == 'fast'


def test_descendant_with_no_usage_or_terminal_evidence_cannot_disappear(tmp_path):
    database = host(tmp_path)
    path = tmp_path/'reviewer.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text('\n'.join(json.dumps(row) for row in rows
                   if row['type'] != 'token_usage_record')+'\n')
    with pytest.raises(ValueError, match='inventory|Terminal'):
        workflow_cost.collect(database, {'initial': 'workflow-initial'})


@pytest.mark.parametrize('mutation', ['missing-thread', 'cycle', 'overlap', 'wrong-parent',
                                      'active-child', 'duplicate-response'])
def test_graph_identity_and_terminal_boundaries(tmp_path, mutation):
    database = host(tmp_path)
    roots = {'initial': 'workflow-initial'}
    with sqlite3.connect(database) as db:
        if mutation == 'missing-thread':
            db.execute('DELETE FROM threads WHERE id="reviewer"')
        if mutation == 'cycle':
            db.execute('INSERT INTO thread_spawn_edges VALUES ("reviewer","initial")')
    if mutation == 'overlap':
        roots['reviewer'] = 'review'
    path = tmp_path/'reviewer.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == 'wrong-parent':
        rows[0]['payload']['parent_thread_id'] = 'another'
    if mutation == 'active-child':
        rows.pop()
    if mutation == 'duplicate-response':
        for row in rows:
            if row['type'] == 'token_usage_record':
                row['payload']['response_id'] = row['payload']['response_id'].replace('reviewer-', 'initial-')
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    with pytest.raises(ValueError):
        workflow_cost.collect(database, roots)


def test_copied_parent_usage_is_not_charged_again_in_child(tmp_path):
    database = host(tmp_path)
    parent = [json.loads(line) for line in (tmp_path/'initial.jsonl').read_text().splitlines()]
    child = tmp_path/'reviewer.jsonl'
    with child.open('a') as stream:
        for row in parent:
            if row['type'] == 'token_usage_record':
                stream.write(json.dumps(row)+'\n')
    result = workflow_cost.collect(database, {'initial': 'workflow-initial'})
    assert result['response_count'] == 6
    assert result['workflow_cost_usd'] == '0.0027'


def copied_parent_fixture(tmp_path):
    """Replay the host fork shape, including an unfinished parent turn."""
    database = host(tmp_path)
    traces = {}
    for thread in ('initial', 'reviewer'):
        path = tmp_path/(thread+'.jsonl')
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            payload = row['payload']
            if 'turn_id' in payload:
                payload['turn_id'] = thread+'-turn'
            if 'root_turn_id' in payload:
                payload['root_turn_id'] = thread+'-turn'
        path.write_text('\n'.join(map(json.dumps, rows))+'\n')
        traces[thread] = rows
    prefix = [deepcopy(row) for row in traces['initial'] if row['type'] != 'token_usage_record'
              and not (row['type'] == 'event_msg' and row['payload'].get('type') in
                       ('thread_settings_applied', 'task_complete', 'token_count'))]
    # The host re-stamps copied records; time alone cannot identify foreign work.
    for row in prefix:
        row['timestamp'] = '2026-11-18T23:15:06.727Z'
    child = traces['reviewer']
    rows = [child[0], *prefix, child[2], child[3], child[1], *child[4:]]
    return database, rows


def test_copied_active_parent_prefix_preserves_every_own_response_and_raw_evidence(tmp_path):
    database, rows = copied_parent_fixture(tmp_path)
    path = tmp_path/'reviewer.jsonl'
    raw = ('\n'.join(map(json.dumps, rows))+'\n').encode()
    path.write_bytes(raw)
    result = workflow_cost.collect(database, {'initial': 'workflow-initial'})
    assert result['telemetry_complete'] and result['response_count'] == 6
    assert result['workflow_cost_usd'] == '0.0027'
    child = result['threads']['reviewer']
    assert child['turns'][0]['turn_id'] == 'reviewer-turn'
    assert child['response_ids'] == ['reviewer-response-'+str(i) for i in range(1, 4)]
    assert child['raw_rollout_sha256'] == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw


@pytest.mark.parametrize('conflicting', [False, True])
def test_copied_ancestor_response_identity_is_checked_before_ownership_filtering(tmp_path, conflicting):
    database, rows = copied_parent_fixture(tmp_path)
    parent = [json.loads(line) for line in (tmp_path/'initial.jsonl').read_text().splitlines()]
    copied = deepcopy(next(row for row in parent if row['type'] == 'token_usage_record'))
    if conflicting:
        copied['payload']['usage'] = usage(2)
    rows.insert(2, copied)
    (tmp_path/'reviewer.jsonl').write_text('\n'.join(map(json.dumps, rows))+'\n')
    if conflicting:
        with pytest.raises(ValueError, match='Conflicting duplicate response'):
            workflow_cost.collect(database, {'initial': 'workflow-initial'})
    else:
        result = workflow_cost.collect(database, {'initial': 'workflow-initial'})
        assert result['response_count'] == 6 and result['workflow_cost_usd'] == '0.0027'


def test_nested_fork_prefix_is_corroborated_against_all_original_ancestors(tmp_path):
    database, parent = copied_parent_fixture(tmp_path)
    (tmp_path/'reviewer.jsonl').write_text('\n'.join(map(json.dumps, parent))+'\n')
    path = tmp_path/'leaf.jsonl'
    own = write_trace(path, 'leaf', 'reviewer')
    for row in own:
        payload = row['payload']
        if 'turn_id' in payload:
            payload['turn_id'] = 'leaf-turn'
        if 'root_turn_id' in payload:
            payload['root_turn_id'] = 'leaf-turn'
    prefix = [row for row in parent if row['type'] != 'token_usage_record'
              and not (row['type'] == 'event_msg' and row['payload'].get('type') in
                       ('thread_settings_applied', 'task_complete', 'token_count'))]
    path.write_text('\n'.join(map(json.dumps,
        [own[0], *prefix, own[2], own[3], own[1], *own[4:]]))+'\n')
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO threads VALUES (?,?)', ('leaf', str(path)))
        db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', ('reviewer', 'leaf'))
    result = workflow_cost.collect(database, {'initial': 'workflow-initial'})
    assert result['thread_count'] == 3 and result['response_count'] == 9
    assert result['workflow_cost_usd'] == '0.0036'


@pytest.mark.parametrize('mutation', ['unknown-parent', 'unknown-turn', 'own-usage', 'counter',
                                      'missing-settings', 'late-ancestor', 'missing-usage',
                                      'missing-terminal'])
def test_copied_prefix_cannot_hide_uncorroborated_or_missing_evidence(tmp_path, mutation):
    database, rows = copied_parent_fixture(tmp_path)
    if mutation == 'unknown-parent':
        rows[1]['payload']['id'] = 'unknown-parent'
    elif mutation == 'unknown-turn':
        rows[2]['payload']['turn_id'] = 'not-in-parent-trace'
    elif mutation == 'own-usage':
        rows.insert(2, deepcopy(next(row for row in rows if row['type'] == 'token_usage_record')))
    elif mutation == 'counter':
        rows.insert(2, deepcopy(next(row for row in rows if row['type'] == 'event_msg'
                                    and row['payload'].get('type') == 'token_count')))
    elif mutation == 'missing-settings':
        rows = [row for row in rows if not (row['type'] == 'event_msg' and
                                            row['payload'].get('type') == 'thread_settings_applied')]
    elif mutation == 'late-ancestor':
        rows.append({'type': 'event_msg', 'payload': {'type': 'task_started',
                                                     'turn_id': 'initial-turn'}})
    elif mutation == 'missing-usage':
        rows = [row for row in rows if not (row['type'] == 'token_usage_record' and
                                            row['payload']['response_id'] == 'reviewer-response-2')]
    elif mutation == 'missing-terminal':
        rows.pop()
    (tmp_path/'reviewer.jsonl').write_text('\n'.join(map(json.dumps, rows))+'\n')
    with pytest.raises(ValueError):
        workflow_cost.collect(database, {'initial': 'workflow-initial'})


def test_copied_parent_prefix_keeps_fresh_followup_turns_and_cumulative_cost(tmp_path):
    database, rows = copied_parent_fixture(tmp_path)
    followup = seed()
    for row in followup:
        payload = row['payload']
        if 'turn_id' in payload:
            payload['turn_id'] = 'reviewer-followup'
        if row['type'] == 'token_usage_record':
            payload.update(thread_id='reviewer', session_id='reviewer',
                           root_turn_id='reviewer-followup')
            payload['response_id'] = 'followup-'+payload['response_id']
        if row['type'] == 'event_msg' and payload.get('type') == 'token_count':
            count = payload['info']['total_token_usage']['input_tokens']//100
            payload['info']['total_token_usage'] = usage(count+3)
        if row['type'] == 'event_msg' and payload.get('type') == 'thread_settings_applied':
            payload['thread_settings']['service_tier'] = 'fast'
    rows.extend(followup)
    (tmp_path/'reviewer.jsonl').write_text('\n'.join(map(json.dumps, rows))+'\n')
    result = workflow_cost.collect(database, {'initial': 'workflow-initial'})
    assert result['response_count'] == 9 and result['workflow_cost_usd'] == '0.0045'
    assert len(result['threads']['reviewer']['turns']) == 2


def test_missing_cache_duration_or_unknown_prices_stay_incomplete(tmp_path):
    database = host(tmp_path)
    path = tmp_path/'initial.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        value = row['payload']
        if row['type'] == 'token_usage_record':
            value['usage']['cache_write_input_tokens'] = 10
        if row['type'] == 'event_msg' and value.get('type') == 'token_count':
            value['info']['last_token_usage']['cache_write_input_tokens'] = 10
            value['info']['total_token_usage']['cache_write_input_tokens'] = (
                value['info']['total_token_usage']['input_tokens']//10)
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    with pytest.raises(ValueError, match='TTLs'):
        workflow_cost.collect(database, {'initial': 'workflow-initial'})


@pytest.mark.parametrize('tool_record', ['message', 'additional_tools'])
def test_actual_tool_schemas_and_instructions_are_independently_frozen(tmp_path, tool_record):
    path = tmp_path/'trace.jsonl'
    rows = write_trace(path, 'initial')
    tools = {'type': 'response_item', 'payload': {'type': tool_record, 'role': 'developer',
        'tools': [{'name': 'native-delegation', 'schema': {'type': 'object'}}]}}
    instruction = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
        'content': [{'text': 'Complete workflow in /arm/baseline'}]}}
    rows[1:1] = [tools, instruction]
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    before = workflow_cost.starting_context(path, replacements={'/arm/baseline': 'ARM'})
    instruction['payload']['content'][0]['text'] = 'Complete workflow in /arm/candidate'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    assert workflow_cost.starting_context(path, replacements={'/arm/candidate': 'ARM'}) == before
    tools['payload']['tools'][0]['schema']['required'] = ['new-field']
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    changed = workflow_cost.starting_context(path, replacements={'/arm/candidate': 'ARM'})
    assert changed['tools'] != before['tools'] and changed['instructions'] == before['instructions']
    instruction['payload']['content'][0]['text'] = 'Skip finalizers in /arm/candidate'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    changed = workflow_cost.starting_context(path, replacements={'/arm/candidate': 'ARM'})
    assert changed['instructions'] != before['instructions']
    assert changed['assignment'] != before['assignment']
    assert changed['host_instructions'] == before['host_instructions']


def test_context_freeze_stops_at_the_first_native_agent_response(tmp_path):
    path = tmp_path/'trace.jsonl'
    rows = write_trace(path, 'initial')
    prefix = [
        {'type': 'response_item', 'payload': {'type': 'additional_tools', 'role': 'developer',
         'tools': [{'type': 'function', 'name': 'read', 'parameters': {'type': 'object'}}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
         'content': [{'text': 'Initial assignment.'}]}},
        {'type': 'response_item', 'payload': {'type': 'agent_message', 'author': 'assistant',
         'recipient': 'all', 'content': [{'text': 'Native execution starts.'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
         'content': [{'text': 'Later runtime notification.'}]}},
    ]
    rows[1:1] = prefix
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    before = workflow_cost.starting_context(path, replacements={})
    prefix[-1]['payload']['content'][0]['text'] = 'Different later notification.'
    path.write_text('\n'.join(map(json.dumps, rows))+'\n')
    assert workflow_cost.starting_context(path, replacements={}) == before


def test_graph_change_during_collection_invalidates_the_measurement(tmp_path, monkeypatch):
    database = host(tmp_path)
    original = workflow_cost.observations.collect_thread
    changed = False

    def collect(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            with sqlite3.connect(database) as db:
                db.execute('INSERT INTO thread_spawn_edges VALUES ("initial","resume")')
            changed = True
        return result

    monkeypatch.setattr(workflow_cost.observations, 'collect_thread', collect)
    with pytest.raises(ValueError, match='changed during collection'):
        workflow_cost.collect(database, {'initial': 'workflow-initial'})
