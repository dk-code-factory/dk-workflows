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
    def test_stops_after_failed_provider(self):
        with patch.object(mod,'run',side_effect=self.git),patch.object(mod,'run_skill_reviewer',side_effect=RuntimeError('failed')) as reviewer:result=self.review()
        self.assertFalse(result['complete']);self.assertEqual(reviewer.call_count,1)
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



if __name__=='__main__':unittest.main()
