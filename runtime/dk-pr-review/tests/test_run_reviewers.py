import importlib.util
import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
SCRIPT = Path(__file__).resolve().parents[1]/'scripts/run_reviewers.py'
spec=importlib.util.spec_from_file_location('run_reviewers',SCRIPT)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
sys.path.insert(0,str(SCRIPT.parents[2]/'dk-pr-decisions/tests'))
from preparation_fixtures import artifact

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        (root/'diff.patch').write_text('diff --git a/x.py b/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n')
        self.context={'patch':str(root/'diff.patch'),'checkout':str(root/'checkout'),'base_sha':'b'*40,'head_sha':'a'*40,
                      'url':'https://github.com/o/r/pull/2','metadata':str(root/'metadata.json')}
        body='## Decisions\n\n- Keep API';(root/'metadata.json').write_text(json.dumps({'body':body}))
        self.decisions={'pr_url':self.context['url'],'head_sha':'a'*40,'body_hash':hashlib.sha256(body.encode()).hexdigest(),
                        'live_decisions':body,'discussion_decisions':[],'local_decisions':[],'verified':True}
        self.providers=[{'skill':'one:review','host':'claude'},{'skill':'two:review','host':'codex'}]
        names=[mod.bindings.key(p) for p in self.providers]
        self.finding={'severity':'Major','path':'x.py','line':1,'message':'broken behavior','fix':'restore behavior'}
        source=root/'metadata.json'
        self.preparation=artifact(self.context,self.decisions,{'production/0':{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}},names)
        self.preparation_path=root/'preparation.json';self.preparation_path.write_text(json.dumps(self.preparation))
        patches=[patch.object(mod,'describe_reviewer',side_effect=lambda p:self.preparation['reviewers'][mod.bindings.key(p)]),
                 patch.object(mod,'skill_path',return_value=root)]
        for mocked in patches:mocked.start();self.addCleanup(mocked.stop)
    def git(self,argv,cwd=None):
        if argv[:3]==['git','rev-parse','HEAD']:return 'a'*40
        if argv[:3]==['git','status','--porcelain=v1']:return ''
        raise AssertionError(argv)
    def review(self,values=None):
        return mod.review(self.context,self.providers if values is None else values,'',self.decisions,'',self.preparation)
    def test_aggregates_in_order_and_forwards_same_preparation(self):
        handoffs=[]
        def reviewer(provider,skill,handoff,checkout):
            data=json.loads(handoff.read_text());handoffs.append(data)
            assert data['preparation']==self.preparation and data['decisions']==self.decisions
            return {'findings':[self.finding]}
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',side_effect=reviewer):result=self.review()
        self.assertTrue(result['complete']);self.assertEqual(len(result['findings']),1)
        self.assertEqual(result['findings'][0]['reviewers'],[mod.bindings.key(p) for p in self.providers])
        self.assertEqual(len(handoffs),2)
    def test_empty_configuration_fails(self):
        with self.assertRaisesRegex(ValueError,'no PR review'):self.review([])
    def test_incomplete_never_becomes_clean(self):
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',return_value={'status':'incomplete','findings':[],'message':'no spec'}):result=self.review()
        self.assertFalse(result['complete']);self.assertEqual(result['reason'],'no spec')
    def test_exhaustion_continues_but_all_exhausted_is_incomplete(self):
        exhausted={'status':'tokens_exhausted','findings':[],'message':'limit'}
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',side_effect=[exhausted,{'findings':[self.finding]}]):result=self.review()
        self.assertTrue(result['complete']);self.assertEqual(len(result['skipped_reviewers']),1)
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',return_value=exhausted):result=self.review()
        self.assertFalse(result['complete']);self.assertEqual(len(result['skipped_reviewers']),2)
    def test_continues_after_failed_provider_without_claiming_completion(self):
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',side_effect=RuntimeError('failed')) as reviewer:result=self.review()
        self.assertFalse(result['complete']);self.assertEqual(reviewer.call_count,2)
        self.assertEqual([f['reviewer'] for f in result['failed_reviewers']],
                         [mod.bindings.key(p) for p in self.providers])
    def test_incomplete_provider_does_not_hide_later_findings(self):
        values = [{'status':'incomplete','findings':[],'message':'API DNS failed'},
                  {'findings':[self.finding]}]
        with patch.object(mod,'run',side_effect=self.git), patch.object(
                mod,'run_skill_reviewer',side_effect=values) as reviewer:
            result = self.review()
        self.assertEqual(reviewer.call_count, 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['executed_reviewers'], [mod.bindings.key(self.providers[1])])
        self.assertEqual(result['findings'][0]['message'], self.finding['message'])
        self.assertEqual(result['failed_reviewers'][0]['reason'], 'API DNS failed')
    def test_checkout_mutation_is_a_guard_failure_and_stops_later_reviewers(self):
        changed = False
        def git(argv, cwd=None):
            return '?? changed' if changed and argv[:3] == ['git','status','--porcelain=v1'] else self.git(argv,cwd)
        def reviewer(*args):
            nonlocal changed
            changed = True
            raise RuntimeError('provider failed after editing checkout')
        with patch.object(mod,'run',side_effect=git), patch.object(
                mod,'run_skill_reviewer',side_effect=reviewer) as provider:
            result = self.review()
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(result['guard_failure'])
        self.assertNotIn('failed_reviewers', result)
    def test_unpinned_findings_fail(self):
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',return_value={'findings':[{**self.finding,'line':99}]}):result=self.review()
        self.assertFalse(result['complete'])
    def test_changed_checkout_fails(self):
        with patch.object(mod,'run',side_effect=['wrong']):
            with self.assertRaisesRegex(RuntimeError,'moved'):self.review()
        with patch.object(mod,'run',side_effect=['a'*40,'?? changed']):
            with self.assertRaisesRegex(RuntimeError,'dirty'):self.review()
    def test_descriptor_change_blocks_launch(self):
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'describe_reviewer',return_value={}),patch.object(mod,'run_skill_reviewer') as provider:result=self.review()
        self.assertFalse(result['complete']);provider.assert_not_called()
    def test_one_correction_then_validation_failure(self):
        handoff=Path(self.tmp.name)/'handoff.json';handoff.write_text(json.dumps({'patch':self.context['patch']}))
        bad=json.dumps({'findings':[{**self.finding,'line':99}]})
        with patch.object(mod,'invoke',side_effect=[bad,json.dumps({'findings':[self.finding]})]) as invoke:
            result=mod.run_skill_reviewer(self.providers[0],Path(self.tmp.name),handoff,Path(self.tmp.name))
        self.assertEqual(result,{'findings':[self.finding]});self.assertEqual(invoke.call_count,2)
        with patch.object(mod,'invoke',return_value=bad) as invoke:
            with self.assertRaisesRegex(ValueError,'after correction'):mod.run_skill_reviewer(self.providers[0],Path(self.tmp.name),handoff,Path(self.tmp.name))
        self.assertEqual(invoke.call_count,2)
    def test_invalid_decisions_block_before_reviewer_launch(self):
        for changed in ({'pr_url': 'https://github.com/o/r/pull/3'},
                        {'head_sha': 'c'*40}, {'body_hash': '0'*64},
                        {'discussion_decisions': [{'text': 'choice', 'references': []}]}):
            with self.subTest(changed=changed), patch.object(mod, 'run') as run:
                with self.assertRaises(ValueError):
                    mod.review(self.context, self.providers[:1], '', {**self.decisions, **changed}, '', self.preparation)
                run.assert_not_called()

    def test_malformed_incomplete_response_gets_one_field_correction(self):
        handoff=Path(self.tmp.name)/'handoff.json';handoff.write_text(json.dumps({'patch':self.context['patch']}))
        invalid={'status':'incomplete','findings':[self.finding],'message':'source missing'}
        stopped={'status':'incomplete','findings':[],'message':'source missing'}
        with patch.object(mod,'invoke',side_effect=[json.dumps(invalid),json.dumps(stopped)]) as invoke:
            result=mod.run_skill_reviewer(self.providers[0],Path(self.tmp.name),handoff,Path(self.tmp.name))
        self.assertEqual(result,stopped)
        self.assertEqual(invoke.call_count,2)
        self.assertIn('empty findings array',invoke.call_args.args[3])
        prompt=mod.build_skill_prompt(Path(self.tmp.name),handoff)
        self.assertIn('{"findings":[]}',prompt)
        self.assertIn('review-result.schema.json',prompt)
        self.assertNotIn('"Major|Minor|Nit"',prompt)

    def test_verified_empty_decisions_are_valid(self):
        (Path(self.context['metadata'])).write_text(json.dumps({'body': ''}))
        empty = {**self.decisions, 'live_decisions': '', 'body_hash': hashlib.sha256(b'').hexdigest()}
        self.assertEqual(mod.validate_decisions(self.context, empty), empty)

    def test_body_edit_requires_refreshed_record(self):
        Path(self.context['metadata']).write_text(json.dumps({'body': '## Decisions\n\n- New choice'}))
        with self.assertRaisesRegex(ValueError, 'body hash mismatch'):
            mod.validate_decisions(self.context, self.decisions)
        refreshed = {**self.decisions, 'body_hash': hashlib.sha256(b'## Decisions\n\n- New choice').hexdigest(),
                     'live_decisions': '## Decisions\n\n- New choice'}
        self.assertEqual(mod.validate_decisions(self.context, refreshed), refreshed)

    def test_versioned_context_rejects_missing_choices_or_bad_record(self):
        record = mod.decision_api.record('Keep API', 'description', [self.context['url']], 'published')
        for records in (None, [], [{**record, 'id': 'wrong'}],
                        [{**record, 'status': 'superseded', 'superseded_by': 'missing'}]):
            envelope = mod.decision_api.envelope('context', {**self.decisions, 'decisions': records})
            with self.subTest(records=records), self.assertRaises(ValueError):
                mod.validate_decisions(self.context, envelope)

    def test_cli_requires_decisions_file_before_agent_launch(self):
        context_file = Path(self.tmp.name)/'context.json'
        context_file.write_text(json.dumps({**self.context, 'repository': str(Path(self.tmp.name)/'repository')}))
        with patch.object(sys, 'argv', ['run_reviewers.py', '--context', str(context_file)]), \
             patch.object(mod, 'run') as run, self.assertRaises(SystemExit):
            mod.main()
        run.assert_not_called()

    def test_both_modes_use_same_session_and_result_contract(self):
        context = {**self.context, 'repository': str(Path(self.tmp.name)/'repository')}
        context_file = Path(self.tmp.name)/'context.json'
        context_file.write_text(json.dumps(context))
        decisions_file = Path(self.tmp.name)/'decisions.json'
        decisions_file.write_text(json.dumps(self.decisions))
        scope_file = Path(self.tmp.name)/'scope.txt'
        rules_file = Path(self.tmp.name)/'rules.txt'
        scope = 'Review shared session behavior and preserve legacy callers.'
        rules = 'Keep iteration budget policy in the iteration adapter.'
        scope_file.write_text(scope)
        rules_file.write_text(rules)
        argv = ['run_reviewers.py', '--context', str(context_file), '--decisions-file',
                str(decisions_file), '--preparation-file', str(self.preparation_path),
                '--scope-file', str(scope_file), '--rules-file', str(rules_file)]
        result = {'complete': True, 'findings': [], 'executed_reviewers': ['one'],
                  'reused_reviewers': [], 'skipped_reviewers': []}
        with patch.object(sys, 'argv', argv), patch.object(mod, 'run', return_value=json.dumps({'reviewers': self.providers})), \
             patch.object(mod.preparation_api, 'load') as load, patch.object(mod, 'Session') as factory, \
             patch.object(mod, 'for_iteration') as capture, patch.object(mod, 'review', return_value=result.copy()) as review, \
             patch('builtins.print'):
            session = factory.return_value
            session.cached.return_value = None
            session.id, session.invocation = 'review-session', 1
            session.finish.return_value = {'path': 'saved', 'sha256': 'fingerprint'}
            for mode in ('standalone', 'iteration'):
                load.return_value = {**self.preparation, 'mode': mode}
                mod.main()
                self.assertEqual(factory.call_args.kwargs['scope'], scope)
                self.assertEqual(factory.call_args.kwargs['rules'], rules)
                self.assertEqual(review.call_args.args[2], scope)
                self.assertEqual(review.call_args.args[4], rules)
                self.assertIs(review.call_args.kwargs['session'], session)
                session.finish.assert_called()
                self.assertEqual(capture.call_args.args[-1], 'review-session:1:1')



if __name__=='__main__':unittest.main()
