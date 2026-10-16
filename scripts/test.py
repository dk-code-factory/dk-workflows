#!/usr/bin/env python3
"""Run independent regression suites without module-name collisions."""
import os,subprocess,sys
from pathlib import Path
r=Path(__file__).resolve().parent.parent
env=dict(os.environ,DK_HOST='codex',PYTHONPATH=str(r/'runtime'))
dirs=[r/'tests']+sorted(r.glob('runtime/*/tests'))+sorted(r.glob('common/*/tests'))
if (r/'runtime/dk-pr-address-reviews/scripts').exists():dirs.append(r/'runtime/dk-pr-address-reviews/scripts')
for d in dirs:
 if not list(d.glob('test_*.py')):continue
 p=subprocess.run([sys.executable,'-m','pytest','-q',str(d),'--tb=short'],cwd=r,env=env)
 if p.returncode:sys.exit(p.returncode)
