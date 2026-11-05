"""Receipt CLI runs the real waiter and final policy through a local gh transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).parent/'fixtures'))
import dollar_exercise


def test_receipt_cli_saves_terminal_policy_evidence_privately(tmp_path):
    root=tmp_path/'arm'
    initialized=subprocess.run([sys.executable,dollar_exercise.__file__,'--root',str(root),
                               '--experiment','4','init'],check=True,capture_output=True,text=True)
    scenario=json.loads(initialized.stdout)
    env={**os.environ,'XDG_STATE_HOME':str(root/'state'),'XDG_CONFIG_HOME':str(root/'config'),
         'XDG_DATA_HOME':str(root/'data'),'PATH':str(root/'bin')+os.pathsep+os.environ['PATH'],
         'PYTHONDONTWRITEBYTECODE':'1'}
    command=[sys.executable,str(root/'frozen/runtime/dk-ci/scripts/ci_wait.py'),'--pr',scenario['pr'],
             '--head',scenario['head'],'--receipt','--interval','1','--timeout','30']
    completed=subprocess.run(command,env=env,cwd=root,capture_output=True,text=True,check=True)
    result=json.loads(completed.stdout)
    assert result['result']=='success' and result['head']==scenario['head']
    reference=result['observer_receipt']
    state=json.loads(Path(reference['path']).read_text())
    receipt=state['current']['receipt']
    assert receipt['identity']['pr']==scenario['pr']
    assert receipt['identity']['head']==scenario['head']
    assert receipt['deadline']==state['current']['started']+30
    assert receipt['final_evidence']['result']=='success'
    assert receipt['final_evidence']['blockers']==[]
    assert Path(reference['path']).stat().st_mode & 0o777==0o600
    pending=json.loads(completed.stderr.splitlines()[0])
    assert pending['result']=='pending' and pending['observer_receipt']==reference
    assert json.loads((root/'ci-transport.json').read_text())['checks']>=3
