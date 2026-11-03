"""Shared evidence preserves original sources and rejects drift before acceptance."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import fetch_pr_conversation as conversation
import fetch_unresolved_review_threads as threads
import pr_evidence as evidence

PR = 'https://github.com/owner/repo/pull/7'


def snapshots():
    metadata = {'owner': 'owner', 'repo': 'repo', 'number': 7, 'url': PR,
                'title': 'Fix', 'description': '## Decisions\nDefer metrics. Exact human choice.',
                'headRefOid': 'abc'}
    values = [{'id': 'T1', 'isResolved': False, 'path': 'a.py', 'line': 1,
               'comments': [{'reference': 'C1', 'text': 'Fix retry', 'url': PR+'#discussion_r1'}]},
              {'id': 'T2', 'isResolved': True, 'path': 'b.py', 'line': 2,
               'comments': [{'reference': 'C2', 'text': 'Defer metrics', 'url': PR+'#discussion_r2'}]}]
    inline = threads.build_output(metadata, values)
    comments = [{'reference': 'issuecomment-3', 'text': 'Keep exact instructions.',
                 'url': PR+'#issuecomment-3'}]
    top = {'complete': True, 'pullRequest': {'url': PR, 'headRefOid': 'abc'},
           'reviews': [], 'reviewCount': 0, 'comments': comments, 'commentCount': 1,
           'items': comments, 'itemCount': 1}
    top['sha256'] = evidence.digest(top)
    return inline, top


def save(tmp_path, inline=None, top=None):
    initial = snapshots()
    inline = inline if inline is not None else initial[0]
    top = top if top is not None else initial[1]
    a, b = tmp_path/'threads.json', tmp_path/'conversation.json'
    a.write_text(json.dumps(inline))
    b.write_text(json.dumps(top))
    return a, b


def collected(tmp_path, **kwargs):
    a, b = save(tmp_path)
    return evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b, **kwargs)


def test_supplied_and_collected_views_preserve_sources_work_and_dispositions(tmp_path):
    inline, top = snapshots()
    supplied = collected(tmp_path)
    with patch.object(threads, 'fetch_stable_snapshot', return_value=inline) as fetch_threads, \
         patch.object(conversation, 'fetch', return_value=top) as fetch_conversation:
        independent = evidence.collect(PR, tmp_path)
    assert fetch_threads.call_count == fetch_conversation.call_count == 1
    for role in ('feedback', 'decisions'):
        first = evidence.view(supplied['path'], role, tmp_path, sha256=supplied['sha256'], pr=PR, head='abc')
        second = evidence.view(independent['path'], role, tmp_path, sha256=independent['sha256'], pr=PR, head='abc')
        assert first == second
        # Domain-owned interpretation receives exactly the same inputs, including deferred choices.
        assert first['threads'] == inline['threads']
        assert first['conversation'] == top['items']
        assert 'Defer metrics' in first['pullRequest']['description']
        assert 'items' not in first and 'reviews' not in first and 'comments' not in first
    assert first['threads'][1]['comments'][0]['reference'] == 'C2'
    assert evidence.view(supplied['path'], 'feedback', tmp_path, sha256=supplied['sha256'])['unresolvedReferences'] == ['T1']


def test_artifact_mode_preserves_full_schema_and_summary_omits_text(tmp_path):
    inline, top = snapshots()
    for kind, source in [('threads', inline), ('conversation', top)]:
        receipt = evidence.write_artifact(source, kind, tmp_path)
        assert evidence.load_artifact(receipt, kind, tmp_path) == source
        assert json.loads(Path(receipt['path']).read_bytes()) == source
        assert 'Fix retry' not in json.dumps(receipt)
        assert 'Keep exact instructions' not in json.dumps(receipt)
        assert receipt['itemCount'] == source['itemCount']
        assert Path(receipt['path']).stat().st_mode & 0o777 == 0o600
    receipt = evidence.write_artifact(inline, 'threads', tmp_path)
    assert receipt['sourceReferences'][1] == {'reference': 'T2', 'comments': ['C2']}


@pytest.mark.parametrize('change', ['edit', 'new', 'decisions', 'resolved_reply'])
def test_unchanged_head_source_drift_rejects_before_recording(tmp_path, change):
    receipt = collected(tmp_path)
    inline, top = deepcopy(snapshots())
    if change == 'edit':
        top['comments'][0]['text'] = 'Edited human instruction'
    elif change == 'new':
        top['comments'].append({'reference': 'issuecomment-4', 'text': 'New feedback'})
        top['itemCount'] += 1
        top['commentCount'] += 1
    elif change == 'decisions':
        inline['pullRequest']['description'] = '## Decisions\nDo metrics now.'
    else:
        inline['threads'][1]['comments'].append({'reference': 'C3', 'text': 'Changed settled choice'})
    # Recollection is supplied by the real stable fetchers in production. This gate must
    # compare full results, rather than trust only head or unresolved-item hashes.
    recorded = []
    with patch.object(threads, 'fetch_stable_snapshot', return_value=inline), \
         patch.object(conversation, 'fetch', return_value=top):
        with pytest.raises(ValueError, match='evidence changed'):
            evidence.verify(receipt['path'], tmp_path, sha256=receipt['sha256'], pr=PR, head='abc')
            recorded.append('accepted result')
    assert not recorded


def test_live_verification_preserves_stability_reads(tmp_path):
    inline, top = snapshots()
    receipt = collected(tmp_path)
    # Use the actual stable thread and conversation loops at the acceptance boundary.
    metadata = {'owner': 'owner', 'repo': 'repo', 'number': 7, 'url': PR,
                'title': 'Fix', 'description': inline['pullRequest']['description'], 'headRefOid': 'abc'}
    raw_comment = {'id': 3, 'body': 'Keep exact instructions.', 'html_url': PR+'#issuecomment-3'}
    with patch.object(threads, 'resolve_pull_request', return_value=metadata) as resolve, \
         patch.object(threads, 'fetch_all_threads', return_value=inline['threads']) as fetch, \
         patch.object(conversation, 'gh_json', side_effect=[[[]], [[raw_comment]], {'headRefOid': 'abc'}]*2) as gh:
        # Normalize fixture conversation using the real fetch, then capture identical input.
        normalized = conversation.fetch(inline, tmp_path)
    a, b = save(tmp_path, inline, normalized)
    receipt = evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b)
    with patch.object(threads, 'resolve_pull_request', return_value=metadata) as resolve, \
         patch.object(threads, 'fetch_all_threads', return_value=inline['threads']) as fetch, \
         patch.object(conversation, 'gh_json', side_effect=[[[]], [[raw_comment]], {'headRefOid': 'abc'}]*2) as gh:
        result = evidence.verify(receipt['path'], tmp_path, sha256=receipt['sha256'], pr=PR, head='abc')
    assert result['unchanged'] is True and result['publicationAuthority'] is False
    assert resolve.call_count == fetch.call_count == 2
    assert gh.call_count == 6


@pytest.mark.parametrize('part', ['threads', 'conversation'])
def test_incomplete_snapshots_and_pagination_are_rejected(tmp_path, part):
    inline, top = snapshots()
    (inline if part == 'threads' else top)['complete'] = False
    a, b = save(tmp_path, inline, top)
    with pytest.raises(ValueError, match='complete snapshot'):
        evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b)
    with patch.object(conversation, 'gh_json', return_value={'partial': []}):
        with pytest.raises(conversation.FetchError, match='incomplete paginated'):
            conversation.pages('endpoint', 'github.com', tmp_path)
    metadata = {'owner': 'owner', 'repo': 'repo', 'number': 7, 'hostname': 'github.com'}
    response = {'data': {'repository': {'pullRequest': {'reviewThreads': {
        'nodes': [], 'pageInfo': {'hasNextPage': True, 'endCursor': None}}}}}}
    with patch.object(threads, 'graphql', return_value=response):
        with pytest.raises(threads.FetchError, match='no endCursor'):
            threads.fetch_all_threads(metadata, tmp_path)


@pytest.mark.parametrize('field', list(snapshots()[0]['pullRequest']))
def test_supplied_snapshots_require_full_pr_metadata(tmp_path, field):
    inline, top = snapshots()
    del inline['pullRequest'][field]
    a, b = save(tmp_path, inline, top)
    with pytest.raises(ValueError):
        evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b)


@pytest.mark.parametrize(('field', 'value'), [('title', None), ('title', ''),
                                              ('description', None), ('description', 7)])
def test_supplied_snapshots_require_original_pr_text(tmp_path, field, value):
    inline, top = snapshots()
    inline['pullRequest'][field] = value
    a, b = save(tmp_path, inline, top)
    with pytest.raises(ValueError, match='PR metadata'):
        evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b)


def test_wrong_pr_head_and_cross_snapshot_identity_are_rejected(tmp_path):
    receipt = collected(tmp_path)
    with pytest.raises(ValueError, match='another PR'):
        evidence.view(receipt['path'], 'feedback', tmp_path, sha256=receipt['sha256'], pr=PR.replace('/7', '/8'))
    with pytest.raises(ValueError, match='another head'):
        evidence.view(receipt['path'], 'feedback', tmp_path, sha256=receipt['sha256'], head='new')
    a, b = save(tmp_path)
    with pytest.raises(ValueError, match='another PR'):
        evidence.collect(PR.replace('/7', '/8'), tmp_path, thread_file=a, conversation_file=b)
    inline, top = snapshots()
    top['pullRequest']['headRefOid'] = 'other'
    top['sha256'] = evidence.digest({k: v for k, v in top.items() if k != 'sha256'})
    a, b = save(tmp_path, inline, top)
    with pytest.raises(ValueError, match='disagree'):
        evidence.collect(PR, tmp_path, thread_file=a, conversation_file=b)


@pytest.mark.parametrize('damage', ['missing', 'bytes', 'hash', 'schema'])
def test_missing_corrupt_artifacts_block_reuse(tmp_path, damage):
    receipt = collected(tmp_path)
    artifact = receipt['snapshots']['threads']
    path = Path(artifact['path'])
    if damage == 'missing':
        path.unlink()
        expected = OSError
    elif damage == 'bytes':
        path.write_bytes(b'broken')
        expected = ValueError
    elif damage == 'hash':
        artifact['sha256'] = '0'*64
        expected = ValueError
    else:
        data = json.loads(path.read_bytes())
        data['items'] = []
        content = evidence.encoded(data)
        path.write_bytes(content)
        artifact['bytes'] = len(content)
        artifact['sha256'] = hashlib.sha256(content).hexdigest()
        expected = ValueError
    handoff = json.loads(Path(receipt['path']).read_bytes())
    handoff['snapshots']['threads'] = artifact
    Path(receipt['path']).write_bytes(evidence.encoded(handoff))
    receipt['sha256'] = evidence.digest(handoff)
    with pytest.raises(expected):
        evidence.view(receipt['path'], 'feedback', tmp_path, sha256=receipt['sha256'])


def test_exact_instruction_bytes_and_source_drift(tmp_path):
    source = tmp_path/'instructions.txt'
    original = b'Keep deferred work.\r\nExact bytes: \xc3\xa9\n'
    source.write_bytes(original)
    receipt = collected(tmp_path, instruction_files=[source])
    handoff, inline, top = evidence.load_handoff(receipt['path'], tmp_path, sha256=receipt['sha256'])
    assert Path(handoff['instructions'][0]['path']).read_bytes() == original
    source.write_bytes(original+b'New instruction\n')
    with patch.object(threads, 'fetch_stable_snapshot', return_value=inline), \
         patch.object(conversation, 'fetch', return_value=top):
        with pytest.raises(ValueError, match='instructions changed'):
            evidence.verify(receipt['path'], tmp_path, sha256=receipt['sha256'])


@pytest.mark.parametrize('link_kind', ['file', 'directory'])
def test_instruction_symlink_retarget_rejects_before_recording(tmp_path, monkeypatch, link_kind):
    first, second = tmp_path/'first', tmp_path/'second'
    first.mkdir()
    second.mkdir()
    (first/'rules.txt').write_text('Keep deferred work')
    (second/'rules.txt').write_text('Implement deferred work now')
    if link_kind == 'file':
        link = tmp_path/'rules.txt'
        link.symlink_to(first/'rules.txt')
        source = Path('rules.txt')
        replacement = second/'rules.txt'
    else:
        link = tmp_path/'current'
        link.symlink_to(first, target_is_directory=True)
        source = Path('current/rules.txt')
        replacement = second
    monkeypatch.chdir(tmp_path)
    receipt = collected(tmp_path, instruction_files=[source])
    handoff, inline, top = evidence.load_handoff(receipt['path'], tmp_path, sha256=receipt['sha256'])
    with patch.object(threads, 'fetch_stable_snapshot', return_value=inline), \
         patch.object(conversation, 'fetch', return_value=top):
        assert evidence.verify(receipt['path'], tmp_path, sha256=receipt['sha256'])['unchanged']
        link.unlink()
        link.symlink_to(replacement, target_is_directory=link_kind == 'directory')
        with pytest.raises(ValueError, match='instructions changed'):
            evidence.verify(receipt['path'], tmp_path, sha256=receipt['sha256'])


def test_helpers_default_output_and_artifact_cli(tmp_path):
    inline, top = snapshots()
    for module, flag, value, kind in [(threads, '--pr', PR, 'threads'),
                                      (conversation, '--snapshot-file', str(save(tmp_path)[0]), 'conversation')]:
        method = 'fetch_stable_snapshot' if kind == 'threads' else 'fetch'
        for artifact in (False, True):
            out = io.StringIO()
            args = ['helper', flag, value, '--cwd', str(tmp_path)] + (['--artifact'] if artifact else [])
            with patch.object(module.sys, 'argv', args), patch.object(module.sys, 'stdout', out), \
                 patch.object(module, method, return_value=inline if kind == 'threads' else top):
                assert module.main() == 0
            result = json.loads(out.getvalue())
            if artifact:
                assert result['contract'] == evidence.ARTIFACT
                assert 'items' not in result and 'threads' not in result
            else:
                assert result == (inline if kind == 'threads' else top)


def test_legacy_conversation_input_remains_compatible(tmp_path):
    inline, _ = snapshots()
    legacy = {key: inline[key] for key in ['complete', 'items', 'itemCount', 'itemsSha256', 'pullRequest']}
    path = tmp_path/'legacy.json'
    path.write_text(json.dumps(legacy))
    with patch.object(conversation.sys, 'argv', ['helper', '--snapshot-file', str(path)]), \
         patch.object(conversation.sys, 'stdout', io.StringIO()), \
         patch.object(conversation, 'gh_json', side_effect=[[[]], [[]], {'headRefOid': 'abc'}]*2):
        assert conversation.main() == 0


@pytest.mark.parametrize(('identity', 'message'), [(None, 'PR identity'),
                         ({}, 'canonical PR URL'), ({'url': 7}, 'canonical PR URL'),
                         ({'url': PR, 'headRefOid': None}, 'captured head')])
def test_malformed_identity_rejected_cleanly(identity, message):
    inline, _ = snapshots()
    inline['pullRequest'] = identity
    with pytest.raises(ValueError, match=message):
        evidence.validate(inline, 'threads')


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'dropped_instructions', 'substituted_sources'])
def test_manifest_integrity_is_bound_to_compact_receipt(tmp_path, damage):
    source = tmp_path/'rules.txt'
    source.write_text('Preserve this exact human instruction')
    receipt = collected(tmp_path, instruction_files=[source])
    path = Path(receipt['path'])
    expected = ValueError
    if damage == 'missing':
        path.unlink()
        expected = OSError
    elif damage == 'corrupt':
        path.write_bytes(b'broken')
    else:
        handoff = json.loads(path.read_bytes())
        if damage == 'dropped_instructions':
            handoff['instructions'] = []
        else:
            inline, _ = snapshots()
            inline['pullRequest']['description'] = 'Changed Decisions'
            handoff['snapshots']['threads'] = evidence.write_artifact(inline, 'threads', tmp_path)
        path.write_bytes(evidence.encoded(handoff))
    with pytest.raises(expected):
        evidence.view(path, 'feedback', tmp_path, sha256=receipt['sha256'])
