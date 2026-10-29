import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preparation_fixtures import Package, ROOT, analysis, prep, response, retain, architecture_review

NATIVE_ARCHITECTURE_ANALYZE = prep.architecture.analyze


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = Package(self.temp.name)

    def test_legacy_preparation_is_historical_only_and_cannot_supply_current_coverage(self):
        from shared import review_coverage, review_sessions
        package = self.package
        saved = package.prepare()
        current = prep.read(saved['artifact'])
        for version in (1, 2):
            with self.subTest(version=version):
                old = copy.deepcopy(current)
                old['preparation_version'] = version
                if version == 1:
                    old = {key: old[key] for key in ('preparation_version', 'mode', 'identity',
                        'sources', 'reviewers', 'analysis', 'readiness')}
                    old['sources'] = {key: {field: source[field] for field in ('path', 'sha256')}
                        for key, source in old['sources'].items() if not key.startswith('architecture')}
                old['preparation_id'] = prep.digest({k: v for k, v in old.items() if k != 'preparation_id'})
                path = Path(saved['artifact']).parent / ('legacy-' + str(version) + '.json')
                path.write_text(json.dumps(old))
                self.assertEqual(prep.historical(path), old)
                with self.assertRaisesRegex(ValueError, 'historical preparation cannot authorize current work'):
                    prep.gate(path, pr_url=package.context['url'], head_sha=package.head)
                session = review_sessions.Session(package.context, old, ['example'])
                reference = session.finish({'complete': True, 'findings': [],
                    'executed_reviewers': ['example'], 'reused_reviewers': [], 'skipped_reviewers': []})
                with self.assertRaisesRegex(ValueError, 'historical preparation cannot authorize current review coverage'):
                    review_coverage.verify(reference, old)
                historical = review_coverage.verify(reference, old, checkout=package.repo)
                self.assertTrue(historical['result']['complete'])
                self.assertNotIn('architecture_review', historical['result'])

    def test_pre_architecture_history_remains_readable_but_cannot_authorize_work(self):
        saved = self.package.prepare()
        old = prep.read(saved['artifact'])
        old.pop('architecture')
        old['sources'].pop('architecture/0')
        old['inputs']['sources'].pop('architecture/0')
        old['inputs']['policy'] = 'previous-policy'
        for node in old['graph'].values():
            node['sources'] = [source for source in node['sources'] if source != 'architecture/0']
        old['engineering_id'] = prep.engineering_id(old)
        old['preparation_id'] = prep.digest({key: value for key, value in old.items() if key != 'preparation_id'})
        path = self.package.write('past-preparation.json', old)
        self.assertEqual(prep.historical(path), old)
        with self.assertRaisesRegex(ValueError, 'policy changed|Architecture preparation missing'):
            prep.gate(path, pr_url=self.package.context['url'], head_sha=self.package.head)

    def test_codex_output_failure_blocks_preparation_without_changing_iteration_history(self):
        package = self.package
        state = module('output_failure_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        state.start_run(record)
        record['cycles'] = [{'number': 1, 'head': package.head}]
        record['repairs'] = [{'cycle': 1, 'number': 1}]
        record['run_history'] = [{'cycles': [{'number': 5}], 'outcome': 'limited'}]
        record_path = package.write('run-record.json', record)
        original = record_path.read_bytes()
        package.manifest['mode'] = 'iteration'
        package.manifest['evidence']['run_history'] = ['run-record.json']
        package.write('manifest.json', package.manifest)
        binary = package.root / 'bin'
        binary.mkdir()
        model = binary / 'codex'
        calls = package.root / 'model-calls.txt'
        model.write_text('#!/usr/bin/env python3\nimport json,pathlib,sys\n'
                         f'with pathlib.Path({str(calls)!r}).open("a") as f: f.write("call\\n")\n'
                         'output = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])\n'
                         'output.write_text(\'{"partial":\')\n'
                         f'print({json.dumps(response())!r})\n'
                         'print(f"Failed to write last message file {json.dumps(str(output))}: Disk quota exceeded", file=sys.stderr)\n')
        model.chmod(0o755)
        env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'DK_HOST': 'codex'}
        env.pop('DK_REVIEW_STATS_TRACE', None)
        versions_before = sorted(package.versions.iterdir())
        result = subprocess.run([sys.executable, str(ROOT / 'dk-pr-decisions/scripts/decisions.py'),
                                 'prepare', '--input-manifest', str(package.manifest_path),
                                 '--output-dir', str(package.versions)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'error')
        self.assertEqual(receipt['error']['code'], 'provider_output_failed')
        self.assertFalse(receipt['publication']['updated'])
        self.assertIn('Repair storage', receipt['error']['message'])
        self.assertNotIn('readiness', result.stdout)
        self.assertNotIn('Model JSON invalid', result.stdout)
        self.assertEqual(calls.read_text(), 'call\n')
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(sorted(package.versions.iterdir()), versions_before)
        with self.assertRaisesRegex(ValueError, 'preparation required'):
            state.next_cycle(record, package.head)
        self.assertEqual(record_path.read_bytes(), original)

    def empty_feedback(self):
        identity = {'url': self.package.context['url'], 'headRefOid': self.package.head}
        inline = {'schemaVersion': 1, 'complete': True, 'pullRequest': identity,
                  'threads': [], 'threadCount': 0, 'threadsSha256': prep.digest([]),
                  'items': [], 'itemCount': 0, 'itemsSha256': prep.digest([])}
        conversation = {'complete': True, 'pullRequest': identity, 'reviews': [], 'reviewCount': 0,
                        'comments': [], 'commentCount': 0, 'items': [], 'itemCount': 0}
        conversation['sha256'] = prep.digest(conversation)
        return inline, conversation

    def test_complete_feedback_requires_pinned_identity_and_supported_collections_before_model(self):
        package = self.package
        package.manifest['evidence']['feedback'] = ['feedback.json']
        package.write('manifest.json', package.manifest)
        malformed = [{'complete': True}, {'complete': False, 'items': []}]
        for snapshot in self.empty_feedback():
            fields = ('pullRequest', 'items', 'itemCount') + (
                ('threads', 'threadCount', 'threadsSha256', 'itemsSha256') if 'threads' in snapshot else
                ('reviews', 'reviewCount', 'comments', 'commentCount', 'sha256'))
            malformed.extend({k: v for k, v in snapshot.items() if k != field} for field in fields)
            for field, changed in (('url', package.context['url'] + '0'), ('headRefOid', 'wrong-head')):
                malformed.append({**snapshot, 'pullRequest': {**snapshot['pullRequest'], field: changed}})
            malformed.append({**snapshot, 'items': None})
            malformed.append({**snapshot, 'itemCount': 1})
            malformed.append({**snapshot, 'schemaVersion': 99})
        for snapshot in malformed:
            if 'sha256' in snapshot:
                snapshot['sha256'] = prep.digest({k: v for k, v in snapshot.items() if k != 'sha256'})
        for snapshot in self.empty_feedback():
            field = 'threadsSha256' if 'threads' in snapshot else 'sha256'
            malformed.append({**snapshot, field: 'invalid fingerprint'})
        for number, snapshot in enumerate(malformed):
            package.write('feedback.json', snapshot)
            with self.subTest(number=number), patch.object(prep, 'analyze') as model:
                with self.assertRaisesRegex(ValueError, 'feedback'):
                    prep.prepare(package.manifest_path, package.root / 'versions')
                model.assert_not_called()

    def test_empty_complete_inline_and_conversation_feedback_are_valid(self):
        package = self.package
        package.write('inline.json', self.empty_feedback()[0])
        package.write('conversation.json', self.empty_feedback()[1])
        package.manifest['evidence']['feedback'] = ['inline.json', 'conversation.json']
        package.write('manifest.json', package.manifest)
        result = package.prepare()
        self.assertEqual(result['readiness'], 'ready')
        self.assertEqual(result['refresh']['model_call_count'], 1)

    def test_complete_sources_local_choices_and_standalone_no_mutations(self):
        package = self.package
        choice = {'text': 'Keep encoding scope narrow', 'source': 'Exact human answer: encoding only'}
        package.decisions['local_decisions'] = [choice]
        decisions_api = module('preparation_decisions', 'dk-pr-decisions/scripts/decisions.py')
        package.decisions['decisions'] = [decisions_api.record(choice['text'], 'local', [], source=choice['source'])]
        package.write('decisions.json', package.decisions)
        prompts = []
        before = prep.git(package.repo, 'rev-parse', 'HEAD')
        def model(prompt, checkout):
            prompts.append(prompt)
            return response()
        result = prep.prepare(package.manifest_path, package.root / 'versions', analyzer=model)
        artifact = prep.load(result['artifact'], package.context, package.decisions)
        self.assertEqual(result['readiness'], 'ready')
        self.assertEqual(artifact['mode'], 'standalone')
        self.assertIn(choice['source'], prompts[0])
        for category in prep.EVIDENCE:
            self.assertIn(category + '/0', artifact['sources'])
        self.assertEqual(prep.git(package.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual(prep.git(package.repo, 'status', '--porcelain=v1'), '')
        retained = Path(result['artifact']).parent / 'evidence/user_instructions/0'
        self.assertEqual(retained.read_bytes(), (package.root / 'user.json').read_bytes())

    def test_cli_uses_real_model_subprocess_and_preserves_api(self):
        package = self.package
        odd_directory = os.fsdecode(b'odd-\xff')
        odd_path = odd_directory + '/' + os.fsdecode(b'file-\xfe.py')
        target = package.repo / odd_path
        target.parent.mkdir()
        target.write_text('value = 1\n')
        def commit():
            prep.git(package.repo, 'add', '-A')
            prep.git(package.repo, 'commit', '-qm', 'Lossless filename fixture')
            package.head = prep.git(package.repo, 'rev-parse', 'HEAD')
            package.context['head_sha'] = package.head
            package.decisions['head_sha'] = package.head
            package.write('context.json', package.context)
            package.write('decisions.json', package.decisions)
        commit()
        initial_response = response()
        for node in initial_response['graph']:
            if node['location'] == '/boundaries/correctness/0':
                node['paths'].append({'path': odd_directory, 'kind': 'subtree'})
        package.write('response.json', initial_response)
        binary = package.root / 'bin'
        binary.mkdir()
        model = binary / 'codex'
        model.write_text('#!/usr/bin/env python3\nimport json, os, pathlib, sys\n'
                         'prompt = sys.stdin.read()\n'
                         'prompt.encode("utf-8", errors="strict")\n'
                         'assert "read-only" in prompt and "INPUT INDEX" in prompt\n'
                         'contract = pathlib.Path(prompt.split("RESPONSE CONTRACT: ", 1)[1].splitlines()[0])\n'
                         'assert contract.is_absolute() and "## Reassessment response" in contract.read_text()\n'
                         'with open(os.environ["MODEL_CALLS"], "a") as calls: calls.write(json.dumps(prompt) + "\\n")\n'
                         'pathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(pathlib.Path(os.environ["MODEL_RESPONSE"]).read_text())\n')
        model.chmod(0o755)
        calls = package.root / 'model-calls.jsonl'
        env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
               'DK_HOST': 'codex', 'MODEL_RESPONSE': str(package.root / 'response.json'), 'MODEL_CALLS': str(calls)}
        def invoke(*args):
            result = subprocess.run([sys.executable, str(ROOT / 'dk-pr-decisions/scripts/decisions.py'),
                                     'prepare', '--input-manifest', str(package.manifest_path),
                                     '--output-dir', str(package.root / 'versions'), *args], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            receipt = json.loads(result.stdout)
            self.assertEqual((receipt['schema_version'], receipt['command'], receipt['status']), (2, 'prepare', 'ok'))
            self.assertEqual(receipt['data']['readiness'], 'ready')
            return receipt['data']
        first = invoke()
        self.assertEqual((first['refresh']['mode'], first['refresh']['model_call_count']), ('initial', 1))
        initial_calls = calls.read_bytes()
        self.assertEqual(len(initial_calls.splitlines()), 1)
        unchanged = invoke('--previous-preparation', first['artifact'])
        self.assertEqual((unchanged['refresh']['mode'], unchanged['refresh']['model_call_count']), ('unchanged', 0))
        self.assertEqual(unchanged['artifact'], first['artifact'])
        self.assertEqual(unchanged['preparation_id'], first['preparation_id'])
        self.assertEqual(calls.read_bytes(), initial_calls)

        parent = prep.read(first['artifact'])
        package.write('response.json', {
            'dispositions': [{'id': key, 'action': 'retain', 'reason': 'Full inspection confirms the existing conclusion'}
                             for key in parent['graph']], 'additions': [], 'impacts': []})
        full = invoke('--previous-preparation', first['artifact'], '--full-analysis')
        self.assertEqual((full['refresh']['mode'], full['refresh']['model_call_count']), ('full', 1))
        self.assertNotEqual(full['artifact'], first['artifact'])
        refreshed = prep.read(full['artifact'])
        self.assertEqual(refreshed['parent']['preparation_id'], first['preparation_id'])
        self.assertEqual(refreshed['analysis'], parent['analysis'])
        prompts = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(len(prompts), 2)
        self.assertIn('REASSESSMENT', prompts[-1])
        self.assertIn('"full_reassessment": true', prompts[-1])
        self.assertIn(first['artifact'], prompts[-1])
        self.assertIn(odd_path, refreshed['inputs']['tree'])
        self.assertEqual(os.fsencode(odd_path), b'odd-\xff/file-\xfe.py')
        target.write_text('value = 2\n')
        commit()
        package.write('response.json', retain(refreshed))
        changed = invoke('--previous-preparation', full['artifact'])
        self.assertEqual((changed['refresh']['mode'], changed['refresh']['model_call_count']), ('incremental', 1))
        self.assertEqual(changed['refresh']['changed_paths'], [odd_path])
        boundary = next(key for key, node in refreshed['graph'].items() if node['location'] == '/boundaries/correctness/0')
        self.assertIn(boundary, changed['refresh']['affected_conclusions'])
        saved = prep.load(changed['artifact'], package.context, package.decisions)
        self.assertIn(odd_path, saved['inputs']['tree'])
        self.assertEqual(saved['analysis'], refreshed['analysis'])
        self.assertEqual(invoke('--previous-preparation', changed['artifact'])['refresh']['model_call_count'], 0)
        prompts = [json.loads(line) for line in calls.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(prompts), 3)
        self.assertIn(json.dumps(odd_path), prompts[-1])

    def test_missing_evidence_and_material_instructions_block_before_model(self):
        for category in prep.EVIDENCE:
            manifest = copy.deepcopy(self.package.manifest)
            manifest['evidence'][category] = []
            self.package.write('manifest.json', manifest)
            with self.subTest(category=category), patch.object(prep, 'analyze') as model:
                with self.assertRaises(ValueError):
                    self.package.prepare()
                model.assert_not_called()
        self.package.write('manifest.json', self.package.manifest)
        descriptor = json.loads((self.package.root / 'reviewer.json').read_text())
        descriptor['complete'] = False
        descriptor['missing'] = ['inaccessible material procedure']
        self.package.write('reviewer.json', descriptor)
        with self.assertRaisesRegex(ValueError, 'adaptation incomplete'):
            self.package.prepare()

    def test_plain_text_prior_absence_reports_source_and_blocks_before_model(self):
        package = self.package
        prior = package.root / 'prior.txt'
        prior.write_text('No previous preparation exists for this run.\n')
        manifest = copy.deepcopy(package.manifest)
        manifest['evidence']['prior_preparation'] = [str(prior)]
        package.write('manifest.json', manifest)
        with patch.object(prep, 'analyze') as model:
            with self.assertRaisesRegex(ValueError, 'prior_preparation.*valid JSON') as error:
                prep.prepare(package.manifest_path, package.root / 'versions', analyzer=model)
            model.assert_not_called()
        self.assertIn(str(prior), str(error.exception))
        self.assertFalse((package.root / 'versions').exists())

    def test_native_analysis_corrects_fenced_json_once(self):
        expected = analysis()
        invalid = '```json\n' + json.dumps(expected) + '\n```'
        with patch('shared.executors.execute', side_effect=[invalid, json.dumps(expected)]) as model:
            result = prep.analyze('Frozen evidence', self.package.repo)
        self.assertEqual(result, expected)
        self.assertEqual(model.call_count, 2)
        self.assertEqual([call.kwargs['attempt'] for call in model.call_args_list], [0, 1])
        correction = model.call_args_list[1].args[0]
        self.assertTrue(correction.startswith('Frozen evidence'))
        self.assertIn('Prior response:\n' + invalid, correction)
        self.assertIn('without Markdown fences', correction)

    def test_native_analysis_corrects_invalid_fields_before_readiness(self):
        invalid = analysis()
        invalid['worker_instructions'] = ['Preserve the supported transform']
        with patch('shared.executors.execute', side_effect=[json.dumps(response(invalid)), json.dumps(response())]) as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions')
        artifact = prep.load(result['artifact'], self.package.context, self.package.decisions)
        self.assertEqual(artifact['readiness'], 'ready')
        self.assertEqual(artifact['analysis'], analysis())
        self.assertEqual(model.call_count, 2)
        self.assertIn('worker instructions and completion criteria required', model.call_args_list[1].args[0])

    def test_call_count_includes_architecture_and_preparation_corrections(self):
        architecture = prep.architecture
        package = self.package
        identity = architecture.context_identity(package.context)
        catalog = architecture.current(package.repo, identity, baseline=package.context['base_sha'])
        architecture_response = {key: catalog[key] for key in ('knowledge', 'rules', 'proposals', 'conflicts')}
        directory, _ = architecture.folder(package.repo, identity)
        architecture.baseline_pointer(directory, package.context['base_sha']).unlink()
        invalid = response()
        invalid['analysis']['worker_instructions'] = []
        with patch.object(architecture, 'analyze', NATIVE_ARCHITECTURE_ANALYZE), \
                patch('shared.executors.execute', side_effect=[
                    'invalid JSON', json.dumps(architecture_response), json.dumps(invalid), json.dumps(response())]) as model:
            result = prep.prepare(package.manifest_path, package.versions)
        self.assertEqual(model.call_count, 4)
        self.assertEqual(result['refresh']['model_call_count'], 4)
        self.assertEqual(prep.read(result['artifact'])['refresh']['model_call_count'], 4)
        with patch('shared.executors.execute') as model:
            reused = prep.prepare(package.manifest_path, package.versions, previous_preparation=result['artifact'])
        model.assert_not_called()
        self.assertEqual(reused['refresh']['model_call_count'], 0)

    def test_call_count_includes_architecture_reinitialization_during_source_recheck(self):
        architecture = prep.architecture
        package = self.package
        identity = architecture.context_identity(package.context)
        catalog = architecture.current(package.repo, identity, baseline=package.context['base_sha'])
        architecture_response = {key: catalog[key] for key in ('knowledge', 'rules', 'proposals', 'conflicts')}
        directory, _ = architecture.folder(package.repo, identity)
        collect = prep.collect
        for recheck in (2, 3):
            collections = 0
            def recollect(*args, **kwargs):
                nonlocal collections
                collections += 1
                if collections == recheck:
                    # A catalog may disappear before either source freshness recheck.
                    architecture.baseline_pointer(directory, package.context['base_sha']).unlink()
                return collect(*args, **kwargs)
            with self.subTest(recheck=recheck), \
                    patch.object(architecture, 'analyze', NATIVE_ARCHITECTURE_ANALYZE), \
                    patch.object(prep, 'collect', side_effect=recollect), \
                    patch('shared.executors.execute', side_effect=[
                        json.dumps(response()), json.dumps(architecture_response)]) as model:
                result = prep.prepare(package.manifest_path, package.versions)
                self.assertEqual(model.call_count, 2)
                self.assertEqual(result['refresh']['model_call_count'], 2)
                self.assertEqual(prep.read(result['artifact'])['refresh']['model_call_count'], 2)

    def test_invalid_analysis_exhausts_correction_without_preparing(self):
        for raw in ('not JSON', json.dumps({'boundaries': {}}), '[]'):
            with self.subTest(raw=raw):
                # Each malformed response is an independent request; restarting
                # identical failed evidence must not replenish its corrections.
                self.package.write('user.json', {'request': 'Validate response case: ' + raw})
                with patch('shared.executors.execute', return_value=raw) as model:
                    with self.assertRaisesRegex(ValueError, 'invalid after one correction'):
                        prep.prepare(self.package.manifest_path, self.package.root / 'versions')
                self.assertEqual(model.call_count, 2)
                self.assertFalse((self.package.root / 'versions').exists())

    def test_provider_failure_is_not_retried_as_invalid_analysis(self):
        with patch('shared.executors.execute', side_effect=RuntimeError('authentication failed')) as model:
            with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
                prep.prepare(self.package.manifest_path, self.package.root / 'versions')
        model.assert_called_once()
        self.assertFalse((self.package.root / 'versions').exists())

    def test_proposals_and_sync_are_readiness_gates(self):
        result = analysis()
        result['boundaries']['correctness'][0].update(agreement='proposal', material=True)
        artifact_path = self.package.prepare(result)['artifact']
        artifact = prep.read(artifact_path)
        self.assertEqual(artifact['readiness'], 'needs_decision')
        with self.assertRaisesRegex(ValueError, 'needs_decision'):
            prep.load(artifact_path, self.package.context, self.package.decisions)
        result = analysis()
        result.update(sync_required=True, sync_reason='Agreed scope still absent from PR prose')
        artifact_path = self.package.prepare(result)['artifact']
        with self.assertRaisesRegex(ValueError, 'sync_pending'):
            prep.load(artifact_path, self.package.context, self.package.decisions)

    def question(self):
        return {'id': 'sanitize-scope', 'status': 'open', 'material': True,
                'statements': 'Blanket guarantee conflicts with encoding-only scope',
                'affected_work': 'Decoder expansion and regression coverage',
                'options': [{'choice': 'Encoding only', 'benefits': 'Bounded change', 'costs': 'Narrower guarantee'},
                            {'choice': 'All input', 'benefits': 'Broad guarantee', 'costs': 'Decoder complexity'}],
                'recommendation': 'Ask human to choose based on product promise',
                'proposed_changes': 'Clarify scope and synchronize prose', 'evidence': ['user_instructions/0#instruction']}

    def test_questions_survive_resume_and_answer_at_same_head_creates_new_version(self):
        result = analysis()
        result['questions'] = [self.question()]
        first = self.package.prepare(result)
        self.package.manifest['evidence']['prior_preparation'] = [first['artifact']]
        self.package.write('manifest.json', self.package.manifest)
        with self.assertRaisesRegex(ValueError, 'unanswered question lost'):
            self.package.prepare(analysis())
        self.package.write('user.json', {'exact_answer': 'Encoding only', 'rationale': 'Keep the PR bounded'})
        resolved = copy.deepcopy(result)
        resolved['questions'][0].update(status='resolved', resolution='Human explicitly chose encoding only; see user_instructions/0')
        second = self.package.prepare(resolved)
        self.assertEqual(second['readiness'], 'ready')
        self.assertNotEqual(first['preparation_id'], second['preparation_id'])
        self.assertTrue(Path(first['artifact']).exists())
        self.assertEqual(prep.read(first['artifact'])['identity']['head_sha'],
                         prep.read(second['artifact'])['identity']['head_sha'])

    def test_stale_instructions_decisions_rules_and_head_are_rejected(self):
        saved = self.package.prepare()
        artifact = prep.read(saved['artifact'])
        for changes in ({'head_sha': 'a' * 40}, {'base_sha': 'b' * 40}, {'url': 'https://github.com/o/r/pull/3'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'identity is stale'):
                prep.validate(artifact, {**self.package.context, **changes}, self.package.decisions)
        with self.assertRaisesRegex(ValueError, 'identity is stale'):
            prep.validate(artifact, self.package.context, {**self.package.decisions, 'local_decisions': [{'text': 'Changed'}]})
        for name in ('user.json', 'absence.json'):
            original = (self.package.root / name).read_text()
            (self.package.root / name).write_text('changed')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'source changed'):
                prep.load(saved['artifact'], self.package.context, self.package.decisions)
            (self.package.root / name).write_text(original)

    def test_retained_source_changes_require_fresh_preparation_and_conflicts_block_work(self):
        package = self.package
        api = module('retained_preparation_decisions', 'dk-pr-decisions/scripts/decisions.py')
        pr = {'url': package.context['url'], 'number': 1, 'body': '', 'headRefOid': package.head}
        ref = pr['url'] + '#issuecomment-1'
        original = {'url': ref, 'body': 'Agreed: preserve the parser.', 'updatedAt': 'first'}
        choice = {'text': 'Preserve the parser', 'references': [ref]}
        state = {'pr': pr, 'comments': {'c': original}, 'decisions': [choice]}
        api.retain_agreements(state)

        def context():
            with patch.object(api, 'gh_json', return_value=pr):
                data = api.decision_context(state)
            data['decisions'] = api.decision_records(state)
            return data

        package.decisions = context()
        package.write('decisions.json', package.decisions)
        prepared = package.prepare()
        artifact = prep.load(prepared['artifact'], package.context, package.decisions)
        for comments in ({'c': {**original, 'body': 'Change the parser.', 'updatedAt': 'second'}}, {}):
            state['comments'] = comments
            package.decisions = context()
            self.assertEqual(package.decisions['head_sha'], package.head)
            self.assertEqual(package.decisions['decisions'][0]['id'], api.decision_id(choice['text']))
            self.assertIn(original['body'], package.decisions['decisions'][0]['source'])
            with self.assertRaisesRegex(ValueError, 'identity is stale'):
                prep.validate(artifact, package.context, package.decisions)
            package.write('decisions.json', package.decisions)
            blocked = analysis()
            question = self.question()
            question.update(statements='Retained parser agreement conflicts with changed source evidence',
                            evidence=['decisions#decisions'])
            blocked['questions'] = [question]
            pending = package.prepare(blocked)
            self.assertEqual(pending['readiness'], 'needs_decision')
            with self.assertRaisesRegex(ValueError, 'needs_decision'):
                prep.load(pending['artifact'], package.context, package.decisions)

    def test_rejects_tampered_readiness_and_invalid_placement_or_strategy(self):
        saved = self.package.prepare()
        artifact = prep.read(saved['artifact'])
        artifact['readiness'] = 'sync_pending'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            prep.validate(artifact, self.package.context, self.package.decisions)
        for field in ('placement', 'test_strategy', 'reviewer_addenda'):
            bad = analysis()
            bad[field] = {} if field != 'test_strategy' else []
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.package.prepare(bad)

    def test_ordered_reviewer_paths_are_fingerprinted_and_cover_descriptors(self):
        artifact = prep.read(self.package.prepare()['artifact'])
        expected = [artifact['sources']['reviewers/0']['path']]
        self.assertEqual(artifact['reviewer_paths'], expected)
        for paths in (None, {}, [], [None], ['missing'], expected * 2):
            bad = {**artifact, 'reviewer_paths': paths}
            with self.subTest(paths=paths):
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    prep.validate(bad, self.package.context, self.package.decisions)
                bad['preparation_id'] = prep.digest({k: v for k, v in bad.items() if k != 'preparation_id'})
                with self.assertRaisesRegex(ValueError, 'ordered reviewer paths'):
                    prep.validate(bad, self.package.context, self.package.decisions)

    def test_refresh_records_order_for_legacy_v2_without_model(self):
        first = self.package.prepare()
        legacy = prep.read(first['artifact'])
        legacy.pop('reviewer_paths')
        legacy['preparation_id'] = prep.digest({k: v for k, v in legacy.items() if k != 'preparation_id'})
        Path(first['artifact']).chmod(0o600)
        Path(first['artifact']).write_text(json.dumps(legacy))
        self.assertEqual(prep.historical(first['artifact']), legacy)
        with patch.object(prep, 'analyze') as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions',
                                  previous_preparation=first['artifact'])
        model.assert_not_called()
        fresh = prep.load(result['artifact'], self.package.context, self.package.decisions)
        self.assertNotEqual(result['artifact'], first['artifact'])
        self.assertEqual(result['refresh']['mode'], 'operational')
        self.assertEqual(result['refresh']['model_call_count'], 0)
        self.assertEqual(fresh['reviewer_paths'], [fresh['sources']['reviewers/0']['path']])
        self.assertEqual(fresh['parent']['preparation_id'], legacy['preparation_id'])
        self.assertEqual(fresh['analysis'], legacy['analysis'])

    def test_changes_during_analysis_and_in_repo_output_are_rejected(self):
        def mutate(prompt, checkout):
            self.package.write('user.json', {'instruction': 'Changed during analysis'})
            return response()
        with self.assertRaisesRegex(ValueError, 'source changed during analysis'):
            prep.prepare(self.package.manifest_path, self.package.root / 'versions', analyzer=mutate)
        with patch.object(prep, 'analyze') as model, self.assertRaisesRegex(ValueError, 'outside the repository'):
            prep.prepare(self.package.manifest_path, self.package.repo / 'artifacts')
        model.assert_not_called()

    def test_iteration_history_validity_and_scope_remain_separate(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        saved = package.prepare()
        state = module('preparation_state', 'dk-pr-iter/scripts/run_state.py')
        record = {'cycles': [{'number': 1, 'head': package.head}], 'local_decisions': [],
                  'repairs': [{'cycle': 1, 'number': 1}], 'events': [{'key': 'prior-work'}]}
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        state.classify(record, 'feedback-1', package.head, 'valid', 'source', 'Reproduced behavior')
        state.assess(record, 'feedback-1', package.head, saved['preparation_id'], 'outside_scope',
                     'Exact agreed exclusion', 'Encoding scope excludes decoder expansion')
        self.assertEqual(record['threads']['feedback-1'][package.head]['classification'], 'valid')
        self.assertEqual(record['assessments'][0]['disposition'], 'outside_scope')
        self.assertEqual(state.fix_plan(record, 'feedback-1', package.head)['action'], 'skip')
        state.next_cycle(record, package.head)
        self.assertEqual(len(record['cycles']), 2)
        self.assertEqual(len(record['repairs']), 1)
        state.record_local_decision(record, 'New agreed condition', 'Exact human answer')
        with self.assertRaisesRegex(ValueError, 'local decisions changed'):
            state.work_gate(record, package.head)
        # Old records cannot start work without obtaining preparation first.
        with self.assertRaisesRegex(ValueError, 'preparation required'):
            state.next_cycle({'cycles': [{'number': 1, 'head': package.head}]}, package.head)
        # A new answer/preparation at the same head retains classifications,
        # earlier scope assessment, cycle counts and repair history.
        package.decisions['local_decisions'] = record['local_decisions']
        decisions_api = module('resume_decisions', 'dk-pr-decisions/scripts/decisions.py')
        package.decisions['decisions'] = [decisions_api.record('New agreed condition', 'local', [], source='Exact human answer')]
        package.write('decisions.json', package.decisions)
        package.manifest['evidence']['prior_preparation'] = [saved['artifact']]
        package.write('manifest.json', package.manifest)
        refreshed = prep.prepare(package.manifest_path, package.root / 'versions',
                                 previous_preparation=saved['artifact'],
                                 analyzer=lambda *_: retain(prep.read(saved['artifact'])))
        state.attach_preparation(record, refreshed['artifact'], package.context['url'], package.head)
        state.assess(record, 'feedback-1', package.head, refreshed['preparation_id'], 'within_scope',
                     'Exact human answer', 'New condition permits this remedy')
        self.assertEqual(state.fix_plan(record, 'feedback-1', package.head)['action'], 'fix')
        self.assertEqual(len(record['assessments']), 2)
        self.assertEqual(len(record['preparations']), 2)
        self.assertEqual(len(record['cycles']), 2)
        self.assertEqual(len(record['repairs']), 1)

    def test_blocked_preparation_can_record_holds_but_cannot_authorize_initial_work(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        result = analysis()
        result['questions'] = [self.question()]
        saved = package.prepare(result)
        state = module('blocked_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        state.start_run(record)
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        state.assess(record, 'feedback-1', package.head, saved['preparation_id'], 'decision_blocked',
                     'Conflicting exact scope statements', 'Human scope decision needed')
        self.assertEqual(state.fix_plan(record, 'feedback-1', package.head)['action'], 'decision_blocked')
        with self.assertRaisesRegex(ValueError, 'needs_decision'):
            state.work_gate(record, package.head)
        with self.assertRaisesRegex(ValueError, 'needs_decision'):
            state.next_cycle(record, package.head)
        with self.assertRaisesRegex(ValueError, 'needs_decision'):
            state.attempt(record, 'repair', 0, package.head)

    def test_registration_rejects_preparation_that_omits_existing_local_choice(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        saved = package.prepare()
        state = module('omitted_choice_state', 'dk-pr-iter/scripts/run_state.py')
        record = {'local_decisions': [{'text': 'Human chosen boundary', 'source': 'Exact user instruction'}]}
        with self.assertRaisesRegex(ValueError, 'omits run-record local choices'):
            state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)

    def test_completion_cannot_bypass_pending_or_stale_preparation(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        saved = package.prepare()
        completion = module('prepared_completion', 'dk-pr-iter/scripts/completion.py')
        args = (package.head, package.head, package.head, True, False, 'success')
        self.assertEqual(completion.completion_gate(*args, stage='review'), 'preparation_missing')
        state = module('completion_preparation_state', 'dk-pr-iter/scripts/run_state.py')
        record = {}
        state.start_run(record)
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        state.next_cycle(record, package.head)
        record_path = package.write('run-record.json', record)
        from shared.review_sessions import Session
        binding = state.review_session(record, package.head, record_path)
        package.write('run-record.json', record)
        session = Session(package.context, prep.read(saved['artifact']), ['example'], path=binding['session_file'])
        item = session.finish({'complete': True, 'findings': [], 'executed_reviewers': ['example'],
                               'architecture_review': architecture_review(prep.read(saved['artifact']), package.context, package.decisions),
                               'reused_reviewers': [], 'skipped_reviewers': []})
        record = state.read(Path(record_path), {})
        state.register_review(record, {'review_artifact': item}, package.head)
        package.write('run-record.json', record)
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   pr_url=package.context['url']), 'run_record_missing')
        verification = {'verification_version': 2, 'engineering_id': saved['engineering_id'], 'preparation_id': saved['preparation_id'],
                        'pr_url': package.context['url'], 'head_sha': package.head,
                        'checks': {name: {'satisfied': True, 'rationale': 'Inspected current transform and unit evidence',
                                          'evidence': [{'path': str(package.repo / 'transform.py'),
                                                        'sha256': prep.file_hash(package.repo / 'transform.py')}]}
                                   for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}}
        verification_path = package.write('verification.json', verification)
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   run_record=record_path, pr_url=package.context['url'], body=''), 'engineering_verification_missing')
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   run_record=record_path, pr_url=package.context['url'], body='', verification=verification_path), 'clean')
        verification['checks']['complexity']['satisfied'] = False
        package.write('verification.json', verification)
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   run_record=record_path, pr_url=package.context['url'], body='', verification=verification_path), 'engineering_verification_invalid')
        package.write('user.json', {'instruction': 'Changed scope'})
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   run_record=record_path, pr_url=package.context['url'], body=''), 'preparation_invalid')

    def test_completion_rechecks_live_run_record_after_same_head_human_choice(self):
        package = self.package
        package.manifest['mode'] = 'iteration'
        package.write('manifest.json', package.manifest)
        saved = package.prepare()
        state = module('completion_live_state', 'dk-pr-iter/scripts/run_state.py')
        completion = module('completion_live_record', 'dk-pr-iter/scripts/completion.py')
        record = {'local_decisions': []}
        state.start_run(record)
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        state.next_cycle(record, package.head)
        record_path = package.write('run-record.json', record)
        from shared.review_sessions import Session
        binding = state.review_session(record, package.head, record_path)
        package.write('run-record.json', record)
        session = Session(package.context, prep.read(saved['artifact']), ['example'], path=binding['session_file'])
        item = session.finish({'complete': True, 'findings': [], 'executed_reviewers': ['example'],
                               'architecture_review': architecture_review(prep.read(saved['artifact']), package.context, package.decisions),
                               'reused_reviewers': [], 'skipped_reviewers': []})
        record = state.read(Path(record_path), {})
        state.register_review(record, {'review_artifact': item}, package.head)
        package.write('run-record.json', record)
        source = package.repo / 'transform.py'
        check = {'satisfied': True, 'rationale': 'Inspected the supported behavior and its boundaries',
                 'evidence': [{'path': str(source), 'sha256': prep.file_hash(source)}]}
        verification_path = package.write('verification.json', {
            'verification_version': 2, 'engineering_id': saved['engineering_id'], 'preparation_id': saved['preparation_id'],
            'pr_url': package.context['url'], 'head_sha': package.head,
            'checks': {name: check for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})
        args = (package.head, package.head, package.head, True, False, 'success')
        kwargs = {'stage': 'review', 'preparation': saved['artifact'], 'pr_url': package.context['url'],
                  'body': '', 'verification': verification_path, 'run_record': record_path}
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'clean')
        record = state.read(Path(record_path), {})
        def refresh_instructions(previous, instructions):
            parent = prep.read(previous['artifact'])
            change = retain(parent)
            for item in change['dispositions']:
                if parent['graph'][item['id']]['location'] == '/worker_instructions':
                    item.update(action='replace', value=instructions)
            return prep.prepare(package.manifest_path, package.root / 'versions', previous_preparation=previous['artifact'],
                                full_analysis=True, analyzer=lambda *_: change)
        instructions = analysis()['worker_instructions']
        other = refresh_instructions(saved, instructions + ' Record the routine placement rationale.')
        state.attach_preparation(record, other['artifact'], package.context['url'], package.head)
        package.write('run-record.json', record)
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'preparation_invalid')
        restored = refresh_instructions(other, instructions)
        state.attach_preparation(record, restored['artifact'], package.context['url'], package.head)
        state.next_cycle(record, package.head)
        binding = state.review_session(record, package.head, record_path)
        package.write('run-record.json', record)
        session = Session(package.context, prep.read(restored['artifact']), ['example'], path=binding['session_file'])
        item = session.finish({'complete': True, 'findings': [], 'executed_reviewers': ['example'],
                               'architecture_review': architecture_review(prep.read(restored['artifact']), package.context, package.decisions),
                               'reused_reviewers': [], 'skipped_reviewers': []})
        state.register_review(record, {'review_artifact': item}, package.head)
        package.write('run-record.json', record)
        verification = prep.read(verification_path)
        verification['preparation_id'] = restored['preparation_id']
        package.write('verification.json', verification)
        kwargs['preparation'] = restored['artifact']
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'clean')
        state.record_local_decision(record, 'New human scope condition', 'Exact answer requiring scope synchronization')
        package.write('run-record.json', record)
        with self.assertRaisesRegex(ValueError, 'local decisions changed'):
            state.work_gate(record, package.head)
        for stage in ('review', 'complete'):
            self.assertEqual(completion.completion_gate(*args, **{**kwargs, 'stage': stage}), 'preparation_invalid')
        argv = ['completion.py', '--cwd', str(package.repo), '--pr', package.context['url'], 'gate',
                '--stage', 'review', '--start-head', package.head, '--final-head', package.head,
                '--preparation-file', restored['artifact'], '--verification-file', str(verification_path),
                '--run-record', str(record_path), '--review-complete']
        with patch.object(sys, 'argv', argv), \
             patch.object(completion, 'live_preparation_scope', return_value={'body': '', 'headRefOid': package.head}), \
             patch('sys.stderr', new_callable=io.StringIO) as error, self.assertRaises(SystemExit) as exit:
            completion.main()
        self.assertEqual(exit.exception.code, 2)
        self.assertIn('local decisions changed', error.getvalue())
        record_path.unlink()
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'preparation_invalid')


if __name__ == '__main__':
    unittest.main()
