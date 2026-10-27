import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'decisions.py'
spec = importlib.util.spec_from_file_location('decisions', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from shared import executors


class DecisionTests(unittest.TestCase):
    def test_discussion_corrects_fenced_model_json_without_changing_evidence(self):
        context = {'pr': {'title': 'Fixture', 'headRefOid': 'a' * 40}, 'linked_issues': [], 'diff': ''}
        valid = {'decisions': [], 'thread_summaries': []}
        invalid = '```json\n' + json.dumps(valid) + '\n```'
        with tempfile.TemporaryDirectory() as td, patch.object(mod, 'execute', side_effect=[invalid, json.dumps(valid)]) as model:
            self.assertEqual(mod.analyze(Path(td), context, {}, {}, {}, set(), set(), set()), ([], {}))
        self.assertEqual(model.call_count, 2)
        self.assertIn(invalid, model.call_args_list[1].args[0])
        self.assertIn('without Markdown fences', model.call_args_list[1].args[0])

    def test_discussion_rejects_unsupported_references_after_one_correction(self):
        context = {'pr': {'title': 'Fixture', 'headRefOid': 'a' * 40}, 'linked_issues': [], 'diff': ''}
        invalid = {'decisions': [{'text': 'Unestablished choice', 'references': ['https://example.test/invented']}], 'thread_summaries': []}
        with tempfile.TemporaryDirectory() as td, patch.object(mod, 'execute', return_value=json.dumps(invalid)) as model:
            with self.assertRaisesRegex(RuntimeError, 'after one correction'):
                mod.analyze(Path(td), context, {}, {}, {}, set(), set(), set())
        self.assertEqual(model.call_count, 2)
        self.assertIn('exact evidence references', model.call_args_list[1].args[0])

    def test_discussion_provider_failure_does_not_spend_a_correction(self):
        context = {'pr': {'title': 'Fixture', 'headRefOid': 'a' * 40}, 'linked_issues': [], 'diff': ''}
        with tempfile.TemporaryDirectory() as td, patch.object(mod, 'execute', side_effect=RuntimeError('authentication failed')) as model:
            with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
                mod.analyze(Path(td), context, {}, {}, {}, set(), set(), set())
        model.assert_called_once()

    def test_description_corrects_references_before_saving_suggestions(self):
        url = 'https://github.com/o/r/pull/2'
        state = {'pr': {'url': url, 'body': 'Preserve the existing API.'}}
        invalid = {'decisions': [{'text': 'Preserve the API', 'references': ['https://example.test/invented']}]}
        valid = {'decisions': [{'text': 'Preserve the API', 'references': [url]}]}
        calls = []
        def model(prompt, checkout, **kwargs):
            calls.append(prompt)
            self.assertNotIn('description_suggestions', state)
            return json.dumps(invalid if len(calls) == 1 else valid)
        with patch.object(mod, 'execute', side_effect=model), patch.object(mod, 'save_state') as save:
            result = mod.suggest_from_description(state, Path('unused-private-cache.json'))
        self.assertEqual(result, valid['decisions'])
        self.assertEqual(state['description_suggestions']['items'], valid['decisions'])
        self.assertEqual(len(calls), 2)
        self.assertIn('exact PR URL', calls[1])
        save.assert_called_once()

    def test_context_keeps_live_unpublished_and_local_choices_without_publishing(self):
        url = 'https://github.com/o/r/pull/2'
        body = 'Intro\n\n## Decisions\n\n- Keep existing API\n'
        discussion = {'text': 'Preserve parser behavior', 'references': [url + '#discussion_r1']}
        state = {'pr': {'url': url, 'number': 2, 'headRefOid': 'a'*40, 'body': body},
                 'decisions': [discussion], 'manual_decisions': []}
        local = [{'text': 'Keep response field', 'source': 'user instruction'}]
        with patch.object(mod, 'gh_json', return_value={'url': url, 'body': body, 'headRefOid': 'a'*40}) as gh:
            result = mod.decision_context(state, local)
        self.assertEqual(result['live_decisions'], '## Decisions\n\n- Keep existing API')
        self.assertEqual(result['discussion_decisions'], [discussion])
        self.assertEqual(result['local_decisions'], local)
        self.assertEqual(result['body_hash'], mod.hashlib.sha256(body.encode()).hexdigest())
        self.assertNotIn('PATCH', str(gh.call_args))

    def test_context_rejects_changed_live_pr(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'number': 2,
                        'headRefOid': 'a'*40, 'body': ''}, 'decisions': [], 'manual_decisions': []}
        with patch.object(mod, 'gh_json', return_value={'url': state['pr']['url'], 'body': 'changed', 'headRefOid': 'a'*40}):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                mod.decision_context(state)

    def test_context_restores_cached_manual_decision_source(self):
        url = 'https://github.com/o/r/pull/2'
        manual = {'text': 'Keep schema stable', 'references': []}
        state = {'pr': {'url': url, 'number': 2, 'headRefOid': 'a'*40, 'body': ''},
                 'decisions': [manual], 'manual_decisions': [manual],
                 'manual_decision_sources': {json.dumps(manual, sort_keys=True): 'user chose stable schema'}}
        with patch.object(mod, 'gh_json', return_value={'url': url, 'body': '', 'headRefOid': 'a'*40}):
            result = mod.decision_context(state)
        self.assertEqual(result['local_decisions'], [{'text': 'Keep schema stable', 'source': 'user chose stable schema'}])

    def test_normal_diff_preserves_patch_when_pinned_refs_are_unchanged(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        live = {key: pr[key] for key in ('baseRefOid', 'baseRefName', 'headRefOid')}
        diff = 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+x\n'
        with patch.object(mod, 'run', return_value=diff) as run, \
             patch.object(mod, 'gh_json', return_value=live) as gh:
            self.assertEqual(mod.pr_diff(pr), diff)
        run.assert_called_once_with(['gh', 'pr', 'diff', '2', '--repo', 'o/r'])
        gh.assert_called_once_with(['pr', 'view', '2', '--repo', 'o/r',
                                    '--json', mod.DIFF_SNAPSHOT_FIELDS])

    def test_normal_diff_ref_changes_stop_discovery_before_retention_or_provider_start(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        for key, changed in (('baseRefOid', 'c'*40), ('baseRefName', 'release'),
                             ('headRefOid', 'd'*40)):
            with self.subTest(changed=key), tempfile.TemporaryDirectory() as td:
                state = {'pr': dict(pr), 'decisions': [{'text': 'Keep API', 'references': ['user']}]}
                path = Path(td) / 'state.json'
                original = json.dumps(state)
                path.write_text(original)
                live = {field: pr[field] for field in ('baseRefOid', 'baseRefName', 'headRefOid')}
                live[key] = changed
                with patch.object(mod, 'run', return_value='patch from changed refs'), \
                     patch.object(mod, 'gh_json', return_value=live), \
                     patch.object(mod, 'execute') as provider:
                    with self.assertRaisesRegex(RuntimeError, 'base or head changed'):
                        mod.suggest_from_code(state, path)
                provider.assert_not_called()
                self.assertEqual(path.read_text(), original)
                self.assertEqual(json.dumps(state), original)
                self.assertEqual(list(Path(td).iterdir()), [path])

    def test_large_diff_uses_pinned_local_three_dot_diff(self):
        base, head = 'a'*40, 'b'*40
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': base,
              'baseRefName': 'main', 'headRefOid': head, 'changedFiles': 1}
        live = {'baseRefOid': base, 'baseRefName': 'main', 'headRefOid': head,
                'changedFiles': 1}
        commands = []

        def fake_run(args, **kwargs):
            commands.append(args)
            if args[:3] == ['gh', 'pr', 'diff']:
                raise RuntimeError('HTTP 406: diff exceeded the maximum number of lines (20000)')
            if args[:3] == ['git', 'init', '--bare'] or args[:2] == ['git', 'fetch']:
                return ''
            if args[:3] == ['git', 'rev-parse', 'FETCH_HEAD']:
                fetches = [cmd for cmd in commands if cmd[:2] == ['git', 'fetch']]
                return (base if len(fetches) == 1 else head) + '\n'
            if args[:2] == ['git', 'merge-base']:
                return 'c'*40 + '\n'
            if '--name-only' in args:
                return 'x.py\0'
            if args[:2] == ['git', 'diff']:
                return 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+x\n'
            self.fail(f'unexpected command {args}')

        with patch.object(mod, 'run', side_effect=fake_run), \
             patch.object(mod, 'gh_json', return_value=live) as gh:
            diff = mod.pr_diff(pr)
        self.assertIn('+x', diff)
        self.assertEqual(gh.call_count, 2)
        self.assertIn(['git', 'fetch', '--quiet', '--no-tags',
                       'https://github.com/o/r.git', 'refs/pull/2/head'], commands)
        self.assertTrue(any(cmd[:2] == ['git', 'diff'] and 'c'*40 in cmd
                            and head in cmd for cmd in commands))

    def test_local_diff_ref_mismatch_fails_closed(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        live = {k: pr[k] for k in ('baseRefOid', 'baseRefName', 'headRefOid')}
        def fake_run(args, **kwargs):
            if args[:3] == ['gh', 'pr', 'diff']:
                raise RuntimeError('HTTP 406: PullRequest.diff too_large')
            if args[:3] == ['git', 'rev-parse', 'FETCH_HEAD']:
                return 'f'*40
            return ''
        with patch.object(mod, 'run', side_effect=fake_run), \
             patch.object(mod, 'gh_json', return_value=live):
            with self.assertRaisesRegex(RuntimeError, 'ref refs/heads/main changed'):
                mod.pr_diff(pr)

    def test_local_diff_live_head_change_fails_before_fetch(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        live = {k: pr[k] for k in ('baseRefOid', 'baseRefName', 'headRefOid')}
        live['headRefOid'] = 'c'*40
        with patch.object(mod, 'run', side_effect=RuntimeError('HTTP 406: PullRequest.diff too_large')) as run, \
             patch.object(mod, 'gh_json', return_value=live):
            with self.assertRaisesRegex(RuntimeError, 'base or head changed'):
                mod.pr_diff(pr)
        self.assertEqual(run.call_count, 1)

    def test_local_diff_incomplete_file_coverage_fails_closed(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40, 'changedFiles': 2}
        live = {k: pr[k] for k in ('baseRefOid', 'baseRefName', 'headRefOid', 'changedFiles')}
        def fake_run(args, **kwargs):
            if args[:3] == ['gh', 'pr', 'diff']:
                raise RuntimeError('HTTP 406: PullRequest.diff too_large')
            if args[:3] == ['git', 'rev-parse', 'FETCH_HEAD']:
                return ('a' if args and fake_run.fetch_count == 1 else 'b')*40
            if args[:2] == ['git', 'fetch']:
                fake_run.fetch_count += 1
            if args[:2] == ['git', 'merge-base']:
                return 'c'*40
            if '--name-only' in args:
                return 'one.py\0'
            return ''
        fake_run.fetch_count = 0
        with patch.object(mod, 'run', side_effect=fake_run), \
             patch.object(mod, 'gh_json', return_value=live):
            with self.assertRaisesRegex(RuntimeError, 'file count differs'):
                mod.pr_diff(pr)

    def test_unrelated_gh_diff_failure_does_not_use_local_fallback(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        with patch.object(mod, 'run', side_effect=RuntimeError('HTTP 401: authentication required')) as run, \
             patch.object(mod, 'gh_json') as gh:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                mod.pr_diff(pr)
        self.assertEqual(run.call_count, 1)
        gh.assert_not_called()

    def test_large_patch_analysis_bounds_prompt_and_preserves_thread_evidence(self):
        url = 'https://github.com/o/r/pull/2#discussion_r1'
        comments = {'c1': {'id': 'c1', 'url': url, 'body': 'Keep the parser behavior',
                           'thread_id': 't1', 'kind': 'inline'}}
        threads = {'t1': {'id': 't1', 'resolved': False, 'path': 'parser.py', 'line': 3,
                          'comment_ids': ['c1']}}
        patch_text = ('diff --git a/parser.py b/parser.py\n+++ b/parser.py\n'
                      '@@ -0,0 +1 @@\n+' + 'x' * (mod.MAX_DISCUSSION_DIFF_CHARS + 10) + '\n')
        context = {'pr': {'title': 'Parser fix', 'headRefOid': 'a'*40},
                   'linked_issues': [], 'diff': patch_text}
        response = {'decisions': [{'text': 'Keep the parser behavior', 'references': [url]}],
                    'thread_summaries': [{'thread_id': 't1', 'summary': 'Parser behavior retained'}]}
        prompts = []
        def fake_execute(prompt, cwd, **kwargs):
            prompts.append(prompt)
            return json.dumps(response)
        with tempfile.TemporaryDirectory() as td, patch.object(mod, 'execute', side_effect=fake_execute):
            decisions, summaries = mod.analyze(Path(td), context, {}, comments, threads,
                                               {'c1'}, {'t1'}, {'https://example.com/line'})
        payload = json.loads(prompts[0].split('EVIDENCE JSON:\n', 1)[1])
        self.assertLess(len(prompts[0]), 35_000)
        self.assertIn('Full PR patch omitted', payload['diff'])
        self.assertIn('parser.py', payload['diff'])
        self.assertEqual(payload['code_permalinks'], {'prefix': '', 'paths': {}})
        self.assertEqual(payload['threads_to_summarize'][0]['all_messages'][0]['body'],
                         'Keep the parser behavior')
        self.assertEqual(decisions[0]['references'], [url])
        self.assertEqual(summaries['t1'], 'Parser behavior retained')

    def test_merge_preserves_other_text_and_existing_entries(self):
        body = 'Intro\n\n## Decisions\n\n- Existing choice\n\n## Testing\n\nDone\n'
        item = {'text': 'Use a cache', 'references': ['https://github.com/o/r/pull/2#issuecomment-1']}
        updated = mod.merge_body(body, [item])
        self.assertIn('- Existing choice', updated)
        self.assertTrue(updated.endswith('## Testing\n\nDone\n'))
        self.assertEqual(mod.merge_body(updated, [item]), updated)

    def test_publish_conflict_keeps_decision_pending(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'number': 2,
                        'headRefOid': 'a'*40, 'body': 'original'}}
        item = {'text': 'Use a cache', 'references': []}
        with patch.object(mod, 'gh_json', return_value={'body': 'changed', 'headRefOid': 'a'*40}) as gh:
            outcome = mod.publish(state, [item])
        self.assertEqual(outcome['pending'], [item])
        self.assertEqual(gh.call_count, 1)

    def test_publish_edits_only_body_once(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'number': 2,
                        'headRefOid': 'a'*40, 'body': 'Intro\n'}}
        item = {'text': 'Use a cache', 'references': ['https://github.com/o/r/pull/2#issuecomment-1']}
        live = {'body': 'Intro\n', 'headRefOid': 'a'*40}
        def fake(args):
            if args[:2] == ['pr', 'view']:
                return live
            live['body'] = args[-1][5:]
            return {'body': live['body']}
        with patch.object(mod, 'gh_json', side_effect=fake) as gh:
            self.assertTrue(mod.publish(state, [item])['updated'])
            state['pr']['body'] = live['body']
            self.assertFalse(mod.publish(state, [item])['updated'])
        self.assertEqual(sum(call.args[0][0] == 'api' for call in gh.call_args_list), 1)

    def test_code_suggestions_are_cached_and_never_publish(self):
        link = 'https://github.com/o/r/blob/' + 'a'*40 + '/x.py#L4'
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'number': 2,
                        'headRefOid': 'a'*40, 'baseRefOid': 'b'*40, 'baseRefName': 'main'}, 'decisions': []}
        live = {key: state['pr'][key] for key in ('baseRefOid', 'baseRefName', 'headRefOid')}
        diff = 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +4,1 @@\n+cache = {}\n'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            with patch.object(mod, 'run', return_value=diff), patch.object(mod, 'execute', return_value=json.dumps({'decisions': [
                    {'text': 'Use a cache', 'references': [link]}]})) as model, \
                 patch.object(mod, 'gh_json', return_value=live), \
                 patch.object(mod, 'publish') as publish:
                result = mod.suggest_from_code(state, path)
                self.assertEqual(result, mod.suggest_from_code(state, path))
                model.assert_called_once()
                publish.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['code_suggestions']['head'], 'a'*40)

    def test_large_code_discovery_uses_retained_complete_evidence(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40},
                 'decisions': [{'text': 'Preserve API', 'references': ['user choice']}]}
        diff = ('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+'
                + 'large patch \" \u2603 ' * 100000 + '\n')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            def model(prompt, checkout):
                self.assertLess(len(json.dumps(prompt)), 20000)
                self.assertNotIn('large patch', prompt)
                manifest = json.loads((checkout / 'manifest.json').read_text())
                self.assertEqual(manifest['head_sha'], 'a'*40)
                for artifact in manifest['artifacts'].values():
                    content = Path(artifact['path']).read_text()
                    self.assertEqual(mod.hashlib.sha256(content.encode()).hexdigest(), artifact['sha256'])
                self.assertEqual((checkout / 'diff.patch').read_text(), diff)
                self.assertEqual(json.loads((checkout / 'existing-decisions.json').read_text()), state['decisions'])
                links = json.loads((checkout / 'code-permalinks.json').read_text())
                self.assertEqual(links['paths'], {'x.py': [[1, 1]]})
                return '{"decisions": []}'
            with patch.object(mod, 'pr_diff', return_value=diff), patch.object(mod, 'execute', side_effect=model):
                self.assertEqual(mod.suggest_from_code(state, path), [])
            retained, = Path(td).glob('code-evidence-*')
            self.assertEqual((retained / 'diff.patch').read_text(), diff)

    def test_failed_discovery_retains_evidence_and_existing_choices(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40},
                 'decisions': [{'text': 'Keep API', 'references': ['user']}],
                 'code_suggestions': {'head': 'b'*40, 'items': []}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            original = json.dumps(state)
            path.write_text(original)
            with patch.object(mod, 'pr_diff', return_value='complete diff'), \
                 patch.object(mod, 'execute', side_effect=RuntimeError('provider failed')):
                with self.assertRaisesRegex(RuntimeError, 'Code evidence retained at'):
                    mod.suggest_from_code(state, path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(json.dumps(state), original)
            retained, = Path(td).glob('code-evidence-*')
            self.assertEqual((retained / 'diff.patch').read_text(), 'complete diff')

    def test_invalid_code_responses_retain_diagnostics_and_preserve_choices(self):
        reason = 'Inspection unavailable: permission denied reading diff.patch'
        link = 'https://github.com/o/r/blob/' + 'a'*40 + '/x.py#L1'
        valid = {'text': 'Keep API', 'references': [link]}
        cases = [('plain failure', reason, reason), ('malformed JSON', '{"error": ' + reason, reason),
                 ('empty reply', '', 'Analysis returned no diagnostic text'),
                 ('whitespace reply', ' \n\t', 'Analysis returned no diagnostic text'),
                 ('deep JSON', '[' * 10000 + '0' + ']' * 10000,
                  ('recursion', 'Invalid code suggestion result')),
                 ('forced decoder recursion', 'force-decoder-recursion', 'recursion')]
        for name, value in [('error object', {'error': reason}), ('string', reason), ('null', None),
                            ('list', []), ('integer', 7), ('boolean', True), ('missing decisions', {}),
                            ('extra key', {'decisions': [], 'error': reason}),
                            ('null decisions', {'decisions': None}), ('object decisions', {'decisions': {}}),
                            ('string decisions', {'decisions': 'partial'})]:
            cases.append((name, json.dumps(value), reason if 'error' in name or name == 'string' or name == 'extra key'
                          else 'Invalid code suggestion result'))
        for name, item in [('null item', None), ('list item', []), ('string item', 'partial'),
                           ('missing text', {'references': [link]}), ('extra item key', {**valid, 'error': reason})]:
            cases.append((name, json.dumps({'decisions': [item]}), 'Code suggestion lacks exact evidence'))
        for value in (None, True, 7, [], {}, '', ' \t', 'first\nsecond', 'first\rsecond'):
            cases.append(('invalid text ' + repr(value), json.dumps({'decisions': [{**valid, 'text': value}]}),
                          'Code suggestion lacks exact evidence'))
        for value in (None, link, {}, [], [None], [True], [{}], [[]], ['https://example.com/invented']):
            cases.append(('invalid references ' + repr(value), json.dumps({'decisions': [{**valid, 'references': value}]}),
                          'Code suggestion lacks exact evidence'))
        cases.append(('partial result', json.dumps({'decisions': [valid, {'text': 'partial', 'references': [[]]}]}),
                      'Code suggestion lacks exact evidence'))
        cases.append(('quota text in invalid decision', json.dumps({'decisions': [
            {'text': 'usage limit reached', 'references': [[]]}]}), 'Code suggestion lacks exact evidence'))
        diff = 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+keep_api()\n'
        original_loads = json.loads
        def decode(value, *args, **kwargs):
            if value == 'force-decoder-recursion':
                raise RecursionError('forced recursion failure')
            return original_loads(value, *args, **kwargs)
        for name, response, explanation in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40},
                         'decisions': [{'text': 'Preserve choice', 'references': ['user']}],
                         'code_suggestions': {'head': 'b'*40, 'items': [valid]}}
                original = json.dumps(state)
                path = Path(td) / 'state.json'
                path.write_text(original)
                with patch.object(mod, 'pr_diff', return_value=diff), \
                     patch.object(mod, 'execute', return_value=response), \
                     patch.object(mod.json, 'loads', side_effect=decode):
                    with self.assertRaises(RuntimeError) as failure:
                        mod.suggest_from_code(state, path)
                self.assertIs(type(failure.exception), RuntimeError)
                message = str(failure.exception)
                retained, = Path(td).glob('code-evidence-*')
                self.assertIn(str(retained / 'manifest.json'), message)
                self.assertIn(str(retained / 'failed-response.txt'), message)
                if isinstance(explanation, tuple):
                    self.assertTrue(any(text in message for text in explanation), message)
                else:
                    self.assertIn(explanation, message)
                self.assertLess(len(message), executors.MAX_PROVIDER_ERROR_CHARS + 1000)
                self.assertEqual((retained / 'failed-response.txt').read_text(), response)
                self.assertEqual((retained / 'diff.patch').read_text(), diff)
                self.assertEqual(path.read_text(), original)
                self.assertEqual(json.dumps(state), original)

    def test_invalid_code_response_excerpt_is_bounded_redacted_and_not_exhaustion(self):
        for mode in ('raw', 'unicode-json', 'ascii-json'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40}, 'decisions': []}
                path = Path(td) / 'state.json'
                original = json.dumps(state)
                path.write_text(original)
                replies = []
                def model(request, checkout):
                    reason = 'Inspection unavailable: usage limit reached while reading evidence'
                    if mode == 'raw':
                        response = reason + '\n' + request + 'x' * 100000 + '\ninspection failed'
                    else:
                        response = json.dumps({'error': reason, 'echo': request, 'padding': 'x' * 100000},
                                              ensure_ascii=mode == 'ascii-json')
                    replies.append(response)
                    return response
                with patch.object(mod, 'pr_diff', return_value='complete diff'), \
                     patch.object(mod, 'language_review_instructions', return_value='private prompt marker "\u2603\r\n'), \
                     patch.object(mod, 'execute', side_effect=model):
                    with self.assertRaises(RuntimeError) as failure:
                        mod.suggest_from_code(state, path)
                self.assertIs(type(failure.exception), RuntimeError)
                message = str(failure.exception)
                self.assertIn('Inspection unavailable: usage limit reached', message)
                self.assertIn('[provider input omitted]', message)
                self.assertNotIn('private prompt marker', message)
                self.assertIn('[provider output truncated]', message)
                retained, = Path(td).glob('code-evidence-*')
                self.assertIn(str(retained / 'manifest.json'), message)
                self.assertLess(len(message), executors.MAX_PROVIDER_ERROR_CHARS + 1000)
                self.assertEqual((retained / 'failed-response.txt').read_bytes(), replies[0].encode('utf-8'))
                self.assertEqual(retained.stat().st_mode & 0o077, 0)
                self.assertEqual(path.read_text(), original)
                self.assertEqual(json.dumps(state), original)

    def test_failed_response_write_does_not_replace_inspection_failure_or_manifest(self):
        reason = 'Inspection unavailable: authentication failed for evidence access'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40}, 'decisions': []}
            original = json.dumps(state)
            path.write_text(original)
            write_text = Path.write_text
            def write(artifact, text, **kwargs):
                if artifact.name == 'failed-response.txt':
                    raise OSError('diagnostic storage unavailable')
                return write_text(artifact, text, **kwargs)
            with patch.object(mod, 'pr_diff', return_value='complete diff'), \
                 patch.object(mod, 'execute', return_value=json.dumps({'error': reason})), \
                 patch.object(Path, 'write_text', write):
                with self.assertRaises(RuntimeError) as failure:
                    mod.suggest_from_code(state, path)
            self.assertIs(type(failure.exception), RuntimeError)
            retained, = Path(td).glob('code-evidence-*')
            self.assertIn(reason, str(failure.exception))
            self.assertIn(str(retained / 'manifest.json'), str(failure.exception))
            self.assertIn('could not be retained', str(failure.exception))
            self.assertFalse((retained / 'failed-response.txt').exists())
            self.assertEqual(path.read_text(), original)
            self.assertEqual(json.dumps(state), original)

    def test_code_execute_exceptions_keep_identity_type_and_context(self):
        for exception in (RuntimeError('transport failed'), ValueError('invalid provider setup'),
                          executors.ProviderExhausted('usage limit reached')):
            with self.subTest(kind=type(exception).__name__), tempfile.TemporaryDirectory() as td:
                state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40}, 'decisions': []}
                path = Path(td) / 'state.json'
                original = json.dumps(state)
                path.write_text(original)
                reason = str(exception)
                with patch.object(mod, 'pr_diff', return_value='complete diff'), \
                     patch.object(mod, 'execute', side_effect=exception):
                    with self.assertRaises(type(exception)) as failure:
                        mod.suggest_from_code(state, path)
                self.assertIs(failure.exception, exception)
                self.assertIs(type(failure.exception), type(exception))
                retained, = Path(td).glob('code-evidence-*')
                self.assertIn(reason, str(failure.exception))
                self.assertIn(str(retained / 'manifest.json'), str(failure.exception))
                self.assertFalse((retained / 'failed-response.txt').exists())
                self.assertEqual(path.read_text(), original)
                self.assertEqual(json.dumps(state), original)

    def test_code_discovery_rejects_oversized_serialized_request_before_provider_start(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40},
                 'decisions': [{'text': 'Keep API', 'references': ['user']}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            original = json.dumps(state)
            path.write_text(original)
            # Unicode escaping alone exceeds the limit; raw input remains smaller.
            with patch.object(mod, 'pr_diff', return_value='complete diff'), \
                 patch.object(mod, 'language_review_instructions', return_value='\u2603' * 160000), \
                 patch.object(executors.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'Provider request too large.*Code evidence retained at'):
                    mod.suggest_from_code(state, path)
            process.assert_not_called()
            self.assertEqual(path.read_text(), original)
            self.assertEqual(json.dumps(state), original)
            retained, = Path(td).glob('code-evidence-*')
            self.assertEqual((retained / 'diff.patch').read_text(), 'complete diff')

    def test_code_discovery_reads_retained_evidence_through_real_executor(self):
        diff = ('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+'
                + 'large patch " \u2603 ' * 100000 + '\n+LATE_PATCH_MARKER\n')
        link = 'https://github.com/o/r/blob/' + 'a'*40 + '/x.py#L2'
        candidate = {'text': 'Keep late patch behavior', 'references': [link]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider = root / 'provider.py'
            provider.write_text('''import hashlib,json,os,sys
from pathlib import Path
prompt = sys.stdin.read()
manifest = json.loads(Path('manifest.json').read_text())
assert json.loads(prompt.split('EVIDENCE MANIFEST JSON:\\n', 1)[1]) == manifest
if os.environ['TEST_CODE_EVIDENCE_ACCESS'] == 'unavailable':
    print('Complete inspection unavailable: code evidence access denied', file=sys.stderr)
    sys.exit(1)
if os.environ['TEST_CODE_EVIDENCE_ACCESS'] in ('plain-response', 'error-response'):
    response = 'Complete inspection unavailable: code evidence access denied'
    if os.environ['TEST_CODE_EVIDENCE_ACCESS'] == 'error-response':
        response = json.dumps({'error': response})
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(response)
    sys.exit(0)
for artifact in manifest['artifacts'].values():
    content = Path(artifact['path']).read_text()
    assert hashlib.sha256(content.encode()).hexdigest() == artifact['sha256']
patch = Path(manifest['artifacts']['diff.patch']['path']).read_text()
assert patch.index('LATE_PATCH_MARKER') > 1_000_000
links = json.loads(Path(manifest['artifacts']['code-permalinks.json']['path']).read_text())
assert links['paths']['x.py'] == [[1, 2]]
assert json.loads(Path('existing-decisions.json').read_text())[0]['text'] == 'Keep API'
result = {'decisions': [{'text': 'Keep late patch behavior',
                        'references': [links['prefix'] + 'x.py#L2']}]}
Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
''')
            run_local = subprocess.run
            def run_provider(argv, **kwargs):
                if argv[0] == 'git':
                    return run_local(argv, **kwargs)
                self.assertEqual(argv[:2], ['codex', 'exec'])
                self.assertEqual(Path(argv[argv.index('-C') + 1]), kwargs['cwd'])
                return run_local([sys.executable, str(provider), *argv[1:]], **kwargs)
            for access in ('available', 'unavailable', 'plain-response', 'error-response'):
                with self.subTest(access=access):
                    state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40},
                             'decisions': [{'text': 'Keep API', 'references': ['user']}]}
                    path = root / (access + '.json')
                    original = json.dumps(state)
                    path.write_text(original)
                    with patch.object(mod, 'pr_diff', return_value=diff), \
                         patch.object(executors, 'managed_update', return_value=None), \
                         patch.object(executors.subprocess, 'run', side_effect=run_provider), \
                         patch.dict(os.environ, {'DK_HOST': 'codex',
                                                 'TEST_CODE_EVIDENCE_ACCESS': access, 'DK_REVIEW_STATS_TRACE': ''}):
                        if access == 'available':
                            self.assertEqual(mod.suggest_from_code(state, path), [candidate])
                            self.assertEqual(json.loads(path.read_text())['code_suggestions']['items'], [candidate])
                        else:
                            with self.assertRaisesRegex(RuntimeError, 'Complete inspection unavailable.*Code evidence retained at') as failure:
                                mod.suggest_from_code(state, path)
                            if access != 'unavailable':
                                self.assertIn('failed-response.txt', str(failure.exception))
                            self.assertEqual(path.read_text(), original)
                            self.assertEqual(json.dumps(state), original)
                    self.assertEqual(state['decisions'], [{'text': 'Keep API', 'references': ['user']}])
            for retained in root.glob('code-evidence-*'):
                self.assertEqual((retained / 'diff.patch').read_text(), diff)

    def test_suggest_preserves_default_code_and_explicit_source_dispatch(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'headRepository': {'nameWithOwner': 'o/r'}, 'headRefOid': 'a'*40}}
        for selection, expected in (([], ['code']), (['--source', 'code'], ['code']),
                                    (['--source', 'description'], ['description']),
                                    (['--source', 'all'], ['code', 'description'])):
            with self.subTest(selection=selection):
                calls = []
                def discover(source):
                    calls.append(source)
                    return [{'text': source + ' candidate', 'references': [state['pr']['url']]}]
                with patch.object(sys, 'argv', ['decisions.py', 'suggest', *selection]), \
                     patch.object(mod, 'transaction', return_value=nullcontext(state['pr']['url'])), \
                     patch.object(mod, 'scan', return_value=(state, Path('unused.json'))), \
                     patch.object(mod, 'suggest_from_code', side_effect=lambda *_: discover('code')), \
                     patch.object(mod, 'suggest_from_description', side_effect=lambda *_: discover('description')), \
                     patch.object(mod, 'publish') as publish, \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    mod.cli()
                self.assertEqual(calls, expected)
                candidates = json.loads(output.getvalue())['data']['decisions']
                self.assertEqual([item['source_type'] for item in candidates], expected)
                self.assertTrue(all(item['status'] == 'candidate' for item in candidates))
                publish.assert_not_called()

    def test_manual_add_records_once_without_publishing(self):
        ref = 'https://github.com/o/r/pull/2#issuecomment-1'
        item = {'text': 'Use a cache', 'references': [ref]}
        state = {'pr': {'headRefOid': 'a'*40}, 'comments': {'c': {'url': ref}},
                 'decisions': [], 'manual_decisions': [], 'code_suggestions': {}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            argv = ['decisions.py', 'add', '--json', json.dumps(item)]
            with patch.object(sys, 'argv', argv), patch.object(mod, 'scan', return_value=(state, path)), \
                 patch.object(mod, 'transaction', return_value=nullcontext('https://github.com/o/r/pull/2')), \
                 patch.object(mod, 'publish', return_value={'updated': True, 'pending': []}) as publish, \
                 patch('sys.stdout', new_callable=io.StringIO):
                mod.cli()
                mod.cli()
            self.assertEqual(state['manual_decisions'], [item])
            publish.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['decisions'], [item])

    def test_manual_add_rejects_unverified_source(self):
        item = {'text': 'Use a cache', 'references': ['https://example.com/unverified']}
        state = {'pr': {'headRefOid': 'a'*40}, 'comments': {}, 'decisions': [],
                 'code_suggestions': {}}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(sys, 'argv', ['decisions.py', 'add', '--json', json.dumps(item)]), \
                 patch.object(mod, 'transaction', return_value=nullcontext('https://github.com/o/r/pull/2')), \
                 patch.object(mod, 'scan', return_value=(state, Path(td)/'state.json')):
                with self.assertRaisesRegex(ValueError, 'exact discussion or code source'):
                    mod.cli()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://github.com/o/r/pull/2'
        self.state = {'pr': {'url': self.url, 'headRepository': {'nameWithOwner': 'o/r'}, 'number': 2, 'headRefOid': 'a'*40,
                             'body': 'Intro\n\n## Decisions\n\n- Keep old behavior\n\n## Testing\nDone\n'},
                      'decisions': [], 'manual_decisions': [], 'comments': {}, 'threads': {}, 'summaries': {}}
        self.item = {'text': 'Use new behavior', 'references': []}

    def invoke(self, args):
        with patch.object(sys, 'argv', ['decisions.py', '--pr', self.url, *args]), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = mod.main()
        return code, json.loads(output.getvalue())

    def test_list_is_offline_and_does_not_touch_cache(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'XDG_DATA_HOME': td}):
            path = mod.cache_path(self.url)
            mod.save_state(self.state, path)
            before = path.stat().st_mtime_ns
            with patch.object(mod, 'scan', side_effect=AssertionError('must not scan')), \
                 patch.object(mod, 'run', side_effect=AssertionError('must not execute')):
                code, result = self.invoke(['list'])
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['data']['cached'])
            self.assertEqual(result['data']['decisions'][0]['text'], 'Keep old behavior')
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_missing_cache_and_invalid_args_return_json_errors(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'XDG_DATA_HOME': td}):
            for args in (['list'], ['unknown'], ['add'], ['replace', '--json', '{}']):
                code, result = self.invoke(args)
                self.assertEqual(code, 1)
                self.assertEqual(result['status'], 'error')
                self.assertIsNotNone(result['error']['message'])
                self.assertIn('publication', result)

    def test_scan_does_not_publish_without_flag(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(mod, 'scan', return_value=(self.state, Path(td)/'state.json')), \
             patch.object(mod, 'publish') as publish:
            code, result = self.invoke(['scan'])
            self.assertEqual(code, 0)
            publish.assert_not_called()
            publish.return_value = {'updated': False, 'pending': [], 'reason': None}
            self.invoke(['scan', '--publish'])
            publish.assert_called_once()

    def test_publish_uses_cached_snapshot_and_updates_cache_after_success(self):
        self.state['decisions'] = [self.item]
        live = {'body': self.state['pr']['body'], 'headRefOid': 'a'*40}
        def gh(args):
            if args[:2] == ['pr', 'view']:
                return dict(live)
            live['body'] = args[-1][5:]
            return {'body': live['body']}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'XDG_DATA_HOME': td}):
            path = mod.cache_path(self.url)
            mod.save_state(self.state, path)
            with patch.object(mod, 'scan', side_effect=AssertionError('must not scan')), \
                 patch.object(mod, 'gh_json', side_effect=gh) as calls:
                code, first = self.invoke(['publish'])
                _, second = self.invoke(['publish'])
            self.assertEqual(code, 0)
            self.assertTrue(first['publication']['updated'])
            self.assertFalse(second['publication']['updated'])
            self.assertEqual(sum(c.args[0][0] == 'api' for c in calls.call_args_list), 1)
            self.assertEqual(json.loads(path.read_text())['pr']['body'], live['body'])

    def test_publish_conflict_and_write_failure_have_same_envelope(self):
        self.state['decisions'] = [self.item]
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'XDG_DATA_HOME': td}):
            mod.save_state(self.state, mod.cache_path(self.url))
            responses = ([{'body': 'external edit', 'headRefOid': 'a'*40}],
                         [{'body': self.state['pr']['body'], 'headRefOid': 'a'*40}, RuntimeError('offline')])
            for response in responses:
                with patch.object(mod, 'gh_json', side_effect=response):
                    code, result = self.invoke(['publish'])
                self.assertEqual(code, 0)
                self.assertEqual(result['status'], 'pending')
                self.assertTrue(result['publication']['pending'])
                self.assertIsNotNone(result['publication']['reason'])

    def test_replace_preserves_history_and_other_sections_and_is_idempotent(self):
        old = mod.decision_records(self.state)[0]
        mod.add_decision(self.state, self.item, 'Change behavior', replaces=old['id'])
        replaced = mod.apply_replacements(self.state['pr']['body'], self.state['replacements'])
        updated = mod.merge_body(replaced, self.state['decisions'])
        self.assertTrue(updated.startswith('Intro\n\n'))
        self.assertTrue(updated.endswith('## Testing\nDone\n'))
        self.assertIn('- Keep old behavior', updated)
        self.assertIn('<details>', updated)
        self.assertEqual(mod.apply_replacements(updated, self.state['replacements']), updated)
        self.state['pr']['body'] = updated
        records = mod.decision_records(self.state)
        old = next(r for r in records if r['text'] == 'Keep old behavior')
        new = next(r for r in records if r['text'] == self.item['text'])
        self.assertEqual(old['status'], 'superseded')
        self.assertEqual(old['superseded_by'], new['id'])
        self.assertEqual(new['publication_status'], 'published')
        self.assertEqual([e['text'] for e in mod.body_entries(updated)], ['Use new behavior'])
        # History remains readable after losing the local replacement cache.
        self.state['replacements'] = []
        self.assertEqual(mod.decision_records(self.state), records)

    def test_replacement_refuses_changed_target_even_after_snapshot_refresh(self):
        old = mod.decision_records(self.state)[0]
        mod.add_decision(self.state, self.item, 'Change behavior', replaces=old['id'])
        self.state['pr']['body'] = self.state['pr']['body'].replace('Keep old behavior', 'Edited old behavior')
        live = {k: self.state['pr'][k] for k in ('body', 'headRefOid')}
        with patch.object(mod, 'gh_json', return_value=live) as gh:
            outcome = mod.publish(self.state, self.state['decisions'])
        self.assertTrue(outcome['pending'])
        self.assertIn('Replaced decision changed', outcome['reason'])
        self.assertEqual(gh.call_count, 1)

    def test_multiline_description_entry_is_one_replaceable_record(self):
        self.state['pr']['body'] = '## Decisions\n\n- Keep API\n  because old clients need it.\n\n- Keep parser\n'
        entries = mod.body_entries(self.state['pr']['body'])
        self.assertEqual(len(entries), 2)
        self.assertIn('because old clients', entries[0]['text'])
        mod.add_decision(self.state, self.item, 'Change API', entries[0]['id'])
        updated = mod.apply_replacements(self.state['pr']['body'], self.state['replacements'])
        self.assertEqual([e['text'] for e in mod.body_entries(updated)], ['Keep parser'])

    def test_description_suggestion_needs_explicit_promotion_and_current_body(self):
        candidate = {'text': 'Use new behavior', 'references': [self.url]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            with patch.object(mod, 'execute', return_value=json.dumps({'decisions': [candidate]})) as run:
                items = mod.suggest_from_description(self.state, path)
                self.assertEqual(items, mod.suggest_from_description(self.state, path))
                self.assertEqual(run.call_count, 1)
                prompt = run.call_args.args[0]
                evidence = json.loads(prompt.split('EVIDENCE JSON:\n')[1])
                self.assertNotIn('Keep old behavior', evidence['description'])
            self.assertEqual(self.state['decisions'], [])
            result = mod.add_decision(self.state, candidate)
            self.assertEqual(result['source_type'], 'description')
            self.state['pr']['body'] += 'changed'
            with self.assertRaisesRegex(ValueError, 'current description suggestion'):
                mod.add_decision(self.state, candidate)

    def test_code_candidate_requires_exact_current_suggestion(self):
        candidate = {'text': 'Use cache', 'references': ['https://github.com/o/r/blob/' + 'a'*40 + '/x.py#L1']}
        self.state['code_suggestions'] = {'head': 'a'*40, 'items': [candidate]}
        self.assertEqual(mod.add_decision(self.state, candidate)['source_type'], 'code')
        with self.assertRaises(ValueError):
            mod.add_decision(self.state, {**candidate, 'text': 'Use another cache'})
        self.state['pr']['headRefOid'] = 'b'*40
        with self.assertRaises(ValueError):
            mod.add_decision(self.state, candidate)

    def test_context_envelope_has_unified_records_and_legacy_snapshot(self):
        live = dict(self.state['pr'])
        with tempfile.TemporaryDirectory() as td, \
             patch.object(mod, 'scan', return_value=(self.state, Path(td)/'state.json')), \
             patch.object(mod, 'gh_json', return_value=live):
            code, result = self.invoke(['context'])
        self.assertEqual(code, 0)
        self.assertEqual(result['schema_version'], 2)
        self.assertTrue(result['data']['verified'])
        self.assertEqual(result['data']['decisions'][0]['source_type'], 'description')
        self.assertIn('live_decisions', result['data'])

    def test_merge_does_not_drop_choice_that_is_substring_of_existing_entry(self):
        body = '## Decisions\n\n- Use cache only for reads\n'
        updated = mod.merge_body(body, [{'text': 'Use cache', 'references': []}])
        self.assertEqual(len(mod.body_entries(updated)), 2)

    def test_replacement_does_not_replace_prefix_of_another_entry(self):
        self.state['pr']['body'] = '## Decisions\n\n- Keep API stable\n- Keep API\n'
        target = mod.decision_id('Keep API')
        mod.add_decision(self.state, self.item, 'Change API', target)
        # Retrying the same local replacement is harmless.
        mod.add_decision(self.state, self.item, 'Change API', target)
        self.assertEqual(len(self.state['replacements']), 1)
        updated = mod.apply_replacements(self.state['pr']['body'], self.state['replacements'])
        self.assertEqual([e['text'] for e in mod.body_entries(updated)], ['Keep API stable'])

    def test_replacement_chain_survives_cache_loss(self):
        first = mod.decision_records(self.state)[0]
        second = mod.add_decision(self.state, self.item, 'First change', first['id'])
        final = {'text': 'Use final behavior', 'references': []}
        mod.add_decision(self.state, final, 'Second change', second['id'])
        updated = mod.merge_body(mod.apply_replacements(self.state['pr']['body'], self.state['replacements']),
                                 self.state['decisions'])
        recovered = mod.decision_records({'pr': {**self.state['pr'], 'body': updated}})
        self.assertEqual(len(recovered), 3)
        ids = {r['id'] for r in recovered}
        self.assertTrue(all(r['superseded_by'] in ids for r in recovered if r['status'] == 'superseded'))
        self.assertEqual([r['text'] for r in recovered if r['status'] == 'active'], ['Use final behavior'])

    def test_refresh_preserves_replacement_and_filters_old_analysis(self):
        old = {'text': 'Keep old behavior', 'references': [self.url + '#issuecomment-1']}
        mod.add_decision(self.state, self.item, 'Change behavior', mod.decision_id(old['text']))
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {'XDG_DATA_HOME': td}):
            path = mod.cache_path(self.url)
            mod.save_state(self.state, path)
            with patch.object(mod, 'gh_json', return_value=self.state['pr']), \
                 patch.object(mod, 'run', return_value='viewer'), \
                 patch.object(mod, 'pr_diff', return_value=''), \
                 patch.object(mod, 'collect_metadata', return_value=({}, {})), \
                 patch.object(mod, 'fetch_bodies', return_value={}), \
                 patch.object(mod, 'linked_issues', return_value=[]), \
                 patch.object(mod, 'analyze', return_value=([old], {})):
                refreshed, _ = mod.scan(self.url)
        self.assertEqual(refreshed['decisions'], [self.item])
        self.assertEqual(refreshed['replacements'], self.state['replacements'])
        self.assertEqual(refreshed['decision_sources'], self.state['decision_sources'])
        records = mod.decision_records(refreshed)
        self.assertEqual(next(r for r in records if r['text'] == old['text'])['status'], 'superseded')

    def test_scan_from_home_allows_private_data_but_nested_checkout_rejects_it(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            checkout = home / 'checkout'
            subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
            nested = checkout / 'src'
            nested.mkdir()
            private = home / 'data'
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(private)}), \
                 patch.object(mod.Path, 'cwd', return_value=home), \
                 patch.object(mod, 'gh_json', return_value=self.state['pr']), \
                 patch.object(mod, 'run', return_value='viewer'), \
                 patch.object(mod, 'pr_diff', return_value=''), \
                 patch.object(mod, 'collect_metadata', return_value=({}, {})), \
                 patch.object(mod, 'fetch_bodies', return_value={}), \
                 patch.object(mod, 'linked_issues', return_value=[]), \
                 patch.object(mod, 'analyze', return_value=([], {})):
                _, saved = mod.scan(self.url)
                self.assertTrue(saved.is_relative_to(private))
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(checkout / 'private')}), \
                 patch.object(mod.Path, 'cwd', return_value=nested), \
                 patch.object(mod, 'gh_json', return_value=self.state['pr']), \
                 patch.object(mod, 'run', return_value='viewer'):
                with self.assertRaisesRegex(RuntimeError, 'outside the code repository'):
                    mod.scan(self.url)

    def test_cache_path_rejects_path_traversal(self):
        for url in ('https://github.com/../r/pull/2', 'https://github.com/o/../pull/2'):
            with self.assertRaises(ValueError):
                mod.cache_path(url)


