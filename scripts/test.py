#!/usr/bin/env python3
"""Run the bundled workflow, PR-domain and full-recipe suites offline."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
suites = [sorted((ROOT/'tests').glob('test_*.py')), [ROOT/'tests/pr']]
suites += [[path] for path in sorted(ROOT.glob('runtime/*/tests'))]
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1',
       'PYTHONPATH':os.pathsep.join([str(ROOT/'runtime'), str(ROOT/'runtime/dk-pr-address-reviews/scripts')])}
with tempfile.TemporaryDirectory(prefix='dk-workflows-tests-') as temporary:
    for index, suite in enumerate(suites):
        command = [sys.executable, '-m', 'pytest', '-q', *map(str, suite), '--tb=short',
                   '--basetemp', str(Path(temporary)/str(index)), '-o',
                   'cache_dir='+str(Path(temporary)/'cache')]
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
