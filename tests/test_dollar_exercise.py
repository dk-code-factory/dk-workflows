"""Frozen fixture construction and native outcome inspection use real helpers."""
import importlib.util
import json
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
    if experiment == 4:
        args = [sys.executable, str(root/'bin/gh'), 'pr', 'checks']
        assert [json.loads(subprocess.check_output(args))[0]['bucket'] for _ in range(3)] == ['pending','pending','pass']
    (root/'frozen/scripts/dk.py').write_text('changed')
    assert not exercise.verify(root)['frozen_intact']