if __name__ == '__main__': unittest.main()


@pytest.mark.parametrize('deleted', [False, True])
def test_code_links_preserve_verified_fork_and_deleted_head_base_fallback(tmp_path, deleted):
    state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'number': 2,
             'headRefOid': 'a' * 40, 'headRefName': 'topic',
             'baseRefOid': 'b' * 40, 'baseRefName': 'main', 'isCrossRepository': True,
             'headRepository': {'name': 'repo', 'id': 'R_head', 'nameWithOwner': ''}}, 'decisions': []}
    rest = {'html_url': state['pr']['url'], 'number': 2,
            'head': {'sha': 'a' * 40, 'ref': 'topic',
                     'repo': {'full_name': 'contributor/repo', 'name': 'repo', 'node_id': 'R_head'}},
            'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'full_name': 'o/r', 'name': 'r'}}}
    if deleted:
        state['pr']['headRepository'] = None
        rest['head']['repo'] = None
    diff = 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+cache = {}\n'
    repository = 'o/r' if deleted else 'contributor/repo'
    prefix = 'https://github.com/' + repository + '/blob/' + 'a' * 40 + '/'
    candidate = {'text': 'Use cache', 'references': [prefix + 'x.py#L1']}
    def model(prompt, checkout):
        links = json.loads((checkout / 'code-permalinks.json').read_text())
        assert links['prefix'] == prefix
        return json.dumps({'decisions': [candidate]})
    with patch.object(mod, 'pr_diff', return_value=diff), patch.object(mod, 'gh_json', return_value=rest), \
         patch.object(mod, 'execute', side_effect=model):
        assert mod.suggest_from_code(state, tmp_path / 'state.json') == [candidate]
    def gh(args):
        return state['pr'] if args[:2] == ['pr', 'view'] else rest
    def analyze(*args, **kwargs):
        assert args[7] == {prefix + 'x.py#L1'}
        return [], {}
    with patch.dict(os.environ, {'XDG_DATA_HOME': str(tmp_path)}), \
         patch.object(mod, 'gh_json', side_effect=gh), patch.object(mod, 'run', return_value='viewer'), \
         patch.object(mod, 'pr_diff', return_value=diff), \
         patch.object(mod, 'collect_metadata', return_value=({}, {})), \
         patch.object(mod, 'fetch_bodies', return_value={}), patch.object(mod, 'linked_issues', return_value=[]), \
         patch.object(mod, 'analyze', side_effect=analyze):
        scanned, path = mod.scan(state['pr']['url'])
        assert path.is_file()
        assert scanned['pr']['headRepository'] == state['pr']['headRepository']


@pytest.mark.parametrize('representation', ['unified', 'empty', 'legacy'])
def test_restatement_targets_follow_authoritative_context_records(representation):
    from shared import decision_restatements
    url = 'https://github.com/example/fixture/pull/1'
    local = [{'text': 'Retired transform choice', 'source': 'Original human choice'},
             {'text': 'Current transform choice', 'source': 'Explicit replacement answer'}]
    old = mod.record(local[0]['text'], 'local', [], source=local[0]['source'])
    state = {'pr': {'url': url, 'number': 1, 'headRefOid': 'a' * 40, 'body': ''},
             'decisions': [], 'manual_decisions': [],
             'replacements': [{'old': old, 'new_id': mod.decision_id(local[1]['text']), 'raw': None}]}
    with patch.object(mod, 'gh_json', return_value={'url': url, 'body': '', 'headRefOid': 'a' * 40}):
        context = mod.decision_context(state, local)
    context['decisions'] = mod.decision_records(state, context['local_decisions'])
    assert context['decisions'][0]['status'] == 'superseded'
    context['live_decisions'] = 'Legacy scope wording'
    if representation == 'empty':
        context['decisions'] = []
    elif representation == 'legacy':
        context.pop('decisions')
    analysis = {'boundaries': {'correctness': [{'text': 'Preserve the supported transform'}]}}
    _, targets = decision_restatements.index(context, context, {}, analysis)
    expected = ['original/analysis#/boundaries/correctness/0']
    if representation == 'unified':
        expected.insert(0, 'original/decisions#/decisions/1')
    elif representation == 'legacy':
        expected = ['original/decisions#/local_decisions/0', 'original/decisions#/local_decisions/1',
                    'original/decisions#/live_decisions', *expected]
    assert targets == expected
