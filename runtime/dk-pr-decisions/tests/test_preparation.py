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
from preparation_fixtures import Package, ROOT, analysis, prep


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
            return analysis()
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
        package.write('response.json', analysis())
        binary = package.root / 'bin'
        binary.mkdir()
        model = binary / 'codex'
        model.write_text('#!/usr/bin/env python3\nimport os, pathlib, sys\n'
                         'prompt = sys.stdin.read()\n'
                         'assert "read-only" in prompt and "INPUT INDEX" in prompt\n'
                         'pathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text(pathlib.Path(os.environ["MODEL_RESPONSE"]).read_text())\n')
        model.chmod(0o755)
        env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
               'MODEL_RESPONSE': str(package.root / 'response.json')}
        result = subprocess.run([sys.executable, str(ROOT / 'dk-pr-decisions/scripts/decisions.py'),
                                 'prepare', '--input-manifest', str(package.manifest_path),
                                 '--output-dir', str(package.root / 'versions')], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        response = json.loads(result.stdout)
        self.assertEqual((response['schema_version'], response['command'], response['status']), (2, 'prepare', 'ok'))
        self.assertEqual(response['data']['readiness'], 'ready')

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
        with patch('shared.executors.execute', side_effect=[json.dumps(invalid), json.dumps(analysis())]) as model:
            result = prep.prepare(self.package.manifest_path, self.package.root / 'versions')
        artifact = prep.load(result['artifact'], self.package.context, self.package.decisions)
        self.assertEqual(artifact['readiness'], 'ready')
        self.assertEqual(artifact['analysis'], analysis())
        self.assertEqual(model.call_count, 2)
        self.assertIn('worker instructions and completion criteria required', model.call_args_list[1].args[0])

    def test_invalid_analysis_exhausts_correction_without_preparing(self):
        for raw in ('not JSON', json.dumps({'boundaries': {}})):
            with self.subTest(raw=raw):
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

    def test_changes_during_analysis_and_in_repo_output_are_rejected(self):
        def mutate(prompt, checkout):
            self.package.write('user.json', {'instruction': 'Changed during analysis'})
            return analysis()
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
        refreshed = package.prepare()
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
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        record_path = package.write('run-record.json', record)
        self.assertEqual(completion.completion_gate(*args, stage='review', preparation=saved['artifact'],
                                                   pr_url=package.context['url']), 'run_record_missing')
        verification = {'verification_version': 1, 'preparation_id': saved['preparation_id'],
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
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        record_path = package.write('run-record.json', record)
        source = package.repo / 'transform.py'
        check = {'satisfied': True, 'rationale': 'Inspected the supported behavior and its boundaries',
                 'evidence': [{'path': str(source), 'sha256': prep.file_hash(source)}]}
        verification_path = package.write('verification.json', {
            'verification_version': 1, 'preparation_id': saved['preparation_id'],
            'pr_url': package.context['url'], 'head_sha': package.head,
            'checks': {name: check for name in ('behavior', 'placement', 'test_sufficiency', 'compatibility', 'complexity')}})
        args = (package.head, package.head, package.head, True, False, 'success')
        kwargs = {'stage': 'review', 'preparation': saved['artifact'], 'pr_url': package.context['url'],
                  'body': '', 'verification': verification_path, 'run_record': record_path}
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'clean')
        other_analysis = analysis()
        other_analysis['worker_instructions'] += ' Record the routine placement rationale.'
        other = package.prepare(other_analysis)
        state.attach_preparation(record, other['artifact'], package.context['url'], package.head)
        package.write('run-record.json', record)
        self.assertEqual(completion.completion_gate(*args, **kwargs), 'preparation_invalid')
        state.attach_preparation(record, saved['artifact'], package.context['url'], package.head)
        state.record_local_decision(record, 'New human scope condition', 'Exact answer requiring scope synchronization')
        package.write('run-record.json', record)
        with self.assertRaisesRegex(ValueError, 'local decisions changed'):
            state.work_gate(record, package.head)
        for stage in ('review', 'complete'):
            self.assertEqual(completion.completion_gate(*args, **{**kwargs, 'stage': stage}), 'preparation_invalid')
        argv = ['completion.py', '--cwd', str(package.repo), '--pr', package.context['url'], 'gate',
                '--stage', 'review', '--start-head', package.head, '--final-head', package.head,
                '--preparation-file', saved['artifact'], '--verification-file', str(verification_path),
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
