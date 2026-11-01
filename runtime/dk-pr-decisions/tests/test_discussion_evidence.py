"""Complete off-prompt discussion bodies support context-dependent acceptances."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('discussion_api', ROOT / 'dk-pr-decisions/scripts/decisions.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.mark.parametrize('proposal_kind', ['general', 'review'])
@pytest.mark.parametrize('correction', [False, True])
def test_unchanged_proposal_and_short_acceptance_share_complete_ordered_evidence(tmp_path, proposal_kind, correction):
    url = 'https://github.com/o/r/pull/2'
    def comment(identity, body, created, kind='general'):
        return {'id': identity, 'body': body, 'createdAt': created, 'updatedAt': created,
                'url': url + '#' + identity, 'kind': kind, 'author': {'login': 'human'}}
    proposal = comment('a-proposal', 'Option 1: keep a local copy. Option 2: share the existing transform.',
                       '2026-10-26T12:00:00Z', proposal_kind)
    acceptance = comment('b-answer', 'Use option 2.', '2026-10-26T12:00:00Z')
    large = comment('z-large', 'Complete intervening context: ' + 'context ' * 180_000,
                    '2026-10-26T11:00:00Z')
    # Deliberately reverse insertion order; creation time and ID determine file order.
    comments = {c['id']: c for c in (acceptance, proposal, large)}
    prior = {'decisions': [], 'comments': {proposal['id']: copy.deepcopy(proposal), large['id']: large}}
    original_prior = copy.deepcopy(prior)
    context = {'pr': {'url': url}, 'linked_issues': [], 'diff': ''}
    manifests, prompts = [], []
    expected = {'decisions': [{'text': 'Share the existing transform',
                              'references': [proposal['url'], acceptance['url']]}], 'thread_summaries': []}
    def execute(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        index = prompt.index('EVIDENCE JSON:\n') + len('EVIDENCE JSON:\n')
        payload = json.JSONDecoder().raw_decode(prompt[index:])[0]
        manifest = payload['discussion_evidence']
        manifests.append(manifest)
        evidence = Path(manifest['path'])
        assert evidence.is_absolute()
        assert evidence.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() == manifest['sha256']
        bodies = json.loads(evidence.read_text())
        assert bodies == [large, proposal, acceptance]
        assert payload['changed_comments'] == []
        assert 'Option 2: share' not in prompt
        assert 'context ' * 20 not in prompt
        assert len(prompt) < 30_000
        assert 'bounded file reads' in prompt
        return '{}' if correction and len(prompts) == 1 else json.dumps(expected)
    with patch.object(api, 'execute', side_effect=execute):
        decisions, summaries = api.analyze(tmp_path, context, prior, comments, {}, {acceptance['id']}, set(), set())
    assert decisions == expected['decisions'] and summaries == {}
    assert len(prompts) == (2 if correction else 1)
    assert all(manifest == manifests[0] for manifest in manifests)
    assert Path(manifests[0]['path']).is_file()
    assert prior == original_prior


def test_edited_proposal_replaces_old_body_in_complete_current_evidence(tmp_path):
    original = {'id': 'p', 'kind': 'review', 'url': 'https://github.com/o/r/pull/2#review-1',
                'createdAt': 'first', 'updatedAt': 'first', 'body': 'Option 2: remove the API'}
    current = {**original, 'updatedAt': 'second', 'body': 'Option 2: preserve the API'}
    answer = {'id': 'a', 'kind': 'general', 'url': 'https://github.com/o/r/pull/2#issuecomment-2',
              'createdAt': 'third', 'updatedAt': 'third', 'body': 'Use option 2'}
    def execute(prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split('EVIDENCE JSON:\n')[1])
        records = json.loads(Path(payload['discussion_evidence']['path']).read_text())
        assert records == [current, answer]
        assert original['body'] not in prompt
        return '{"decisions":[],"thread_summaries":[]}'
    with patch.object(api, 'execute', side_effect=execute):
        api.analyze(tmp_path, {'pr': {}, 'linked_issues': [], 'diff': ''}, {'comments': {'p': original}},
                    {'p': current, 'a': answer}, {}, {'p', 'a'}, set(), set())


@pytest.mark.parametrize('failure', ['missing', 'unreadable', 'changed'])
def test_required_evidence_failure_blocks_without_replacing_prior_agreements(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    url = 'https://github.com/o/r/pull/2'
    comments = {'a': {'id': 'a', 'kind': 'general', 'url': 'https://github.com/o/r/pull/2#issuecomment-1',
                      'createdAt': 'first', 'updatedAt': 'second', 'body': 'Use option 2'}}
    prior = {'version': 2, 'pr': {'url': url, 'number': 2, 'body': '', 'headRefOid': 'a' * 40},
             'decisions': [{'text': 'Keep existing API', 'references': [url + '#issuecomment-old']}],
             'comments': {'old': {'url': url + '#issuecomment-old', 'body': 'Keep existing API'}},
             'threads': {}, 'summaries': {}}
    api.retain_agreements(prior)
    path = api.storage.cache_path(url)
    api.save_state(prior, path)
    original = path.read_bytes()
    saved = json.loads(original)['saved_decisions']
    assert saved
    calls = []
    def execute(prompt, *_args, **_kwargs):
        calls.append(prompt)
        payload = json.loads(prompt.split('EVIDENCE JSON:\n')[1])
        evidence = Path(payload['discussion_evidence']['path'])
        if failure == 'changed':
            evidence.write_text('[]')
        else:
            evidence.unlink()
            if failure == 'unreadable':
                evidence.mkdir()
        return '{}'
    with patch.object(api, 'execute', side_effect=execute), \
            patch.object(api, 'gh_json', return_value=prior['pr']), \
            patch.object(api, 'run', return_value='viewer'), \
            patch.object(api, 'collect_metadata', return_value=(comments, {})), \
            patch.object(api, 'fetch_bodies', return_value={'a': comments['a']['body']}), \
            patch.object(api, 'pr_diff', return_value=''), \
            patch.object(api, 'linked_issues', return_value=[]), \
            patch.object(api, 'resolve_pr_identity', return_value=(None, 'o/r', 'o/r')), \
            pytest.raises((RuntimeError, OSError), match='evidence|No such file|directory'):
        api._scan(url)
    assert len(calls) == 1
    assert path.read_bytes() == original
    assert api.storage.read(path, url)['saved_decisions'] == saved


def test_missing_cached_general_body_blocks_before_extraction(tmp_path):
    with patch.object(api, 'execute') as model, pytest.raises(RuntimeError, match='body is missing'):
        api.analyze(tmp_path, {'pr': {}, 'linked_issues': [], 'diff': ''}, {},
                    {'a': {'id': 'a', 'kind': 'general'}}, {}, {'a'}, set(), set())
    model.assert_not_called()
