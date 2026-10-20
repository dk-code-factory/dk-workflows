"""End-to-end command binding delivery with real pinned Git and preparation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'dk-pr-decisions/tests'))
from preparation_fixtures import Package,analysis,prep
spec=importlib.util.spec_from_file_location('prepared_runner',ROOT/'dk-pr-review/scripts/run_reviewers.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

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
    def prepare(self):
        descriptor=runner.describe_reviewer(self.provider)
        self.package.write('reviewer.json',descriptor)
        return prep.read(self.package.prepare(analysis((self.name,)))['artifact'])
    def test_command_receives_full_evidence_and_does_not_change_checkout(self):
        artifact=self.prepare();before=prep.git(self.package.repo,'rev-parse','HEAD')
        result=runner.review(self.package.context,[self.provider],'Narrow scope',self.package.decisions,'',artifact)
        self.assertTrue(result['complete'],result)
        data=json.loads(self.received.read_text())
        self.assertEqual(data['preparation'],artifact)
        self.assertEqual(data['decisions'],self.package.decisions)
        self.assertEqual(data['reviewer_addendum'],artifact['analysis']['reviewer_addenda'][self.name])
        self.assertEqual(prep.git(self.package.repo,'rev-parse','HEAD'),before)
        self.assertEqual(prep.git(self.package.repo,'status','--porcelain=v1'),'')
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
