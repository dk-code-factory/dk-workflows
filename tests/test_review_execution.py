"""Offline proofs distinguish native specialist execution from review prose."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from shared import review_execution as execution

BINDING = {'skill': 'code-review-ad:code-review-ad', 'host': 'codex'}
ROLES = ['Toolkit', 'Standards', 'Spec']


def stream(rows):
    return '\n'.join(json.dumps(row) for row in rows)


def codex_rows(roles=ROLES, *, state='completed', sender='root', message='Inspected; no findings.'):
    rows = [{'type': 'thread.started', 'thread_id': 'root'}, {'type': 'turn.started'}]
    for number, role in enumerate(roles):
        child = f'child-{number}'
        rows.append({'type': 'item.completed', 'item': {
            'id': f'spawn-{number}', 'type': 'collab_tool_call', 'tool': 'spawn_agent',
            'sender_thread_id': sender, 'receiver_thread_ids': [child],
            'prompt': f'DK_REVIEW_PASS={role}\nInspect the pinned scope.',
            'agents_states': {child: {'status': 'running', 'message': None}}, 'status': 'completed'}})
        rows.append({'type': 'item.completed', 'item': {
            'id': f'wait-{number}', 'type': 'collab_tool_call', 'tool': 'wait',
            'sender_thread_id': sender, 'receiver_thread_ids': [child], 'prompt': None,
            'agents_states': {child: {'status': state, 'message': message}}, 'status': 'completed'}})
    rows.append({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
    return rows


def claude_result(number, text='No findings.'):
    return text + (f"\nagentId: child-{number} (use SendMessage with to: 'child-{number}' to continue this agent)"
                   '\n<usage>total_tokens: 10\ntool_uses: 1\nduration_ms: 1</usage>')


def claude_rows(roles=ROLES, *, tool='Task', background=False, is_error=False, session='session', parent=None):
    rows = [{'type': 'system', 'subtype': 'init', 'session_id': session}]
    for number, role in enumerate(roles):
        call = f'call-{number}'
        rows.append({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': parent,
                     'message': {'content': [{'type': 'tool_use', 'name': tool, 'id': call,
                         'input': {'prompt': f'DK_REVIEW_PASS={role}\nInspect.', 'run_in_background': background}}]}})
        rows.append({'type': 'user', 'session_id': session, 'parent_tool_use_id': parent,
                     'message': {'content': [{'type': 'tool_result', 'tool_use_id': call,
                         'is_error': is_error, 'content': [{'type': 'text', 'text':
                             f'Async agent launched successfully.\nagentId: child-{number}\nThe agent is working in the background.'
                             if background else claude_result(number)}]}]}})
    rows.append({'type': 'result', 'subtype': 'success', 'is_error': False,
                 'session_id': session, 'result': '{"findings": []}'})
    return rows


@pytest.fixture
def proof(tmp_path):
    repository = tmp_path / 'repository'
    repository.mkdir()
    patch = repository / 'change.patch'
    patch.write_text('diff --git a/core.py b/core.py\n+++ b/core.py\n@@ -1 +1 @@\n-return 0\n+return 1\n')
    handoff = {'head_sha': 'head-a', 'base_sha': 'base-a', 'pr_url': 'https://github.com/o/r/pull/1',
               'response_budget': 'session:reviewer', 'patch': str(patch)}
    path = tmp_path / 'handoff.json'
    path.write_text(json.dumps(handoff))
    descriptor = {'execution': {'executor': 'codex', 'version': '1.0.7', 'root': '/retained',
                               'bundle_digest': 'bundle-a', 'procedure': '/retained/skill.md'},
                  'sources': [{'path': '/retained/' + relative, 'sha256': sha}
                              for relative, sha in execution._METHODS[(BINDING['skill'], 'codex')]['sources'].items()]}
    return repository, path, descriptor


def retained(proof, rows=None, *, host='codex', returncode=0, result=None):
    repository, path, descriptor = proof
    result = {'findings': []} if result is None else result
    with execution.collect(BINDING, descriptor, path, repository) as collector:
        execution.record(host, stream(codex_rows([*ROLES, 'Performance']) if rows is None else rows), 'stderr kept', returncode, 0)
        reference = execution.finish(collector, result)
    return reference


@pytest.mark.parametrize('parser,rows', [(execution.codex_passes, codex_rows()),
                                        (execution.claude_passes, claude_rows()),
                                        (execution.claude_passes, claude_rows(tool='Agent'))])
def test_native_launch_and_completed_result_establish_each_role(parser, rows):
    parsed = parser(stream(rows))
    assert parsed['launched'] == sorted(ROLES)
    assert parsed['completed'] == sorted(ROLES)
    assert parsed['native_session']


@pytest.mark.parametrize('state', ['pending_init', 'running', 'interrupted', 'errored', 'shutdown', 'not_found'])
def test_codex_spawn_or_terminal_failure_is_not_a_completed_specialist(state):
    parsed = execution.codex_passes(stream(codex_rows(state=state)))
    assert parsed['launched'] == sorted(ROLES)
    assert parsed['completed'] == []


def test_codex_pending_initialization_can_later_complete(proof):
    rows = codex_rows([*ROLES, 'Performance'])
    for row in rows:
        item = row.get('item', {})
        if item.get('tool') == 'spawn_agent':
            child = item['receiver_thread_ids'][0]
            item['agents_states'][child]['status'] = 'pending_init'
    reference = retained(proof, rows)
    execution.verify(reference, BINDING, proof[2], proof[1])


def test_codex_empty_completion_and_foreign_sender_are_not_proof():
    assert execution.codex_passes(stream(codex_rows(message='')))['completed'] == []
    assert execution.codex_passes(stream(codex_rows(sender='other-root')))['launched'] == []


@pytest.mark.parametrize('rows', [claude_rows(background=True), claude_rows(is_error=True),
                                  claude_rows(parent='nested-tool')])
def test_claude_background_failure_or_nested_transcript_cannot_prove_completion(rows):
    assert execution.claude_passes(stream(rows))['completed'] == []


def test_claude_implicit_background_launch_is_not_completion(proof):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': 'claude', 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = 'claude'
    rows = claude_rows(['Audit'], background=True)
    del rows[1]['message']['content'][0]['input']['run_in_background']
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution missing:.*Audit'):
        execution.verify(reference, binding, descriptor, path)


@pytest.mark.parametrize('text', ['No findings.', 'Unexpected native result status.'])
def test_claude_unknown_success_result_is_unavailable(proof, text):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': 'claude', 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = 'claude'
    rows = claude_rows(['Audit'])
    rows[2]['message']['content'][0]['content'] = text
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, binding, descriptor, path)
    assessment = json.loads(Path(reference['path']).read_text())['assessment']
    assert 'unsupported Claude specialist result envelope' in assessment['unknown']


@pytest.mark.parametrize('target', ['child-0', 'reviewer'])
def test_claude_sendmessage_reactivation_invalidates_completion(proof, target):
    rows = claude_rows()
    rows[1]['message']['content'][0]['input']['name'] = 'reviewer'
    rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_use', 'name': 'SendMessage', 'id': 'followup',
         'input': {'to': target, 'message': 'Finish the remaining required check.', 'summary': 'Review correction'}}]}})
    rows.insert(-1, {'type': 'user', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': 'followup', 'content': json.dumps({
            'success': True, 'message': f'Agent "{target}" was stopped (completed); resumed it in the background.'})}]}})
    parsed = execution.claude_passes(stream(rows))
    assert parsed['completed'] == ['Spec', 'Standards']
    assert parsed['unfinished'] == ['Toolkit']
    repository, path, descriptor = proof
    binding = {**BINDING, 'host': 'claude'}
    descriptor['execution']['executor'] = 'claude'
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution missing:.*Toolkit'):
        execution.verify(reference, binding, descriptor, path)


def test_claude_shutdown_message_preserves_completed_review():
    rows = claude_rows()
    rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_use', 'name': 'SendMessage', 'id': 'close',
         'input': {'to': 'child-0', 'message': {'type': 'shutdown_request'}}}]}})
    assert execution.claude_passes(stream(rows))['completed'] == sorted(ROLES)


def test_claude_one_native_child_cannot_impersonate_independent_roles():
    rows = claude_rows()
    rows[4]['message']['content'][0]['content'] = claude_result(0)
    with pytest.raises(ValueError, match='duplicate Claude specialist identity'):
        execution.claude_passes(stream(rows))


def test_claude_unrelated_sendmessage_preserves_completed_review():
    rows = claude_rows()
    rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_use', 'name': 'SendMessage', 'id': 'unrelated',
         'input': {'to': 'other-agent', 'message': 'Unrelated work.', 'summary': 'Other task'}}]}})
    assert execution.claude_passes(stream(rows))['completed'] == sorted(ROLES)


@pytest.mark.parametrize('prompt', ['Finish the required check.', 'DK_REVIEW_PASS=Toolkit\nFinish.'])
def test_claude_resume_without_supported_completion_evidence_is_unavailable(prompt):
    rows = claude_rows()
    rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Agent', 'id': 'resume',
         'input': {'resume': 'child-0', 'prompt': prompt}}]}})
    with pytest.raises(ValueError, match='unsupported Claude specialist resume evidence'):
        execution.claude_passes(stream(rows))


def test_claude_foreign_session_tool_events_cannot_supply_results():
    rows = claude_rows()
    rows[-1]['session_id'] = 'other-session'
    parsed = execution.claude_passes(stream(rows))
    assert parsed['launched'] == parsed['completed'] == []


def test_claude_unrelated_result_and_text_cannot_impersonate_task_completion():
    rows = claude_rows()
    for row in rows:
        if row['type'] == 'user':
            row['message']['content'][0]['tool_use_id'] = 'unrelated'
    assert execution.claude_passes(stream(rows))['completed'] == []
    prose = [{'type': 'assistant', 'session_id': 'session', 'message': {'content': [
        {'type': 'text', 'text': 'DK_REVIEW_PASS=Toolkit\nAll specialists ran.'}]}}, rows[-1]]
    assert execution.claude_passes(stream(prose))['completed'] == []


@pytest.mark.parametrize('parser,rows,index', [(execution.codex_passes, codex_rows(), 2),
                                              (execution.claude_passes, claude_rows(), 1)])
def test_duplicate_native_launch_identity_rejected(parser, rows, index):
    rows.insert(index + 1, copy.deepcopy(rows[index]))
    with pytest.raises(ValueError, match='duplicate'):
        parser(stream(rows))


@pytest.mark.parametrize('parser,raw', [(execution.codex_passes, ''),
                                      (execution.codex_passes, '{"findings":[]}'),
                                      (execution.codex_passes, 'All agents completed'),
                                      (execution.claude_passes, '{}'),
                                      (execution.claude_passes, stream([{'type': 'result', 'subtype': 'success'}]))])
def test_missing_or_unsupported_trace_is_unavailable(parser, raw):
    with pytest.raises((ValueError, TypeError)):
        parser(raw)


def test_codex_missing_turn_completion_is_unavailable():
    with pytest.raises(ValueError, match='incomplete|unsupported'):
        execution.codex_passes(stream(codex_rows()[:-1]))


@pytest.mark.parametrize('receivers', [None, [], 'child', ['child-a', 'child-b'],
                                      ['root'], [''], [3], [{}]])
def test_unsupported_marked_codex_spawn_is_unavailable_after_completed_roles(proof, receivers):
    rows = codex_rows([*ROLES, 'Performance'])
    rows.insert(-1, {'type': 'item.completed', 'item': {
        'type': 'collab_tool_call', 'tool': 'spawn_agent', 'sender_thread_id': 'root',
        'status': 'completed', 'prompt': 'DK_REVIEW_PASS=Toolkit\nContinue the required check.',
        'receiver_thread_ids': receivers,
        'agents_states': {'child-a': {'status': 'running'}, 'child-b': {'status': 'running'}}}})
    reference = retained(proof, rows)
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, BINDING, proof[2], proof[1])
    assessment = json.loads(Path(reference['path']).read_text())['assessment']
    assert 'unsupported Codex specialist receiver state' in assessment['unknown']


def test_multiple_roles_in_one_native_child_are_not_independent_passes():
    rows = codex_rows(['Toolkit'])
    rows[2]['item']['prompt'] += '\nDK_REVIEW_PASS=Standards\nDK_REVIEW_PASS=Spec'
    with pytest.raises(ValueError, match='ambiguous specialist pass markers'):
        execution.codex_passes(stream(rows))


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('markers', ['Audit\nDK_REVIEW_PASS=Other', 'Audit\nDK_REVIEW_PASS=Audit'])
@pytest.mark.parametrize('correction_attempt', [False, True])
def test_ambiguous_marked_launch_is_unavailable_after_completed_roles(proof, host, markers, correction_attempt):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': host, 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = host
    make_rows = codex_rows if host == 'codex' else claude_rows
    rows = make_rows(['Audit'])
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record(host, stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    execution.verify(reference, binding, descriptor, path)

    extra = copy.deepcopy(rows[2 if host == 'codex' else 1])
    if host == 'codex':
        extra['item'].update(receiver_thread_ids=['extra-child'], prompt='DK_REVIEW_PASS=' + markers,
                             agents_states={'extra-child': {'status': 'running'}})
    else:
        extra['message']['content'][0].update(id='extra-call', input={'prompt': 'DK_REVIEW_PASS=' + markers})
    if correction_attempt:
        correction = make_rows([])
        correction.insert(-1, extra)
    else:
        rows.insert(-1, extra)
    original = {'findings': [{'message': 'Original finding retained.'}]}
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record(host, stream(rows), '', 0, 0)
        if correction_attempt:
            execution.record(host, stream(correction), '', 0, 1)
        reference = execution.finish(collector, original)
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, binding, descriptor, path, original)
    evidence = json.loads(Path(reference['path']).read_text())
    assert evidence['original_result'] == original
    assert 'ambiguous specialist pass markers' in evidence['assessment']['unknown']


@pytest.mark.parametrize('tool', ['Task', 'Agent'])
@pytest.mark.parametrize('identity', [{}, {'id': None}, {'id': ''}, {'id': 0},
                                      {'id': False}, {'id': []}, {'id': {}}])
def test_unsupported_marked_claude_launch_is_unavailable_after_completed_roles(proof, tool, identity):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': 'claude', 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = 'claude'
    rows = claude_rows(['Audit'], tool=tool)
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    execution.verify(reference, binding, descriptor, path)

    extra = copy.deepcopy(rows[1])
    block = extra['message']['content'][0]
    del block['id']
    block.update(identity)
    rows.insert(-1, extra)
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, binding, descriptor, path)
    assessment = json.loads(Path(reference['path']).read_text())['assessment']
    assert 'unsupported Claude specialist call identity' in assessment['unknown']


def malformed_execution_events():
    events = []
    for tool in ('spawn_agent', 'wait', 'send_input', 'close_agent'):
        item = {'type': 'collab_tool_call', 'tool': tool, 'sender_thread_id': 'root',
                'receiver_thread_ids': ['extra-child' if tool == 'spawn_agent' else 'child-0'],
                'status': 'completed', 'prompt': 'DK_REVIEW_PASS=Audit\nContinue inspection.',
                'agents_states': {}}
        for field, values in (
                ('status', [None, 'unsupported', 1]),
                ('agents_states', [None, [], 'unsupported', 1]),
                ('receiver_thread_ids', [None, [], 'child-0', [''], [1], ['root']])):
            for value in values:
                events.append(('codex', {'type': 'item.completed', 'item': {**item, field: value}}))
        for field in ('status', 'sender_thread_id'):
            missing = copy.deepcopy(item)
            del missing[field]
            events.append(('codex', {'type': 'item.completed', 'item': missing}))
        if tool != 'spawn_agent':
            missing = copy.deepcopy(item)
            del missing['agents_states']
            events.append(('codex', {'type': 'item.completed', 'item': missing}))
        events.append(('codex', {'type': 'item.completed', 'item': {
            **item, 'agents_states': {'child-0': {'status': 'unsupported'}}}}))
    for kind in ('assistant', 'user'):
        row = {'type': kind, 'session_id': 'session'}
        events.append(('claude', row))
        for message in (None, [], 'unsupported', {}, {'content': None}, {'content': {}},
                        {'content': 'unsupported'}, {'content': [None]}, {'content': ['unsupported']}):
            events.append(('claude', {**row, 'message': message}))
    for tool in ('Task', 'Agent', 'SendMessage'):
        block = {'type': 'tool_use', 'name': tool, 'id': 'extra-call'}
        for args in (None, [], 'unsupported'):
            events.append(('claude', {'type': 'assistant', 'session_id': 'session',
                'message': {'content': [{**block, 'input': args}]}}))
        if tool != 'SendMessage':
            for args in ({}, {'prompt': None}, {'prompt': []}):
                events.append(('claude', {'type': 'assistant', 'session_id': 'session',
                    'message': {'content': [{**block, 'input': args}]}}))
    result = {'type': 'tool_result', 'tool_use_id': 'call-0', 'is_error': False,
              'content': claude_result(0)}
    for field, values in (
            ('tool_use_id', [None, '', 0]),
            ('is_error', [None, 'false', 0]),
            ('content', [None, {}, [], [None], [{'type': 'text', 'text': None}]])):
        for value in values:
            events.append(('claude', {'type': 'user', 'session_id': 'session',
                'message': {'content': [{**result, field: value}]}}))
    events.append(('claude', {'type': 'user', 'session_id': 'session',
        'message': {'content': [{'type': 'tool_result', 'content': claude_result(0)}]}}))
    return events


@pytest.mark.parametrize('host,event', malformed_execution_events())
@pytest.mark.parametrize('correction_attempt', [False, True])
def test_malformed_execution_events_cannot_reuse_prior_completion(proof, host, event, correction_attempt):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': host, 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = host
    make_rows = codex_rows if host == 'codex' else claude_rows
    rows = make_rows(['Audit'])
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record(host, stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    execution.verify(reference, binding, descriptor, path)

    if correction_attempt:
        correction = make_rows(['Audit'])
        correction.insert(-1, event)
    else:
        rows.insert(-1, event)
    original = {'findings': [{'message': 'Original finding retained.'}]}
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record(host, stream(rows), '', 0, 0)
        if correction_attempt:
            execution.record(host, stream(correction), '', 0, 1)
        reference = execution.finish(collector, original)
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, binding, descriptor, path, original)
    evidence = json.loads(Path(reference['path']).read_text())
    assert evidence['original_result'] == original
    assert evidence['assessment']['status'] == 'unavailable'
    assert evidence['assessment']['unknown']


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_supported_metadata_and_excluded_events_preserve_complete_evidence(proof, host):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': host, 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = host
    if host == 'codex':
        rows = codex_rows(['Audit'])
        rows.insert(-1, {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Review complete.', 'optional_metadata': {'x': 1}}})
        rows.insert(-1, {'type': 'item.completed', 'item': {
            'type': 'collab_tool_call', 'sender_thread_id': 'foreign-root', 'status': None}})
        rows[2]['item']['optional_metadata'] = {'x': 1}
    else:
        rows = claude_rows(['Audit'])
        rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {'content': [
            {'type': 'thinking', 'thinking': 'Reviewing the evidence.'},
            {'type': 'text', 'text': 'Review complete.', 'optional_metadata': {'x': 1}},
            {'type': 'tool_use', 'name': 'Read', 'id': 'read', 'input': {'file_path': 'source.py'}}]}})
        rows.insert(-1, {'type': 'assistant', 'session_id': 'foreign-session', 'message': None})
        rows.insert(-1, {'type': 'user', 'session_id': 'session', 'parent_tool_use_id': 'nested', 'message': None})
        rows.insert(-1, {'type': 'user', 'session_id': 'session', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'read', 'content': {'unrelated': 'metadata'}}]}})
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record(host, stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    execution.verify(reference, binding, descriptor, path)


def test_claude_failed_child_result_cannot_reuse_earlier_completion(proof):
    repository, path, descriptor = proof
    binding = {'skill': 'fixture:review', 'host': 'claude', 'nested_review_roles': ['Audit']}
    descriptor['execution']['executor'] = 'claude'
    rows = claude_rows(['Audit'])
    rows.insert(-1, {'type': 'user', 'session_id': 'session', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': 'call-0', 'is_error': True, 'content': 'Child failed.'}]}})
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(rows), '', 0, 0)
        reference = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution missing:.*Audit'):
        execution.verify(reference, binding, descriptor, path)
    assessment = json.loads(Path(reference['path']).read_text())['assessment']
    assert assessment['unknown'] == []
    assert assessment['completed'] == []


def test_retained_valid_proof_binds_findings_and_private_transport(proof):
    result = {'findings': [{'message': 'Original finding retained.'}]}
    ref = retained(proof, result=result)
    _, path, descriptor = proof
    execution.verify(ref, BINDING, descriptor, path, result)
    evidence = json.loads(Path(ref['path']).read_text())
    attempt = json.loads(Path(evidence['attempts'][0]['path']).read_text())
    assert attempt['stderr'] == 'stderr kept'
    assert attempt['binding']['version'] == '1.0.7'
    assert attempt['binding']['head_sha'] == 'head-a'
    assert evidence['assessment']['status'] == 'complete'
    assert Path(ref['path']).stat().st_mode & 0o777 == 0o600
    assert not execution.active()
    assert evidence['original_result'] == result


@pytest.mark.parametrize('roles,expected', [(['Toolkit', 'Standards'], 'Spec'), ([], 'Toolkit')])
def test_clean_json_without_required_native_pass_is_missing(proof, roles, expected):
    ref = retained(proof, codex_rows([*roles, 'Performance']))
    _, path, descriptor = proof
    with pytest.raises(ValueError, match='execution missing:.*' + expected):
        execution.verify(ref, BINDING, descriptor, path, {'findings': []})
    evidence = json.loads(Path(ref['path']).read_text())
    assert evidence['original_result'] == {'findings': []}
    assert evidence['assessment']['status'] == 'missing'


def test_absent_native_evidence_is_unavailable_not_missing(proof):
    repository, path, descriptor = proof
    with execution.collect(BINDING, descriptor, path, repository) as collector:
        ref = execution.finish(collector, {'findings': []})
    assert json.loads(Path(ref['path']).read_text())['assessment']['status'] == 'unavailable'
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(ref, BINDING, descriptor, path)
    with pytest.raises(ValueError, match='historical clean JSON'):
        execution.verify(None, BINDING, descriptor, path)


@pytest.mark.parametrize('returncode', [1, None, -9])
def test_failed_provider_does_not_supply_completed_coverage(proof, returncode):
    ref = retained(proof, returncode=returncode)
    _, path, descriptor = proof
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(ref, BINDING, descriptor, path)


def test_evidence_from_wrong_executor_cannot_satisfy_binding(proof):
    with pytest.raises(ValueError, match='identity|executor|unavailable'):
        retained(proof, claude_rows(), host='claude')


@pytest.mark.parametrize('field', ['version', 'bundle_digest', 'procedure', 'executor'])
def test_changed_installed_skill_or_executor_invalidates_proof(proof, field):
    ref = retained(proof)
    _, path, descriptor = proof
    descriptor['execution'][field] = 'changed'
    with pytest.raises(ValueError, match='identity mismatch'):
        execution.verify(ref, BINDING, descriptor, path)


@pytest.mark.parametrize('field', ['head_sha', 'base_sha', 'response_budget', 'pr_url'])
def test_changed_pinned_scope_or_review_session_invalidates_proof(proof, field):
    ref = retained(proof)
    _, path, descriptor = proof
    handoff = json.loads(path.read_text())
    handoff[field] = 'changed'
    path.write_text(json.dumps(handoff))
    with pytest.raises(ValueError, match='identity mismatch'):
        execution.verify(ref, BINDING, descriptor, path)


def test_edited_original_result_is_rejected(proof):
    ref = retained(proof)
    _, path, descriptor = proof
    with pytest.raises(ValueError, match='result mismatch'):
        execution.verify(ref, BINDING, descriptor, path, {'findings': ['replacement']})


def test_tampered_summary_and_native_transport_are_both_rejected(proof):
    ref = retained(proof)
    _, path, descriptor = proof
    summary = Path(ref['path'])
    summary.write_text(summary.read_text() + ' ')
    with pytest.raises(ValueError, match='evidence changed'):
        execution.verify(ref, BINDING, descriptor, path)
    ref['sha256'] = hashlib.sha256(summary.read_bytes()).hexdigest()
    evidence = json.loads(summary.read_text())
    transport = Path(evidence['attempts'][0]['path'])
    transport.write_text(transport.read_text() + ' ')
    with pytest.raises(ValueError, match='evidence changed'):
        execution.verify(ref, BINDING, descriptor, path)


def test_correctly_rehashed_foreign_invocation_is_still_rejected(proof):
    ref = retained(proof)
    _, path, descriptor = proof
    summary = Path(ref['path'])
    evidence = json.loads(summary.read_text())
    attempt_ref = evidence['attempts'][0]
    transport = Path(attempt_ref['path'])
    attempt = json.loads(transport.read_text())
    attempt['invocation'] = 'older-invocation'
    transport.write_text(json.dumps(attempt))
    attempt_ref['sha256'] = hashlib.sha256(transport.read_bytes()).hexdigest()
    summary.write_text(json.dumps(evidence))
    ref['sha256'] = hashlib.sha256(summary.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='identity mismatch'):
        execution.verify(ref, BINDING, descriptor, path)


def test_conditional_performance_is_not_an_unconditional_ad_requirement(proof):
    _, path, _ = proof
    assert execution.requirements(BINDING, json.loads(path.read_text())) == ROLES


def test_declared_custom_roles_require_native_execution(proof):
    _, path, _ = proof
    binding = {'command': ['custom', '{handoff}'], 'nested_review_roles': ['Audit']}
    assert execution.requirements(binding, json.loads(path.read_text())) == ['Audit']
    with pytest.raises(ValueError, match='evidence unavailable'):
        execution.verify(None, binding, {}, path)


def test_providers_without_nested_obligations_keep_existing_result_contract(proof):
    repository, path, descriptor = proof
    with execution.collect({'command': ['custom', '{handoff}']}, descriptor, path, repository) as collector:
        assert collector is None
        assert execution.finish(collector, {'findings': []}) is None
    execution.verify(None, {'command': ['custom', '{handoff}']}, descriptor, path)


def test_collector_scopes_do_not_leak_and_restore_parent_after_failure(proof):
    repository, path, descriptor = proof
    with execution.collect(BINDING, descriptor, path, repository) as outer:
        with pytest.raises(RuntimeError, match='stop'):
            with execution.collect(BINDING, descriptor, path, repository) as inner:
                assert inner['invocation'] != outer['invocation']
                assert execution.active() is inner
                raise RuntimeError('stop')
        assert execution.active() is outer
    assert execution.active() is None


def test_unfinished_second_child_cannot_hide_behind_completed_child_of_same_role(proof):
    rows = codex_rows([*ROLES, 'Performance'])
    rows.insert(-1, {'type': 'item.completed', 'item': {
        'id': 'extra-spawn', 'type': 'collab_tool_call', 'tool': 'spawn_agent',
        'sender_thread_id': 'root', 'receiver_thread_ids': ['extra-child'],
        'prompt': 'DK_REVIEW_PASS=Toolkit\nAdditional inspection.',
        'agents_states': {'extra-child': {'status': 'running', 'message': None}}, 'status': 'completed'}})
    ref = retained(proof, rows)
    _, path, descriptor = proof
    with pytest.raises(ValueError, match='missing|incomplete|unfinished'):
        execution.verify(ref, BINDING, descriptor, path)


def test_correction_attempt_cannot_hide_an_unfinished_launched_pass(proof):
    repository, path, descriptor = proof
    with execution.collect(BINDING, descriptor, path, repository) as collector:
        execution.record('codex', stream(codex_rows([*ROLES, 'Performance'])), '', 0, 0)
        execution.record('codex', stream(codex_rows(['Toolkit'], state='running')), '', 0, 1)
        ref = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='missing|incomplete|unfinished'):
        execution.verify(ref, BINDING, descriptor, path)


def applicability_reference(proof, *, mutate=None, required=False, source_role='Toolkit',
                            parent_only=False, complete_performance=False, host='codex'):
    repository, path, descriptor = proof
    binding = {**BINDING, 'host': host}
    descriptor = copy.deepcopy(descriptor)
    descriptor['execution']['executor'] = host
    with execution.collect(binding, descriptor, path, repository) as collector:
        identity = collector['binding']
        value = {'head_sha': identity['head_sha'], 'base_sha': identity['base_sha'],
                 'invocation': collector['invocation'], 'descriptor_sha256': identity['descriptor_sha256'],
                 'roles': {'Performance': {'required': required, 'reason': 'Pinned change is a small configuration edit; no hot path or performance request.'}}}
        if mutate:
            mutate(value)
        report = 'Independent scope review.\nDK_REVIEW_APPLICABILITY_JSON=' + json.dumps(value)
        roles = [*ROLES, 'Performance'] if complete_performance else ROLES
        rows = codex_rows(roles) if host == 'codex' else claude_rows(roles)
        if parent_only:
            if host == 'codex':
                rows.insert(-1, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': report}})
            else:
                rows.insert(-1, {'type': 'assistant', 'session_id': 'session', 'message': {
                    'content': [{'type': 'text', 'text': report}]}})
        elif host == 'codex':
            number = roles.index(source_role)
            rows[3 + number * 2]['item']['agents_states'][f'child-{number}']['message'] = report
        else:
            number = roles.index(source_role)
            rows[2 + number * 2]['message']['content'][0]['content'] = claude_result(number, report)
        execution.record(host, stream(rows), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    return ref, binding, descriptor


@pytest.mark.parametrize('host', ['codex'])
def test_independent_primary_can_establish_conditional_inapplicability(proof, host):
    ref, binding, descriptor = applicability_reference(proof, host=host)
    execution.verify(ref, binding, descriptor, proof[1], {'findings': []})
    assessment = json.loads(Path(ref['path']).read_text())['assessment']
    assert assessment['status'] == 'complete'
    assert assessment['applicability_assessments'][0]['roles']['Performance']['required'] is False
    assert assessment['completed'] == sorted(ROLES)


@pytest.mark.parametrize('host', ['codex'])
def test_primary_required_conditional_role_still_needs_actual_execution(proof, host):
    ref, binding, descriptor = applicability_reference(proof, required=True, host=host)
    with pytest.raises(ValueError, match='execution missing:.*Performance'):
        execution.verify(ref, binding, descriptor, proof[1])
    ref, binding, descriptor = applicability_reference(proof, required=True, host=host, complete_performance=True)
    execution.verify(ref, binding, descriptor, proof[1])


@pytest.mark.parametrize('field', ['head_sha', 'base_sha', 'invocation', 'descriptor_sha256'])
def test_stale_scope_or_invocation_cannot_establish_conditional_inapplicability(proof, field):
    ref, binding, descriptor = applicability_reference(proof, mutate=lambda value: value.update({field: 'stale'}))
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, binding, descriptor, proof[1])


@pytest.mark.parametrize('mutate', [
    lambda value: value['roles']['Performance'].update(reason=''),
    lambda value: value['roles']['Performance'].update(reason='   '),
    lambda value: value['roles']['Performance'].update(required='false'),
    lambda value: value['roles'].clear(),
    lambda value: value['roles'].update(Other={'required': False, 'reason': 'irrelevant'}),
    lambda value: value.update(extra='unsupported'),
    lambda value: value['roles']['Performance'].update(extra='unsupported'),
])
def test_incomplete_or_malformed_applicability_is_unknown(proof, mutate):
    ref, binding, descriptor = applicability_reference(proof, mutate=mutate)
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, binding, descriptor, proof[1])


@pytest.mark.parametrize('host', ['codex'])
@pytest.mark.parametrize('parent_only,source_role', [(True, 'Toolkit'), (False, 'Standards')])
def test_parent_or_nonprimary_report_cannot_waive_conditional_pass(proof, parent_only, source_role, host):
    ref, binding, descriptor = applicability_reference(proof, parent_only=parent_only, source_role=source_role, host=host)
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, binding, descriptor, proof[1])


def test_conditional_gate_never_infers_inapplicability_from_clean_json(proof):
    ref = retained(proof, codex_rows())
    _, path, descriptor = proof
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, BINDING, descriptor, path, {'findings': []})


def test_all_conditional_passes_can_complete_without_an_applicability_report(proof):
    ref = retained(proof)
    _, path, descriptor = proof
    execution.verify(ref, BINDING, descriptor, path)
    assessment = json.loads(Path(ref['path']).read_text())['assessment']
    assert assessment['applicability_assessments'] == []
    assert assessment['status'] == 'complete'


def test_later_reactivation_of_native_child_invalidates_earlier_completion(proof):
    rows = codex_rows([*ROLES, 'Performance'])
    rows.insert(-1, {'type': 'item.completed', 'item': {
        'id': 'followup', 'type': 'collab_tool_call', 'tool': 'send_input',
        'sender_thread_id': 'root', 'receiver_thread_ids': ['child-0'],
        'prompt': 'Finish the remaining required check.',
        'agents_states': {'child-0': {'status': 'running', 'message': None}}, 'status': 'completed'}})
    ref = retained(proof, rows)
    with pytest.raises(ValueError, match='missing|unfinished|incomplete'):
        execution.verify(ref, BINDING, proof[2], proof[1])


@pytest.mark.parametrize('states', ['absent', None, [], 'unsupported', {}, {'child-0': None},
                                  {'other-child': {'status': 'running'}}])
def test_codex_unsupported_reactivation_state_is_unavailable(proof, states):
    rows = codex_rows([*ROLES, 'Performance'])
    item = {'type': 'collab_tool_call', 'tool': 'send_input', 'sender_thread_id': 'root',
            'receiver_thread_ids': ['child-0'], 'status': 'completed', 'prompt': 'Finish the required check.'}
    if states != 'absent':
        item['agents_states'] = states
    rows.insert(-1, {'type': 'item.completed', 'item': item})
    reference = retained(proof, rows)
    with pytest.raises(ValueError, match='execution unavailable'):
        execution.verify(reference, BINDING, proof[2], proof[1])
    assessment = json.loads(Path(reference['path']).read_text())['assessment']
    assert 'unsupported Codex reactivation state' in assessment['unknown']


def test_closing_completed_native_child_preserves_its_completed_review(proof):
    rows = codex_rows([*ROLES, 'Performance'])
    rows.insert(-1, {'type': 'item.completed', 'item': {
        'id': 'close', 'type': 'collab_tool_call', 'tool': 'close_agent',
        'sender_thread_id': 'root', 'receiver_thread_ids': ['child-0'], 'prompt': None,
        'agents_states': {'child-0': {'status': 'shutdown', 'message': None}}, 'status': 'completed'}})
    ref = retained(proof, rows)
    execution.verify(ref, BINDING, proof[2], proof[1])


@pytest.mark.parametrize('drift', ['version', 'changed-source', 'missing-source', 'extra-source'])
def test_unknown_installed_method_cannot_claim_complete_even_when_all_known_roles_ran(proof, drift):
    _, path, descriptor = proof
    if drift == 'version':
        descriptor['execution']['version'] = '999.0.0'
    elif drift == 'changed-source':
        descriptor['sources'][0]['sha256'] = 'same-version-different-procedure'
    elif drift == 'missing-source':
        descriptor['sources'].pop()
    else:
        descriptor['sources'].append({'path': '/retained/new-required-pass.md', 'sha256': 'new-obligation'})
    ref = retained(proof)
    with pytest.raises(ValueError, match='execution unavailable:.*unknown installed method'):
        execution.verify(ref, BINDING, descriptor, path)
    assessment = json.loads(Path(ref['path']).read_text())['assessment']
    assert assessment['completed'] == sorted([*ROLES, 'Performance'])
    assert assessment['status'] == 'unavailable'


def test_toolkit_all_applicable_specialists_require_real_native_completions(proof):
    repository, path, descriptor = proof
    binding = {'skill': 'pr-review-toolkit:review-pr', 'host': 'claude'}
    profile = execution._METHODS[(binding['skill'], 'claude')]
    descriptor['execution'].update(executor='claude', version=profile['version'])
    descriptor['sources'] = [{'path': '/retained/' + relative, 'sha256': sha}
                             for relative, sha in profile['sources'].items()]
    primary, conditional = execution.conditional(binding)
    with execution.collect(binding, descriptor, path, repository) as collector:
        identity = collector['binding']
        assessment = {'head_sha': identity['head_sha'], 'base_sha': identity['base_sha'],
                      'invocation': collector['invocation'], 'descriptor_sha256': identity['descriptor_sha256'],
                      'roles': {role: {'required': role == 'pr-test-analyzer',
                                       'reason': 'Pinned test changes need specialist review.' if role == 'pr-test-analyzer'
                                       else 'No applicable change under the pinned method criterion.'}
                                for role in conditional}}
        rows = claude_rows([primary])
        rows[2]['message']['content'][0]['content'] = claude_result(0, 'DK_REVIEW_APPLICABILITY_JSON=' + json.dumps(assessment))
        execution.record('claude', stream(rows), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution missing:.*pr-test-analyzer'):
        execution.verify(ref, binding, descriptor, path)


def test_toolkit_unknown_applicability_blocks_without_all_potential_specialists(proof):
    repository, path, descriptor = proof
    binding = {'skill': 'pr-review-toolkit:review-pr', 'host': 'claude'}
    profile = execution._METHODS[(binding['skill'], 'claude')]
    descriptor['execution'].update(executor='claude', version=profile['version'])
    descriptor['sources'] = [{'path': '/retained/' + relative, 'sha256': sha}
                             for relative, sha in profile['sources'].items()]
    primary, conditional = execution.conditional(binding)
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(claude_rows([primary])), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, binding, descriptor, path)
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(claude_rows([primary, *conditional])), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    execution.verify(ref, binding, descriptor, path)



def test_claude_ad_method_requires_three_agents_without_codex_performance_condition(proof):
    repository, path, descriptor = proof
    binding = {**BINDING, 'host': 'claude'}
    descriptor['execution']['executor'] = 'claude'
    assert execution.conditional(binding) == (None, [])
    with execution.collect(binding, descriptor, path, repository) as collector:
        execution.record('claude', stream(claude_rows(ROLES)), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    execution.verify(ref, binding, descriptor, path)


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('spec_available', [False, True])
def test_matpocock_no_spec_skip_needs_independent_bound_confirmation(proof, host, spec_available):
    repository, path, descriptor = proof
    binding = {'skill': 'code-review-matpocock:code-review-matpocock', 'host': host}
    profile = execution._METHODS[(binding['skill'], host)]
    descriptor['execution'].update(executor=host, version=profile['version'])
    descriptor['sources'] = [{'path': '/retained/' + relative, 'sha256': sha}
                             for relative, sha in profile['sources'].items()]
    assert execution.requirements(binding, {}) == ['Standards']
    assert execution.conditional(binding) == ('Standards', ['Spec'])
    with execution.collect(binding, descriptor, path, repository) as collector:
        identity = collector['binding']
        report = {'head_sha': identity['head_sha'], 'base_sha': identity['base_sha'],
                  'invocation': collector['invocation'], 'descriptor_sha256': identity['descriptor_sha256'],
                  'roles': {'Spec': {'required': spec_available, 'reason':
                    'Issue supplies the specification.' if spec_available else
                    'Pinned user instruction explicitly confirms there is no specification.'}}}
        rows = codex_rows(['Standards']) if host == 'codex' else claude_rows(['Standards'])
        text = 'DK_REVIEW_APPLICABILITY_JSON=' + json.dumps(report)
        if host == 'codex':
            rows[3]['item']['agents_states']['child-0']['message'] = text
        else:
            rows[2]['message']['content'][0]['content'] = claude_result(0, text)
        execution.record(host, stream(rows), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    if spec_available:
        with pytest.raises(ValueError, match='execution missing:.*Spec'):
            execution.verify(ref, binding, descriptor, path)
    else:
        execution.verify(ref, binding, descriptor, path)


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_matpocock_missing_spec_alone_cannot_imply_permission_to_skip(proof, host):
    repository, path, descriptor = proof
    binding = {'skill': 'code-review-matpocock:code-review-matpocock', 'host': host}
    profile = execution._METHODS[(binding['skill'], host)]
    descriptor['execution'].update(executor=host, version=profile['version'])
    descriptor['sources'] = [{'path': '/retained/' + relative, 'sha256': sha}
                             for relative, sha in profile['sources'].items()]
    with execution.collect(binding, descriptor, path, repository) as collector:
        rows = codex_rows(['Standards']) if host == 'codex' else claude_rows(['Standards'])
        execution.record(host, stream(rows), '', 0, 0)
        ref = execution.finish(collector, {'findings': []})
    with pytest.raises(ValueError, match='execution unavailable:.*conditional applicability'):
        execution.verify(ref, binding, descriptor, path)
