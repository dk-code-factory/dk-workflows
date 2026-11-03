#!/usr/bin/env python3
"""Run offline workflow unit and full-recipe tests with an explicit skill dependency."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--pr-skills-root', type=Path, default=os.environ.get('DK_PR_SKILLS_ROOT'))
a = p.parse_args()
dependency = Path(a.pr_skills_root or ROOT/'.dependencies/dk-pr-skills').resolve()
pin = json.loads((ROOT/'test-dependencies.json').read_text())['pr_skills']
manifest = dependency/'.codex-plugin/plugin.json'
if not manifest.is_file():
    p.error('Provide --pr-skills-root or DK_PR_SKILLS_ROOT for '+pin['repository']+' at '+pin['revision'])
metadata = json.loads(manifest.read_text())
if metadata.get('name') != 'dk-pr-skills' or metadata.get('version') != pin['version']:
    p.error('The test dependency must match the pinned skill identity/version')
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'DK_PR_SKILLS_ROOT':str(dependency)}
with tempfile.TemporaryDirectory(prefix='dk-workflows-v2-tests-') as temporary:
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(ROOT/'tests'), '--tb=short',
                             '--basetemp', str(Path(temporary)/'tests')], cwd=ROOT, env=env)
    raise SystemExit(result.returncode)
