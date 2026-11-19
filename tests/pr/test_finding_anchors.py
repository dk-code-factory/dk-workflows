"""Offline original-source identity, revision, arrival and planner boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import finding_anchors as anchors
import finding_followups as followups
import pr_evidence as evidence

PR = 'https://github.com/owner/repo/pull/7'
A, B, C = (c * 40 for c in 'abc')
OLD = '11111111-1111-4111-8111-111111111111'
NEW = 'cf5754d9-20f4-45f1-8283-9dc97ed065bf'
T0, T1, T2, T3 = ('2026-11-18T08:' + value + 'Z' for value in ('00:00', '30:14', '30:44', '34:19'))


def block(finding=OLD, title='Existing defect', number=1):
    return (f'<details>\n<summary> {number}. {title} <code>🐞 Bug</code></summary>\n'
            f'> <details><summary>Description</summary><pre>Return **empty** results.</pre></details>\n'
            f'[Dismiss](https://app.example.qodo.ai/findings/{finding}?action=dismiss) | '
            f'[View](https://app.example.qodo.ai/findings/{finding})\n</details>')


def summary(*blocks, head=A):
    return '<h3>Code Review by Qodo</h3>\n' + '\n'.join(blocks) + f'\n<!-- {PR.rsplit("/pull/", 1)[0]}/commit/{head} -->'


def node(*bodies):
    return {'id': 'IC_fixture', 'body': bodies[-1], 'createdAt': T0,
            'lastEditedAt': [T0, T2, T3][len(bodies) - 1], 'includesCreatedEdit': True,
            'edits': [{'id': 'UCE_' + str(i), 'editedAt': time, 'deletedAt': None, 'diff': body}
                      for i, (body, time) in enumerate(zip(bodies, [T0, T2, T3]))]}


def source(kind='issue_comment', head=None):
    return {'reference': 'issuecomment-9', 'id': 9, 'nodeId': 'IC_fixture', 'kind': kind,
            'reviewCommit': head, 'createdAt': T0, 'pr': PR, 'originalInline': True}


def pushes():
    return [{'id': 'PUSH_A', 'createdAt': T0, 'afterCommit': {'oid': A}},
            {'id': 'PUSH_B', 'createdAt': T1, 'beforeCommit': {'oid': A}, 'afterCommit': {'oid': B}}]


def resolve(value, **kwargs):
    return anchors.resolve(kwargs.get('source', source()), value['body'], value, kwargs.get('pushes', pushes()))


def test_issue_228_new_finding_uses_introduction_after_force_push():
    value = node(summary(block()), summary(block(number=2), head=B),
                 summary(block(NEW, 'Failed keyspace lookups grow session memory'), block(number=2), head=B))
    result = resolve(value)
    old, new = (result['qodo:' + finding] for finding in (OLD, NEW))
    assert (old['anchor'], old['revision'], old['requestedAt']) == (A, 'UCE_0', T0)
    assert (new['anchor'], new['revision'], new['requestedAt']) == (B, 'UCE_2', T3)
    assert new['push'] == 'PUSH_B' and new['sourceId'] == 9
    assert new['findingId'] == 'qodo:' + NEW


def test_footer_number_formatting_and_unrelated_finding_edits_preserve_original():
    original = summary(block())
    formatted = summary(block(number=7).replace('Existing defect', '<b>Existing defect</b>'), block(NEW), head=B)
    result = resolve(node(original, formatted))
    assert result['qodo:' + OLD]['anchor'] == A
    assert result['qodo:' + OLD]['revision'] == 'UCE_0'
    assert result['qodo:' + NEW]['anchor'] == B


@pytest.mark.parametrize('change', ['deleted', 'missing_original', 'missing_edit', 'body', 'timestamp', 'duplicate', 'node_id'])
def test_missing_deleted_or_inconsistent_revision_history_is_unknown(change):
    value = node(summary(block()), summary(block(), head=B))
    if change == 'deleted':
        value['edits'][0].update(deletedAt=T1, diff=None)
    elif change == 'missing_original':
        value['includesCreatedEdit'] = False
    elif change == 'missing_edit':
        value['edits'].pop(0)
    elif change == 'body':
        value['edits'][-1]['diff'] = 'different body'
    elif change == 'timestamp':
        value['edits'][-1]['editedAt'] = T0
    elif change == 'node_id':
        value['id'] = 'IC_other'
    else:
        value['edits'].append(deepcopy(value['edits'][0]))
    result = resolve(value)['qodo:' + OLD]
    assert result['status'] == 'unknown' and result['anchor'] is None
    assert result['reason']


@pytest.mark.parametrize('kind', ['review', 'inline_comment'])
def test_immutable_review_commit_resolves_original_content_without_edit_history(kind):
    value = {'id': 'IC_fixture', 'body': 'Fix empty input.', 'createdAt': T0,
             'lastEditedAt': None, 'includesCreatedEdit': False, 'edits': []}
    result = resolve(value, source=source(kind, A))['issuecomment-9']
    assert result['status'] == 'resolved' and result['anchor'] == A
    assert result['revision'] == 'IC_fixture'


def test_added_unstructured_review_request_cannot_inherit_original_review_commit():
    result = resolve(node('Original defect.', 'Original defect. New defect.'), source=source('review', A))
    assert result['issuecomment-9']['status'] == 'unknown'


def test_inline_reply_cannot_reuse_old_review_anchor():
    src = source('inline_comment', A)
    src['originalInline'] = False
    assert resolve(node('Please fix.'), source=src)['issuecomment-9']['status'] == 'unknown'


@pytest.mark.parametrize('body', ['A plain bot summary.', 'See https://example.test/findings/' + OLD,
                                summary(block()).replace('1.', 'Finding:')])
def test_unsupported_formats_never_get_agent_assigned_anchors(body):
    assert all(value['status'] == 'unknown' for value in resolve(node(body)).values())


@pytest.mark.parametrize('change', ['substance', 'removed_and_readded', 'duplicate_block'])
def test_ambiguous_changes_to_stable_id_do_not_reset_its_age(change):
    original = summary(block())
    if change == 'substance':
        value = node(original, summary(block().replace('empty', 'nonempty'), head=B))
    elif change == 'removed_and_readded':
        value = node(original, summary(block(NEW), head=B), summary(block(), head=B))
    else:
        value = node(original, summary(block(), block(), head=B))
    assert resolve(value)['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('events', [[], [{'id': 'too-late', 'createdAt': T3, 'afterCommit': {'oid': A}}],
                                  [{'id': 'deleted-head', 'createdAt': T0, 'afterCommit': None}]])
def test_no_commit_timestamps_or_container_dates_substitute_for_push_arrival(events):
    result = resolve(node(summary(block())), pushes=events)['qodo:' + OLD]
    assert result['status'] == 'unknown' and result['anchor'] is None


def test_code_names_in_summary_are_substance_not_status_badges():
    before = summary(block(title='Fix <code>old_name</code>'))
    after = summary(block(title='Fix <code>new_name</code>'), head=B)
    assert resolve(node(before, after))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('code', ['<pre>use("a  b")</pre>', '<code>use("a  b")</code>',
                                '`use("a  b")`', '```python\nuse("a  b")\n```',
                                '    use("a  b")\n'])
def test_significant_code_whitespace_never_inherits_original_anchor(code):
    before = summary(block().replace('<pre>Return **empty** results.</pre>', code))
    after = before.replace('a  b', 'a b').replace('/commit/' + A, '/commit/' + B)
    assert resolve(node(before, after))['qodo:' + OLD]['status'] == 'unknown'


def test_formal_review_indentation_change_has_unknown_anchor():
    before = '```python\nif enabled:\n    reject()\nreturn value\n```'
    after = before.replace('\nreturn value', '\n    return value')
    result = resolve(node(before, after), source=source('review', A))
    assert result['issuecomment-9']['status'] == 'unknown'


@pytest.mark.parametrize('before,after', [
    ('>     if enabled:\n>         reject()\n>     return value',
     '>     if enabled:\n>         reject()\n>         return value'),
    ('~~~yaml\nroot:\n  child: true', '~~~yaml\nroot:\nchild: true'),
    ('~~~python\nif enabled:\n  reject()\nreturn value\n~~~~',
     '~~~python\nif enabled:\n  reject()\n  return value\n~~~~'),
    ('-     root:\n      child: true', '-     root:\n    child: true'),
    ('>\tif enabled:\n>\t\treturn value', '>\tif enabled:\n>\treturn value'),
])
def test_unsupported_code_layouts_keep_substantive_indentation(before, after):
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'
    first = summary(block().replace('<pre>Return **empty** results.</pre>', before))
    second = summary(block().replace('<pre>Return **empty** results.</pre>', after), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('wrapper', ['<pre>{}</pre>', '<code>{}</code>', '```html\n{}\n```',
                                   '> {}', '    {}', '{}\nReport text after the comment.',
                                   '~~~html\n{}', '<pre>\n{}'])
def test_commit_comment_in_finding_content_cannot_replace_report_footer(wrapper):
    citation = wrapper.format(f'<!-- https://github.com/owner/repo/commit/{A} -->')
    body = summary(block()).split('\n<!--', 1)[0]
    assert resolve(node(body + '\n' + citation))['qodo:' + OLD]['status'] == 'unknown'


def test_code_citation_with_real_standalone_footer_keeps_report_binding():
    code = f'<pre><!-- https://github.com/owner/repo/commit/{A} --></pre>'
    body = summary(block().replace('<pre>Return **empty** results.</pre>', code))
    assert resolve(node(body))['qodo:' + OLD]['anchor'] == A


def test_report_footer_can_precede_the_recognized_qodo_attribution():
    body = summary(block()) + '\n\n*Powered by [Qodo](https://www.qodo.ai)*\n'
    assert resolve(node(body))['qodo:' + OLD]['anchor'] == A


@pytest.mark.parametrize('quote', ["'", '&#39;', '&apos;'])
def test_prose_apostrophe_cannot_hide_a_later_literal_change(quote):
    before = f'Don{quote}t use normalize({quote}a  b{quote}).'
    after = before.replace('a  b', 'a b')
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'
    first, second = summary(block(title=before)), summary(block(title=after), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


def test_numbering_only_edit_preserves_a_quoted_finding_title():
    title = "Don't use normalize('a  b')."
    value = node(summary(block(title=title)), summary(block(title=title, number=9), head=B))
    assert resolve(value)['qodo:' + OLD]['anchor'] == A


@pytest.mark.parametrize('prefix', [' \t', '  \t', '   \t'])
def test_mixed_space_tab_indentation_cannot_hide_control_flow_changes(prefix):
    before = f'{prefix}if enabled:\n{prefix}    reject()\n{prefix}return value'
    after = before.replace(prefix + 'return', prefix + '    return')
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'
    first = summary(block().replace('<pre>Return **empty** results.</pre>', '\n' + before + '\n'))
    second = summary(block().replace('<pre>Return **empty** results.</pre>', '\n' + after + '\n'), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('code', ['```html\n</details><details>\n```',
                                '<pre></details><details></pre>', '<code></details><details></code>'])
def test_details_tokens_inside_code_cannot_truncate_the_compared_finding(code):
    finding = (f'<details><summary>1. Existing defect</summary>\n'
               f'[View](https://app.example.qodo.ai/findings/{OLD})\n\n'
               f'{code}\n\nPlease fix the original request.\n</details>')
    first = summary(finding)
    second = summary(finding.replace('original request', 'new substantive request'), head=B)
    assert resolve(node(first))['qodo:' + OLD]['anchor'] == A
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('quoted', ['<!-- </details><details> -->',
                                  '<span title="</details><details>">text</span>'])
def test_html_comments_and_attributes_do_not_delimit_findings(quoted):
    finding = (f'<details><summary>1. Existing defect</summary>\n'
               f'[View](https://app.example.qodo.ai/findings/{OLD})\n{quoted}\n'
               f'Please fix the original request.\n</details>')
    first = summary(finding)
    second = summary(finding.replace('original request', 'new substantive request'), head=B)
    assert resolve(node(first))['qodo:' + OLD]['anchor'] == A
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('declaration', ['<![bogus]>', '<!DOCTYPE html>', '<![CDATA[</details><details>]]>'])
def test_unsupported_html_declaration_remains_unknown(declaration):
    body = summary(block().replace('<pre>Return **empty** results.</pre>', declaration))
    assert resolve(node(body))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('quoted', ['>     </details><details>', '-     </details><details>'])
def test_unsupported_indented_containers_cannot_delimit_findings(quoted):
    body = summary(block().replace('<pre>Return **empty** results.</pre>', '\n' + quoted + '\n'))
    assert resolve(node(body))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('prefix', ['<!-- quoted source\n', '<blockquote>\n', '<blockquote/>\n'])
def test_quoted_footer_comment_is_not_report_metadata(prefix):
    body = summary(block()).replace('\n<!--', '\n' + prefix + '<!--')
    assert resolve(node(body))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('tag', ['pre', 'code', 'script', 'style', 'textarea', 'xmp'])
def test_unterminated_raw_code_attribute_cannot_expose_report_structure(tag):
    body = f'<{tag} title="></{tag}>\n' + summary(block())
    result = resolve(node(body))['qodo:' + OLD]
    assert result['status'] == 'unknown' and result['anchor'] is None


@pytest.mark.parametrize('quote', ['"', "'"])
def test_quoted_raw_code_attribute_preserves_supported_finding(quote):
    finding = block().replace('<pre>', f'<pre title={quote}>{quote}>')
    result = resolve(node(summary(finding), summary(finding, head=B)))['qodo:' + OLD]
    assert result['status'] == 'resolved' and result['anchor'] == A


def test_fenced_summary_does_not_turn_later_code_into_status_metadata():
    before = '```html\n<summary>\n```\nChange the label to <code>🐞 Bug</code>.'
    after = before.replace('🐞 Bug', '⭐ New')
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'
    first = summary(block().replace('<pre>Return **empty** results.</pre>', before))
    second = summary(block().replace('<pre>Return **empty** results.</pre>', after), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('literal', ['Set <code>🐞 Bug</code> as the label', 'Set label to <code>🐞 Bug</code>'])
def test_only_trailing_outer_summary_badges_are_presentation(literal):
    title = "Don't use normalize('a  b')."
    first = summary(block(title=title))
    second = summary(block(title=title, number=9).replace('</summary>', ' <code>⭐ New</code></summary>', 1), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['anchor'] == A
    first = summary(block(title=literal))
    second = first.replace(literal, literal.replace('🐞 Bug', '⭐ New'))
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


def test_escaped_details_tokens_cannot_truncate_a_substantive_request():
    finding = (f'<details><summary>1. Fix</summary>\n[View](https://app.example.qodo.ai/findings/{OLD})\n\n'
               + r'\</details>\<details>' + '\n\nFix the original request.\n</details>')
    first, second = summary(finding), summary(finding.replace('original request', 'new request'), head=B)
    assert resolve(node(first, second))['qodo:' + OLD]['status'] == 'unknown'


@pytest.mark.parametrize('before', ["Use normalize('a `b` c  d `e` f').",
                                  '~~~python\n`a`\n  return value\n`b`\n'])
def test_ambiguous_content_between_code_spans_is_not_whitespace_normalized(before):
    after = before.replace('c  d', 'c d').replace('  return', 'return')
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'


@pytest.mark.parametrize('before,after', [('Preserve a ** b ** c.', 'Preserve a b c.'),
                                       (r'Use \**literal**.', r'Use \literal.')])
def test_nonpresentation_asterisks_are_substantive(before, after):
    assert resolve(node(before, after), source=source('review', A))['issuecomment-9']['status'] == 'unknown'


def test_supported_bold_prose_preserves_finding_age():
    value = node(summary(block()), summary(block(title='**Existing defect**'), head=B))
    assert resolve(value)['qodo:' + OLD]['anchor'] == A


def test_quoted_literal_in_review_prose_keeps_significant_spaces():
    result = resolve(node('Use normalize("a b").', 'Use normalize("a  b").'),
                     source=source('review', A))
    assert result['issuecomment-9']['status'] == 'unknown'


def test_presentation_like_tags_inside_code_are_substantive():
    before = '```html\n<summary>1. value <code>⭐ New</code></summary>\n```'
    after = before.replace('1. value', '2. value')
    result = resolve(node(before, after), source=source('review', A))
    assert result['issuecomment-9']['status'] == 'unknown'


def planner_context(value, members):
    resolved = resolve(value)
    src = source()
    src.update(url=PR + '#issuecomment-9', linkedCommits=[A, B], kind='issue_comment')
    return {'pr': PR, 'head': members[-1], 'sources': {src['reference']: src},
            'resolutions': {src['reference']: resolved}, 'commits': members,
            'commitEvidence': {'newCommits': {sha: len(members) - i - 1 for i, sha in enumerate(members)}},
            'operations': []}


def assessment(context, finding=OLD, **changes):
    item = {'key': 'native-title', 'source': 'issuecomment-9', 'disposition': 'confirmed',
            'evidence': 'Independent current-head reproduction.', 'latestRequest': 'issuecomment-9',
            'requests': [{'reference': 'issuecomment-9', 'findingId': 'qodo:' + finding,
                          'commit': context['head'], 'anchorEvidence': 'Agent picked latest footer'}],
            'coverage': {'openThreads': [], 'currentRequests': [], 'evidence': 'No equivalent thread or request.'},
            'correction': 'Please fix empty input.', 'location': {'path': 'code.py', 'line': 1, 'side': 'RIGHT'}}
    item.update(changes)
    return {'head': context['head'], 'context': evidence.digest(context), 'findings': [item],
            'permissions': {'post_review': True, 'reply_to_feedback': False}}


@pytest.mark.parametrize('members,finding,status,age', [([A, B], OLD, 'review_candidate', 1),
        ([A, B], NEW, 'recent_request', 0), ([A, B, C], NEW, 'review_candidate', 1)])
def test_provenance_drives_zero_one_boundaries_ignoring_native_sha(members, finding, status, age):
    value = node(summary(block()), summary(block(), block(NEW), head=B))
    context = planner_context(value, members)
    native = assessment(context, finding)
    for _ in range(2):
        result = followups.plan(deepcopy(context), deepcopy(native), {})['findings'][0]
        assert (result['status'], result['newCommits']) == (status, age)


def test_rewritten_anchor_retains_authorized_unknown_age_fallback():
    context = planner_context(node(summary(block())), [B, C])
    result = followups.plan(context, assessment(context), {})['findings'][0]
    assert result['status'] == 'review_candidate' and result['unknownAgeFallback'] is True
    assert 'newCommits' not in result


def test_missing_resolution_never_trusts_prose_or_matching_commit_link():
    context = planner_context(node(summary(block())), [A, B])
    context['resolutions'] = {}
    result = followups.plan(context, assessment(context, coverage=None), {})['findings'][0]
    assert result['status'] == 'uncertain_age'


def test_fresh_process_reuses_same_original_source_without_registry(tmp_path):
    value = node(summary(block()), summary(block(), head=B))
    request = {'source': source(), 'text': value['body'], 'node': value, 'pushes': pushes()}
    script = 'import json,sys; import finding_anchors as a; print(json.dumps(a.resolve(**json.load(sys.stdin)),sort_keys=True))'
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': os.pathsep.join([
        str(Path(anchors.__file__).resolve().parent), str(Path(anchors.__file__).resolve().parents[2])])}
    results = [subprocess.check_output([sys.executable, '-c', script], input=json.dumps(request),
                                       cwd=tmp_path, env=env, text=True, timeout=10) for _ in range(2)]
    assert results[0] == results[1]
    assert list(tmp_path.iterdir()) == []


def test_edit_pagination_checks_count_cursor_and_source_freshness(monkeypatch):
    value = node(summary(block()), summary(block(), head=B))
    calls = []
    def read(query, variables, hostname, cwd):
        calls.append(variables['cursor'])
        index = 0 if variables['cursor'] is None else 1
        return {'data': {'node': {key: item for key, item in value.items() if key != 'edits'} | {
            'userContentEdits': {'totalCount': 2, 'nodes': [value['edits'][index]],
                                 'pageInfo': {'hasNextPage': index == 0, 'endCursor': 'page2'}}}}}
    monkeypatch.setattr(anchors, 'graphql', read)
    assert anchors.capture_node(source(), value['body'], 'github.com', Path('.')) == value
    assert calls == [None, 'page2']


@pytest.mark.parametrize('failure,message', [('missing', 'missing revision page'),
        ('repeat_cursor', 'incomplete history pagination'), ('count', 'history changed during pagination'),
        ('changed_body', 'source changed during history capture'), ('null_node', 'source changed during history capture')])
def test_collector_rejects_incomplete_or_changed_history(monkeypatch, failure, message):
    value = node(summary(block()))
    calls = []
    def read(query, variables, hostname, cwd):
        calls.append(variables['cursor'])
        assert len(calls) <= 2, 'collector did not reject a repeated cursor'
        count = 2 if failure in {'missing', 'repeat_cursor'} else (2 if variables['cursor'] is None else 3)
        body = value['body'] if failure != 'changed_body' else 'changed'
        return {'data': {'node': None if failure == 'null_node' else {
            **value, 'body': body, 'userContentEdits': {'totalCount': count,
            'nodes': [value['edits'][0]], 'pageInfo': {'hasNextPage': failure in {'repeat_cursor', 'count'}, 'endCursor': 'same'}}}}}
    monkeypatch.setattr(anchors, 'graphql', read)
    with pytest.raises(ValueError, match=message):
        anchors.capture_node(source(), value['body'], 'github.com', Path('.'))


def test_filtered_timeline_total_is_not_filtered_event_count(monkeypatch):
    def read(query, variables, hostname, cwd):
        return {'data': {'repository': {'pullRequest': {'headRefOid': B, 'timelineItems': {
            'totalCount': 17, 'nodes': pushes(), 'pageInfo': {'hasNextPage': False, 'endCursor': 'done'}}}}}}
    monkeypatch.setattr(anchors, 'graphql', read)
    assert anchors.capture_pushes(PR, B, Path('.')) == pushes()


def test_collect_missing_permissions_preserves_unknown_and_writes_nothing(monkeypatch):
    text = summary(block())
    src = source()
    inline = {'pullRequest': {'url': PR, 'headRefOid': B}, 'threads': []}
    top = {'items': [{'reference': src['reference'], 'text': text}]}
    def fail(*args):
        raise anchors.conversation.FetchError('not accessible')
    monkeypatch.setattr(anchors, 'graphql', fail)
    result = anchors.collect(inline, top, {src['reference']: src}, Path('.'))
    assert result[src['reference']]['qodo:' + OLD]['status'] == 'unknown'
