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
from shared.review_sessions import result_artifact
spec=importlib.util.spec_from_file_location('prepared_runner',ROOT/'dk-pr-review/scripts/run_reviewers.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
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
            sidecar=json.loads((self.package.root/'review-stats.json').read_bytes().decode('utf-8'))
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
