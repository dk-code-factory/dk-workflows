"""Frozen fixture construction and native outcome inspection use real helpers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parent/'fixtures'))
import dollar_exercise as exercise


@pytest.mark.parametrize('experiment', range(1,6))
def test_frozen_workload_has_real_state_and_explicit_boundaries(tmp_path, experiment):
    root = tmp_path/'arm'
    subprocess.run([sys.executable, exercise.__file__, '--root', str(root),
                    '--experiment', str(experiment), 'init'], check=True, capture_output=True)
    verified = exercise.verify(root)
    assert verified['frozen_intact']
    assert verified['assessment'] is None
    assert not verified['scenario']['remote_writes_permitted']
    if experiment in (2,3):
        manifest = verified['manifest']
        assert len(manifest['entries']) == 3
        assert len([e for e in manifest['entries'].values() if e.get('claim')]) == 1
        run = manifest['runs']['retained']
        assert run['cycles'] == ['c1']
        assert len(run['progress']) == (242 if experiment == 3 else 2)
        assert verified['results'][0]['original'].startswith('Original F1:')
    if experiment == 3:
        command = [sys.executable, str(root/'interface.py'), '--root', str(root), 'append']
        assert json.loads(subprocess.check_output(command))['progress_count'] == 243
        assert subprocess.run(command, capture_output=True).returncode != 0
        assert len(exercise.verify(root)['manifest']['runs']['retained']['progress']) == 243
    if experiment == 4:
        args = [sys.executable, str(root/'bin/gh'), 'pr', 'checks']
        assert [json.loads(subprocess.check_output(args))[0]['bucket'] for _ in range(3)] == ['pending','pending','pass']
    if experiment == 5:
        env = {**os.environ, 'XDG_STATE_HOME':str(root/'state'),
               'XDG_CONFIG_HOME':str(root/'config'), 'XDG_DATA_HOME':str(root/'data')}
        base = [sys.executable, str(root/'frozen/scripts/dk.py'), '--repository', exercise.REPO,
                '--pr', exercise.PR, '--checkout', str(root/'checkout')]
        hook = json.loads(subprocess.check_output([*base, 'hook-spec', '--definition',
            str(root/'inputs/hook-definition.json'), '--phase', 'before_cycle',
            '--index', '0', '--run', 'discovery', '--cycle', 'c1'], env=env))
        finalizer = json.loads(subprocess.check_output([*base, 'finalizer-spec', '--definition',
            str(root/'inputs/finalizer-definition.json'), '--finalizer', 'fixture:finalizer',
            '--run', 'discovery'], env=env))
        assert hook['task']['id'].startswith('cycle-hook:')
        assert finalizer['task']['id'].startswith('finalizer:')
    (root/'frozen/scripts/dk.py').write_text('changed')
    assert not exercise.verify(root)['frozen_intact']


@pytest.mark.parametrize('bundle', [[2],[2,3],[2,4],[2,3,4,5]])
def test_combined_workload_preserves_accepted_scope_and_frozen_inputs(tmp_path,bundle):
    root=tmp_path/'combined'
    subprocess.run([sys.executable,exercise.__file__,'--root',str(root),'--experiment','6',
                    '--bundle',','.join(map(str,bundle)),'init'],check=True,capture_output=True)
    verified=exercise.verify(root)
    assert verified['frozen_intact'] and verified['inputs_intact']
    assert verified['scenario']['accepted_proposals']==bundle
    assert verified['manifest']['runs']['retained']['cycles']==['c1']
    assert len(verified['manifest']['runs']['retained']['progress'])==(242 if 3 in bundle else 2)
    if 3 in bundle:
        subprocess.run([sys.executable,str(root/'interface.py'),'--root',str(root),'append'],
                       check=True,capture_output=True)
    if 4 in bundle:
        assert (root/'bin/gh').exists()
    if 5 in bundle:
        assert (root/'inputs/finalizer-definition.json').exists()
