#!/usr/bin/env python3
"""Offline standalone suites without a sibling checkout or shared temp names."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
suites = [root/'tests', *sorted(root.glob('runtime/*/tests'))]
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1',
       'PYTHONPATH':os.pathsep.join([str(root/'runtime'), str(root/'runtime/dk-pr-address-reviews/scripts')])}
with tempfile.TemporaryDirectory(prefix=root.name+'-tests-') as temporary:
    for index, suite in enumerate(suites):
        if not suite.exists():
            continue
        command = [sys.executable, '-m', 'pytest', '-q', str(suite), '--tb=short',
                   '--basetemp', str(Path(temporary)/str(index)), '-o',
                   'cache_dir='+str(Path(temporary)/'cache')]
        result = subprocess.run(command, cwd=root, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
