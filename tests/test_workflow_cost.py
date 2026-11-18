"""Complete accounting needs host descendants, terminal turns and actual controls."""
from copy import deepcopy
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


def test_actual_tool_schemas_and_instructions_are_independently_frozen(tmp_path):
    path = tmp_path/'trace.jsonl'
    rows = write_trace(path, 'initial')
    tools = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer',
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
    assert workflow_cost.starting_context(path, replacements={'/arm/candidate': 'ARM'})['instructions'] != before['instructions']


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
