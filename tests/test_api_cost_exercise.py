"""Verify the complete native fixture with offline stand-ins, not model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT/'tests/fixtures/api_cost_exercise.py'
DRIVE = '''
import json,sys
from pathlib import Path
root,phase=Path(sys.argv[1]),int(sys.argv[2])
sys.path.insert(0,str(root/'frozen/runtime'))
from tasks import Tasks
tasks=Tasks('github.com/fixture/dollars','https://github.com/fixture/dollars/pull/7',checkout=root/'checkout')
evidence=json.loads((root/'inputs/evidence.json').read_text())
counts={}
for item in json.loads((root/'inputs/tasks.json').read_text()):
    prepared=tasks.prepare({'run':'api-cost','tasks':[item['descriptor']]})['tasks'][0]
    counts[prepared['action']]=counts.get(prepared['action'],0)+1
    if prepared['action']=='reuse':
        tasks.result(prepared['key'],'normalized')
        continue
    report={'complete':True,'findings':[],**{key:evidence[key] for key in ('finding','acceptance','source')}}
    report['checklist']=[{'id':i['id'],'status':'completed','evidence':[str(root/'inputs/evidence.json')]}
                         for i in prepared['checklist']['items']]
    result=tasks.finish(prepared['key'],prepared['claim']['id'],json.dumps(report).encode(),report)
    if result['action']=='recheck':
        check={'complete':True,'checklist':report['checklist']}
        result=tasks.finish(prepared['key'],result['claim'],json.dumps(check).encode(),check,round_id=result['round'])
    assert result['checklist_complete']
(root/'artifacts'/('phase-'+str(phase)+'.json')).write_text(json.dumps({
    'complete':True,'blockers':[],**{key:evidence[key] for key in ('finding','acceptance','source')}}))
print(json.dumps(counts))
'''


def command(root, *arguments):
    return subprocess.run([sys.executable, str(INTERFACE), '--root', str(root), *arguments],
                          text=True, capture_output=True, timeout=60)


@pytest.fixture
def completed(tmp_path):
    root = tmp_path/'arm'
    initialized = command(root, 'init', '--source', str(ROOT))
    assert initialized.returncode == 0, initialized.stderr
    environment = {**os.environ, **{'XDG_'+key+'_HOME': str(root/folder) for key,folder in
                   [('STATE','state'),('CONFIG','config'),('DATA','data')]}}
    counts = []
    for phase in (1, 2):
        result = subprocess.run([sys.executable, '-c', DRIVE, str(root), str(phase)],
                                env=environment, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout+result.stderr
        counts.append(json.loads(result.stdout))
    assert counts == [{'invoke': 16}, {'reuse': 13, 'invoke': 3}]
    return root


def test_complete_selected_fixture_and_fresh_process_resume_preserve_evidence(completed):
    result = command(completed, 'verify')
    assert result.returncode == 0, result.stderr
    verified = json.loads(result.stdout)
    assert verified['passed'], verified['problems']
    assert len(verified['tasks']) == 16


@pytest.mark.parametrize('mutation', ['input', 'resource', 'assessment', 'result'])
def test_verifier_rejects_changed_sources_missing_assessments_and_damaged_results(completed, mutation):
    root = completed
    if mutation == 'input':
        (root/'inputs/evidence.json').write_text('{}')
    if mutation == 'resource':
        (root/'frozen/docs/task-state.md').write_text('Changed')
    if mutation == 'assessment':
        (root/'artifacts/phase-2.json').unlink()
    if mutation == 'result':
        next((root/'state').glob('**/results/*.json')).unlink()
    result = command(root, 'verify')
    assert result.returncode == 0, result.stderr
    assert not json.loads(result.stdout)['passed']
