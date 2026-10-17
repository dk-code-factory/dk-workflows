import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'decisions.py'
spec = importlib.util.spec_from_file_location('decisions', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


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
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'number': 2,
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

    def test_large_diff_uses_pinned_local_three_dot_diff(self):
        base, head = 'a'*40, 'b'*40
        pr = {'url': 'https://github.com/o/r/pull/2', 'baseRefOid': base,
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
        pr = {'url': 'https://github.com/o/r/pull/2', 'baseRefOid': 'a'*40,
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
        pr = {'url': 'https://github.com/o/r/pull/2', 'baseRefOid': 'a'*40,
              'baseRefName': 'main', 'headRefOid': 'b'*40}
        live = {k: pr[k] for k in ('baseRefOid', 'baseRefName', 'headRefOid')}
        live['headRefOid'] = 'c'*40
        with patch.object(mod, 'run', side_effect=RuntimeError('HTTP 406: PullRequest.diff too_large')) as run, \
             patch.object(mod, 'gh_json', return_value=live):
            with self.assertRaisesRegex(RuntimeError, 'base or head changed'):
                mod.pr_diff(pr)
        self.assertEqual(run.call_count, 1)

    def test_local_diff_incomplete_file_coverage_fails_closed(self):
        pr = {'url': 'https://github.com/o/r/pull/2', 'baseRefOid': 'a'*40,
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
        pr = {'url': 'https://github.com/o/r/pull/2', 'baseRefOid': 'a'*40,
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
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'number': 2,
                        'headRefOid': 'a'*40, 'body': 'original'}}
        item = {'text': 'Use a cache', 'references': []}
        with patch.object(mod, 'gh_json', return_value={'body': 'changed', 'headRefOid': 'a'*40}) as gh:
            outcome = mod.publish(state, [item])
        self.assertEqual(outcome['pending'], [item])
        self.assertEqual(gh.call_count, 1)

    def test_publish_edits_only_body_once(self):
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'number': 2,
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
        state = {'pr': {'url': 'https://github.com/o/r/pull/2', 'number': 2,
                        'headRefOid': 'a'*40}, 'decisions': []}
        diff = 'diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -0,0 +4,1 @@\n+cache = {}\n'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            with patch.object(mod, 'run', return_value=diff), patch.object(mod, 'execute', return_value=json.dumps({'decisions': [
                    {'text': 'Use a cache', 'references': [link]}]})), \
                 patch.object(mod, 'publish') as publish:
                result = mod.suggest_from_code(state, path)
                self.assertEqual(result, mod.suggest_from_code(state, path))
                publish.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['code_suggestions']['head'], 'a'*40)

    def test_manual_add_records_once_without_publishing(self):
        ref = 'https://github.com/o/r/pull/2#issuecomment-1'
        item = {'text': 'Use a cache', 'references': [ref]}
        state = {'pr': {'headRefOid': 'a'*40}, 'comments': {'c': {'url': ref}},
                 'decisions': [], 'manual_decisions': [], 'code_suggestions': {}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            argv = ['decisions.py', 'add', '--json', json.dumps(item)]
            with patch.object(sys, 'argv', argv), patch.object(mod, 'scan', return_value=(state, path)), \
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
                 patch.object(mod, 'scan', return_value=(state, Path(td)/'state.json')):
                with self.assertRaisesRegex(ValueError, 'exact discussion or code source'):
                    mod.cli()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://github.com/o/r/pull/2'
        self.state = {'pr': {'url': self.url, 'number': 2, 'headRefOid': 'a'*40,
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

    def test_cache_path_rejects_path_traversal(self):
        for url in ('https://github.com/../r/pull/2', 'https://github.com/o/../pull/2'):
            with self.assertRaises(ValueError):
                mod.cache_path(url)


if __name__ == '__main__': unittest.main()
