"""End-to-end command binding delivery with real pinned Git and preparation."""
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
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'dk-pr-decisions/tests'))
from preparation_fixtures import Package,analysis,prep
from shared import review_coverage
from shared import architecture
from shared.executors import ProviderExhausted, ProviderCancelled
from shared.review_sessions import result_artifact, Session
spec=importlib.util.spec_from_file_location('prepared_runner',ROOT/'dk-pr-review/scripts/run_reviewers.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
spec=importlib.util.spec_from_file_location('delivery_run_state',ROOT/'dk-pr-iter/scripts/run_state.py')
state=importlib.util.module_from_spec(spec);spec.loader.exec_module(state)
from stats_capture import api as stats_api

class PreparedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.package=Package(self.temp.name)
        self.script=self.package.root/'provider.py'
        self.received=self.package.root/'received.json'
        self.script.write_text('import json,sys,pathlib\nh=json.loads(pathlib.Path(sys.argv[1]).read_text())\n'
                              'assert h["contract"]=="git-diff-v1"\n'
                              'assert h["preparation"]["readiness"]=="ready"\n'
                              f'pathlib.Path({str(self.received)!r}).write_text(json.dumps(h))\n'
                              'print(json.dumps({"findings":[]}))\n')
        self.provider={'command':[sys.executable,str(self.script),'{handoff}']}
        self.name=runner.bindings.key(self.provider)
    def prepare(self, value=None):
        descriptor=runner.describe_reviewer(self.provider)
        self.package.write('reviewer.json',descriptor)
        self.artifact_path=self.package.prepare(value if value is not None else analysis((self.name,)))['artifact']
        return prep.read(self.artifact_path)

    def lossless_filename(self, name=None, *, quote_path=True):
        name=os.fsdecode(b'odd-\xff.py') if name is None else name
        (self.package.repo/name).write_text('"""Fixture\n++ "b/decoy.py"\n"""\npass\n')
        prep.git(self.package.repo,'add','-A');prep.git(self.package.repo,'commit','-qm','Lossless path fixture')
        self.package.head=prep.git(self.package.repo,'rev-parse','HEAD')
        self.package.context['head_sha']=self.package.head;self.package.decisions['head_sha']=self.package.head
        self.package.write('context.json',self.package.context);self.package.write('decisions.json',self.package.decisions)
        Path(self.package.context['patch']).write_text(prep.git(self.package.repo,'-c','core.quotePath='+str(quote_path).lower(),
            'diff','--binary',self.package.context['base_sha'],self.package.head)+'\n')
        return name
    def test_command_receives_full_evidence_and_does_not_change_checkout(self):
        name=self.lossless_filename()
        artifact=self.prepare();before=prep.git(self.package.repo,'rev-parse','HEAD')
        result=runner.review(self.package.context,[self.provider],'Narrow scope',self.package.decisions,'',artifact)
        self.assertTrue(result['complete'],result)
        data=json.loads(self.received.read_bytes().decode('utf-8'))
        self.assertIn(name,data['preparation']['inputs']['tree'])
        self.assertEqual(data['preparation'],artifact)
        self.assertEqual(data['decisions'],self.package.decisions)
        self.assertEqual(data['reviewer_addendum'],artifact['analysis']['reviewer_addenda'][self.name])
        self.assertEqual(prep.git(self.package.repo,'rev-parse','HEAD'),before)
        self.assertEqual(prep.git(self.package.repo,'status','--porcelain=v1'),'')

    def test_lossless_placement_reaches_native_review_sessions_results_and_stats(self):
        name=self.lossless_filename()
        finding={'severity':'Minor','path':name,'line':4,'message':'Inspect '+name,'fix':'Keep byte-exact paths'}
        self.assertEqual(runner.added_lines(Path(self.package.context['patch']).read_text()),{name:{1,2,3,4}})
        code=self.script.read_text().replace('print(json.dumps({"findings":[]}))',f'print({json.dumps({"findings":[finding]})!r})')
        self.script.write_text(code)
        self.package.manifest['mode']='iteration';self.package.write('manifest.json',self.package.manifest)
        value=analysis((self.name,));value['placement']['production']['locations']=[name]
        with patch.dict(os.environ,{'XDG_DATA_HOME':str(self.package.root/'private')}):
            artifact=self.prepare(value)
            self.assertEqual(review_coverage.digest({'路径':'café/😀.py'}),
                             'b0b00c3f2839ac87ddfbc1327e54cb1eb3c7ad03f2caf309ca7720e31d67c12e')
            self.assertEqual(stats_api.digest({'路径':'café/😀.py'}),'897ac0633727f2f346163ea0')
            session=self.package.root/'review-session.json'
            argv=['run_reviewers.py','--context',str(self.package.root/'context.json'),
                  '--decisions-file',str(self.package.root/'decisions.json'),'--preparation-file',self.artifact_path,
                  '--session-file',str(session),'--stats-cycle','1','--stats-attempt','lossless']
            original_run=runner.run
            def configured(args,cwd=None):
                if args[-2:]==['reviewers','list']:
                    return json.dumps({'reviewers':[self.provider]})
                return original_run(args,cwd)
            with patch.object(sys,'argv',argv),patch.object(runner,'run',side_effect=configured), \
                    patch('sys.stdout',new_callable=io.StringIO) as output,patch('sys.stderr',new_callable=io.StringIO) as errors:
                runner.main()
            wire=json.loads(output.getvalue().encode('utf-8').decode('utf-8'))
            self.assertTrue(wire['complete'],wire)
            self.assertEqual(wire['executed_reviewers'],[self.name])
            self.assertEqual(os.fsencode(wire['findings'][0]['path']),b'odd-\xff.py')
            self.assertEqual(wire['findings'][0]['message'],finding['message'])
            self.assertNotIn('missing data',errors.getvalue())
            saved=json.loads(session.read_bytes().decode('utf-8'))
            checkpoint=saved['reviews'][self.name]['checkpoint']
            self.assertEqual(json.loads(Path(checkpoint['path']).read_bytes().decode('utf-8'))['result']['findings'],[finding])
            retained=result_artifact(session,wire['review_artifact'])
            self.assertEqual(retained['preparation_id'],artifact['preparation_id'])
            self.assertEqual(review_coverage.verify(wire['review_artifact'],artifact),retained)
            received=json.loads(self.received.read_bytes().decode('utf-8'))
            self.assertEqual(os.fsencode(received['preparation']['analysis']['placement']['production']['locations'][0]),b'odd-\xff.py')
            sidecar=json.loads((Path(runner.preparation_api.context_for(artifact)['patch']).parent/'review-stats.json').read_bytes().decode('utf-8'))
            self.assertEqual(sidecar['run']['reviews'][self.name]['status'],'completed')
            stats_results=[json.loads(Path(path).read_bytes().decode('utf-8'))
                           for path in sidecar['run']['reviews'][self.name]['artifacts'] if Path(path).suffix=='.json']
            self.assertTrue(any(value.get('result')=={'findings':[finding]} for value in stats_results))
            for path in (self.package.root/'private').rglob('*.json'):
                json.loads(path.read_bytes().decode('utf-8'))
            for path in (self.package.root/'private').rglob('*.md'):
                path.read_bytes().decode('utf-8')
    def test_git_quoted_unicode_filename_preserves_literal_utf8_and_escapes(self):
        name=self.lossless_filename('café-"quoted"\t.py',quote_path=False)
        patch_text=Path(self.package.context['patch']).read_text()
        self.assertIn('+++ "b/café-\\"quoted\\"\\t.py"',patch_text)
        self.assertEqual(runner.added_lines(patch_text),{name:{1,2,3,4}})
    def test_git_filename_record_preserves_unicode_separator_and_space(self):
        name=self.lossless_filename('review\u2028 notes.py',quote_path=False)
        patch_text=Path(self.package.context['patch']).read_text()
        self.assertIn('+++ b/'+name+'\t\n',patch_text)
        changed=runner.added_lines(patch_text)
        self.assertEqual(changed,{name:{1,2,3,4}})
        finding={'severity':'Minor','path':name,'line':4,'message':'Inspect unusual path','fix':'Keep path exact'}
        self.assertEqual(runner.validate_findings({'findings':[finding]},changed),{'findings':[finding]})
    def test_changed_provider_source_blocks_before_launch(self):
        artifact=self.prepare()
        retained=Path(artifact['reviewers'][self.name]['execution']['argv'][1])
        retained.chmod(0o644);retained.write_text('print("changed")')
        with patch.object(runner,'invoke') as launch:
            with self.assertRaisesRegex((ValueError, RuntimeError),'changed'):runner.review(self.package.context,[self.provider],'',self.package.decisions,'',artifact)
        launch.assert_not_called()
    def test_descriptor_contract_change_blocks_even_when_source_remains(self):
        artifact=self.prepare();changed=dict(artifact['reviewers'][self.name],effective_prompt='different')
        with patch.object(runner,'describe_reviewer',return_value=changed),patch.object(runner,'invoke') as launch:
            result=runner.review(self.package.context,[self.provider],'',self.package.decisions,'',artifact)
        self.assertFalse(result['complete']);self.assertIn('instructions changed',result['reason']);launch.assert_not_called()
    def test_relocated_patch_is_safe_but_different_patch_blocks_before_launch(self):
        artifact=self.prepare();alternate=self.package.root/'alternate.patch'
        alternate.write_bytes(Path(self.package.context['patch']).read_bytes())
        context={**self.package.context,'patch':str(alternate)}
        runner.preparation_api.validate(artifact,context,self.package.decisions)
        alternate.write_text('diff --git a/other.py b/other.py\n+++ b/other.py\n@@ -0,0 +1 @@\n+unrelated\n')
        with patch.object(runner,'invoke') as launch,self.assertRaisesRegex(ValueError,'supplied patch'):
            runner.review(context,[self.provider],'',self.package.decisions,'',artifact)
        launch.assert_not_called()

    def test_removed_command_resource_uses_retained_copy(self):
        artifact=self.prepare()
        self.script.unlink()
        result=runner.review(self.package.context,[self.provider],'',self.package.decisions,'',artifact)
        self.assertTrue(result['complete'],result)
        self.assertTrue(self.received.is_file())

    def test_worker_exception_architecture_gate_and_completion_coverage(self):
        p=self.package
        user={'actor':'user','instruction':'Allow an independent enforcer to approve this local adapter.',
              'source':'fixture user request'}
        rule={'id':'arch-local','revision':1,'text':'Keep the transform local.', 'origin':'user-created',
              'sources':[], 'agents':['worker'], 'patterns':['transform.py'],
              'auto_approve':True, 'approval_when':'The supported transform still returns its input.'}
        architecture.edit(p.repo,rule,user=user,expected=architecture.current(p.repo,baseline=p.context['base_sha'])['fingerprint'])
        prepared=self.prepare()
        worker=architecture.handoff(prepared['architecture'],p.context,'worker',['transform.py'])
        request=architecture.exception_request(worker,p.repo,'arch-local','One local adapter.', ['transform.py'],
            [{'path':'transform.py','start':1,'end':2}],'The adapter preserves the transform.')
        approved=architecture.enforce(request,p.repo,evaluator=lambda *_:{'established':True,
            'rationale':'Inspected supported behavior.', 'evidence':[{'path':'transform.py','start':1,'end':2}]})
        enforcer=architecture.load(architecture.load(approved)['enforcer_packet'])
        self.assertIn('user_instructions/0',enforcer['governing_sources'])
        self.assertIn('decisions',enforcer['scope_contents'])
        p.manifest['architecture_exceptions']=[approved];p.write('manifest.json',p.manifest)
        prepared=self.prepare()
        verdict={'complete':True,'reason':'','coverage':[{'rule_id':'arch-local','revision':1,
            'status':'approved_exception','exception':approved['fingerprint'],'paths':['transform.py'],
            'rationale':'The approved adapter stays within scope.',
            'evidence':[{'path':'transform.py','start':1,'end':2}]}]}
        with patch.object(architecture,'analyze',return_value=verdict):
            result=runner.review(p.context,[self.provider],'',p.decisions,'',prepared)
        self.assertTrue(result['complete'],result)
        self.assertEqual(result['executed_reviewers'],[self.name])
        received=json.loads(self.received.read_text())
        self.assertIn('architecture_packet',received)
        self.assertEqual(architecture.load(received['architecture_packet'])['agent'],self.name)
        session=Session(p.context,prepared,[self.name])
        item=session.finish(result)
        review_coverage.verify(item,prepared)
        # Configured reviewer success cannot waive a missing architecture gate.
        session=Session(p.context,prepared,[self.name])
        without={k:v for k,v in result.items() if k!='architecture_review'}
        item=session.finish(without)
        with self.assertRaisesRegex(ValueError,'architecture evidence'):
            review_coverage.verify(item,prepared,accepted_failures=[self.name])
        # An unchanged PR/head cannot retain an exception after a user changes
        # the governing task requirement outside the PR description/Decisions.
        p.write('user.json',{'instruction':'The adapter must now preserve an additional public guarantee.'})
        with self.assertRaisesRegex(ValueError,'does not cover'):
            self.prepare()

    def test_architecture_provider_failure_keeps_configured_success_incomplete(self):
        prepared=self.prepare()
        with patch.object(architecture,'analyze',side_effect=RuntimeError('Architecture provider unavailable')):
            result=runner.review(self.package.context,[self.provider],'',self.package.decisions,'',prepared)
        self.assertFalse(result['complete'])
        self.assertTrue(result['guard_failure'])
        self.assertEqual(result['executed_reviewers'],[self.name])
        self.assertIn('Architecture provider unavailable',result['reason'])

    def test_architecture_exhaustion_retry_requires_no_other_failure_and_keeps_coverage_mandatory(self):
        p=self.package
        p.manifest['mode']='iteration';p.write('manifest.json',p.manifest)
        prepared=self.prepare()
        for kind in ('exhausted','invalid_evidence','configured_failure'):
            with self.subTest(kind=kind):
                record={};state.start_run(record)
                state.attach_preparation(record,self.artifact_path,p.context['url'],p.head)
                for _ in range(5):state.next_cycle(record,p.head)
                binding=state.review_session(record,p.head,p.root/'run.json')
                session=Session(p.context,prepared,[self.name],path=binding['session_file'])
                provider_result=({'status':'incomplete','findings':[],'message':'Configured provider unavailable'}
                                 if kind=='configured_failure' else {'findings':[]})
                failure=({'return_value':{'complete':True,'reason':'','coverage':[{}]}}
                         if kind=='invalid_evidence' else {'side_effect':ProviderExhausted('Architecture quota reached')})
                with session.execution(),patch.object(architecture,'analyze',**failure), \
                        patch.object(runner,'run_skill_reviewer',return_value=provider_result):
                    result=runner.review(p.context,[self.provider],'',p.decisions,'',prepared,session=session)
                    reference=session.finish(result)
                retained=Path(reference['path']).read_bytes()
                state.register_review(record,{'review_artifact':reference},p.head)
                self.assertFalse(result['complete'])
                self.assertNotIn('architecture_review',result)
                for accepted in ([],[self.name]):
                    with self.assertRaisesRegex(ValueError,'architecture evidence'):
                        review_coverage.verify(reference,prepared,accepted_failures=accepted)
                if kind=='exhausted':
                    self.assertEqual(result['outcome'],'usage_exhausted')
                    self.assertFalse(result.get('guard_failure'))
                    self.assertEqual(result['executed_reviewers'],[self.name])
                    self.assertIn('Architecture quota reached',result['reason'])
                    self.assertEqual(state.retry_exhausted_cycle(record,p.head)['number'],5)
                    with self.assertRaisesRegex(ValueError,'retry already recorded'):
                        state.retry_exhausted_cycle(record,p.head)
                else:
                    self.assertEqual(result['outcome'],'guard_failed' if kind=='invalid_evidence' else 'provider_failed')
                    self.assertEqual(bool(result.get('guard_failure')),kind=='invalid_evidence')
                    with self.assertRaisesRegex(ValueError,'not an incomplete usage-limited review'):
                        state.retry_exhausted_cycle(record,p.head)
                self.assertEqual(len(record['cycles']),5)
                self.assertEqual(Path(reference['path']).read_bytes(),retained)

    def test_architecture_cancellation_preserves_session_for_explicit_resume(self):
        p=self.package;prepared=self.prepare()
        path=p.root/'cancelled-session.json'
        session=Session(p.context,prepared,[self.name],path=path)
        with self.assertRaises(KeyboardInterrupt),session.execution(), \
                patch.object(architecture,'analyze',side_effect=ProviderCancelled('Review interrupted')):
            result=runner.review(p.context,[self.provider],'',p.decisions,'',prepared,session=session)
            session.finish(result)
        saved=json.loads(path.read_text())
        self.assertEqual(saved['status'],'active')
        self.assertEqual(saved['results'],[])
        self.assertEqual(saved['reviews'][self.name]['status'],'completed')
        checkpoint=saved['reviews'][self.name]['checkpoint']
        checkpoint_bytes=Path(checkpoint['path']).read_bytes()
        resumed=Session(p.context,prepared,[self.name],path=path,resume=True)
        with resumed.execution(),patch.object(runner,'run_skill_reviewer') as launch, \
                patch.object(architecture,'analyze',return_value={'complete':True,'reason':'','coverage':[]}) as analyze:
            result=runner.review(p.context,[self.provider],'',p.decisions,'',prepared,session=resumed)
            reference=resumed.finish(result)
        launch.assert_not_called();analyze.assert_called_once()
        self.assertTrue(result['complete'])
        self.assertEqual(result['reused_reviewers'],[self.name])
        self.assertEqual(result['executed_reviewers'],[])
        review_coverage.verify(reference,prepared)
        self.assertEqual(Path(checkpoint['path']).read_bytes(),checkpoint_bytes)
